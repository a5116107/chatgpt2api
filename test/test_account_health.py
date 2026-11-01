from __future__ import annotations

import copy
import time
import unittest
from threading import Event, Lock
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.account_health import (
    AccountHealthService, check_login, classify_response, classify_transport,
    health_context, health_view,
)
from services.account_service import AccountService
from services.config import config
from services.openai_backend_api import OpenAIBackendAPI
from test.test_account_token_lifecycle import MemoryStorage, account


def response(status=200, text="", body=None, headers=None):
    return SimpleNamespace(status_code=status, text=text, headers=headers or {}, json=lambda: body)


class AccountHealthTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("services.account_service.runtime_profile_service.ensure_account_profile",
                             side_effect=lambda item: (item, {}))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(AccountService, "_sync_risk_control_capabilities")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.accounts = AccountService(MemoryStorage([account("fixture-token")]))
        self.health = AccountHealthService(self.accounts)

    def test_denial_classification_and_no_unproven_ban(self):
        self.assertIsNone(classify_response(response(200, '<script src="challenge-platform/widget.js"></script>')))
        cases = [
            (response(403, "Enable JavaScript and cookies to continue"), "browser_verification_required"),
            (response(403, headers={"cf-mitigated": "challenge"}), "browser_verification_required"),
            (response(403), "access_denied"),
            (response(403, "unsupported_country"), "region_restricted"),
            (response(401), "login_expired"), (response(429), "rate_limited"),
            (response(407), "proxy_auth_required"), (response(302), "login_redirect"),
            (response(503), "upstream_error"),
        ]
        for value, state in cases:
            with self.subTest(state=state):
                self.assertEqual(classify_response(value), state)

    def run_check(self, responses):
        backend = mock.MagicMock()
        backend.base_url = "https://chatgpt.com"
        backend.__enter__.return_value = backend
        backend._get_homepage.side_effect = responses[:1]
        backend.session.get.side_effect = responses[1:]
        with mock.patch("services.openai_backend_api.OpenAIBackendAPI", return_value=backend):
            result = check_login({"access_token": "fixture-token"})
        return result, backend

    def test_browser_challenge_stops_before_authenticated_request(self):
        result, backend = self.run_check([response(403, "Enable JavaScript and cookies to continue")])
        self.assertEqual(result["state"], "browser_verification_required")
        backend._get_homepage.assert_called_once_with(timeout=15, allow_redirects=False)
        backend.session.get.assert_not_called()
        backend.session.post.assert_not_called()

    def test_login_check_requires_identity_not_just_http_200(self):
        for body, expected in [({"id": "user-fixture"}, "login_verified"),
                               ({}, "invalid_response"), ({"error": "fixture"}, "invalid_response")]:
            with self.subTest(body=body):
                result, backend = self.run_check([response(), response(body=body)])
                self.assertEqual(result["state"], expected)
                self.assertEqual(result["phase"], "login")
                self.assertEqual(backend.session.get.call_count, 1)
                backend._get_homepage.assert_called_once_with(timeout=15, allow_redirects=False)
                for call in backend.session.get.call_args_list:
                    self.assertFalse(call.kwargs["allow_redirects"])
                    self.assertEqual(call.kwargs["timeout"], 15)
                backend.session.post.assert_not_called()

    def test_no_raw_proxy_error_is_saved(self):
        secret = "http://PRIVATE_USER:PRIVATE_PASSWORD@proxy.test:3129"
        result, _ = self.run_check([RuntimeError("CONNECT tunnel failed, response 403 " + secret)])
        self.assertEqual(result["state"], "proxy_tunnel_rejected")
        self.assertNotIn("PRIVATE", str(result))
        for code, expected in [(28, "network_timeout"), (60, "tls_error"), (7, "network_error")]:
            error = RuntimeError("fixture")
            error.code = code
            self.assertEqual(classify_transport(error), expected)

    def test_diagnostic_client_uses_snapshot_without_profile_changes_or_recovery(self):
        snapshot = account("fixture-token", proxy="http://proxy.test:3129")
        before = copy.deepcopy(snapshot)
        with mock.patch("services.openai_backend_api.runtime_profile_service.ensure_account_profile") as ensure, \
             mock.patch("services.openai_backend_api.account_service.get_account") as fetch, \
             mock.patch("services.openai_backend_api.AccountProxySession") as recovery, \
             mock.patch("services.openai_backend_api.requests.Session") as session:
            with OpenAIBackendAPI("fixture-token", diagnostic_account=snapshot) as client:
                self.assertEqual(client.account, snapshot)
                self.assertTrue(session.call_args.kwargs["verify"])
                self.assertEqual(session.call_args.kwargs["proxy"], snapshot["proxy"])
            ensure.assert_not_called()
            fetch.assert_not_called()
            recovery.assert_not_called()
            session.return_value.close.assert_called_once()
        self.assertEqual(snapshot, before)

    def test_homepage_restores_bearer_after_a_transport_failure(self):
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        backend.base_url = "https://fixture.invalid"
        backend.session = mock.Mock()
        backend.session.headers = {"Authorization": "Bearer fixture-token"}
        backend._bootstrap_headers = mock.Mock(return_value={})
        def fail(*args, **kwargs):
            self.assertNotIn("Authorization", backend.session.headers)
            self.assertNotIn("Authorization", kwargs["headers"])
            self.assertFalse(kwargs["allow_redirects"])
            raise TimeoutError("fixture")
        backend.session.get.side_effect = fail
        with self.assertRaises(TimeoutError):
            backend._get_homepage(timeout=15)
        self.assertEqual(backend.session.headers["Authorization"], "Bearer fixture-token")

    def test_save_merges_metadata_without_mutating_credentials_or_health_policy(self):
        before = self.accounts.health_snapshot("fixture-token")
        context = health_context(before)
        result = {"state": "browser_verification_required", "checked_at": time.time()}
        self.assertTrue(self.accounts.save_health_check("fixture-token", context, result))
        saved = self.accounts.health_snapshot("fixture-token")
        saved.pop("health_check")
        self.assertEqual(saved, before)
        view = self.accounts.list_accounts()[0]["health_check"]
        self.assertNotIn("context_hash", view)
        self.assertFalse(view["stale"])
        self.assertTrue(health_view(self.accounts._accounts["fixture-token"], now=time.time() + 901)["stale"])

    def test_stale_results_never_overwrite_changed_or_deleted_accounts(self):
        context = health_context(self.accounts.health_snapshot("fixture-token"))
        self.accounts._accounts["fixture-token"]["proxy"] = "http://changed.test:3129"
        self.assertFalse(self.accounts.save_health_check("fixture-token", context, {"state": "login_verified"}))
        self.accounts._accounts.clear()
        self.assertFalse(self.accounts.save_health_check("fixture-token", context, {"state": "login_verified"}))
        self.assertFalse(self.accounts._accounts)

    def test_changed_profile_and_proxy_mark_previous_result_stale(self):
        value = self.accounts.health_snapshot("fixture-token")
        value["health_check"] = {"state": "login_verified", "checked_at": time.time(),
                                 "context_hash": health_context(value)}
        for key, changed in [("access_token", "new-token"), ("fp", {"impersonate": "chrome"}),
                             ("proxy", "http://new.test:80"), ("runtime_profile_id", "new-profile")]:
            with self.subTest(key=key):
                mutated = {**value, key: changed}
                self.assertTrue(health_view(mutated)["stale"])

    def test_failed_storage_rolls_back_metadata(self):
        value = self.accounts.health_snapshot("fixture-token")
        with mock.patch.object(self.accounts.storage, "save_accounts", side_effect=OSError("fixture")):
            with self.assertRaises(OSError):
                self.accounts.save_health_check("fixture-token", health_context(value), {"state": "login_verified"})
        self.assertNotIn("health_check", self.accounts.health_snapshot("fixture-token"))

    def test_busy_accounts_are_skipped_and_recent_results_are_cached(self):
        self.accounts._image_inflight["fixture-token"] = 1
        with mock.patch("services.account_health.check_login") as check:
            self.assertEqual(self.health._check_one("fixture-token"), "skipped")
            check.assert_not_called()
        self.accounts._image_inflight.clear()
        with mock.patch("services.account_health.check_login", return_value={"state": "login_verified", "checked_at": time.time()}) as check:
            self.assertEqual(self.health._check_one("fixture-token"), "login_verified")
            self.assertEqual(self.health._check_one("fixture-token"), "cached")
            check.assert_called_once()

    def test_progress_initialized_and_duplicate_start_reuses_active_job(self):
        with mock.patch("services.account_health.Thread") as thread:
            job = self.health.start(["fixture-token", "fixture-token"])
            self.assertEqual(job["total"], 1)
            self.assertEqual(job["processed"], 0)
            self.assertEqual(self.health.current()["progress_id"], job["progress_id"])
            self.assertEqual(self.health.start(["other"])["progress_id"], job["progress_id"])
            thread.return_value.start.assert_called_once()
            self.health._run(job["progress_id"], ["missing"])
        self.assertIsNone(self.health.current())
        self.assertEqual(self.health.progress(job["progress_id"])["counts"], {"skipped": 1})

    def test_parallel_checks_are_bounded_and_failures_do_not_cancel_siblings(self):
        entered, release = Event(), Event()
        lock = Lock()
        active = 0
        peak = 0
        def check(token):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 3:
                    entered.set()
            release.wait(3)
            with lock:
                active -= 1
            if token == "bad":
                raise OSError("private text must not leak")
            return "login_verified"
        with mock.patch.object(self.health, "_check_one", side_effect=check):
            job = self.health.start(["a", "b", "bad", "c"])
            self.assertTrue(entered.wait(3))
            release.set()
            deadline = time.monotonic() + 3
            while not self.health.progress(job["progress_id"])["done"] and time.monotonic() < deadline:
                Event().wait(0.01)
            done = self.health.progress(job["progress_id"])
        self.assertEqual(peak, 3)
        self.assertTrue(done["done"])
        self.assertEqual(done["processed"], 4)
        self.assertEqual(done["counts"], {"login_verified": 3, "check_error": 1})
        self.assertNotIn("private", str(done))


class AccountHealthApiTests(unittest.TestCase):
    def setUp(self):
        import api.accounts as module
        self.health = mock.Mock()
        patcher = mock.patch.object(module, "AccountHealthService", return_value=self.health)
        patcher.start()
        self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(module.create_router())
        self.client = TestClient(app)
        self.headers = {"Authorization": f"Bearer {config.auth_key}"}

    def test_detection_and_progress_require_admin(self):
        for method, path in [("post", "/api/accounts/health-check"),
                             ("get", "/api/accounts/health-check/current"),
                             ("get", "/api/accounts/health-check/fixture")]:
            kwargs = {"json": {}} if method == "post" else {}
            result = getattr(self.client, method)(path, **kwargs)
            self.assertIn(result.status_code, (401, 403))
        self.health.start.assert_not_called()
        self.health.progress.assert_not_called()
        self.health.current.assert_not_called()

    def test_selected_tokens_deduplicated_and_current_route_is_not_job_id(self):
        self.health.start.return_value = {"progress_id": "fixture", "done": False}
        result = self.client.post("/api/accounts/health-check", headers=self.headers,
                                  json={"access_tokens": [" first ", "first", "second"]})
        self.assertEqual(result.status_code, 200)
        self.health.start.assert_called_once_with(["first", "second"])
        self.health.current.return_value = None
        self.assertEqual(self.client.get("/api/accounts/health-check/current", headers=self.headers).json(), {"job": None})
        self.health.progress.return_value = None
        self.assertEqual(self.client.get("/api/accounts/health-check/unknown", headers=self.headers).status_code, 404)

    def test_empty_pool_returns_actionable_error(self):
        self.health.start.side_effect = ValueError("账号池为空，请先导入账号")
        result = self.client.post("/api/accounts/health-check", headers=self.headers, json={})
        self.assertEqual(result.status_code, 400)


if __name__ == "__main__":
    unittest.main()
