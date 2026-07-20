from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import unquote, urlparse
from unittest import mock

os.environ.setdefault("CHATGPT2API_AUTH_KEY", "chatgpt2api")

from api import support as support_module
from api.support import _select_keepalive_tokens
from services.config import ConfigStore
from services.openai_backend_api import OpenAIBackendAPI
from services import risk_control_service as risk_module
from services.proxy_service import (
    FlareSolverrClearanceProvider,
    _apply_dynamic_proxy_session,
)


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


class AccountWatcherSchedulingTests(unittest.TestCase):
    def test_keepalive_excludes_every_token_already_selected_for_refresh(self) -> None:
        selected = ["limited", "normal", "expiring", "recovery"]
        candidates = ["normal", "expiring", "keepalive", "keepalive", "later"]

        result = _select_keepalive_tokens(candidates, selected, max_batch=1)

        self.assertEqual(result, ["keepalive"])

    def test_explicit_empty_proxy_status_url_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                '{"auth-key":"test-auth-key","account_watcher":{"proxy_status_url":""}}',
                encoding="utf-8",
            )

            watcher = ConfigStore(path).get_account_watcher_settings()

        self.assertEqual(watcher["proxy_status_url"], "")

    def test_single_proxy_mode_skips_dynamic_pool_status_lookup(self) -> None:
        watcher = {
            "require_proxy_ready": True,
            "proxy_ready_timeout_secs": 8,
            "proxy_status_url": "",
            "max_batch": 4,
        }
        with (
            mock.patch.object(support_module, "test_proxy", return_value={"ok": True}),
            mock.patch.object(support_module, "_account_watcher_proxy_status") as pool_status,
        ):
            result = support_module._account_watcher_proxy_ready(watcher)

        pool_status.assert_not_called()
        self.assertEqual(result["mode"], "single_proxy")
        self.assertEqual(result["available"], 4)


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


class ProxySessionRoutingTests(unittest.TestCase):
    def test_flaresolverr_rejects_non_clearance_cookies_without_direct_fallback(self) -> None:
        requests = []

        def fake_request(endpoint, body, headers, timeout):
            requests.append(json.loads(body.decode("utf-8")))
            return json.dumps(
                {
                    "status": "ok",
                    "solution": {
                        "status": 403,
                        "cookies": [
                            {
                                "name": "__cf_bm",
                                "value": "ordinary-cookie",
                                "domain": ".auth.openai.com",
                            }
                        ],
                        "userAgent": "ua",
                    },
                }
            ).encode("utf-8")

        provider = FlareSolverrClearanceProvider(
            "http://flaresolverr:8191",
            request_method=fake_request,
        )

        bundle = provider.get_clearance(
            "https://auth.openai.com",
            proxy_url="socks5h://ACCESS:SECRET@socks.example.test:1080",
        )

        self.assertIsNone(bundle)
        self.assertEqual(len(requests), 1)
        self.assertIn("proxy", requests[0])

    def test_flaresolverr_accepts_cf_clearance_from_the_requested_proxy(self) -> None:
        requests = []

        def fake_request(endpoint, body, headers, timeout):
            requests.append(json.loads(body.decode("utf-8")))
            return json.dumps(
                {
                    "status": "ok",
                    "solution": {
                        "status": 200,
                        "cookies": [
                            {
                                "name": "cf_clearance",
                                "value": "clearance-cookie",
                                "domain": ".auth.openai.com",
                            }
                        ],
                        "userAgent": "ua",
                    },
                }
            ).encode("utf-8")

        proxy = "socks5h://ACCESS:SECRET@socks.example.test:1080"
        provider = FlareSolverrClearanceProvider(
            "http://flaresolverr:8191",
            request_method=fake_request,
        )

        bundle = provider.get_clearance(
            "https://auth.openai.com",
            proxy_url=proxy,
        )

        self.assertIsNotNone(bundle)
        self.assertEqual(bundle.proxy_url, proxy)
        self.assertEqual(bundle.cookies, {"cf_clearance": "clearance-cookie"})
        self.assertEqual(len(requests), 1)

    def test_parameterized_proxy_replaces_only_the_session_id(self) -> None:
        proxy = (
            "http://ACCESS-country-US-sid-old-ttl-5-probe-slot-ttl-120:"
            "SECRET%2BVALUE@socks.example.test:8080"
        )

        rotated = _apply_dynamic_proxy_session(proxy, "sess-profile-123")
        parsed = urlparse(rotated)

        self.assertEqual(
            unquote(parsed.username or ""),
            "ACCESS-country-US-sid-sess-profile-123-ttl-120",
        )
        self.assertEqual(parsed.password, "SECRET%2BVALUE")
        self.assertEqual(parsed.hostname, "socks.example.test")
        self.assertEqual(parsed.port, 8080)

    def test_parameterized_proxy_session_is_stable_per_profile(self) -> None:
        proxy = "socks5h://ACCESS-country-RAND-sid-old-ttl-5:SECRET@socks.example.test:1080"

        first = _apply_dynamic_proxy_session(proxy, "sess-profile-a")
        repeated = _apply_dynamic_proxy_session(proxy, "sess-profile-a")
        second = _apply_dynamic_proxy_session(proxy, "sess-profile-b")

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, second)

    def test_plain_authenticated_proxy_is_not_rewritten(self) -> None:
        proxy = "http://ACCESS:SECRET@socks.example.test:8080"

        self.assertEqual(
            _apply_dynamic_proxy_session(proxy, "sess-profile-123"),
            proxy,
        )


if __name__ == "__main__":
    unittest.main()
