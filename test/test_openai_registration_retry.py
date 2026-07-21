import os
import time
import unittest
from urllib.parse import unquote, urlparse
from unittest.mock import Mock, patch

from curl_cffi.const import CurlHttpVersion

os.environ.setdefault("CHATGPT2API_AUTH_KEY", "chatgpt2api")

from services.register import openai_register, openai_signup_primitives
from utils import sentinel as sentinel_utils


class FakeResponse:
    def __init__(
        self,
        status_code=200,
        payload=None,
        *,
        url="https://auth.example.test/",
        headers=None,
        history=None,
    ):
        self.status_code = status_code
        self._payload = payload or {}
        self.url = url
        self.headers = headers or {}
        self.history = history or []
        self.text = ""

    def json(self):
        return self._payload


class OpenAiSignupPrimitiveTests(unittest.TestCase):
    def test_generated_password_meets_registration_requirements(self):
        password = openai_signup_primitives.random_password(16)

        self.assertEqual(len(password), 16)
        self.assertTrue(any(character.isupper() for character in password))
        self.assertTrue(any(character.islower() for character in password))
        self.assertTrue(any(character.isdigit() for character in password))
        self.assertTrue(any(character in "!@#$%" for character in password))

    def test_authorize_url_and_callback_params_round_trip(self):
        target = openai_signup_primitives.build_authorize_url(
            "https://auth.example.test",
            {
                "state": "state value",
                "redirect_uri": "https://platform.example.test/auth/callback",
            },
        )
        callback = openai_signup_primitives.extract_oauth_callback_params(
            "https://platform.example.test/auth/callback?code=abc&state=state%20value&scope=openid"
        )

        self.assertIn("state=state+value", target)
        self.assertEqual(
            callback,
            {"code": "abc", "state": "state value", "scope": "openid"},
        )

    def test_continue_state_prefers_nested_page_payload_url(self):
        state = openai_signup_primitives.extract_continue_state(
            {
                "continue_url": "https://auth.example.test/fallback",
                "page": {
                    "type": "about_you",
                    "payload": {
                        "url": "https://auth.example.test/authorize/continue?state=expected"
                    },
                },
            }
        )

        self.assertEqual(
            state,
            {
                "continue_url": "https://auth.example.test/authorize/continue?state=expected",
                "page_type": "about_you",
            },
        )

    def test_callback_extraction_walks_redirect_history_and_validates_state(self):
        callback_url = (
            "https://platform.example.test/auth/callback"
            "?code=durable-code&state=expected-state&scope=openid"
        )
        response = FakeResponse(
            url="https://platform.example.test/auth/callback-complete",
            history=[
                FakeResponse(
                    status_code=302,
                    url="https://auth.example.test/authorize/continue",
                    headers={"location": callback_url},
                ),
                FakeResponse(status_code=302, url=callback_url),
            ],
        )

        callback = openai_signup_primitives.extract_oauth_callback_from_response(
            response,
            expected_state="expected-state",
        )

        self.assertEqual(callback["code"], "durable-code")
        self.assertEqual(callback["state"], "expected-state")
        self.assertEqual(callback["callback_url"], callback_url)

    def test_callback_extraction_rejects_state_mismatch(self):
        response = FakeResponse(
            url="https://platform.example.test/auth/callback?code=abc&state=unexpected"
        )

        with self.assertRaisesRegex(ValueError, "oauth_state_mismatch"):
            openai_signup_primitives.extract_oauth_callback_from_response(
                response,
                expected_state="expected",
            )

    def test_debug_url_redacts_oauth_query_values(self):
        response = FakeResponse(
            url="https://platform.openai.com/auth/callback?code=secret-code&state=secret-state&scope=openid"
        )

        detail = openai_signup_primitives.response_debug_detail(response)

        self.assertNotIn("secret-code", detail)
        self.assertNotIn("secret-state", detail)
        self.assertIn("code=%5BREDACTED%3A11%5D", detail)
        self.assertIn("state=%5BREDACTED%3A12%5D", detail)


class SentinelTransportRecoveryTests(unittest.TestCase):
    def test_req_retries_tls_failure_with_http1_on_same_session(self):
        session = Mock()
        cookie_jar = object()
        session.cookies = cookie_jar
        response = Mock(status_code=200, text='{"token":"challenge-token"}')
        response.json.return_value = {"token": "challenge-token"}
        session.post.side_effect = [
            RuntimeError("curl: (35) TLS connect error"),
            response,
        ]

        token, oai_sc = sentinel_utils.build_sentinel_token(
            session,
            "device-id",
            "username_password_create",
        )

        self.assertEqual(session.post.call_count, 2)
        first_call, recovery_call = session.post.call_args_list
        self.assertNotIn("http_version", first_call.kwargs)
        self.assertEqual(
            recovery_call.kwargs["http_version"],
            CurlHttpVersion.V1_1,
        )
        self.assertIs(session.cookies, cookie_jar)
        self.assertIn('"c":"challenge-token"', token)
        self.assertEqual(oai_sc, "0challenge-token")

    def test_req_does_not_retry_non_tls_failure(self):
        session = Mock()
        session.post.side_effect = RuntimeError("proxy authentication failed")

        with self.assertRaisesRegex(RuntimeError, "proxy authentication failed"):
            sentinel_utils.build_sentinel_token(
                session,
                "device-id",
                "username_password_create",
            )

        session.post.assert_called_once()


class PlatformRegistrationStateMachineTests(unittest.TestCase):
    @staticmethod
    def registrar(session):
        registrar = openai_register.PlatformRegistrar.__new__(
            openai_register.PlatformRegistrar
        )
        registrar.session = session
        registrar.device_id = "device-id"
        registrar.fp = {"oai-session-id": "session-id"}
        registrar.proxy = ""
        registrar.egress_proxy = ""
        registrar.clearance_user_agent = ""
        registrar.clearance_failure_reason = ""
        registrar.runtime_profile = {}
        registrar.oauth_state = "expected-state"
        registrar.platform_auth_code = ""
        return registrar

    def test_authorize_continue_submits_email_and_records_next_state(self):
        session = Mock()
        session.request.return_value = FakeResponse(
            payload={
                "continue_url": "https://auth.openai.com/create-account/password",
                "page": {"type": "create_account_password"},
            },
            url="https://auth.openai.com/api/accounts/authorize/continue",
        )
        registrar = self.registrar(session)

        with (
            patch.object(openai_register, "build_sentinel_token", return_value="sentinel"),
            patch.object(
                openai_register,
                "_headers_with_clearance",
                side_effect=lambda headers, *_args, **_kwargs: headers,
            ),
        ):
            state = registrar._authorize_continue("alias@example.test", 1)

        request = session.request.call_args
        self.assertEqual(request.args[0], "POST")
        self.assertTrue(request.args[1].endswith("/api/accounts/authorize/continue"))
        self.assertEqual(
            request.kwargs["json"],
            {
                "username": {"kind": "email", "value": "alias@example.test"},
                "screen_hint": "signup",
            },
        )
        self.assertEqual(request.kwargs["headers"]["openai-sentinel-token"], "sentinel")
        self.assertEqual(state["page_type"], "create_account_password")

    def test_authorize_continue_is_skipped_after_authorize_accepts_email(self):
        password_page = FakeResponse(
            url="https://auth.openai.com/create-account/password"
        )
        email_page = FakeResponse(
            url="https://auth.openai.com/log-in-or-create-account?usernameKind=email"
        )

        self.assertFalse(openai_register._authorize_continue_required(password_page))
        self.assertTrue(openai_register._authorize_continue_required(email_page))

    def test_platform_authorize_rebuilds_http1_session_after_tls_exhaustion(self):
        failed_session = Mock()
        failed_session.request.side_effect = RuntimeError(
            "curl: (35) TLS connect error"
        )
        recovered_session = Mock()
        recovered_session.request.return_value = FakeResponse(
            status_code=200,
            url="https://auth.openai.com/create-account/password",
        )
        registrar = self.registrar(failed_session)
        registrar.proxy = "http://proxy.example.test:8080"
        registrar.runtime_profile = {"id": "profile-id"}

        with (
            patch.object(openai_register.time, "sleep"),
            patch.object(
                openai_register,
                "create_session",
                return_value=recovered_session,
            ) as create_session,
            patch.object(
                openai_register,
                "_install_create_account_fallback",
                side_effect=lambda session, **_kwargs: session,
            ),
            patch.object(
                openai_register,
                "_headers_with_clearance",
                side_effect=lambda headers, *_args, **_kwargs: headers,
            ),
        ):
            continue_required = registrar._platform_authorize(
                "alias@example.test", 1
            )

        self.assertFalse(continue_required)
        self.assertEqual(failed_session.request.call_count, 4)
        failed_session.close.assert_called_once()
        create_session.assert_called_once_with(
            registrar.proxy,
            registrar.runtime_profile,
            http_version=openai_register.CurlHttpVersion.V1_1,
        )
        recovered_session.request.assert_called_once()

    def test_follow_oauth_continue_captures_callback_before_final_page(self):
        callback_url = (
            "https://platform.openai.com/auth/callback"
            "?code=durable-code&state=expected-state&scope=openid"
        )
        session = Mock()
        session.request.return_value = FakeResponse(
            url="https://platform.openai.com/",
            history=[
                FakeResponse(
                    status_code=302,
                    url="https://auth.openai.com/authorize/continue",
                    headers={"location": callback_url},
                ),
                FakeResponse(status_code=302, url=callback_url),
            ],
        )
        registrar = self.registrar(session)

        with patch.object(
            openai_register,
            "_headers_with_clearance",
            side_effect=lambda headers, *_args, **_kwargs: headers,
        ):
            callback = registrar._follow_oauth_continue(
                "https://auth.openai.com/authorize/continue?state=expected-state",
                1,
                referer="https://auth.openai.com/about-you",
                require_code=True,
            )

        self.assertEqual(callback["code"], "durable-code")
        self.assertEqual(registrar.platform_auth_code, "durable-code")
        self.assertEqual(session.request.call_args.args[0], "GET")
        self.assertTrue(session.request.call_args.kwargs["allow_redirects"])

    def test_follow_oauth_continue_refreshes_clearance_for_challenged_host(self):
        callback_url = (
            "https://platform.openai.com/auth/callback"
            "?code=durable-code&state=expected-state&scope=openid"
        )
        challenge = FakeResponse(status_code=403, url=callback_url)
        challenge.text = "<title>Just a moment...</title>"
        recovered = FakeResponse(status_code=200, url=callback_url)
        session = Mock()
        session.request.side_effect = [challenge, recovered]
        registrar = self.registrar(session)
        registrar._refresh_cloudflare_clearance = Mock(return_value=object())

        with patch.object(
            openai_register,
            "_headers_with_clearance",
            side_effect=lambda headers, *_args, **_kwargs: headers,
        ):
            callback = registrar._follow_oauth_continue(
                "https://auth.openai.com/authorize/continue?state=expected-state",
                1,
                referer="https://auth.openai.com/about-you",
                require_code=True,
            )

        registrar._refresh_cloudflare_clearance.assert_called_once_with(
            callback_url,
            1,
            stage="oauth_continue",
        )
        self.assertEqual(callback["code"], "durable-code")
        self.assertEqual(session.request.call_count, 2)

    def test_registration_proxy_uses_fixed_region_and_long_lease(self):
        proxy = "http://ACCESS-country-RAND-sid-old-ttl-60:SECRET@socks.example.test:8080"
        with patch.dict(
            openai_register.config,
            {"proxy_region": "SG", "proxy_session_ttl_seconds": 900},
            clear=False,
        ):
            normalized = openai_register._normalize_registration_proxy(proxy)

        parsed = urlparse(normalized)
        self.assertEqual(
            unquote(parsed.username or ""),
            "ACCESS-country-SG-sid-old-ttl-900",
        )
        self.assertEqual(parsed.password, "SECRET")

        preserved = openai_register._normalize_registration_proxy(
            "http://ACCESS-country-US-sid-old-ttl-1800:SECRET@socks.example.test:8080"
        )
        self.assertEqual(unquote(urlparse(preserved).username or ""), "ACCESS-country-US-sid-old-ttl-1800")

    def test_registration_proxy_converts_second_lease_for_minute_gateway(self):
        proxy = "http://ACCESS-country-RAND-sid-old-ttl-900:SECRET@socks.example.test:1080"
        with patch.dict(
            openai_register.config,
            {
                "proxy_region": "RAND",
                "proxy_session_ttl_seconds": 900,
                "proxy_ttl_unit": "minutes",
            },
            clear=False,
        ):
            normalized = openai_register._normalize_registration_proxy(proxy)

        self.assertEqual(
            unquote(urlparse(normalized).username or ""),
            "ACCESS-country-RAND-sid-old-ttl-15",
        )

    def test_proxy_ttl_unit_defaults_to_legacy_seconds_for_invalid_values(self):
        self.assertEqual(
            openai_register.openai_registration_policy.normalize_proxy_settings(900, "sg", "invalid"),
            (900, "SG", "seconds"),
        )
        self.assertEqual(
            openai_register.openai_registration_policy.normalize_proxy_settings(900, "rand", "minutes"),
            (900, "RAND", "minutes"),
        )

    def test_clearance_target_drops_oauth_query(self):
        target = openai_register._clearance_target_url(
            "https://platform.openai.com/auth/callback?code=secret&state=state"
        )

        self.assertEqual(target, "https://platform.openai.com/auth/callback")

    def test_refresh_clearance_uses_effective_proxy_and_path_only(self):
        session = Mock()
        session.headers = {}
        registrar = self.registrar(session)
        registrar.proxy = "http://ACCESS:SECRET@socks.example.test:8080"
        registrar.egress_proxy = registrar.proxy
        registrar.runtime_profile = {"id": "profile-id"}
        bundle = openai_register.ClearanceBundle(
            target_host="platform.openai.com",
            proxy_url=registrar.proxy,
            cookies={"cf_clearance": "cookie"},
            user_agent="ua",
        )
        profile = Mock(clearance_enabled=True)

        with (
            patch.object(openai_register.proxy_settings, "get_profile", return_value=profile),
            patch.object(openai_register.proxy_settings, "refresh_clearance", return_value=bundle) as refresh,
        ):
            registrar._refresh_cloudflare_clearance(
                "https://platform.openai.com/auth/callback?code=secret&state=state",
                1,
                stage="oauth_continue",
            )

        self.assertEqual(refresh.call_args.kwargs["target_url"], "https://platform.openai.com/auth/callback")
        self.assertEqual(refresh.call_args.kwargs["proxy"], registrar.proxy)

    def test_register_executes_complete_oauth_state_machine_and_persists_metadata(self):
        registrar = self.registrar(Mock())
        registrar.runtime_profile = {"id": "profile-id"}
        registrar.proxy = "http://proxy.example.test:8080"
        registrar.account_already_exists = False
        registrar._resolve_mail_proxy = Mock(return_value="")

        flow = Mock()
        registrar._platform_authorize = flow.platform_authorize
        registrar._platform_authorize.return_value = True
        registrar._authorize_continue = flow.authorize_continue
        registrar._authorize_continue.return_value = {
            "continue_url": "https://auth.openai.com/create-account/password",
            "page_type": "create_account_password",
        }
        registrar._register_user = flow.register_user
        registrar._register_user.return_value = {
            "continue_url": "https://auth.openai.com/api/accounts/email-otp/send",
            "page_type": "email_otp_send",
        }
        registrar._send_otp = flow.send_otp
        registrar._send_otp.return_value = {
            "continue_url": "https://auth.openai.com/email-verification",
            "page_type": "email_otp_verification",
        }
        registrar._validate_otp = flow.validate_otp
        registrar._validate_otp.return_value = {
            "continue_url": "https://auth.openai.com/about-you",
            "page_type": "about_you",
        }
        registrar._follow_oauth_continue = flow.follow_oauth_continue
        registrar._follow_oauth_continue.return_value = {}
        registrar._create_account = flow.create_account
        registrar._create_account.return_value = {
            "continue_url": "https://auth.openai.com/authorize/continue",
            "page_type": "oauth_callback",
        }
        registrar._exchange_registered_tokens = flow.exchange_registered_tokens
        registrar._exchange_registered_tokens.return_value = {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "id_token": "id-token",
        }
        mailbox = {
            "address": "alias@example.test",
            "provider": "outlook_external",
            "mode": "outlook_alias",
        }

        with (
            patch.object(openai_register, "create_mailbox", return_value=mailbox),
            patch.object(openai_register, "wait_for_code", return_value="123456"),
            patch.object(openai_register.mail_provider, "mark_mailbox_result"),
            patch.object(
                openai_register.runtime_profile_service,
                "bind_account",
                side_effect=lambda _profile_id, account: (account, {"id": "profile-id"}),
            ),
        ):
            result = registrar.register(1)

        self.assertEqual(
            [call[0] for call in flow.method_calls],
            [
                "platform_authorize",
                "authorize_continue",
                "register_user",
                "send_otp",
                "validate_otp",
                "follow_oauth_continue",
                "create_account",
                "follow_oauth_continue",
                "exchange_registered_tokens",
            ],
        )
        self.assertEqual(result["oauth_client_id"], openai_register.platform_oauth_client_id)
        self.assertEqual(result["oauth_token_url"], "https://auth.openai.com/oauth/token")
        self.assertEqual(result["oauth_scope"], "openid profile email offline_access")
        self.assertEqual(result["oauth_source"], "platform_web_registration")


class RegistrationRetryTests(unittest.TestCase):
    def test_registration_attempt_budget_is_clamped_to_supported_bounds(self):
        self.assertEqual(openai_signup_primitives.registration_max_attempts(None), 6)
        self.assertEqual(openai_signup_primitives.registration_max_attempts("invalid"), 6)
        self.assertEqual(openai_signup_primitives.registration_max_attempts(0), 1)
        self.assertEqual(openai_signup_primitives.registration_max_attempts(-4), 1)
        self.assertEqual(openai_signup_primitives.registration_max_attempts(1), 1)
        self.assertEqual(openai_signup_primitives.registration_max_attempts(20), 20)
        self.assertEqual(openai_signup_primitives.registration_max_attempts(99), 20)

    def test_transient_proxy_tls_failures_are_retryable(self):
        for message in (
            "curl: (35) TLS connect error",
            "curl: (55) failed sending data",
            "curl: (56) failure receiving data",
        ):
            with self.subTest(message=message):
                self.assertTrue(openai_register._is_retryable_registration_error(message))

    def test_oauth_http_403_is_retryable_with_cloudflare_backoff(self):
        error = "oauth_continue_http_403"

        self.assertTrue(openai_register._is_retryable_registration_error(error))
        self.assertGreater(
            openai_signup_primitives.registration_retry_delay_seconds(error, 1),
            openai_signup_primitives.registration_retry_delay_seconds(
                "account_creation_failed", 1
            ),
        )

    def test_registration_retry_delay_is_longest_for_rate_limit_and_cloudflare(self):
        tls_delay = openai_signup_primitives.registration_retry_delay_seconds(
            "curl: (35) TLS connect error", 1
        )
        domain_delay = openai_signup_primitives.registration_retry_delay_seconds(
            "account_creation_failed", 1
        )
        cloudflare_delay = openai_signup_primitives.registration_retry_delay_seconds(
            "Cloudflare clearance retry status=403", 1
        )
        rate_limit_delay = openai_signup_primitives.registration_retry_delay_seconds(
            "rate_limit_exceeded HTTP 429", 1
        )

        self.assertGreater(tls_delay, 0)
        self.assertGreater(domain_delay, tls_delay)
        self.assertGreater(cloudflare_delay, domain_delay)
        self.assertGreaterEqual(rate_limit_delay, cloudflare_delay)

    def test_registration_disallowed_creates_a_fresh_registrar_and_retries(self):
        rejected_registrar = Mock()
        rejected_registrar.runtime_profile = None
        rejected_registrar.register.side_effect = RuntimeError(
            "create_account: registration_disallowed"
        )
        successful_registrar = Mock()
        successful_registrar.register.return_value = {
            "email": "fresh@example.test",
            "password": "Generated123!",
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "id_token": "id-token",
        }
        accepted_account = {
            "email": "fresh@example.test",
            "password": "Generated123!",
            "access_token": "durable-access-token",
            "refresh_token": "durable-refresh-token",
            "id_token": "durable-id-token",
            "registration_token_rotated": True,
        }

        with (
            patch.object(
                openai_register,
                "PlatformRegistrar",
                side_effect=[rejected_registrar, successful_registrar],
            ) as registrar_factory,
            patch.object(openai_register.account_service, "add_account_items"),
            patch.object(
                openai_register.account_service,
                "refresh_accounts",
                return_value={"errors": []},
            ),
            patch.object(
                openai_register.account_service,
                "accept_registered_account",
                return_value=accepted_account,
            ) as accept_registered_account,
            patch.object(
                openai_register.account_service,
                "get_account",
                return_value={"email": "fresh@example.test"},
            ),
            patch.object(openai_register.time, "sleep") as sleep_mock,
            patch.dict(
                openai_register.stats,
                {"done": 0, "success": 0, "fail": 0, "start_time": time.time()},
                clear=True,
            ),
        ):
            result = openai_register.worker(1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["email"], "fresh@example.test")
        self.assertEqual(registrar_factory.call_count, 2)
        rejected_registrar.register.assert_called_once_with(1)
        successful_registrar.register.assert_called_once_with(1)
        accept_registered_account.assert_called_once_with("access-token")
        self.assertEqual(result["result"]["access_token"], "durable-access-token")
        sleep_mock.assert_called_once()
        self.assertGreater(sleep_mock.call_args.args[0], 0)

    def test_worker_uses_configured_max_attempts_for_transient_failures(self):
        registrars = []

        def registrar_factory(_proxy):
            registrar = Mock()
            registrar.runtime_profile = None
            registrar.register.side_effect = RuntimeError(
                "curl: (35) TLS connect error"
            )
            registrars.append(registrar)
            return registrar

        with (
            patch.object(
                openai_register,
                "PlatformRegistrar",
                side_effect=registrar_factory,
            ) as factory,
            patch.object(openai_register.time, "sleep") as sleep_mock,
            patch.dict(
                openai_register.config,
                {"max_attempts": 6},
                clear=False,
            ),
            patch.dict(
                openai_register.stats,
                {"done": 0, "success": 0, "fail": 0, "start_time": time.time()},
                clear=True,
            ),
        ):
            result = openai_register.worker(1)

        self.assertFalse(result["ok"])
        self.assertEqual(factory.call_count, 6)
        self.assertEqual(len(registrars), 6)
        self.assertEqual(sleep_mock.call_count, 5)
        for registrar in registrars:
            registrar.register.assert_called_once_with(1)
            registrar.close.assert_called()


if __name__ == "__main__":
    unittest.main()
