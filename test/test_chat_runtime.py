from __future__ import annotations

import unittest
from unittest import mock
from types import SimpleNamespace

from services import openai_backend_api as backend_module
from services.config import _normalize_chat_runtime_settings
from services.protocol import conversation as conversation_module


class ChatRuntimeConfigTests(unittest.TestCase):
    def test_defaults_and_minimums_are_normalized(self) -> None:
        defaults = _normalize_chat_runtime_settings(None)
        self.assertEqual(defaults["connect_timeout_secs"], 10)
        self.assertEqual(defaults["response_timeout_secs"], 60)
        self.assertEqual(defaults["max_account_rotates"], 8)
        self.assertTrue(defaults["rotate_on_timeout"])

        normalized = _normalize_chat_runtime_settings({
            "connect_timeout_secs": 0,
            "response_timeout_secs": 1,
            "max_account_rotates": -2,
            "rotate_on_timeout": "false",
        })
        self.assertEqual(normalized["connect_timeout_secs"], 1)
        self.assertEqual(normalized["response_timeout_secs"], 10)
        self.assertEqual(normalized["max_account_rotates"], 0)
        self.assertFalse(normalized["rotate_on_timeout"])


class ChatStreamDeadlineTests(unittest.TestCase):
    def test_bootstrap_browser_verification_keeps_403_and_never_changes_transport(self) -> None:
        from utils.helper import UpstreamHTTPError

        backend = object.__new__(backend_module.OpenAIBackendAPI)
        backend.base_url = "https://fixture.invalid"
        backend.session = mock.Mock()
        backend.session.headers = {}
        backend.session.get.return_value.status_code = 403
        backend.session.get.return_value.text = "<html>Enable JavaScript and cookies to continue</html>"
        backend._bootstrap_headers = mock.Mock(return_value={})
        with self.assertRaises(UpstreamHTTPError) as raised:
            backend._bootstrap()
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.context, "bootstrap")
        self.assertEqual(raised.exception.body, "upstream_browser_verification_required")
        backend.session.get.assert_called_once()

    def test_bootstrap_does_not_send_inherited_authorization(self) -> None:
        backend = object.__new__(backend_module.OpenAIBackendAPI)
        backend.base_url = "https://fixture.invalid"
        backend.session = mock.Mock()
        backend.session.headers = {"Authorization": "Bearer fixture-token", "User-Agent": "fixture"}
        observed = []
        def get(_url, **kwargs):
            observed.append(dict(backend.session.headers) | dict(kwargs.get("headers") or {}))
            return SimpleNamespace(status_code=200, text="<html></html>")
        backend.session.get.side_effect = get
        backend._bootstrap_headers = mock.Mock(return_value={"Authorization": "Bearer fixture-token"})
        backend._bootstrap()
        self.assertNotIn("Authorization", observed[0])
        self.assertEqual(backend.session.headers["Authorization"], "Bearer fixture-token")

    def test_bootstrap_denial_does_not_retry_another_text_account(self) -> None:
        backend = mock.Mock(access_token="fixture-token")
        with (
            mock.patch.object(conversation_module, "conversation_events", side_effect=RuntimeError("bootstrap failed: status=403")),
            mock.patch.object(conversation_module.account_service, "get_text_access_token") as select,
            mock.patch.object(conversation_module, "_record_runtime_risk"),
        ):
            with self.assertRaisesRegex(RuntimeError, "status=403"):
                list(conversation_module.stream_text_deltas(backend, conversation_module.ConversationRequest(prompt="hello")))
        select.assert_not_called()
        backend.close.assert_called_once()

    def test_chat_request_routes_the_requested_model_before_backend_creation(self) -> None:
        from services.protocol import openai_v1_chat_complete as chat

        with (
            mock.patch.object(chat, "text_chat_parts", return_value=("fixture-model", [])),
            mock.patch.object(chat, "text_backend") as backend,
            mock.patch.object(chat, "collect_text", return_value="ok"),
            mock.patch.object(chat, "completion_response", return_value={"ok": True}),
            mock.patch.object(chat.chat_completion_cache, "get_or_compute_response", side_effect=lambda key, fn: fn()),
        ):
            self.assertEqual(chat.handle({"model": "fixture-model", "messages": []}), {"ok": True})
        backend.assert_called_once_with(model="fixture-model")

    def test_responses_request_routes_the_requested_model_before_backend_creation(self) -> None:
        from services.protocol import openai_v1_response as responses

        with (
            mock.patch.object(responses, "is_text_response_request", return_value=True),
            mock.patch.object(responses, "text_response_parts", return_value=("fixture-model", [])),
            mock.patch.object(responses, "text_backend") as backend,
            mock.patch.object(responses, "stream_text_response", return_value=iter([{"type": "response.completed"}])),
            mock.patch.object(responses.chat_completion_cache, "get_or_compute_stream", side_effect=lambda key, fn: fn()),
        ):
            self.assertEqual(list(responses.response_events({"model": "fixture-model"})), [{"type": "response.completed"}])
        backend.assert_called_once_with(model="fixture-model")

    def test_stream_conversation_uses_configured_deadline(self) -> None:
        backend = backend_module.OpenAIBackendAPI.__new__(backend_module.OpenAIBackendAPI)
        backend.base_url = "https://example.test"
        backend._bootstrap = mock.Mock()
        backend._get_chat_requirements = mock.Mock(return_value=backend_module.ChatRequirements(token="req"))
        backend._chat_target = mock.Mock(return_value=("/backend-api/conversation", "Asia/Shanghai"))
        backend._conversation_payload = mock.Mock(return_value={"action": "next"})
        backend._conversation_headers = mock.Mock(return_value={})
        response = mock.Mock()
        response.close = mock.Mock()
        backend.session = mock.Mock()
        backend.session.post.return_value = response

        with (
            mock.patch.object(
                backend_module.config,
                "get_chat_runtime_settings",
                return_value={"connect_timeout_secs": 7, "response_timeout_secs": 41},
            ),
            mock.patch.object(backend_module, "ensure_ok"),
            mock.patch.object(backend_module, "iter_sse_payloads", return_value=iter(["payload"])),
        ):
            payloads = list(backend.stream_conversation(prompt="hello"))

        self.assertEqual(payloads, ["payload"])
        self.assertEqual(backend.session.post.call_args.kwargs["timeout"], (7, 41))
        response.close.assert_called_once()


class ChatTimeoutRotationTests(unittest.TestCase):
    def test_denials_never_rotate_to_another_text_account(self) -> None:
        request = conversation_module.ConversationRequest(prompt="hello")
        for message in (
            "/backend-api/f/conversation failed: status=403, body=",
            "chat_requirements failed: status=403, body=denied",
            "chat_requirements failed: HTTP 429",
            "CONNECT tunnel failed, response 403. curl: (56)",
        ):
            with self.subTest(message=message):
                backend = mock.Mock(access_token="fixture-token")
                with (
                    mock.patch.object(conversation_module, "conversation_events", side_effect=RuntimeError(message)),
                    mock.patch.object(conversation_module.account_service, "get_text_access_token") as select,
                    mock.patch.object(conversation_module, "_record_runtime_risk") as risk,
                ):
                    with self.assertRaises(RuntimeError):
                        list(conversation_module.stream_text_deltas(backend, request))
                select.assert_not_called()
                risk.assert_called_once()

    def test_timeout_before_first_delta_rotates_to_next_account(self) -> None:
        class InitialBackend:
            access_token = "token-1"

            def close(self) -> None:
                return None

        class FakeBackend:
            def __init__(self, access_token: str):
                self.access_token = access_token
                self.proxy_url = ""

            def close(self) -> None:
                return None

        def fake_events(backend, **_kwargs):
            if backend.access_token == "token-1":
                raise TimeoutError("operation timed out")
            yield {"type": "conversation.delta", "delta": "ok"}

        request = conversation_module.ConversationRequest(prompt="hello")
        with (
            mock.patch.object(conversation_module, "OpenAIBackendAPI", FakeBackend),
            mock.patch.object(conversation_module, "conversation_events", side_effect=fake_events),
            mock.patch.object(
                conversation_module.config,
                "get_chat_runtime_settings",
                return_value={"max_account_rotates": 2, "rotate_on_timeout": True},
            ),
            mock.patch.object(
                conversation_module.account_service,
                "get_text_access_token",
                return_value="token-2",
            ),
            mock.patch.object(conversation_module.account_service, "mark_text_used"),
            mock.patch.object(conversation_module, "_record_runtime_success"),
            mock.patch.object(conversation_module, "_record_runtime_risk"),
        ):
            result = list(conversation_module.stream_text_deltas(InitialBackend(), request))

        self.assertEqual(result, ["ok"])


if __name__ == "__main__":
    unittest.main()
