from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from services import risk_control_service as module


class ProxyInventoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.proxies = root / "proxies.json"
        self.events = root / "events.json"
        for name, value in (
            ("PROXIES_FILE", self.proxies),
            ("RISK_EVENTS_FILE", self.events),
        ):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.url = "http://fixture-user:fixture-password@proxy.example.test:8080"
        patcher = patch.object(
            module, "_runtime_aggregate_proxy_urls", return_value={self.url}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.service = module.RiskControlService()

    def test_runtime_inventory_never_returns_credentials(self):
        items = self.service.list_proxies()
        self.assertEqual(len(items), 1)
        serialized = json.dumps(items)
        self.assertNotIn("fixture-user", serialized)
        self.assertNotIn("fixture-password", serialized)
        self.assertIn("proxy.example.test:8080", serialized)

    def test_untested_runtime_is_unknown_without_a_score(self):
        item = self.service.list_proxies()[0]
        self.assertEqual(item["status"], "unknown")
        self.assertIsNone(item["score"])

    def test_manual_credentials_remain_server_side_for_testing(self):
        url = "http://manual-user:manual-password@manual.example.test:3128"
        item = self.service.upsert_proxy(url)
        self.assertNotIn("manual-password", json.dumps(item))
        stored = json.loads(self.proxies.read_text(encoding="utf-8"))["items"]
        self.assertEqual(
            next(row for row in stored if row["id"] == item["id"])["proxy"], url
        )

    def test_rebuilding_scores_preserves_inventory_and_success_observations(self):
        url = "http://manual.example.test:3128"
        self.service.report_proxy_event(url, "success")
        self.service.report_proxy_event(url, "success")
        before = {item["id"]: item for item in self.service.list_proxies()}
        self.service.rebuild_proxy_scores()
        after = {item["id"]: item for item in self.service.list_proxies()}
        self.assertEqual(set(after), set(before))
        node_id = self.service.proxy_id(url)
        self.assertEqual(after[node_id]["success"], 2)
        self.assertEqual(
            after[node_id]["last_success_at"], before[node_id]["last_success_at"]
        )

    def test_batch_test_uses_private_url_and_redacts_result(self):
        with patch(
            "services.proxy_service.test_proxy",
            return_value={"ok": True, "status": 200},
        ) as test:
            result = self.service.test_proxy_pool(limit=1)
        test.assert_called_once_with(self.url, timeout=12.0)
        self.assertNotIn("fixture-password", json.dumps(result))
        self.assertEqual(result["ok"], 1)

    def test_legacy_masked_entry_is_skipped_without_losing_inventory(self):
        self.service._save_items(
            self.proxies,
            [
                {
                    "id": "legacy",
                    "proxy": "http://***:***@old.example.test:8080",
                    "status": "unknown",
                }
            ],
        )
        with patch(
            "services.proxy_service.test_proxy",
            return_value={"ok": True, "status": 200},
        ) as test:
            result = self.service.test_proxy_pool(limit=10)
        self.assertEqual(test.call_count, 1)
        self.assertEqual(result["skipped"], 1)
        self.assertIn("legacy", {node["id"] for node in self.service.list_proxies()})

    def test_feedback_by_id_updates_original_without_duplicate_masked_record(self):
        original = self.service.list_proxies()[0]
        self.service.report_proxy_event("", "success", 200, proxy_id=original["id"])
        current = self.service.list_proxies()
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["success"], 1)
        self.assertEqual(current[0]["status"], "healthy")
        self.assertEqual(current[0]["observation_scope"], "project_target")

    def test_stale_observation_is_not_current_health(self):
        original = self.service.upsert_proxy("http://old.example.test:8080")
        items = self.service._load_items(self.proxies)
        items[0].update(
            success=1,
            status="healthy",
            score=100,
            last_success_at="2000-01-01T00:00:00+00:00",
        )
        self.service._save_items(self.proxies, items)
        current = next(
            item for item in self.service.list_proxies() if item["id"] == original["id"]
        )
        self.assertEqual(current["status"], "stale")

    def test_bulk_import_is_idempotent_and_never_echoes_invalid_credentials(self):
        value = "http://batch-user:batch-secret@proxy.example.test:3129"
        result = self.service.import_proxies(
            value + "\n" + value + "\nhttp://private:secret@invalid:not-a-port"
        )
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(result["invalid"][0]["line"], 3)
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(self.service.import_proxies(value)["duplicates"], 1)

    def test_unchecked_and_target_denied_proxies_are_not_assignable(self):
        item = self.service.upsert_proxy("http://test.example.test:3129")
        with self.assertRaises(ValueError):
            self.service.activate_proxies([item["id"]])
        probe = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "network_ok": True,
            "target_ok": False,
            "target_status": 403,
        }
        with patch("services.proxy_service.probe_proxy_route", return_value=probe):
            result = self.service.test_proxy_pool(ids=[item["id"]])
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["items"][0]["result"]["network_ok"], True)
        with self.assertRaises(ValueError):
            self.service.activate_proxies([item["id"]])

    def test_tested_assignment_stays_private_and_disable_preserves_inventory(self):
        imported = self.service.import_proxies(
            "http://batch-user:batch-secret@proxy.example.test:3129"
        )
        probe = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "network_ok": True,
            "target_ok": True,
            "target_status": 200,
            "country": "US",
            "exit_ip": "192.0.2.1",
        }
        with patch("services.proxy_service.probe_proxy_route", return_value=probe):
            self.service.test_proxy_pool(ids=imported["ids"])
        self.service.activate_proxies(imported["ids"])
        groups = self.service.assigned_proxy_groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["capacity"], 1)
        self.assertIn("batch-secret", groups[0]["proxy_url"])
        self.assertNotIn("batch-secret", json.dumps(self.service.list_proxies()))
        self.assertEqual(self.service.delete_proxies(ids=imported["ids"])["removed"], 0)
        self.service.activate_proxies(imported["ids"], enabled=False)
        self.assertEqual(self.service.assigned_proxy_groups(), [])
        self.assertIn(
            imported["ids"][0], {item["id"] for item in self.service.list_proxies()}
        )

    def test_stale_probe_does_not_admit_new_account_assignments(self):
        self.service.upsert_proxy("http://stale.example.test:3129")
        items = self.service._load_items(self.proxies)
        items[0].update(
            assignment_enabled=True,
            probe={
                "network_ok": True,
                "target_ok": True,
                "observed_at": "2000-01-01T00:00:00+00:00",
            },
        )
        self.service._save_items(self.proxies, items)
        self.assertEqual(self.service.assigned_proxy_groups(), [])

    def test_monitor_only_checks_enabled_due_entries(self):
        item = self.service.upsert_proxy("http://due.example.test:3129")
        self.service.upsert_proxy("http://unused.example.test:3129")
        items = self.service._load_items(self.proxies)
        items[0].update(
            assignment_enabled=True, probe={"observed_at": "2000-01-01T00:00:00+00:00"}
        )
        self.service._save_items(self.proxies, items)
        with patch.object(
            self.service, "test_proxy_pool", return_value={"tested": 1}
        ) as check:
            self.service.recheck_enabled_proxies()
        check.assert_called_once_with(ids=[item["id"]], limit=10, include_dead=True)

    def test_parallel_test_request_is_bounded(self):
        self.service._proxy_test_lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError, "正在执行"):
                self.service.test_proxy_pool(ids=[])
        finally:
            self.service._proxy_test_lock.release()

    def test_2000_import_boundary_and_2001_rejection_are_atomic(self):
        lines = [f"http://proxy-{index}.example.test:3129" for index in range(2001)]
        self.assertEqual(
            self.service.import_proxies("\n".join(lines[:2000]))["imported"], 2000
        )
        before = self.proxies.read_bytes()
        with self.assertRaises(ValueError):
            self.service.import_proxies("\n".join(lines))
        self.assertEqual(self.proxies.read_bytes(), before)
        self.assertEqual(
            self.service.import_proxies("\n".join(lines[:2000]))["duplicates"], 2000
        )

    def test_batch_probe_saves_twice_instead_of_per_proxy(self):
        ids = self.service.import_proxies(
            "\n".join(
                f"http://proxy-{index}.example.test:3129" for index in range(2000)
            )
        )["ids"]
        probe = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "network_ok": True,
            "target_ok": True,
            "target_status": 200,
            "country": "US",
        }
        with (
            patch("services.proxy_service.probe_proxy_route", return_value=probe),
            patch.object(
                self.service, "_save_items", wraps=self.service._save_items
            ) as save,
        ):
            result = self.service.test_proxy_pool(ids=ids[:200], limit=200)
        self.assertEqual(result["ok"], 200)
        self.assertEqual(save.call_count, 2)

    def test_large_enabled_inventory_monitor_checks_200_oldest(self):
        self.service.import_proxies(
            "\n".join(
                f"http://proxy-{index}.example.test:3129" for index in range(2000)
            )
        )
        items = self.service._load_items(self.proxies)
        for item in items:
            item.update(
                assignment_enabled=True,
                probe={"observed_at": "2000-01-01T00:00:00+00:00"},
            )
        self.service._save_items(self.proxies, items)
        with patch.object(
            self.service, "test_proxy_pool", return_value={"tested": 200}
        ) as check:
            self.service.recheck_enabled_proxies()
        self.assertEqual(len(check.call_args.kwargs["ids"]), 200)
        self.assertEqual(check.call_args.kwargs["limit"], 200)


if __name__ == "__main__":
    unittest.main()
