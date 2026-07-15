from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from services.openai_backend_api import OpenAIBackendAPI
from services import risk_control_service as risk_module


class UserInfoSessionTests(unittest.TestCase):
    def test_get_user_info_uses_the_calling_thread_for_one_session(self) -> None:
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        backend.access_token = "test-access-token"
        caller_thread = threading.get_ident()
        calls: list[tuple[str, int]] = []

        def response(name: str, payload: dict[str, object]):
            def call(*_args, **_kwargs) -> dict[str, object]:
                calls.append((name, threading.get_ident()))
                return payload

            return call

        backend._get_me = response("me", {"email": "user@example.com", "id": "user-1"})
        backend._get_conversation_init = response(
            "init",
            {
                "limits_progress": [{"feature_name": "image_gen", "remaining": 2, "reset_after": "later"}],
                "default_model_slug": "auto",
            },
        )
        backend._get_default_account = response("account", {"plan_type": "free"})

        result = backend.get_user_info()

        self.assertEqual([name for name, _ in calls], ["me", "init", "account"])
        self.assertEqual({thread_id for _, thread_id in calls}, {caller_thread})
        self.assertEqual(result["quota"], 2)


class CapabilitySyncTests(unittest.TestCase):
    def _sync(self, old_item: dict[str, object]) -> dict[str, object]:
        account = {
            "email": "user@example.com",
            "status": "正常",
            "quota": 5,
            "runtime_profile_id": "profile-1",
        }
        with tempfile.TemporaryDirectory() as directory:
            capability_file = Path(directory) / "account_capabilities.json"
            service = risk_module.RiskControlService()
            with (
                mock.patch.object(risk_module, "CAPABILITIES_FILE", capability_file),
                mock.patch.object(risk_module, "account_service") as account_service,
            ):
                account_service.list_accounts.return_value = [account]
                service._save_items(capability_file, [old_item])
                service.sync_account_capabilities()
                return service._load_items(capability_file)[0]

    def test_sync_replaces_stale_derived_snapshot(self) -> None:
        item = self._sync(
            {
                "account_key": "user@example.com",
                "source": "derived",
                "image": False,
                "image_edit": False,
                "image_variation": False,
                "quota": 0,
            }
        )

        self.assertEqual(item["source"], "derived")
        self.assertEqual(item["quota"], 5)
        self.assertTrue(item["image"])
        self.assertTrue(item["image_edit"])
        self.assertTrue(item["image_variation"])

    def test_sync_preserves_explicit_manual_override(self) -> None:
        item = self._sync(
            {
                "account_key": "user@example.com",
                "source": "manual",
                "image": False,
                "image_edit": False,
                "image_variation": False,
                "quota": 0,
            }
        )

        self.assertEqual(item["source"], "manual")
        self.assertEqual(item["quota"], 5)
        self.assertFalse(item["image"])
        self.assertFalse(item["image_edit"])
        self.assertFalse(item["image_variation"])


if __name__ == "__main__":
    unittest.main()
