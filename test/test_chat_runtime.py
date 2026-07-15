from __future__ import annotations

import unittest
from unittest import mock

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
