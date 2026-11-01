from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from services.config import _public_proxy_url, _restore_proxy_url


class RegisterSchedulerTests(unittest.TestCase):
    """No network, mailbox creation, or production state in scheduler tests."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.worker = Mock(return_value={"ok": False, "error": "upstream unavailable"})
        self.preflight = Mock(return_value={"enabled": False, "remaining": None})
        registrar = ModuleType("services.register.openai_register")
        registrar.config = {"threads": 1, "total": 10, "max_attempts": 1, "proxy": "", "mail": {}}
        registrar.stats = {}
        registrar.stats_lock = threading.RLock()
        registrar.worker = self.worker
        registrar.runtime_profile_service = SimpleNamespace(registration_preflight=self.preflight)
        registrar.base_dir = str(self.root)
        package = ModuleType("services.register")
        package.mail_provider = Mock()
        package.openai_register = registrar
        package.openai_registration_policy = SimpleNamespace(normalize_proxy_settings=lambda *args: (900, "", "seconds"))
        package.openai_signup_primitives = SimpleNamespace(registration_max_attempts=lambda value: max(1, int(value or 1)))
        config = ModuleType("services.config")
        config.DATA_DIR = self.root
        config._public_proxy_url = _public_proxy_url
        config._restore_proxy_url = _restore_proxy_url
        accounts = ModuleType("services.account_service")
        accounts.account_service = SimpleNamespace(list_accounts=lambda: [])
        routes = ModuleType("services.register.openai_signup_compat.route_stats")
        routes.summarize_route_stats = Mock()
        routes.suggest_route_retirement = Mock()
        path = Path(__file__).resolve().parents[1] / "services/register_service.py"
        spec = importlib.util.spec_from_file_location("scheduler_fixture", path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"services.config": config, "services.account_service": accounts, "services.register": package, "services.register.openai_signup_compat.route_stats": routes}):
            spec.loader.exec_module(self.module)
        self.service = self.module.RegisterService(self.root / "job.json")
        self.service.update({"mode": "available", "threads": 1, "target_available": 10})

    def run_job(self):
        self.service.start()
        runner = self.service._runner
        if runner:
            runner.join(timeout=2)
            if runner.is_alive():
                self.service.stop()
                runner.join(timeout=2)
                self.fail("scheduler did not stop within its bounded test budget")
        return self.service.get()

    def test_preflight_failure_does_not_start_or_reset_previous_statistics(self):
        self.service._config["stats"].update(success=5, fail=66891, done=66896)
        self.preflight.side_effect = RuntimeError("account proxy pool capacity is exhausted")
        result = self.service.start()
        self.assertFalse(result["enabled"])
        self.assertEqual(result["stats"]["done"], 66896)
        self.assertEqual(result["stats"]["success"], 5)
        self.assertIn("capacity is exhausted", result["stats"]["last_error"])
        self.assertEqual(result["stats"]["start_blocked_reason"], "preflight_failed")
        self.worker.assert_not_called()

    def test_proxy_credentials_are_redacted_and_round_trip_without_starting(self):
        original = "http://fixture-user:fixture-secret@proxy.example.test:8080"
        self.service.update({"proxy": original, "mail": {"proxy": original}})
        public = self.service.get()
        self.assertNotIn("fixture-secret", json.dumps(public))
        self.assertNotIn("fixture-user", json.dumps(public))
        self.service.update({"proxy": public["proxy"], "mail": public["mail"]})
        self.assertEqual(self.service._config["proxy"], original)
        self.assertEqual(self.service._config["mail"]["proxy"], original)
        with self.assertRaises(ValueError):
            self.service.update({"mail": {"proxy": public["mail"]["proxy"].replace("proxy.example", "changed.example")}})
        self.assertEqual(self.service._config["mail"]["proxy"], original)
        self.worker.assert_not_called()

    def test_partial_capacity_does_not_start_a_target_it_cannot_complete(self):
        self.preflight.return_value = {"enabled": True, "strict": True, "remaining": 2}
        self.service._config["stats"].update(success=5, fail=66891, done=66896)
        result = self.service.start()
        self.assertFalse(result["enabled"])
        self.assertEqual(result["stats"]["done"], 66896)
        self.assertEqual(result["stats"]["proxy_capacity"]["remaining"], 2)
        self.assertEqual(result["stats"]["proxy_capacity"]["required"], 10)
        self.assertEqual(result["stats"]["proxy_capacity"]["shortfall"], 8)
        self.assertIn("还差 8 个名额", result["stats"]["last_error"])
        self.worker.assert_not_called()

    def test_unhandled_worker_exception_stops_without_resubmission(self):
        self.worker.side_effect = RuntimeError("local constructor failed")
        result = self.run_job()
        self.assertEqual(self.worker.call_count, 1)
        self.assertEqual(result["stats"]["finish_reason"], "worker_exception")
        self.assertIn("local constructor failed", result["stats"]["last_error"])
        self.assertEqual(result["stats"]["done"], 1)
        self.assertEqual(result["stats"]["fail"], 1)

    def test_fatal_result_stops_without_resubmission(self):
        self.worker.return_value = {"ok": False, "fatal": True, "error": "capacity exhausted", "error_code": "initialization_failed"}
        result = self.run_job()
        self.assertEqual(self.worker.call_count, 1)
        self.assertEqual(result["stats"]["finish_reason"], "initialization_failed")

    def test_consecutive_failures_back_off_and_trip_circuit(self):
        self.service._stop_event = Mock(wraps=threading.Event())
        self.service._stop_event.wait = Mock(return_value=False)
        result = self.run_job()
        self.assertEqual(self.worker.call_count, self.module.MAX_CONSECUTIVE_FAILURES)
        self.assertEqual(result["stats"]["finish_reason"], "consecutive_failures")
        delays = [call.args[0] for call in self.service._stop_event.wait.call_args_list]
        self.assertGreater(len(delays), 0)
        for index, delay in enumerate(delays):
            self.assertAlmostEqual(delay, 2.0 ** index, delta=0.1)

    def test_stop_interrupts_failure_backoff(self):
        self.service.start()
        self.service.stop()
        self.service._runner.join(timeout=2)
        self.assertFalse(self.service._runner.is_alive())
        result = self.service.get()
        self.assertEqual(result["stats"]["finish_reason"], "stopped_by_user")
        self.assertEqual(result["stats"]["running"], 0)

    def test_repeated_pool_checks_are_not_logged_or_saved(self):
        config = self.service.get()
        with patch.object(self.service, "_save", wraps=self.service._save) as save:
            for _ in range(100):
                self.service._target_reached(config, 0)
        self.assertEqual(len(self.service.get()["logs"]), 1)
        self.assertLessEqual(save.call_count, 1)

    def test_total_attempts_exhausted_is_not_target_reached(self):
        self.service.update({"mode": "total", "total": 1})
        result = self.run_job()
        self.assertEqual(self.worker.call_count, 1)
        self.assertEqual(result["stats"]["finish_reason"], "attempts_exhausted")

    def test_successful_total_job_closes_and_counts_once(self):
        self.worker.return_value = {"ok": True}
        self.service.update({"mode": "total", "total": 3})
        result = self.run_job()
        self.assertEqual(result["stats"]["finish_reason"], "target_reached")
        self.assertEqual((result["stats"]["success"], result["stats"]["fail"], result["stats"]["done"]), (3, 0, 3))
        self.assertFalse(result["enabled"])
        self.assertEqual(result["stats"]["running"], 0)

    def test_runner_failure_always_closes_job(self):
        with patch.object(self.module, "ThreadPoolExecutor", side_effect=RuntimeError("executor unavailable")):
            result = self.run_job()
        self.assertFalse(result["enabled"])
        self.assertEqual(result["stats"]["finish_reason"], "scheduler_error")
        self.assertIn("executor unavailable", result["stats"]["last_error"])

    def test_reset_during_active_job_is_rejected(self):
        self.service._config["enabled"] = True
        with self.assertRaisesRegex(ValueError, "运行"):
            self.service.reset()

    def test_error_details_redact_secrets_and_bound_length(self):
        self.worker.side_effect = RuntimeError("https://user:secret@proxy.test:8080?token=secret Bearer sensitive-token user@example.test " + "x" * 3000)
        result = self.run_job()
        detail = result["stats"]["last_error"]
        self.assertNotIn("secret", detail)
        self.assertNotIn("sensitive-token", detail)
        self.assertNotIn("user@example.test", detail)
        self.assertLessEqual(len(detail), 1000)
        persisted = json.loads(self.service._store_file.read_text())
        self.assertEqual(persisted["stats"]["last_error"], detail)

    def test_target_mode_has_a_hard_attempt_budget_even_with_successes(self):
        self.worker.return_value = {"ok": True}
        with patch.object(self.module, "MAX_JOB_ATTEMPTS", 3):
            result = self.run_job()
        self.assertEqual(self.worker.call_count, 3)
        self.assertEqual(result["stats"]["finish_reason"], "attempt_budget_exhausted")

    def test_stopped_job_metrics_are_live_without_rewriting_job_history(self):
        self.service._config["stats"].update(success=5, fail=66891)
        self.module.account_service.list_accounts = lambda: [{"status": "正常", "quota": 2}]
        with patch.object(self.service, "_save") as save:
            result = self.service.get()
        self.assertEqual(result["stats"]["current_available"], 1)
        self.assertEqual(result["stats"]["current_quota"], 2)
        self.assertEqual(result["stats"]["success"], 5)
        self.assertEqual(result["stats"]["fail"], 66891)
        save.assert_not_called()

    def test_five_simultaneous_constructor_errors_are_drained_without_refill(self):
        barrier = threading.Barrier(5)
        def fail_together(_index):
            barrier.wait(timeout=1)
            raise RuntimeError("constructor capacity exhausted")
        self.worker.side_effect = fail_together
        self.service.update({"threads": 5})
        result = self.run_job()
        self.assertEqual(self.worker.call_count, 5)
        self.assertEqual(result["stats"]["fail"], 5)
        self.assertEqual(result["stats"]["running"], 0)
        self.assertEqual(result["stats"]["finish_reason"], "worker_exception")


if __name__ == "__main__":
    unittest.main()
