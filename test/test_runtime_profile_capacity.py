from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import runtime_profile_service as module


class RuntimeProfileCapacityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.pool = root / "pool.json"
        patcher = patch.object(module, "ACCOUNT_PROXY_POOL_FILE", self.pool)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.service = module.RuntimeProfileService(root / "profiles.json")
        from services.risk_control_service import risk_control_service
        patcher = patch.object(risk_control_service, "assigned_proxy_groups", return_value=[])
        self.imported_groups = patcher.start()
        self.addCleanup(patcher.stop)

    def configure(self, capacity=1):
        self.pool.write_text(json.dumps({"enabled": True, "strict_capacity": True, "groups": [{"id": "fixture", "proxy_url": "http://proxy.example.test:8080", "template": "chrome_win_145", "capacity": capacity}]}))

    def test_preflight_without_pool_does_not_create_profiles(self):
        self.assertEqual(self.service.registration_preflight(), {"enabled": False, "remaining": None})
        self.assertFalse(self.service.path.exists())

    def test_preflight_counts_capacity_without_reserving_it(self):
        self.configure(capacity=2)
        self.service._profiles = {"existing": {"status": "active", "proxy_policy": {"group": "fixture"}}}
        for _ in range(3):
            self.assertEqual(self.service.registration_preflight()["remaining"], 1)
        self.assertEqual(len(self.service._profiles), 1)
        self.assertFalse(self.service.path.exists())

    def test_exhausted_capacity_stops_preflight_and_allocation(self):
        self.configure()
        self.service._profiles = {"existing": {"status": "active", "proxy_policy": {"group": "fixture"}}}
        with self.assertRaisesRegex(RuntimeError, "capacity is exhausted"):
            self.service.registration_preflight()
        with self.assertRaisesRegex(RuntimeError, "capacity is exhausted"):
            self.service._allocate_account_proxy_group_locked()

    def test_invalid_configuration_fails_closed(self):
        self.pool.write_text("not json")
        with self.assertRaisesRegex(RuntimeError, "configuration is invalid"):
            self.service.registration_preflight()

    def test_selected_imported_proxy_adds_capacity_and_binding_is_retained(self):
        self.configure(capacity=1)
        self.imported_groups.return_value = [{"id": "px_external", "proxy_url": "http://fixture:private@external.example.test:3129", "template": "chrome_win_145", "capacity": 1, "preferred_node_id": ""}]
        self.assertEqual(self.service.registration_preflight()["remaining"], 2)
        profile = self.service.create_profile(source="register")
        self.assertEqual(profile["proxy_policy"]["group"], "px_external")
        self.assertEqual(profile["proxy_policy"]["mode"], "account_sticky")
        self.assertFalse(profile["proxy_policy"]["allow_rotate"])
        self.imported_groups.return_value = []
        self.assertEqual(self.service.get(profile["id"])["proxy_policy"]["runtime_proxy"], profile["proxy_policy"]["runtime_proxy"])


if __name__ == "__main__":
    unittest.main()
