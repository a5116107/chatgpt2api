from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from services.image_task_service import ImageTaskService


OWNER = {"id": "owner-1", "name": "Owner", "role": "admin"}
OTHER_OWNER = {"id": "owner-2", "name": "Other", "role": "user"}


def wait_for_task(service: ImageTaskService, identity: dict[str, object], task_id: str, status: str, timeout: float = 2.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        result = service.list_tasks(identity, [task_id])
        last = (result.get("items") or [None])[0]
        if last and last.get("status") == status:
            return last
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach {status}, last={last}")


class ImageTaskServiceTests(unittest.TestCase):
    def make_service(self, path: Path, handler=None) -> ImageTaskService:
        return ImageTaskService(
            path,
            generation_handler=handler or (lambda _payload: {"data": [{"url": "http://example.test/image.png"}]}),
            edit_handler=handler or (lambda _payload: {"data": [{"url": "http://example.test/edit.png"}]}),
            output_handler=lambda item, _size, _base_url: item,
            retention_days_getter=lambda: 30,
        )

    def test_web_task_requests_one_source_result_and_runs_output_processing(self):
        captured = {}

        def generation_handler(payload):
            captured.update(payload)
            return {"data": [{"b64_json": "source", "url": "https://example.test/original.png"}]}

        def output_handler(item, size, base_url):
            self.assertEqual(item["b64_json"], "source")
            self.assertEqual(size, "1536x1536")
            self.assertEqual(base_url, "https://example.test")
            return {
                "url": "https://example.test/upscaled.png",
                "original_url": item["url"],
                "source_width": 1024,
                "source_height": 1024,
                "width": 1536,
                "height": 1536,
                "output_transform": "lanczos_upscale",
            }

        with tempfile.TemporaryDirectory() as tmp_dir:
            service = ImageTaskService(
                Path(tmp_dir) / "image_tasks.json",
                generation_handler=generation_handler,
                output_handler=output_handler,
                retention_days_getter=lambda: 30,
            )
            service.submit_generation(
                OWNER,
                client_task_id="web-output-task",
                prompt="draw",
                model="gpt-image-2",
                size="1536x1536",
                base_url="https://example.test",
            )
            task = wait_for_task(service, OWNER, "web-output-task", "success")

        self.assertEqual(captured["response_format"], "b64_json")
        self.assertTrue(captured["_single_result"])
        self.assertEqual(task["data"][0]["original_url"], "https://example.test/original.png")

    def test_codex_task_keeps_existing_url_output_path(self):
        captured = {}

        def generation_handler(payload):
            captured.update(payload)
            return {"data": [{"url": "https://example.test/codex.png"}]}

        def output_handler(*_args):
            raise AssertionError("Codex output must not enter the Web output processor")

        with tempfile.TemporaryDirectory() as tmp_dir:
            service = ImageTaskService(
                Path(tmp_dir) / "image_tasks.json",
                generation_handler=generation_handler,
                output_handler=output_handler,
                retention_days_getter=lambda: 30,
            )
            service.submit_generation(
                OWNER,
                client_task_id="codex-output-task",
                prompt="draw",
                model="codex-gpt-image-2",
                size="2048x2048",
            )
            task = wait_for_task(service, OWNER, "codex-output-task", "success")

        self.assertEqual(task["data"][0]["url"], "https://example.test/codex.png")
        self.assertEqual(captured["response_format"], "url")
        self.assertFalse(captured["_single_result"])

    def test_duplicate_submit_uses_existing_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            calls = 0

            def handler(_payload):
                nonlocal calls
                calls += 1
                time.sleep(0.05)
                return {"data": [{"url": "http://example.test/image.png"}]}

            service = self.make_service(Path(tmp_dir) / "image_tasks.json", handler)
            first = service.submit_generation(
                OWNER,
                client_task_id="task-1",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            second = service.submit_generation(
                OWNER,
                client_task_id="task-1",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            self.assertEqual(first["id"], "task-1")
            self.assertEqual(second["id"], "task-1")
            task = wait_for_task(service, OWNER, "task-1", "success")
            self.assertEqual(task["data"][0]["url"], "http://example.test/image.png")
            self.assertEqual(calls, 1)

    def test_different_owner_cannot_query_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            service.submit_generation(
                OWNER,
                client_task_id="private-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            wait_for_task(service, OWNER, "private-task", "success")
            result = service.list_tasks(OTHER_OWNER, ["private-task"])

            self.assertEqual(result["items"], [])
            self.assertEqual(result["missing_ids"], ["private-task"])

    def test_success_task_persists_to_new_service_instance(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            service = self.make_service(path)
            service.submit_generation(
                OWNER,
                client_task_id="persisted-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "persisted-task", "success")

            reloaded = self.make_service(path)
            result = reloaded.list_tasks(OWNER, ["persisted-task"])

            self.assertEqual(result["missing_ids"], [])
            self.assertEqual(result["items"][0]["status"], "success")
            self.assertEqual(result["items"][0]["data"][0]["url"], "http://example.test/image.png")

    def test_startup_marks_unfinished_tasks_as_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            path.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "queued-task",
                                "owner_id": "owner-1",
                                "status": "queued",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "created_at": "2099-01-01 00:00:00",
                                "updated_at": "2099-01-01 00:00:00",
                            },
                            {
                                "id": "running-task",
                                "owner_id": "owner-1",
                                "status": "running",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "created_at": "2099-01-01 00:00:00",
                                "updated_at": "2099-01-01 00:00:00",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            service = self.make_service(path)
            result = service.list_tasks(OWNER, ["queued-task", "running-task"])

            self.assertEqual([item["status"] for item in result["items"]], ["error", "error"])
            self.assertTrue(all("已中断" in item.get("error", "") for item in result["items"]))


if __name__ == "__main__":
    unittest.main()
