from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.system as system_module
from services.account_service import account_service


def pool_stats(*, schedulable: int) -> dict:
    return {
        "total": 3,
        "cumulative_total": 3,
        "active": 3,
        "limited": 0,
        "abnormal": 0,
        "disabled": 0,
        "total_quota": 15,
        "unlimited_quota_count": 0,
        "total_success": 2,
        "total_fail": 1,
        "by_type": {"free": 3},
        "image_pool_states": {
            "ready": schedulable,
            "probation": 0,
            "cooldown": 3 - schedulable,
            "exhausted": 0,
            "quarantined": 0,
            "disabled": 0,
        },
        "image_schedulable_accounts": schedulable,
        "image_schedulable_quota": 5 * schedulable,
        "image_verified_quota": 5 * schedulable,
        "image_probe_due": 1,
        "image_probe_inflight": 1,
        "image_available_slots": 3 * schedulable,
    }


class ImageAccountHealthApiTests(unittest.TestCase):
    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(system_module.create_router("test"))
        return TestClient(app)

    def test_health_is_degraded_when_active_rows_are_not_schedulable(self) -> None:
        storage = mock.Mock()
        storage.get_backend_info.return_value = {"type": "json"}
        storage.health_check.return_value = {"ok": True}
        with (
            mock.patch.object(account_service, "get_stats", return_value=pool_stats(schedulable=0)),
            mock.patch.object(system_module.config, "get_storage_backend", return_value=storage),
            mock.patch.object(system_module.proxy_settings, "get_runtime_status", return_value={}),
        ):
            response = self._client().get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["healthy"])
        self.assertEqual(response.json()["status"], "degraded")

    def test_health_dashboard_exposes_pool_capacity_and_state(self) -> None:
        storage = mock.Mock()
        storage.get_backend_info.return_value = {"type": "json"}
        storage.health_check.return_value = {"ok": True}
        with (
            mock.patch.object(account_service, "get_stats", return_value=pool_stats(schedulable=1)),
            mock.patch.object(system_module.config, "get_storage_backend", return_value=storage),
            mock.patch.object(system_module.proxy_settings, "get_runtime_status", return_value={}),
        ):
            response = self._client().get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertIn("可调度账号", response.text)
        self.assertIn("图片号池状态", response.text)


if __name__ == "__main__":
    unittest.main()
