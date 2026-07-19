from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.accounts as accounts_module
from services.account_service import account_service


class ImageAccountProbeApiTests(unittest.TestCase):
    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(accounts_module.create_router())
        return TestClient(app)

    def test_admin_can_run_a_bounded_due_probe_batch(self) -> None:
        probe_result = {
            "checked": 2,
            "healthy": 1,
            "removed": 1,
            "quarantined": 1,
            "failures": [{"account_hash": "token:abc", "error": "token invalid"}],
        }
        items = [{"access_token": "token-1", "image_pool_state": "ready"}]
        with (
            mock.patch.object(accounts_module, "require_admin", return_value={"role": "admin"}),
            mock.patch.object(account_service, "probe_image_candidates", return_value=probe_result) as probe,
            mock.patch.object(account_service, "get_stats", return_value={"image_schedulable_accounts": 1}),
            mock.patch.object(account_service, "list_accounts", return_value=items),
        ):
            response = self._client().post(
                "/api/accounts/image-pool/probe",
                json={"limit": 12},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checked"], 2)
        self.assertEqual(response.json()["removed"], 1)
        self.assertEqual(response.json()["items"], items)
        probe.assert_called_once_with(12)

    def test_probe_batch_limit_is_validated(self) -> None:
        with mock.patch.object(accounts_module, "require_admin", return_value={"role": "admin"}):
            response = self._client().post(
                "/api/accounts/image-pool/probe",
                json={"limit": 101},
            )

        self.assertEqual(response.status_code, 422)

    def test_refresh_progress_exists_before_background_task_is_scheduled(self) -> None:
        def capture_task(coroutine):
            coroutine.close()
            return mock.Mock()

        router = accounts_module.create_router()
        endpoint = next(
            route.endpoint
            for route in router.routes
            if route.path == "/api/accounts/refresh"
        )

        async def invoke() -> dict:
            with (
                mock.patch.object(
                    accounts_module,
                    "require_admin",
                    return_value={"role": "admin"},
                ),
                mock.patch.object(
                    account_service,
                    "list_tokens",
                    return_value=["token-1"],
                ),
                mock.patch.object(
                    accounts_module.asyncio,
                    "create_task",
                    side_effect=capture_task,
                ),
            ):
                return await endpoint(
                    accounts_module.AccountRefreshRequest(access_tokens=[]),
                    authorization=None,
                )

        started = asyncio.run(invoke())
        progress_id = started["progress_id"]
        progress = account_service.get_refresh_progress(progress_id)

        self.addCleanup(account_service.clean_refresh_progress, progress_id)
        self.assertIsNotNone(progress)
        self.assertEqual(progress["total"], 1)
        self.assertEqual(progress["processed"], 0)
        self.assertFalse(progress["done"])


if __name__ == "__main__":
    unittest.main()
