from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from services import openai_backend_api as backend_module
from services.config import ConfigStore
from services.protocol import conversation as conversation_module


class ImageProtocolTests(unittest.TestCase):
    def test_web_single_result_prompt_and_url_selection_are_opt_in(self) -> None:
        prompt = conversation_module.build_image_prompt(
            "draw a lighthouse",
            "1536x1536",
            "high",
            single_image=True,
        )
        request = conversation_module.ConversationRequest(single_result=True)

        self.assertIn("仅生成一张图片", prompt)
        self.assertEqual(
            conversation_module._select_image_result_urls(
                ["https://example.test/one.png", "https://example.test/two.png"],
                request,
                "conversation-1",
            ),
            ["https://example.test/one.png"],
        )
        self.assertEqual(
            conversation_module._select_image_result_urls(
                ["https://example.test/one.png", "https://example.test/two.png"],
                conversation_module.ConversationRequest(),
                "conversation-1",
            ),
            ["https://example.test/one.png", "https://example.test/two.png"],
        )

    def backend(self, prepare_data: dict | None = None):
        backend = backend_module.OpenAIBackendAPI.__new__(backend_module.OpenAIBackendAPI)
        backend.base_url = "https://example.test"
        backend.account = {"default_model_slug": "auto"}
        backend.image_deadline_monotonic = None
        backend._headers = lambda _path, headers=None: dict(headers or {})
        prepare_response = mock.Mock()
        prepare_response.json.return_value = prepare_data or {"conduit_token": "conduit-token"}
        start_response = mock.Mock()
        backend.session = mock.Mock()
        backend.session.post.side_effect = [prepare_response, start_response]
        return backend, start_response

    def test_prepare_and_start_share_context_and_match_picture_protocol(self) -> None:
        backend, _response = self.backend()
        requirements = backend_module.ChatRequirements(
            token="requirements-token",
            so_token="so-token",
        )

        with mock.patch.object(backend_module, "ensure_ok"):
            context = backend._prepare_image_conversation("draw a lighthouse", requirements, "gpt-image-2")
            backend._start_image_generation(
                "draw a lighthouse",
                requirements,
                context,
                "gpt-image-2",
            )

        prepare_call, start_call = backend.session.post.call_args_list
        prepare_payload = prepare_call.kwargs["json"]
        start_payload = start_call.kwargs["json"]

        self.assertEqual(prepare_payload["client_prepare_state"], "none")
        self.assertEqual(start_payload["client_prepare_state"], "sent")
        self.assertEqual(prepare_payload["thinking_effort"], "standard")
        self.assertEqual(start_payload["thinking_effort"], "standard")
        self.assertEqual(prepare_payload["parent_message_id"], start_payload["parent_message_id"])
        self.assertEqual(prepare_payload["partial_query"]["id"], start_payload["messages"][0]["id"])
        self.assertEqual(prepare_payload["model"], "auto")
        self.assertEqual(start_payload["model"], "auto")
        self.assertEqual(prepare_call.kwargs["headers"]["X-Conduit-Token"], "no-token")
        self.assertEqual(prepare_call.kwargs["headers"]["OpenAI-Sentinel-SO-Token"], "so-token")
        self.assertEqual(start_call.kwargs["headers"]["X-Conduit-Token"], "conduit-token")
        self.assertEqual(start_call.kwargs["headers"]["OpenAI-Sentinel-SO-Token"], "so-token")

    def test_missing_conduit_token_fails_before_start(self) -> None:
        backend, _response = self.backend({"other": "value"})
        requirements = backend_module.ChatRequirements(token="requirements-token")

        with mock.patch.object(backend_module, "ensure_ok"):
            with self.assertRaisesRegex(RuntimeError, "missing conduit_token"):
                backend._prepare_image_conversation("draw a lighthouse", requirements, "gpt-image-2")

    def test_duplicate_file_and_sediment_reference_is_resolved_once(self) -> None:
        backend = backend_module.OpenAIBackendAPI.__new__(backend_module.OpenAIBackendAPI)
        backend._get_file_download_url = mock.Mock(return_value="https://example.test/file.png")
        backend._get_attachment_download_url = mock.Mock(return_value="https://example.test/attachment.png")

        urls = backend._resolve_image_urls("conversation-1", ["file-1"], ["file-1"])

        self.assertEqual(urls, ["https://example.test/file.png"])
        backend._get_attachment_download_url.assert_not_called()

    def test_sediment_reference_remains_fallback_when_file_resolution_fails(self) -> None:
        backend = backend_module.OpenAIBackendAPI.__new__(backend_module.OpenAIBackendAPI)
        backend._get_file_download_url = mock.Mock(side_effect=RuntimeError("file lookup failed"))
        backend._get_attachment_download_url = mock.Mock(return_value="https://example.test/attachment.png")

        urls = backend._resolve_image_urls("conversation-1", ["file-1"], ["file-1"])

        self.assertEqual(urls, ["https://example.test/attachment.png"])
        backend._get_attachment_download_url.assert_called_once_with("conversation-1", "file-1")

    def test_cancelled_poll_race_loser_is_not_logged_as_timeout(self) -> None:
        backend = backend_module.OpenAIBackendAPI.__new__(backend_module.OpenAIBackendAPI)
        backend.image_deadline_monotonic = None
        backend.image_request_id = "cancelled-poll-test"
        backend._report_progress = lambda _step: None
        cancel_event = threading.Event()

        def get_conversation(*_args, **_kwargs):
            cancel_event.set()
            return {}

        backend._get_conversation = get_conversation
        with (
            mock.patch.object(backend_module.logger, "info") as info_log,
            mock.patch.object(backend_module.logger, "debug") as debug_log,
        ):
            with self.assertRaisesRegex(backend_module.ImagePollTimeoutError, "cancelled"):
                backend._poll_image_results(
                    "conversation-1",
                    timeout_secs=1.0,
                    cancel_event=cancel_event,
                    initial_wait_secs=0.0,
                )

        info_events = [
            call.args[0].get("event")
            for call in info_log.call_args_list
            if call.args and isinstance(call.args[0], dict)
        ]
        debug_events = [
            call.args[0].get("event")
            for call in debug_log.call_args_list
            if call.args and isinstance(call.args[0], dict)
        ]
        self.assertNotIn("image_poll_timeout", info_events)
        self.assertIn("image_poll_cancelled", debug_events)


class ImageConfigTests(unittest.TestCase):
    def config(self, value: object = None, *, include: bool = True) -> ConfigStore:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "config.json"
        data: dict[str, object] = {"auth-key": "test-auth-key"}
        if include:
            data["image_stream_close_timeout_secs"] = value
        path.write_text(json.dumps(data), encoding="utf-8")
        return ConfigStore(path)

    def test_image_stream_close_timeout_is_available_to_runtime_and_public_config(self) -> None:
        config = self.config(0.75)

        self.assertEqual(config.image_stream_close_timeout_secs, 0.75)
        self.assertEqual(config.get()["image_stream_close_timeout_secs"], 0.75)

    def test_image_stream_close_timeout_has_bounded_fallback(self) -> None:
        self.assertEqual(self.config(include=False).image_stream_close_timeout_secs, 0.5)
        self.assertEqual(self.config("invalid").image_stream_close_timeout_secs, 0.5)
        self.assertEqual(self.config(0).image_stream_close_timeout_secs, 0.1)

    def test_image_attempt_timeout_is_exposed_and_bounded(self) -> None:
        config = self.config(20)
        config.data["image_attempt_timeout_secs"] = 42
        config.data["image_min_retry_budget_secs"] = 12

        self.assertEqual(config.image_attempt_timeout_secs, 42.0)
        self.assertEqual(config.get()["image_attempt_timeout_secs"], 42.0)
        self.assertEqual(config.image_min_retry_budget_secs, 12.0)
        self.assertEqual(config.get()["image_min_retry_budget_secs"], 12.0)
        config.data["image_attempt_timeout_secs"] = 1
        config.data["image_min_retry_budget_secs"] = 1
        self.assertEqual(config.image_attempt_timeout_secs, 15.0)
        self.assertEqual(config.image_min_retry_budget_secs, 5.0)


class ImageAccountFailoverTests(unittest.TestCase):
    def test_poll_timeout_excludes_account_and_uses_per_attempt_deadline(self) -> None:
        started = time.monotonic()
        request = conversation_module.ConversationRequest(
            model="gpt-image-2",
            prompt="draw a test image",
            response_format="url",
            started_monotonic=started,
            deadline_monotonic=started + 120.0,
        )
        seen_exclusions: list[set[str]] = []
        deadlines: list[float] = []

        def select_token(**kwargs) -> str:
            excluded = set(kwargs.get("excluded_tokens") or set())
            seen_exclusions.append(excluded)
            return "token-2" if "token-1" in excluded else "token-1"

        class FakeBackend:
            def __init__(self, access_token: str) -> None:
                self.access_token = access_token
                self.progress_callback = None

            def set_image_request_context(self, _request_id: str, deadline: float) -> None:
                deadlines.append(deadline)

            def close(self) -> None:
                return None

        def stream(backend, attempt_request, _index, _total):
            if backend.access_token == "token-1":
                raise backend_module.ImagePollTimeoutError("attempt timed out")
            yield conversation_module.ImageOutput(
                kind="result",
                model="gpt-image-2",
                index=1,
                total=1,
                data=[{"url": "https://example.test/image.png"}],
            )

        with (
            mock.patch.object(conversation_module.account_service, "get_available_access_token", side_effect=select_token),
            mock.patch.object(
                conversation_module.account_service,
                "get_account",
                side_effect=lambda token: {"access_token": token, "email": f"{token}@example.test"},
            ),
            mock.patch.object(conversation_module.account_service, "has_alternative_image_account", return_value=True),
            mock.patch.object(conversation_module.account_service, "mark_image_result"),
            mock.patch.object(conversation_module, "OpenAIBackendAPI", FakeBackend),
            mock.patch.object(conversation_module, "stream_image_outputs", side_effect=stream),
            mock.patch.object(conversation_module, "_record_runtime_risk"),
            mock.patch.object(conversation_module, "_record_runtime_success"),
            mock.patch.object(
                type(conversation_module.config),
                "image_attempt_timeout_secs",
                new_callable=mock.PropertyMock,
                return_value=55.0,
            ),
        ):
            outputs = conversation_module._generate_single_image(request, 1, 1)

        self.assertEqual(outputs[-1].kind, "result")
        self.assertEqual(seen_exclusions, [set(), {"token-1"}])
        self.assertEqual(len(deadlines), 2)
        self.assertLessEqual(max(deadlines), started + 55.1)
        self.assertLess(max(deadlines), request.deadline_monotonic)

    def test_retry_does_not_start_when_remaining_budget_is_too_short(self) -> None:
        started = time.monotonic()
        request = conversation_module.ConversationRequest(
            model="gpt-image-2",
            prompt="draw a test image",
            started_monotonic=started,
            deadline_monotonic=started + 10.0,
        )

        class FakeBackend:
            def __init__(self, access_token: str) -> None:
                self.access_token = access_token
                self.progress_callback = None

            def set_image_request_context(self, *_args) -> None:
                return None

            def close(self) -> None:
                return None

        def timeout_stream(*_args, **_kwargs):
            raise backend_module.ImagePollTimeoutError("attempt timed out")
            yield

        with (
            mock.patch.object(conversation_module.account_service, "get_available_access_token", return_value="token-1") as select,
            mock.patch.object(
                conversation_module.account_service,
                "get_account",
                return_value={"access_token": "token-1", "email": "token-1@example.test"},
            ),
            mock.patch.object(conversation_module.account_service, "has_alternative_image_account", return_value=True),
            mock.patch.object(conversation_module.account_service, "mark_image_result"),
            mock.patch.object(conversation_module, "OpenAIBackendAPI", FakeBackend),
            mock.patch.object(conversation_module, "stream_image_outputs", side_effect=timeout_stream),
            mock.patch.object(conversation_module, "_record_runtime_risk"),
            mock.patch.object(
                type(conversation_module.config),
                "image_min_retry_budget_secs",
                new_callable=mock.PropertyMock,
                return_value=20.0,
            ),
        ):
            with self.assertRaisesRegex(backend_module.ImagePollTimeoutError, "useful retry"):
                conversation_module._generate_single_image(request, 1, 1)

        select.assert_called_once()


if __name__ == "__main__":
    unittest.main()
