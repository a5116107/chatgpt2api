from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from services.image_task_service import ImageTaskService


class ImageTaskServiceTests(unittest.TestCase):
    def wait_for_task(self, service: ImageTaskService, task_id: str) -> dict:
        deadline = time.time() + 2.0
        while time.time() < deadline:
            task = service.list_tasks({"id": "user-1"}, [task_id])["items"][0]
            if task["status"] in {"success", "error"}:
                return task
            time.sleep(0.01)
        self.fail("image task did not finish")

    def test_web_task_requests_one_source_result_and_runs_output_processing(self) -> None:
        captured: dict = {}

        def generation_handler(payload: dict) -> dict:
            captured.update(payload)
            return {"data": [{"b64_json": "source", "url": "https://example.test/original.png"}]}

        def output_handler(item: dict, size: object, base_url: str | None) -> dict:
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

        with tempfile.TemporaryDirectory() as directory:
            service = ImageTaskService(
                Path(directory) / "tasks.json",
                generation_handler=generation_handler,
                output_handler=output_handler,
            )
            service.submit_generation(
                {"id": "user-1"},
                client_task_id="web-task",
                prompt="draw",
                model="gpt-image-2",
                size="1536x1536",
                base_url="https://example.test",
            )
            task = self.wait_for_task(service, "web-task")

        self.assertEqual(task["status"], "success")
        self.assertEqual(captured["response_format"], "b64_json")
        self.assertTrue(captured["_single_result"])
        self.assertEqual(task["data"][0]["original_url"], "https://example.test/original.png")

    def test_codex_task_keeps_the_existing_url_path(self) -> None:
        captured: dict = {}

        def generation_handler(payload: dict) -> dict:
            captured.update(payload)
            return {"data": [{"url": "https://example.test/codex.png"}]}

        def output_handler(*_args):
            raise AssertionError("Codex output must not enter the Web output processor")

        with tempfile.TemporaryDirectory() as directory:
            service = ImageTaskService(
                Path(directory) / "tasks.json",
                generation_handler=generation_handler,
                output_handler=output_handler,
            )
            service.submit_generation(
                {"id": "user-1"},
                client_task_id="codex-task",
                prompt="draw",
                model="codex-gpt-image-2",
                size="2048x2048",
            )
            task = self.wait_for_task(service, "codex-task")

        self.assertEqual(task["status"], "success")
        self.assertEqual(captured["response_format"], "url")
        self.assertFalse(captured["_single_result"])


if __name__ == "__main__":
    unittest.main()
