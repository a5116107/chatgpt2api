from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from services.register.openai_signup_compat.legacy_create_account import install_create_account_fallback
from services.register.openai_signup_compat.sentinel_runner import SentinelHeaders


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload


class CreateAccountFallbackTests(unittest.TestCase):
    def test_create_account_uses_sdk_and_so_headers_first(self) -> None:
        calls: list[tuple[str, dict]] = []

        def original_post(url, **kwargs):
            calls.append((str(url), dict(kwargs)))
            if "sentinel/req" in str(url):
                return _Resp(200, {"token": "challenge-token", "proofofwork": {"required": False}, "so": {"required": True}})
            headers = kwargs.get("headers") or {}
            so = ""
            for k, v in headers.items():
                if str(k).lower() == "openai-sentinel-so-token":
                    so = str(v)
            if so:
                return _Resp(200, {"continue_url": "https://platform.openai.com/auth/callback?code=abc"})
            return _Resp(400, {"error": {"code": "registration_disallowed", "message": "Sorry"}})

        session = SimpleNamespace(post=original_post, cookies={})
        sdk_headers = SentinelHeaders(
            token='{"p":"x","t":"","c":"y","id":"did","flow":"oauth_create_account"}',
            so_token="so-token-value",
            token_len=10,
            so_len=14,
            so_present=True,
            route="sdk",
            flow="oauth_create_account",
            sdk="sdk.js",
            observer_wait_ms=5000,
        )

        with mock.patch(
            "services.register.openai_signup_compat.legacy_create_account.generate_sentinel_headers",
            return_value=sdk_headers,
        ), mock.patch(
            "services.register.openai_signup_compat.legacy_create_account.record_route_event",
            return_value=None,
        ):
            install_create_account_fallback(session, owner=SimpleNamespace(device_id="did-1", email="a@b.c", mail_provider="tempmail_lol"))
            resp = session.post(
                "https://auth.openai.com/api/accounts/create_account",
                headers={"openai-sentinel-token": "legacy", "user-agent": "UA"},
                json={"name": "A B", "birthdate": "1990-01-01"},
            )

        self.assertEqual(resp.status_code, 200)
        create_calls = [kwargs for url, kwargs in calls if url.endswith("/api/accounts/create_account")]
        self.assertEqual(len(create_calls), 1)
        first_headers = {str(k).lower(): v for k, v in (create_calls[0].get("headers") or {}).items()}
        self.assertEqual(first_headers.get("openai-sentinel-token"), sdk_headers.token)
        self.assertEqual(first_headers.get("openai-sentinel-so-token"), "so-token-value")

    def test_request_entrypoint_also_uses_sdk_and_so_headers_first(self) -> None:
        create_calls: list[dict] = []

        def original_post(url, **_kwargs):
            if "sentinel/req" in str(url):
                return _Resp(
                    200,
                    {
                        "token": "challenge-token",
                        "proofofwork": {"required": False},
                        "so": {"required": True},
                    },
                )
            raise AssertionError(f"unexpected post: {url}")

        def original_request(method, url, **kwargs):
            self.assertEqual(method, "POST")
            create_calls.append(dict(kwargs))
            headers = {
                str(key).lower(): value
                for key, value in (kwargs.get("headers") or {}).items()
            }
            if headers.get("openai-sentinel-so-token"):
                return _Resp(200, {"continue_url": "https://platform.openai.com/auth/callback?code=abc"})
            return _Resp(400, {"error": {"code": "registration_disallowed"}})

        session = SimpleNamespace(
            post=original_post,
            request=original_request,
            cookies={},
        )
        sdk_headers = SentinelHeaders(
            token="sdk-token",
            so_token="sdk-so-token",
            token_len=9,
            so_len=12,
            so_present=True,
            route="sdk",
            flow="oauth_create_account",
            sdk="sdk.js",
            observer_wait_ms=5000,
        )

        with mock.patch(
            "services.register.openai_signup_compat.legacy_create_account.generate_sentinel_headers",
            return_value=sdk_headers,
        ), mock.patch(
            "services.register.openai_signup_compat.legacy_create_account.record_route_event",
            return_value=None,
        ):
            install_create_account_fallback(
                session,
                owner=SimpleNamespace(
                    device_id="did-1",
                    email="a@b.c",
                    mail_provider="outlook_external",
                ),
            )
            response = session.request(
                "POST",
                "https://auth.openai.com/api/accounts/create_account",
                headers={"openai-sentinel-token": "legacy", "user-agent": "UA"},
                json={"name": "A B", "birthdate": "1990-01-01"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(create_calls), 1)
        first_headers = {
            str(key).lower(): value
            for key, value in (create_calls[0].get("headers") or {}).items()
        }
        self.assertEqual(first_headers.get("openai-sentinel-token"), "sdk-token")
        self.assertEqual(first_headers.get("openai-sentinel-so-token"), "sdk-so-token")

    def test_sdk_first_429_does_not_retry_legacy_post(self) -> None:
        create_calls: list[dict] = []

        def original_post(url, **kwargs):
            if "sentinel/req" in str(url):
                return _Resp(200, {"token": "challenge-token", "so": {"required": True}})
            create_calls.append(dict(kwargs))
            return _Resp(429, {"error": {"code": "rate_limit_exceeded"}})

        session = SimpleNamespace(post=original_post, cookies={})
        sdk_headers = SentinelHeaders(
            token="sdk-token",
            so_token="sdk-so-token",
            token_len=9,
            so_len=12,
            so_present=True,
            route="sdk",
            flow="oauth_create_account",
            sdk="sdk.js",
            observer_wait_ms=5000,
        )

        with mock.patch(
            "services.register.openai_signup_compat.legacy_create_account.generate_sentinel_headers",
            return_value=sdk_headers,
        ), mock.patch(
            "services.register.openai_signup_compat.legacy_create_account.record_route_event",
            return_value=None,
        ):
            install_create_account_fallback(session)
            response = session.post(
                "https://auth.openai.com/api/accounts/create_account",
                headers={"openai-sentinel-token": "legacy", "user-agent": "UA"},
                json={"name": "A B", "birthdate": "1990-01-01"},
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(create_calls), 1)

    def test_sdk_first_429_does_not_retry_legacy_request(self) -> None:
        create_calls: list[dict] = []

        def original_post(url, **_kwargs):
            if "sentinel/req" in str(url):
                return _Resp(200, {"token": "challenge-token", "so": {"required": True}})
            raise AssertionError(f"unexpected post: {url}")

        def original_request(_method, _url, **kwargs):
            create_calls.append(dict(kwargs))
            return _Resp(429, {"error": {"code": "rate_limit_exceeded"}})

        session = SimpleNamespace(
            post=original_post,
            request=original_request,
            cookies={},
        )
        sdk_headers = SentinelHeaders(
            token="sdk-token",
            so_token="sdk-so-token",
            token_len=9,
            so_len=12,
            so_present=True,
            route="sdk",
            flow="oauth_create_account",
            sdk="sdk.js",
            observer_wait_ms=5000,
        )

        with mock.patch(
            "services.register.openai_signup_compat.legacy_create_account.generate_sentinel_headers",
            return_value=sdk_headers,
        ), mock.patch(
            "services.register.openai_signup_compat.legacy_create_account.record_route_event",
            return_value=None,
        ):
            install_create_account_fallback(session)
            response = session.request(
                "POST",
                "https://auth.openai.com/api/accounts/create_account",
                headers={"openai-sentinel-token": "legacy", "user-agent": "UA"},
                json={"name": "A B", "birthdate": "1990-01-01"},
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(create_calls), 1)

    def test_registration_disallowed_keeps_one_legacy_ab_attempt(self) -> None:
        create_calls: list[dict] = []

        def original_post(url, **kwargs):
            if "sentinel/req" in str(url):
                return _Resp(200, {"token": "challenge-token", "so": {"required": True}})
            create_calls.append(dict(kwargs))
            headers = {
                str(key).lower(): value
                for key, value in (kwargs.get("headers") or {}).items()
            }
            if headers.get("openai-sentinel-so-token"):
                return _Resp(400, {"error": {"code": "registration_disallowed"}})
            return _Resp(200, {"continue_url": "https://platform.openai.com/auth/callback?code=abc"})

        session = SimpleNamespace(post=original_post, cookies={})
        sdk_headers = SentinelHeaders(
            token="sdk-token",
            so_token="sdk-so-token",
            token_len=9,
            so_len=12,
            so_present=True,
            route="sdk",
            flow="oauth_create_account",
            sdk="sdk.js",
            observer_wait_ms=5000,
        )

        with mock.patch(
            "services.register.openai_signup_compat.legacy_create_account.generate_sentinel_headers",
            return_value=sdk_headers,
        ), mock.patch(
            "services.register.openai_signup_compat.legacy_create_account.record_route_event",
            return_value=None,
        ) as record_event:
            install_create_account_fallback(session)
            response = session.post(
                "https://auth.openai.com/api/accounts/create_account",
                headers={"openai-sentinel-token": "legacy", "user-agent": "UA"},
                json={"name": "A B", "birthdate": "1990-01-01"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(create_calls), 2)
        second_headers = {
            str(key).lower(): value
            for key, value in (create_calls[1].get("headers") or {}).items()
        }
        self.assertNotIn("openai-sentinel-so-token", second_headers)
        self.assertEqual(second_headers.get("openai-sentinel-token"), "legacy")
        route_outcomes = [
            (call.kwargs["sentinel_route"], call.kwargs["success"])
            for call in record_event.call_args_list
        ]
        self.assertEqual(route_outcomes, [("sdk", False), ("legacy", True)])


if __name__ == "__main__":
    unittest.main()
