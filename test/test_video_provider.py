from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import video_task_service as module


class VideoProviderTests(unittest.TestCase):
    def test_historical_demo_is_reported_invalid_without_erasing_audit_data(self):
        source = {"id": "old", "status": "success", "data": [{"provider": "local", "bytes": 601}]}
        public = module._public_task(source)
        self.assertEqual(public["status"], "failed")
        self.assertEqual(public["source_status"], "success")
        self.assertEqual(public["error_code"], "invalid_demo_media")
        self.assertEqual(source["status"], "success")
        self.assertEqual(source["data"][0]["bytes"], 601)

    def test_local_and_mock_never_claim_real_generation_success(self):
        with tempfile.TemporaryDirectory() as directory:
            service = module.VideoTaskService(Path(directory) / "tasks.json")
            for provider in ("local", "mock"):
                with self.subTest(provider=provider), self.assertRaisesRegex(RuntimeError, "not a real video"):
                    service._generate_with_provider(provider, {"id": "fixture"}, {})
            self.assertEqual(list(Path(directory).glob("*.mp4")), [])

    def test_unconfigured_task_records_failure_without_fake_output(self):
        with tempfile.TemporaryDirectory() as directory:
            service = module.VideoTaskService(Path(directory) / "tasks.json")
            service._tasks["fixture:video"] = {"id": "video", "owner_id": "fixture", "key": "fixture:video", "model": "sora", "data": []}
            with (
                patch.object(module.config, "get_video_settings", return_value={"enabled": True, "provider": "local"}),
                patch.object(module, "_sync_unified_task"),
                patch.object(module.log_service, "add"),
            ):
                service._run_task("fixture:video", {"id": "fixture"})
            task = service._tasks["fixture:video"]
            self.assertEqual(task["status"], "failed")
            self.assertEqual(task["error_code"], "video_provider_not_configured")
            self.assertEqual(task["data"], [])


if __name__ == "__main__":
    unittest.main()
