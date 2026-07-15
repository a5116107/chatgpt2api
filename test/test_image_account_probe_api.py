from __future__ import annotations

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
        self.assertEqual(response.json()["items"], items)
        probe.assert_called_once_with(12)

    def test_probe_batch_limit_is_validated(self) -> None:
        with mock.patch.object(accounts_module, "require_admin", return_value={"role": "admin"}):
            response = self._client().post(
                "/api/accounts/image-pool/probe",
                json={"limit": 101},
            )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
