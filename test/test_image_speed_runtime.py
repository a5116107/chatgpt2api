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
from services.openai_backend_api import OpenAIBackendAPI
from services.protocol import conversation as conversation_module


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
        backend.progress_callback = None
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


if __name__ == "__main__":
    unittest.main()
