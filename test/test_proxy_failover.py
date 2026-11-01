from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock, patch
import tempfile
from pathlib import Path

from curl_cffi.requests import Session
from curl_cffi.requests.exceptions import RequestException
from services.proxy_service import AccountProxySession
from services.runtime_profile_service import RuntimeProfileService
from services import account_service as accounts
from test.test_account_token_lifecycle import MemoryStorage, account


class ProxyFailoverTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.profiles = RuntimeProfileService(Path(directory.name) / "profiles.json")
        self.old = "http://old.example.test:3129"
        self.new = "http://new.example.test:3129"
        self.profile = self.profiles.normalize_profile(
            {
                "id": "fixture-profile",
                "account_key": "fixture",
                "proxy_policy": {
                    "runtime_proxy": self.old,
                    "group": "old",
                    "country": "US",
                },
            }
        )
        self.profiles._profiles = {self.profile["id"]: self.profile}
        self.item = account(
            "fixture", proxy=self.old, runtime_profile_id=self.profile["id"]
        )
        self.groups = [
            {"id": "new", "proxy_url": self.new, "country": "US", "capacity": 1}
        ]
        patcher = patch(
            "services.risk_control_service.risk_control_service.assigned_proxy_groups",
            side_effect=lambda: self.groups,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_same_country_recovery_preserves_identity_and_cooldown(self):
        result = self.profiles.failover_proxy(self.item, self.old, "connection_failed")
        self.assertEqual(result["proxy"], self.new)
        current = self.profiles.get(self.profile["id"])
        for key in ["tls", "headers", "openai"]:
            self.assertEqual(current[key], self.profile[key])
        self.assertEqual(current["proxy_policy"]["failover_count"], 1)
        self.assertIsNone(
            self.profiles.failover_proxy(result, self.new, "connection_failed")
        )
        self.assertNotIn("fixture-password", str(self.profiles.public_profile(current)))

    def test_disabled_unknown_region_capacity_and_other_country_do_not_switch(self):
        for variant in ["disabled", "unknown", "full", "other"]:
            with self.subTest(variant=variant):
                self.profiles._profiles = {self.profile["id"]: deepcopy(self.profile)}
                self.groups[0]["country"] = "US"
                if variant == "disabled":
                    self.profiles.set_proxy_failover(self.profile["id"], False)
                if variant == "unknown":
                    self.profiles._profiles[self.profile["id"]]["proxy_policy"][
                        "country"
                    ] = ""
                if variant == "full":
                    self.profiles._profiles["occupied"] = {
                        "status": "active",
                        "proxy_policy": {"group": "new"},
                    }
                if variant == "other":
                    self.groups[0]["country"] = "DE"
                self.assertIsNone(
                    self.profiles.failover_proxy(
                        self.item, self.old, "connection_failed"
                    )
                )

    def test_concurrent_old_requests_only_switch_once(self):
        with (
            patch.object(
                accounts.runtime_profile_service,
                "ensure_account_profile",
                side_effect=lambda item: (item, {}),
            ),
            patch.object(accounts.AccountService, "_sync_risk_control_capabilities"),
        ):
            service = accounts.AccountService(MemoryStorage([self.item]))
        with (
            patch.object(accounts, "runtime_profile_service", self.profiles),
            patch(
                "services.risk_control_service.risk_control_service.proxy_connection_state",
                return_value={"country": "US"},
            ),
            patch(
                "services.risk_control_service.risk_control_service.invalidate_proxy_connection"
            ),
        ):
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(
                    executor.map(
                        lambda _: service.handle_proxy_failure(
                            "fixture", self.old, "connection_failed"
                        ),
                        range(20),
                    )
                )
        self.assertTrue(all(result["proxy"] == self.new for result in results))
        self.assertEqual(
            self.profiles.get(self.profile["id"])["proxy_policy"]["failover_count"], 1
        )

    def test_account_save_failure_restores_binding_and_profile(self):
        with (
            patch.object(
                accounts.runtime_profile_service,
                "ensure_account_profile",
                side_effect=lambda item: (item, {}),
            ),
            patch.object(accounts.AccountService, "_sync_risk_control_capabilities"),
        ):
            service = accounts.AccountService(MemoryStorage([self.item]))
        with (
            patch.object(accounts, "runtime_profile_service", self.profiles),
            patch(
                "services.risk_control_service.risk_control_service.proxy_connection_state",
                return_value={"country": "US"},
            ),
            patch(
                "services.risk_control_service.risk_control_service.invalidate_proxy_connection"
            ),
            patch.object(
                service, "_save_accounts", side_effect=OSError("fixture disk failure")
            ),
        ):
            with self.assertRaises(OSError):
                service.handle_proxy_failure("fixture", self.old, "connection_failed")
        self.assertEqual(service.get_account("fixture")["proxy"], self.old)
        self.assertEqual(self.profiles.get(self.profile["id"]), self.profile)

    def test_profile_save_failure_restores_memory(self):
        with patch.object(self.profiles, "_save_locked", side_effect=OSError):
            with self.assertRaises(OSError):
                self.profiles.set_proxy_failover(self.profile["id"], False)
        self.assertTrue(
            self.profiles.get(self.profile["id"])["proxy_policy"]["failover_enabled"]
        )

    def test_session_get_retries_once_and_uses_replacement(self):
        replacement = {**self.item, "proxy": self.new}
        response = Mock(status_code=200)
        with (
            AccountProxySession(account=self.item, proxy=self.old) as session,
            patch.object(
                Session,
                "request",
                side_effect=[RequestException("connection reset", code=56), response],
            ) as request,
            patch.object(
                accounts.account_service,
                "handle_proxy_failure",
                return_value=replacement,
            ) as failover,
        ):
            self.assertIs(session.get("https://fixture.example.test"), response)
            self.assertEqual(request.call_count, 2)
            failover.assert_called_once()
            self.assertEqual(session.proxies["all"], self.new)

    def test_post_and_stream_are_never_replayed(self):
        for method, options in [("POST", {}), ("GET", {"stream": True})]:
            with (
                self.subTest(method=method),
                AccountProxySession(account=self.item, proxy=self.old) as session,
                patch.object(
                    Session,
                    "request",
                    side_effect=RequestException("connection reset", code=56),
                ) as request,
                patch.object(
                    accounts.account_service,
                    "handle_proxy_failure",
                    return_value={**self.item, "proxy": self.new},
                ),
            ):
                with self.assertRaises(RequestException):
                    session.request(method, "https://fixture.example.test", **options)
                self.assertEqual(request.call_count, 1)
                self.assertEqual(session.bound_account["proxy"], self.new)

    def test_denied_responses_do_not_switch(self):
        for status in [401, 403, 429]:
            with (
                self.subTest(status=status),
                AccountProxySession(account=self.item, proxy=self.old) as session,
                patch.object(Session, "request", return_value=Mock(status_code=status)),
                patch.object(
                    accounts.account_service, "handle_proxy_failure"
                ) as failover,
            ):
                self.assertEqual(
                    session.get("https://fixture.example.test").status_code, status
                )
                failover.assert_not_called()
            with (
                AccountProxySession(account=self.item, proxy=self.old) as session,
                patch.object(
                    Session,
                    "request",
                    side_effect=RequestException(
                        f"CONNECT tunnel failed, response {status}", code=56
                    ),
                ),
                patch.object(
                    accounts.account_service, "handle_proxy_failure"
                ) as failover,
            ):
                with self.assertRaises(RequestException):
                    session.get("https://fixture.example.test")
                failover.assert_not_called()

    def test_407_can_recover_but_repeated_failure_is_bounded(self):
        with (
            AccountProxySession(account=self.item, proxy=self.old) as session,
            patch.object(
                Session, "request", return_value=Mock(status_code=407)
            ) as request,
            patch.object(
                accounts.account_service,
                "handle_proxy_failure",
                return_value={**self.item, "proxy": self.new},
            ),
        ):
            self.assertEqual(
                session.get("https://fixture.example.test").status_code, 407
            )
            self.assertEqual(request.call_count, 2)

    def test_real_http_transport_recovers_through_persisted_account_binding(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        import socket

        class ProxyHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.server.broken:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                payload = b"fixture-proxy-recovered"
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        servers = []
        for broken in [True, False]:
            server = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
            server.broken = broken
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            servers.append(server)
        old, new = [f"http://127.0.0.1:{server.server_port}" for server in servers]
        self.item["proxy"] = old
        self.profile["proxy_policy"]["runtime_proxy"] = old
        self.groups[0]["proxy_url"] = new
        with (
            patch.object(
                accounts.runtime_profile_service,
                "ensure_account_profile",
                side_effect=lambda item: (item, {}),
            ),
            patch.object(accounts.AccountService, "_sync_risk_control_capabilities"),
        ):
            service = accounts.AccountService(MemoryStorage([self.item]))
        with (
            patch.object(accounts, "runtime_profile_service", self.profiles),
            patch.object(accounts, "account_service", service),
            patch(
                "services.risk_control_service.risk_control_service.proxy_connection_state",
                return_value={"country": "US"},
            ),
            patch(
                "services.risk_control_service.risk_control_service.invalidate_proxy_connection"
            ),
            AccountProxySession(
                account=self.item, proxy=old, trust_env=False
            ) as session,
        ):
            response = session.get("http://fixture-target.invalid/health", timeout=3)
        self.assertEqual(response.text, "fixture-proxy-recovered")
        self.assertEqual(service.get_account("fixture")["proxy"], new)
        self.assertEqual(
            self.profiles.get(self.profile["id"])["proxy_policy"]["failover_count"], 1
        )

    def test_monitor_does_not_treat_exit_lookup_failure_as_proxy_failure(self):
        with (
            patch.object(
                accounts.runtime_profile_service,
                "ensure_account_profile",
                side_effect=lambda item: (item, {}),
            ),
            patch.object(accounts.AccountService, "_sync_risk_control_capabilities"),
        ):
            service = accounts.AccountService(MemoryStorage([self.item]))
        from datetime import datetime, timezone

        observation = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "network_ok": False,
            "network_error": "connection_failed",
            "target_status": 200,
            "target_error": "",
        }
        with (
            patch(
                "services.risk_control_service.risk_control_service.proxy_connection_state",
                return_value=observation,
            ),
            patch.object(service, "handle_proxy_failure") as change,
        ):
            self.assertEqual(service.reconcile_proxy_bindings()["switched"], 0)
            change.assert_not_called()
            observation.update(target_status=0, target_error="target_access_denied")
            service.reconcile_proxy_bindings()
            change.assert_not_called()
            observation.update(target_error="connection_failed")
            service.reconcile_proxy_bindings()
            change.assert_called_once()

    def test_policy_api_requires_admin_and_does_not_expose_credentials(self):
        from api import runtime_profiles as module
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(module.create_router())
        client = TestClient(app)
        with (
            patch.object(
                module, "require_admin", side_effect=HTTPException(status_code=401)
            ),
            patch.object(module.account_service, "set_proxy_failover") as update,
        ):
            self.assertEqual(
                client.post(
                    "/api/runtime-profiles/proxy-policy",
                    json={"profile_id": "fixture", "enabled": False},
                ).status_code,
                401,
            )
            update.assert_not_called()
        with (
            patch.object(module, "require_admin"),
            patch.object(
                module.account_service, "set_proxy_failover", return_value=None
            ),
        ):
            self.assertEqual(
                client.post(
                    "/api/runtime-profiles/proxy-policy",
                    json={"profile_id": "fixture", "enabled": False},
                ).status_code,
                404,
            )
        profile = deepcopy(self.profile)
        profile["proxy_policy"]["runtime_proxy"] = (
            "http://fixture-user:fixture-secret@proxy.example.test:3129"
        )
        profile["proxy_policy"]["register_proxy"] = profile["proxy_policy"][
            "runtime_proxy"
        ]
        public = self.profiles.public_profile(profile)
        self.assertNotIn("fixture-user", str(public))
        self.assertNotIn("fixture-secret", str(public))
        self.assertIn("fixture-secret", profile["proxy_policy"]["runtime_proxy"])
