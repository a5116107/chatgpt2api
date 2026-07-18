from __future__ import annotations

import unittest
import time
from unittest import mock

from services.account_service import AccountService
from services.account_service import config as account_service_config
from services.storage.base import StorageBackend


class MemoryStorage(StorageBackend):
    def __init__(self, accounts: list[dict] | None = None) -> None:
        self.accounts = [dict(item) for item in (accounts or [])]
        self.auth_keys: list[dict] = []

    def load_accounts(self) -> list[dict]:
        return [dict(item) for item in self.accounts]

    def save_accounts(self, accounts: list[dict]) -> None:
        self.accounts = [dict(item) for item in accounts]

    def load_auth_keys(self) -> list[dict]:
        return [dict(item) for item in self.auth_keys]

    def save_auth_keys(self, auth_keys: list[dict]) -> None:
        self.auth_keys = [dict(item) for item in auth_keys]

    def health_check(self) -> dict:
        return {"status": "ok"}

    def get_backend_info(self) -> dict:
        return {"type": "memory"}


def account(token: str, **updates: object) -> dict:
    item = {
        "access_token": token,
        "email": f"{token}@example.test",
        "status": "正常",
        "quota": 5,
        "type": "free",
        "profile_snapshot": {"status": "active"},
        "profile_status": "active",
    }
    item.update(updates)
    return item


class AccountTokenLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch(
            "services.account_service.runtime_profile_service.ensure_account_profile",
            side_effect=lambda item: (item, {}),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        sync_patcher = mock.patch.object(AccountService, "_sync_risk_control_capabilities")
        sync_patcher.start()
        self.addCleanup(sync_patcher.stop)

    def service(self, *accounts: dict) -> AccountService:
        return AccountService(MemoryStorage(list(accounts)))

    def test_chat_only_revocation_is_recoverable_but_image_revocation_is_terminal(self) -> None:
        self.assertFalse(
            AccountService._access_token_hard_dead(
                {"last_refresh_error": "text_stream:token_revoked"}
            )
        )
        for value in ("image_stream:token_revoked", "status=401 code=token_revoked"):
            with self.subTest(value=value):
                self.assertTrue(AccountService._access_token_hard_dead({"last_refresh_error": value}))

    def test_probe_revocation_field_prevents_candidate_selection(self) -> None:
        service = self.service(account("revoked", image_last_probe_error="status=401 code=token_revoked"))

        self.assertEqual(service._list_ready_candidate_tokens(), [])
        self.assertEqual(service.get_text_access_token(), "")
        with mock.patch.object(
            type(account_service_config),
            "auto_remove_invalid_accounts",
            new_callable=mock.PropertyMock,
            return_value=False,
        ):
            result = service.probe_image_candidates(limit=1)
        self.assertEqual(result["checked"], 0)
        self.assertEqual(result["quarantined"], 1)
        self.assertEqual(service.get_account("revoked")["status"], "禁用")

    def test_remove_revoked_token_quarantines_idempotently(self) -> None:
        service = self.service(account("revoked"))

        service.remove_invalid_token("revoked", "image_stream:token_revoked", quiet=True)
        first = service.get_account("revoked")
        service.remove_invalid_token("revoked", "text_stream:token_revoked", quiet=True)
        second = service.get_account("revoked")

        self.assertEqual(first["status"], "禁用")
        self.assertEqual(first["quota"], 0)
        self.assertEqual(first["token_status"], "revoked")
        self.assertTrue(first["token_revoked_at"])
        self.assertEqual(first["invalid_count"], 1)
        self.assertEqual(second["invalid_count"], 1)
        self.assertEqual(second["token_revoked_at"], first["token_revoked_at"])

    def test_probe_batches_rotate_across_the_whole_pool(self) -> None:
        service = self.service(*(account(f"token-{index}") for index in range(4)))
        visited: list[str] = []

        class FakeBackend:
            def __init__(self, token: str) -> None:
                visited.append(token)

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def set_image_request_context(self, *_args) -> None:
                return None

            def _bootstrap(self) -> None:
                return None

            def _get_chat_requirements(self) -> None:
                return None

        with mock.patch("services.openai_backend_api.OpenAIBackendAPI", FakeBackend):
            first = service.probe_image_candidates(limit=2)
            second = service.probe_image_candidates(limit=2)

        self.assertEqual(first["checked"], 2)
        self.assertEqual(second["checked"], 2)
        self.assertEqual(set(visited), {"token-0", "token-1", "token-2", "token-3"})

    def test_probe_quarantines_explicit_revocation(self) -> None:
        service = self.service(account("revoked", image_quota_updated_at=time.time()))

        class RevokedBackend:
            def __init__(self, _token: str) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def set_image_request_context(self, *_args) -> None:
                return None

            def _bootstrap(self) -> None:
                return None

            def _get_chat_requirements(self) -> None:
                raise RuntimeError("status=401 code=token_revoked")

        with (
            mock.patch("services.openai_backend_api.OpenAIBackendAPI", RevokedBackend),
            mock.patch.object(
                type(account_service_config),
                "auto_remove_invalid_accounts",
                new_callable=mock.PropertyMock,
                return_value=False,
            ),
        ):
            result = service.probe_image_candidates(limit=1)

        self.assertEqual(result["quarantined"], 1)
        self.assertEqual(service.get_account("revoked")["status"], "禁用")

    def test_successful_token_rotation_clears_terminal_markers(self) -> None:
        service = self.service(account(
            "old-token",
            status="禁用",
            quota=0,
            token_status="revoked",
            token_revoked=True,
            token_revoked_at="2026-01-01T00:00:00+00:00",
            token_revoked_source="image_stream:token_revoked",
        ))

        new_token = service._apply_refreshed_tokens(
            "old-token",
            {"access_token": "new-token", "refresh_token": "new-refresh"},
            "test_refresh",
        )

        refreshed = service.get_account(new_token)
        self.assertEqual(new_token, "new-token")
        self.assertEqual(refreshed["status"], "正常")
        self.assertEqual(refreshed["token_status"], "active")
        self.assertFalse(refreshed["token_revoked"])
        self.assertIsNone(refreshed.get("token_revoked_at"))
        self.assertIsNone(refreshed.get("token_revoked_source"))

    def test_image_selection_honors_exclusions_and_detects_alternatives(self) -> None:
        service = self.service(account("first"), account("second"))

        selected = service.get_available_access_token(excluded_tokens={"first"})

        self.assertEqual(selected, "second")
        self.assertTrue(service.has_alternative_image_account("first"))
        self.assertFalse(service.has_alternative_image_account("first", excluded_tokens={"second"}))
        service.release_image_slot(selected)

    def test_image_selection_prefers_the_least_loaded_healthy_account(self) -> None:
        service = self.service(account("first"), account("second"))
        service._image_inflight["first"] = 1
        service._image_inflight_meta["first"] = [time.time()]

        with mock.patch.object(type(account_service_config), "image_account_concurrency", new_callable=mock.PropertyMock, return_value=3):
            selected = service.get_available_access_token()

        self.assertEqual(selected, "second")
        service.release_image_slot(selected)

    def test_image_slot_is_not_released_at_the_old_45_second_boundary(self) -> None:
        service = self.service(account("first"), account("second"))
        service._image_inflight["first"] = 1
        service._image_inflight_meta["first"] = [time.time() - 60.0]

        with (
            mock.patch.object(type(account_service_config), "image_account_concurrency", new_callable=mock.PropertyMock, return_value=1),
            mock.patch.object(type(account_service_config), "image_request_deadline_secs", new_callable=mock.PropertyMock, return_value=120.0),
        ):
            selected = service.get_available_access_token()

        self.assertEqual(selected, "second")
        self.assertEqual(service._image_inflight["first"], 1)
        service.release_image_slot(selected)


if __name__ == "__main__":
    unittest.main()
