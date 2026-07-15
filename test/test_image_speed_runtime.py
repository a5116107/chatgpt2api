from __future__ import annotations

import base64
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from services.account_service import AccountService
from services.config import config
from services.image_task_service import (
    ImageTaskService,
    TASK_STATUS_ERROR,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SUCCESS,
)
from services.openai_backend_api import ChatRequirements, ImagePollTimeoutError, OpenAIBackendAPI
from services.protocol import conversation as conversation_module
from utils.helper import UpstreamHTTPError


FILE_ID = "file_00000000" + "a" * 24


def image_conversation(file_id: str = "") -> dict:
    parts = []
    if file_id:
        parts.append({"content_type": "image_asset_pointer", "asset_pointer": f"file-service://{file_id}"})
    return {
        "mapping": {
            "tool-message": {
                "message": {
                    "author": {"role": "tool"},
                    "metadata": {"async_task_type": "image_gen"},
                    "content": {"content_type": "multimodal_text", "parts": parts},
                    "create_time": 1,
                }
            }
        }
    }


class ImagePollingTests(unittest.TestCase):
    def backend(self) -> OpenAIBackendAPI:
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        backend.image_request_id = "task-test"
        backend.image_deadline_monotonic = None
        backend.progress_callback = None
        return backend

    def test_conversation_hit_returns_before_tasks_side_channel(self) -> None:
        backend = self.backend()
        backend._get_conversation = mock.Mock(return_value=image_conversation(FILE_ID))
        backend._query_backend_tasks = mock.Mock(side_effect=AssertionError("tasks must not precede conversation"))

        file_ids, sediment_ids = backend._poll_image_results(
            "conversation-1",
            timeout_secs=2,
            initial_wait_secs=0,
        )

        self.assertEqual(file_ids, [FILE_ID])
        self.assertEqual(sediment_ids, [])
        backend._query_backend_tasks.assert_not_called()

    def test_low_latency_polling_defaults_are_bounded(self) -> None:
        with mock.patch.dict(config.data, {}, clear=True):
            self.assertEqual(config.image_poll_initial_wait_secs, 0.25)
            self.assertEqual(config.image_poll_interval_secs, 0.5)
            self.assertEqual(config.image_poll_request_timeout_secs, 2.0)
            self.assertEqual(config.image_poll_progress_persist_interval_secs, 2.0)
            self.assertEqual(config.image_png_compress_level, 1)

        with mock.patch.dict(config.data, {
            "image_attempt_timeout_secs": 1,
            "image_min_retry_budget_secs": 1,
        }, clear=True):
            self.assertEqual(config.image_attempt_timeout_secs, 15.0)
            self.assertEqual(config.image_min_retry_budget_secs, 5.0)
            self.assertEqual(config.get()["image_attempt_timeout_secs"], 15.0)
            self.assertEqual(config.get()["image_min_retry_budget_secs"], 5.0)

    def test_transient_conversation_404_is_retried_without_aborting_polling(self) -> None:
        backend = self.backend()
        backend._get_conversation = mock.Mock(side_effect=[
            UpstreamHTTPError("conversation", 404, {"detail": "not ready"}),
            image_conversation(FILE_ID),
        ])
        backend._query_backend_tasks = mock.Mock(return_value=[])

        with mock.patch("services.openai_backend_api.time.sleep", return_value=None):
            file_ids, _ = backend._poll_image_results(
                "conversation-eventually-consistent",
                timeout_secs=2,
                initial_wait_secs=0,
            )

        self.assertEqual(file_ids, [FILE_ID])
        self.assertEqual(backend._get_conversation.call_count, 2)
        self.assertLessEqual(backend._get_conversation.call_args.kwargs["timeout_secs"], 2.0)

    def test_single_result_url_resolution_stops_after_first_valid_file(self) -> None:
        backend = self.backend()
        backend._get_file_download_url = mock.Mock(return_value="https://example.test/first.png")
        backend._get_attachment_download_url = mock.Mock(
            side_effect=AssertionError("single-result resolution must stop after the first URL")
        )

        urls = backend._resolve_image_urls(
            "conversation-1",
            [FILE_ID, f"{FILE_ID}b"],
            ["sediment-1"],
            limit=1,
        )

        self.assertEqual(urls, ["https://example.test/first.png"])
        backend._get_file_download_url.assert_called_once_with(FILE_ID)
        backend._get_attachment_download_url.assert_not_called()

    def test_tasks_is_checked_only_as_bounded_side_channel(self) -> None:
        backend = self.backend()
        calls = 0

        def get_conversation(_conversation_id: str, timeout_secs: float = 5.0) -> dict:
            nonlocal calls
            calls += 1
            return image_conversation(FILE_ID if calls >= 5 else "")

        backend._get_conversation = mock.Mock(side_effect=get_conversation)
        backend._query_backend_tasks = mock.Mock(return_value=[])
        with mock.patch("services.openai_backend_api.time.sleep", return_value=None):
            with mock.patch.dict(config.data, {
                "image_tasks_check_every": 4,
                "image_tasks_timeout_secs": 2.0,
                "image_poll_interval_secs": 0.5,
            }):
                file_ids, _ = backend._poll_image_results(
                    "conversation-2",
                    timeout_secs=2,
                    initial_wait_secs=0,
                )

        self.assertEqual(file_ids, [FILE_ID])
        self.assertEqual(backend._query_backend_tasks.call_count, 1)
        self.assertLessEqual(backend._query_backend_tasks.call_args.kwargs["timeout_secs"], 2.0)

    def test_tasks_can_complete_the_race_with_an_image_pointer(self) -> None:
        backend = self.backend()
        backend._get_conversation = mock.Mock(return_value=image_conversation())
        backend._query_backend_tasks = mock.Mock(return_value=[{
            "image_gen_message": {
                "author": {"role": "tool"},
                "metadata": {"async_task_type": "image_gen"},
                "content": {
                    "content_type": "multimodal_text",
                    "parts": [{
                        "content_type": "image_asset_pointer",
                        "asset_pointer": f"file-service://{FILE_ID}",
                    }],
                },
            },
        }])

        with mock.patch("services.openai_backend_api.time.sleep", return_value=None):
            with mock.patch.dict(config.data, {
                "image_tasks_check_every": 1,
                "image_tasks_timeout_secs": 2.0,
                "image_poll_interval_secs": 0.5,
            }):
                file_ids, sediment_ids = backend._poll_image_results(
                    "conversation-tasks-result",
                    timeout_secs=2,
                    initial_wait_secs=0,
                )

        self.assertEqual(file_ids, [FILE_ID])
        self.assertEqual(sediment_ids, [])
        backend._query_backend_tasks.assert_called_once()


class ImageProtocolTests(unittest.TestCase):
    def backend(self, prepare_data: dict | None = None) -> tuple[OpenAIBackendAPI, mock.Mock]:
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        backend.base_url = "https://example.test"
        backend.account = {"default_model_slug": "auto"}
        backend.client_version = "test-client"
        backend.image_deadline_monotonic = None
        backend.image_request_id = "protocol-test"
        backend.progress_callback = None
        backend._headers = lambda _path, headers=None: dict(headers or {})
        prepare_response = mock.Mock()
        prepare_response.json.return_value = prepare_data or {"conduit_token": "conduit-token"}
        start_response = mock.Mock()
        backend.session = mock.Mock()
        backend.session.post.side_effect = [prepare_response, start_response]
        return backend, start_response

    def test_prepare_and_start_share_context_and_sentinel_headers(self) -> None:
        backend, _response = self.backend()
        requirements = ChatRequirements(token="requirements-token", so_token="so-token")

        with mock.patch("services.openai_backend_api.ensure_ok"):
            context = backend._prepare_image_conversation("draw a lighthouse", requirements, "gpt-image-2")
            backend._start_image_generation("draw a lighthouse", requirements, context, "gpt-image-2")

        prepare_call, start_call = backend.session.post.call_args_list
        prepare_payload = prepare_call.kwargs["json"]
        start_payload = start_call.kwargs["json"]
        self.assertEqual(prepare_payload["client_prepare_state"], "none")
        self.assertEqual(start_payload["client_prepare_state"], "sent")
        self.assertEqual(prepare_payload["parent_message_id"], start_payload["parent_message_id"])
        self.assertEqual(prepare_payload["partial_query"]["id"], start_payload["messages"][0]["id"])
        self.assertEqual(prepare_call.kwargs["headers"]["X-Conduit-Token"], "no-token")
        self.assertEqual(prepare_call.kwargs["headers"]["OpenAI-Sentinel-SO-Token"], "so-token")
        self.assertEqual(start_call.kwargs["headers"]["X-Conduit-Token"], "conduit-token")
        self.assertEqual(start_call.kwargs["headers"]["OpenAI-Sentinel-SO-Token"], "so-token")

    def test_missing_conduit_token_fails_before_start(self) -> None:
        backend, _response = self.backend({"other": "value"})

        with mock.patch("services.openai_backend_api.ensure_ok"):
            with self.assertRaisesRegex(RuntimeError, "missing conduit_token"):
                backend._prepare_image_conversation(
                    "draw a lighthouse",
                    ChatRequirements(token="requirements-token"),
                    "gpt-image-2",
                )

    def test_duplicate_file_and_sediment_reference_is_resolved_once(self) -> None:
        backend, _response = self.backend()
        backend._get_file_download_url = mock.Mock(return_value="https://example.test/file.png")
        backend._get_attachment_download_url = mock.Mock(return_value="https://example.test/attachment.png")

        urls = backend._resolve_image_urls("conversation-1", ["file-1"], ["file-1"])

        self.assertEqual(urls, ["https://example.test/file.png"])
        backend._get_attachment_download_url.assert_not_called()


class ImageAccountSelectionTests(unittest.TestCase):
    def service(self) -> AccountService:
        service = AccountService.__new__(AccountService)
        service._lock = threading.RLock()
        return service

    def test_selection_honors_exclusions(self) -> None:
        service = self.service()
        seen_exclusions: list[set[str]] = []

        def acquire(**kwargs) -> str:
            seen_exclusions.append(set(kwargs["excluded_tokens"]))
            return "second"

        service._acquire_next_candidate_token = mock.Mock(side_effect=acquire)
        service.get_account = mock.Mock(return_value={"access_token": "second"})
        service._is_image_account_available = mock.Mock(return_value=True)
        service._should_skip_remote_image_preflight = mock.Mock(return_value=True)

        selected = service.get_available_access_token(excluded_tokens={"first"})

        self.assertEqual(selected, "second")
        self.assertEqual(seen_exclusions, [{"first"}])

    def test_alternative_selection_excludes_current_and_prior_failures(self) -> None:
        service = self.service()
        service._resolve_access_token_locked = mock.Mock(return_value="first")
        service._list_ready_candidate_tokens = mock.Mock(return_value=["third"])

        self.assertTrue(service.has_alternative_image_account("first", excluded_tokens={"second"}))
        self.assertEqual(
            service._list_ready_candidate_tokens.call_args.kwargs["excluded_tokens"],
            {"first", "second"},
        )


class BlockingResponse:
    def __init__(self) -> None:
        self.closed = threading.Event()

    def iter_lines(self):
        yield b'data: {"conversation_id":"conversation-race"}'
        self.closed.wait(5)

    def close(self) -> None:
        self.closed.set()


class SlowCloseResponse:
    def __init__(self) -> None:
        self.quit_now = threading.Event()
        self.close_started = threading.Event()
        self.close_finished = threading.Event()

    def iter_lines(self):
        yield b'data: {"conversation_id":"conversation-slow-close"}'
        self.quit_now.wait(5)

    def close(self) -> None:
        self.close_started.set()
        time.sleep(0.75)
        self.close_finished.set()


class PollBackend:
    def __init__(self) -> None:
        self.closed = False

    def _poll_image_results(self, *_args, **_kwargs):
        time.sleep(0.02)
        return [FILE_ID], []

    def close(self) -> None:
        self.closed = True


class ImageRaceTests(unittest.TestCase):
    def test_poll_wins_stalled_sse_and_reader_is_released(self) -> None:
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        backend.image_request_id = "race-test"
        backend.image_deadline_monotonic = time.monotonic() + 2
        stages: list[str] = []
        backend.progress_callback = stages.append
        poll_backend = PollBackend()
        backend._clone_for_image_poll = mock.Mock(return_value=poll_backend)
        response = BlockingResponse()

        payloads = list(backend._iter_image_sse_race(response))

        synthetic = json.loads(payloads[-2])
        self.assertEqual(payloads[-1], "[DONE]")
        self.assertEqual(synthetic["conversation_id"], "conversation-race")
        self.assertIn(FILE_ID, json.dumps(synthetic))
        self.assertTrue(response.closed.is_set())
        self.assertTrue(poll_backend.closed)
        self.assertIn("sse_first_event", stages)
        self.assertIn("conversation_ready", stages)
        self.assertIn("result_pointer_ready", stages)
        self.assertFalse(any(t.name == "image-sse-reader-race-test" for t in threading.enumerate()))

    def test_slow_response_close_does_not_delay_a_poll_winner(self) -> None:
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        backend.image_request_id = "slow-close-test"
        backend.image_deadline_monotonic = time.monotonic() + 2
        backend.progress_callback = None
        backend._clone_for_image_poll = mock.Mock(return_value=PollBackend())
        response = SlowCloseResponse()

        started = time.monotonic()
        with mock.patch.dict(config.data, {"image_stream_close_timeout_secs": 0.05}):
            payloads = list(backend._iter_image_sse_race(response))
        elapsed = time.monotonic() - started

        self.assertEqual(payloads[-1], "[DONE]")
        self.assertLess(elapsed, 0.5)
        self.assertTrue(response.close_started.is_set())
        self.assertFalse(response.close_finished.is_set())
        self.assertFalse(any(t.name == "image-sse-reader-slow-close-test" for t in threading.enumerate()))
        self.assertTrue(response.close_finished.wait(2))
        self.assertFalse(any(t.name == "image-sse-close-slow-close-test" for t in threading.enumerate()))


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

        def stream(backend, _attempt_request, _index, _total):
            if backend.access_token == "token-1":
                raise ImagePollTimeoutError("attempt timed out")
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
            mock.patch.dict(config.data, {"image_attempt_timeout_secs": 55.0}),
        ):
            outputs = conversation_module._generate_single_image(request, 1, 1)

        self.assertEqual(outputs[-1].kind, "result")
        self.assertEqual(seen_exclusions, [set(), {"token-1"}])
        self.assertEqual(len(deadlines), 2)
        self.assertLessEqual(max(deadlines), started + 55.1)
        self.assertLess(max(deadlines), request.deadline_monotonic)

    def test_retry_does_not_start_without_a_useful_time_budget(self) -> None:
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
            raise ImagePollTimeoutError("attempt timed out")
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
            mock.patch.dict(config.data, {"image_min_retry_budget_secs": 20.0}),
        ):
            with self.assertRaisesRegex(ImagePollTimeoutError, "useful retry"):
                conversation_module._generate_single_image(request, 1, 1)

        select.assert_called_once()


class ImageTaskLifecycleTests(unittest.TestCase):
    def test_stage_metrics_heartbeat_and_account_hash_are_persisted(self) -> None:
        release_handler = threading.Event()

        def handler(payload: dict) -> dict:
            payload["progress_callback"]("getting_account")
            payload["progress_callback"]("starting_generation")
            release_handler.wait(1)
            return {"data": [{"url": "https://example.test/image.png"}], "_account_hash": "token:abc123"}

        with tempfile.TemporaryDirectory() as directory:
            service = ImageTaskService(
                Path(directory) / "tasks.json",
                generation_handler=handler,
                output_handler=lambda item, _size, _base_url: item,
                heartbeat_interval_getter=lambda: 0.05,
            )
            service.submit_generation(
                {"id": "owner", "name": "owner", "role": "admin"},
                client_task_id="task-1",
                prompt="test",
                model="gpt-image-2",
                size="1024x1024",
            )
            time.sleep(0.14)
            with service._lock:
                running = dict(service._tasks["owner:task-1"])
            self.assertEqual(running["status"], TASK_STATUS_RUNNING)
            self.assertGreater(running["last_heartbeat_ts"], running["started_ts"])
            release_handler.set()

            deadline = time.time() + 2
            while time.time() < deadline:
                item = service.list_tasks({"id": "owner"}, ["task-1"])["items"][0]
                if item["status"] == TASK_STATUS_SUCCESS:
                    break
                time.sleep(0.01)
            self.assertEqual(item["status"], TASK_STATUS_SUCCESS)
            self.assertEqual(item["account_hash"], "token:abc123")
            self.assertIn("getting_account", item["stage_metrics"])
            self.assertIn("starting_generation", item["stage_metrics"])
            self.assertIn("source_ready", item["stage_metrics"])
            self.assertIn("output_processing", item["stage_metrics"])
            self.assertIn("output_ready", item["stage_metrics"])

    def test_polling_progress_is_compacted_and_hot_path_persistence_is_throttled(self) -> None:
        def handler(payload: dict) -> dict:
            payload["progress_callback"]("conversation_ready")
            for attempt in range(1, 51):
                payload["progress_callback"](f"polling:{attempt}")
            return {"data": [{"url": "https://example.test/image.png"}]}

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch("services.image_task_service._sync_unified_task") as sync_task:
                service = ImageTaskService(
                    Path(directory) / "tasks.json",
                    generation_handler=handler,
                    output_handler=lambda item, _size, _base_url: item,
                    progress_persist_interval_getter=lambda: 30.0,
                )
                with mock.patch.object(service, "_save_locked", wraps=service._save_locked) as save_tasks:
                    service.submit_generation(
                        {"id": "owner", "name": "owner", "role": "admin"},
                        client_task_id="task-polling",
                        prompt="test",
                        model="gpt-image-2",
                        size="1024x1024",
                    )
                    deadline = time.time() + 2
                    while time.time() < deadline:
                        item = service.list_tasks({"id": "owner"}, ["task-polling"])["items"][0]
                        if item["status"] == TASK_STATUS_SUCCESS:
                            break
                        time.sleep(0.01)

        self.assertEqual(item["status"], TASK_STATUS_SUCCESS)
        self.assertEqual(item["stage_metrics"]["polling"]["attempts"], 50)
        self.assertFalse(any(key.startswith("polling:") for key in item["stage_metrics"]))
        polling_syncs = [
            call for call in sync_task.call_args_list
            if str(call.kwargs.get("progress") or "").startswith("polling:")
        ]
        self.assertEqual(polling_syncs, [])
        self.assertLess(save_tasks.call_count, 15)

    def test_legacy_polling_metrics_are_compacted_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            path.write_text(json.dumps({"tasks": [{
                "id": "legacy-task",
                "owner_id": "owner",
                "status": TASK_STATUS_SUCCESS,
                "mode": "generate",
                "model": "gpt-image-2",
                "created_at": "2099-01-01 00:00:00",
                "updated_at": "2099-01-01 00:00:00",
                "stage_metrics": {
                    "polling:1": {"elapsed_ms": 100, "stage_ms": 100},
                    "polling:12": {"elapsed_ms": 1200, "stage_ms": 80},
                    "output_ready": {"elapsed_ms": 1300, "stage_ms": 100},
                },
            }]}), encoding="utf-8")

            service = ImageTaskService(path)
            item = service.list_tasks({"id": "owner"}, ["legacy-task"])["items"][0]

        self.assertEqual(item["stage_metrics"]["polling"]["attempts"], 12)
        self.assertEqual(item["stage_metrics"]["polling"]["elapsed_ms"], 1200)
        self.assertNotIn("polling:1", item["stage_metrics"])

    def test_terminal_compare_and_set_rejects_late_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = ImageTaskService(Path(directory) / "tasks.json")
            key = "owner:task-cas"
            service._tasks[key] = {
                "id": "task-cas",
                "owner_id": "owner",
                "status": TASK_STATUS_RUNNING,
                "lease_id": "lease-a",
                "created_at": "2026-07-14 00:00:00",
                "updated_at": "2026-07-14 00:00:00",
            }
            self.assertTrue(service._transition_task(
                key,
                expected_statuses={TASK_STATUS_RUNNING},
                expected_lease_id="lease-a",
                status=TASK_STATUS_ERROR,
                error="lease expired",
            ))
            self.assertFalse(service._transition_task(
                key,
                expected_statuses={TASK_STATUS_RUNNING},
                expected_lease_id="lease-a",
                status=TASK_STATUS_SUCCESS,
                data=[{"url": "late"}],
            ))
            self.assertEqual(service._tasks[key]["status"], TASK_STATUS_ERROR)


class ImageAccountScoreTests(unittest.TestCase):
    def test_recent_success_and_latency_drive_selection_order(self) -> None:
        service = AccountService.__new__(AccountService)
        service._lock = threading.Lock()
        service._image_slot_condition = threading.Condition(service._lock)
        service._image_inflight = {}
        service._accounts = {
            "slow": {
                "access_token": "slow",
                "status": "normal",
                "quota": 1,
                "type": "free",
                "image_health_samples": 10,
                "image_success_ema": 0.4,
                "image_latency_ema_ms": 80000,
                "image_probe_samples": 2,
                "image_probe_success_ema": 0.5,
                "image_probe_latency_ema_ms": 12000,
            },
            "fast": {
                "access_token": "fast",
                "status": "normal",
                "quota": 1,
                "type": "free",
                "image_health_samples": 10,
                "image_success_ema": 0.95,
                "image_latency_ema_ms": 30000,
                "image_probe_samples": 2,
                "image_probe_success_ema": 0.95,
                "image_probe_latency_ema_ms": 3000,
            },
        }
        service._capability_allows = mock.Mock(return_value=True)
        service._access_token_hard_dead = mock.Mock(return_value=False)

        tokens = service._list_available_candidate_tokens()

        self.assertEqual(tokens, ["fast", "slow"])

    def test_successful_requirements_probe_precedes_an_unknown_account(self) -> None:
        service = AccountService.__new__(AccountService)
        service._lock = threading.Lock()
        service._image_slot_condition = threading.Condition(service._lock)
        service._image_inflight = {}
        service._accounts = {
            "probed": {
                "access_token": "probed",
                "status": "normal",
                "quota": 1,
                "type": "free",
                "image_consecutive_failures": 2,
                "image_probe_samples": 1,
                "image_probe_success_ema": 1.0,
                "image_last_probe_at": "2026-07-14 10:00:00",
                "image_last_probe_error": None,
            },
            "unknown": {
                "access_token": "unknown",
                "status": "normal",
                "quota": 1,
                "type": "free",
                "image_consecutive_failures": 0,
                "image_probe_samples": 0,
            },
        }
        service._capability_allows = mock.Mock(return_value=True)
        service._access_token_hard_dead = mock.Mock(return_value=False)

        self.assertEqual(service._list_available_candidate_tokens(), ["probed", "unknown"])

    def test_probe_candidates_prioritize_unknown_then_oldest_probe(self) -> None:
        service = AccountService.__new__(AccountService)
        service._lock = threading.Lock()
        service._image_slot_condition = threading.Condition(service._lock)
        service._image_inflight = {}
        service._accounts = {
            "recent": {
                "access_token": "recent", "status": "normal", "quota": 1, "type": "free",
                "image_probe_samples": 1, "image_last_probe_at": "2026-07-14 12:00:00",
                "image_probe_success_ema": 1.0, "image_last_probe_error": None,
            },
            "unknown": {
                "access_token": "unknown", "status": "normal", "quota": 1, "type": "free",
                "image_probe_samples": 0,
            },
            "old": {
                "access_token": "old", "status": "normal", "quota": 1, "type": "free",
                "image_probe_samples": 1, "image_last_probe_at": "2026-07-14 08:00:00",
                "image_probe_success_ema": 1.0, "image_last_probe_error": None,
            },
        }
        service._capability_allows = mock.Mock(return_value=True)
        service._access_token_hard_dead = mock.Mock(return_value=False)

        self.assertEqual(service._list_image_probe_candidate_tokens(3), ["unknown", "old", "recent"])

    def test_acquire_uses_the_best_ranked_idle_account_without_round_robin_override(self) -> None:
        service = AccountService.__new__(AccountService)
        service._lock = threading.Lock()
        service._image_slot_condition = threading.Condition(service._lock)
        service._image_inflight = {}
        service._image_inflight_meta = {}
        service._index = 2
        service._accounts = {
            "healthy": {"access_token": "healthy"},
            "failed": {"access_token": "failed"},
            "unknown": {"access_token": "unknown"},
        }
        ranked = ["healthy", "failed", "unknown"]
        service._list_ready_candidate_tokens = mock.Mock(return_value=ranked)
        service._list_available_candidate_tokens = mock.Mock(return_value=ranked)

        token = service._acquire_next_candidate_token()

        self.assertEqual(token, "healthy")
        self.assertEqual(service._image_inflight, {"healthy": 1})


class ImageQualityTests(unittest.TestCase):
    def test_image_bytes_are_saved_without_reencoding(self) -> None:
        original = b"\x89PNG\r\n\x1a\nexact-upstream-image-bytes"
        captured: list[bytes] = []

        def save_image_bytes(data: bytes, _base_url: str | None = None) -> str:
            captured.append(data)
            return "https://example.test/original.png"

        with mock.patch.object(conversation_module, "save_image_bytes", side_effect=save_image_bytes):
            result = conversation_module.format_image_result(
                [{"b64_json": base64.b64encode(original).decode("ascii")}],
                "prompt",
                "url",
            )

        self.assertEqual(captured, [original])
        self.assertEqual(result["data"][0]["url"], "https://example.test/original.png")

    def test_web_task_can_defer_original_storage_to_output_processor(self) -> None:
        original = b"exact-upstream-image-bytes"

        with mock.patch.object(conversation_module, "save_image_bytes") as save_image_bytes:
            result = conversation_module.format_image_result(
                [{"b64_json": base64.b64encode(original).decode("ascii")}],
                "prompt",
                "b64_json",
                persist=False,
            )

        save_image_bytes.assert_not_called()
        self.assertNotIn("url", result["data"][0])
        self.assertEqual(base64.b64decode(result["data"][0]["b64_json"]), original)


if __name__ == "__main__":
    unittest.main()
