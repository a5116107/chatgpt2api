import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("CHATGPT2API_AUTH_KEY", "chatgpt2api")

from services.register import mail_provider


class OutlookExternalProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _outlook_external(**entry_overrides):
        entry = {
            "provider_ref": "outlook_external#1",
            "api_key": "test-key",
            "use_plus_alias": False,
            "realtime_preflight": False,
            **entry_overrides,
        }
        return mail_provider.OutlookExternalApiProvider(
            entry, mail_provider._config({})
        )

    def test_outlook_login_landing_is_retired_and_retried(self):
        used_file = Path(self.temp_dir.name) / "outlook_external_used.json"
        mailbox = {
            "provider": "outlook_external",
            "address": "alias@example.com",
            "resolved_email": "main@example.com",
        }
        with patch.object(mail_provider, "_outlook_external_used_file", return_value=used_file):
            mail_provider.mark_mailbox_result(mailbox, success=False, error="invalid_auth_step")
            used = json.loads(used_file.read_text(encoding="utf-8"))

        self.assertIn("main@example.com", used)

    def test_outlook_prefers_accounts_with_successful_mail_refresh(self):
        provider = self._outlook_external()
        provider._list_accounts = lambda: [
            {
                "email": "unknown@outlook.com",
                "status": "active",
                "last_refresh_status": None,
            },
            {
                "email": "failed@outlook.com",
                "status": "active",
                "last_refresh_status": "failed",
            },
            {
                "email": "healthy@outlook.com",
                "status": "active",
                "last_refresh_status": "success",
            },
        ]
        try:
            with (
                patch.object(mail_provider, "provider_index", 0),
                patch.object(mail_provider, "_load_outlook_external_used", return_value=set()),
            ):
                mailbox = provider.create_mailbox()
        finally:
            provider.close()

        self.assertEqual(mailbox["address"], "healthy@outlook.com")

    def test_outlook_falls_back_to_unknown_refresh_and_excludes_failed(self):
        provider = self._outlook_external()
        provider._list_accounts = lambda: [
            {
                "email": "failed@outlook.com",
                "status": "active",
                "last_refresh_status": "failed",
            },
            {
                "email": "unknown@outlook.com",
                "status": "active",
                "last_refresh_status": None,
            },
        ]
        try:
            with (
                patch.object(mail_provider, "provider_index", 0),
                patch.object(mail_provider, "_load_outlook_external_used", return_value=set()),
            ):
                mailbox = provider.create_mailbox()
        finally:
            provider.close()

        self.assertEqual(mailbox["address"], "unknown@outlook.com")

    def test_outlook_does_not_reactivate_disabled_accounts(self):
        provider = self._outlook_external()
        provider._list_accounts = lambda: [
            {
                "email": "disabled@outlook.com",
                "status": "disabled",
                "last_refresh_status": "success",
            },
            {
                "email": "suspended@outlook.com",
                "status": "suspended",
                "last_refresh_status": None,
            },
        ]
        try:
            with (
                patch.object(mail_provider, "provider_index", 0),
                patch.object(mail_provider, "_load_outlook_external_used", return_value=set()),
            ):
                with self.assertRaisesRegex(RuntimeError, "可取信主邮箱已耗尽"):
                    provider.create_mailbox()
        finally:
            provider.close()

    def test_outlook_realtime_preflight_skips_an_unreadable_account(self):
        provider = self._outlook_external(realtime_preflight=True, preflight_attempts=3)
        provider._list_accounts = lambda: [
            {
                "email": "stale@outlook.com",
                "status": "active",
                "last_refresh_status": "success",
                "last_refresh_at": "2026-07-13 12:20:12",
            },
            {
                "email": "readable@outlook.com",
                "status": "active",
                "last_refresh_status": "success",
                "last_refresh_at": "2026-07-03 23:46:33",
            },
        ]
        checked = []

        def fake_request(method, path, **kwargs):
            email = kwargs["params"]["email"]
            checked.append(email)
            if email == "stale@outlook.com":
                raise RuntimeError("all mailbox methods failed")
            return {"success": True, "emails": []}

        provider._request = fake_request
        try:
            with (
                patch.object(mail_provider, "provider_index", 0),
                patch.object(mail_provider, "_load_outlook_external_used", return_value=set()),
            ):
                mailbox = provider.create_mailbox()
        finally:
            provider.close()

        self.assertEqual(checked, ["stale@outlook.com", "readable@outlook.com"])
        self.assertEqual(mailbox["address"], "readable@outlook.com")
        self.assertTrue(mailbox["preflight_checked"])
        self.assertEqual(mailbox["preflight_skipped"], 1)

    def test_outlook_default_preflight_reaches_a_readable_account_after_ten_stale_accounts(self):
        provider = self._outlook_external(realtime_preflight=True)
        provider._list_accounts = lambda: [
            {
                "email": f"stale-{index:02d}@outlook.com",
                "status": "active",
                "last_refresh_status": "success",
                "last_refresh_at": f"2026-07-20 12:{59 - index:02d}:00",
            }
            for index in range(10)
        ] + [
            {
                "email": "readable@outlook.com",
                "status": "active",
                "last_refresh_status": "success",
                "last_refresh_at": "2026-07-20 11:00:00",
            }
        ]
        checked = []

        def fake_request(method, path, **kwargs):
            email = kwargs["params"]["email"]
            checked.append(email)
            if email.startswith("stale-"):
                raise RuntimeError("all mailbox methods failed")
            return {"success": True, "emails": []}

        provider._request = fake_request
        try:
            with (
                patch.object(mail_provider, "provider_index", 0),
                patch.object(mail_provider, "_load_outlook_external_used", return_value=set()),
            ):
                mailbox = provider.create_mailbox()
        finally:
            provider.close()

        self.assertEqual(len(checked), 11)
        self.assertEqual(mailbox["address"], "readable@outlook.com")
        self.assertEqual(mailbox["preflight_skipped"], 10)

    def test_outlook_realtime_preflight_falls_back_from_stale_healthy_to_unknown(self):
        provider = self._outlook_external(realtime_preflight=True, preflight_attempts=2)
        provider._list_accounts = lambda: [
            {
                "email": "stale@outlook.com",
                "status": "active",
                "last_refresh_status": "success",
            },
            {
                "email": "unknown@outlook.com",
                "status": "active",
                "last_refresh_status": None,
            },
        ]
        checked = []

        def fake_request(method, path, **kwargs):
            email = kwargs["params"]["email"]
            checked.append(email)
            if email == "stale@outlook.com":
                raise RuntimeError("stale refresh token")
            return {"success": True, "emails": []}

        provider._request = fake_request
        try:
            with (
                patch.object(mail_provider, "provider_index", 0),
                patch.object(mail_provider, "_load_outlook_external_used", return_value=set()),
            ):
                mailbox = provider.create_mailbox()
        finally:
            provider.close()

        self.assertEqual(checked, ["stale@outlook.com", "unknown@outlook.com"])
        self.assertEqual(mailbox["address"], "unknown@outlook.com")
        self.assertEqual(mailbox["preflight_skipped"], 1)

    def test_outlook_realtime_preflight_fails_when_no_account_is_readable(self):
        provider = self._outlook_external(realtime_preflight=True, preflight_attempts=1)
        provider._list_accounts = lambda: [
            {
                "email": "stale@outlook.com",
                "status": "active",
                "last_refresh_status": "success",
                "last_refresh_at": "2026-07-13 12:20:12",
            }
        ]
        provider._request = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("all mailbox methods failed")
        )
        try:
            with (
                patch.object(mail_provider, "provider_index", 0),
                patch.object(mail_provider, "_load_outlook_external_used", return_value=set()),
            ):
                with self.assertRaisesRegex(RuntimeError, "实时取信预检失败"):
                    provider.create_mailbox()
        finally:
            provider.close()

    def test_outlook_prefers_server_otp_long_poll(self):
        provider = self._outlook_external(otp_wait_seconds=45, otp_poll_interval=5)
        requests = []

        def fake_request(method, path, params=None, **kwargs):
            requests.append((method, path, params, kwargs))
            return {"success": True, "code": "123456", "email": {"folder": "inbox"}}

        provider._request = fake_request
        provider.fetch_recent_messages = lambda mailbox: [
            {
                "subject": "Your OpenAI verification code",
                "sender": "noreply@tm.openai.com",
                "text_content": "Your verification code is 123456",
                "html_content": "",
                "received_at": datetime.now(timezone.utc),
            }
        ]
        provider._wait_for_code_from_list = lambda mailbox: self.fail("list polling fallback should not run")
        try:
            code = provider.wait_for_code(
                {
                    "address": "alias@example.com",
                    "resolved_email": "main@example.com",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        finally:
            provider.close()

        self.assertEqual(code, "123456")
        self.assertEqual(len(requests), 1)
        method, path, params, kwargs = requests[0]
        self.assertEqual(method, "GET")
        self.assertEqual(path, "/api/external/otp")
        self.assertEqual(params["email"], "alias@example.com")
        self.assertEqual(params["folder"], "all")
        self.assertEqual(params["wait_seconds"], 45)
        self.assertEqual(params["poll_interval"], 5)
        self.assertEqual(kwargs["retries"], 1)
        self.assertGreaterEqual(kwargs["timeout"], 60)

    def test_outlook_rejects_stale_server_otp_before_list_fallback(self):
        provider = self._outlook_external()
        provider._request = lambda *args, **kwargs: {"success": True, "code": "123456"}
        provider.fetch_recent_messages = lambda mailbox: [
            {
                "subject": "Your OpenAI verification code",
                "sender": "noreply@tm.openai.com",
                "text_content": "Your verification code is 123456",
                "html_content": "",
                "received_at": datetime.now(timezone.utc) - timedelta(minutes=10),
            }
        ]
        mailbox = {
            "address": "alias@example.com",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            code, status = provider._wait_for_server_otp(mailbox)
        finally:
            provider.close()

        self.assertIsNone(code)
        self.assertEqual(status, "unverified")

    def test_outlook_server_otp_defaults_to_openai_sender_filter(self):
        provider = self._outlook_external()
        try:
            self.assertEqual(provider.otp_from_contains, "openai")
            self.assertEqual(provider.otp_code_regex, r"(?<!\d)(\d{6})(?!\d)")
        finally:
            provider.close()

    def test_outlook_server_otp_uses_provider_defaults_not_global_mail_wait_settings(self):
        provider = mail_provider.OutlookExternalApiProvider(
            {
                "provider_ref": "outlook_external#1",
                "api_key": "test-key",
                "realtime_preflight": False,
            },
            mail_provider._config({"wait_timeout": 30, "wait_interval": 2}),
        )
        try:
            self.assertEqual(provider.otp_wait_seconds, 120)
            self.assertEqual(provider.otp_poll_interval, 3)
            self.assertEqual(provider.otp_http_timeout, 180.0)
        finally:
            provider.close()

    def test_outlook_provider_proxy_is_isolated_from_global_mail_proxy(self):
        provider = self._outlook_external(
            proxy="http://mail-proxy.example:8118",
            request_timeout=45,
        )
        try:
            self.assertEqual(provider.conf["proxy"], "http://mail-proxy.example:8118")
            self.assertEqual(provider.conf["request_timeout"], 45.0)
            self.assertGreaterEqual(provider.otp_http_timeout, 135.0)
        finally:
            provider.close()

    def test_outlook_empty_provider_proxy_inherits_global_mail_proxy(self):
        provider = mail_provider.OutlookExternalApiProvider(
            {
                "provider_ref": "outlook_external#1",
                "api_key": "test-key",
                "proxy": "",
                "realtime_preflight": False,
            },
            mail_provider._config({"proxy": "http://global-mail-proxy.example:8118"}),
        )
        try:
            self.assertEqual(provider.conf["proxy"], "http://global-mail-proxy.example:8118")
        finally:
            provider.close()

    def test_outlook_server_otp_unavailable_falls_back_to_message_list(self):
        provider = self._outlook_external()
        mailbox = {"address": "alias@example.com", "resolved_email": "main@example.com"}
        provider._request = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("outlook_external GET /api/external/otp HTTP 404")
        )
        provider.fetch_recent_messages = lambda item: [
            {
                "provider": "outlook_external",
                "mailbox": item["address"],
                "message_id": "message-1",
                "subject": "OpenAI verification code",
                "sender": "noreply@openai.com",
                "text_content": "Your verification code is 654321",
                "received_at": None,
                "raw": {},
            }
        ]
        try:
            code = provider.wait_for_code(mailbox)
        finally:
            provider.close()

        self.assertEqual(code, "654321")
        self.assertTrue(mailbox["_server_otp_disabled"])

    def test_outlook_server_otp_network_error_falls_back_to_message_list(self):
        provider = self._outlook_external()
        mailbox = {"address": "alias@example.com", "resolved_email": "main@example.com"}
        provider._request = lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("mail api timeout"))
        provider.fetch_recent_messages = lambda item: [
            {
                "provider": "outlook_external",
                "mailbox": item["address"],
                "message_id": "message-timeout",
                "subject": "OpenAI verification code",
                "sender": "noreply@openai.com",
                "text_content": "Your verification code is 741852",
                "received_at": None,
                "raw": {},
            }
        ]
        try:
            code = provider.wait_for_code(mailbox)
        finally:
            provider.close()

        self.assertEqual(code, "741852")
        self.assertNotIn("_server_otp_disabled", mailbox)

    def test_outlook_server_otp_timeout_does_not_repeat_list_wait(self):
        provider = self._outlook_external()
        provider._request = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("outlook_external GET /api/external/otp HTTP 504")
        )
        provider.fetch_recent_messages = lambda mailbox: self.fail("server wait already elapsed")
        try:
            code = provider.wait_for_code({"address": "alias@example.com"})
        finally:
            provider.close()

        self.assertIsNone(code)

if __name__ == "__main__":
    unittest.main()
