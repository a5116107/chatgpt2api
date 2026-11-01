from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.proxy_platform import NoRedirect, ProxyPlatformClient
from services import proxy_service
from services.config import ConfigStore


class ProxyPlatformTests(unittest.TestCase):
    def client(self, payload=None):
        settings = {"platform_url": "https://platform.example.test", "proxy_url": "http://project-one-country-US-sid-session-one-ttl-1:fixture-secret@proxy.example.test:8080"}
        store = Mock()
        store.get_proxy_runtime_settings.side_effect = lambda: settings
        opener = Mock()
        if payload is None:
            payload = {"schema_version": 1, "source": "proxy-control", "status": "ready", "capacity": {"max_concurrency": 4}, "nodes": [], "valid_until": (datetime.now(timezone.utc) + timedelta(seconds=15)).isoformat()}
        opener.open.side_effect = lambda *args, **kwargs: io.BytesIO(json.dumps(payload).encode())
        return ProxyPlatformClient(store, opener), opener, settings

    def test_project_credential_stays_in_header_and_response_is_scoped(self):
        client, opener, _ = self.client()
        result = client.status()
        self.assertEqual(result["status"], "ready")
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, "https://platform.example.test/api/proxy/project/status")
        self.assertTrue(req.headers["Authorization"].startswith("Basic "))
        self.assertEqual(req.headers["User-agent"], "chatgpt2api-proxy-platform/1.0")
        self.assertEqual(req.headers["Accept"], "application/json")
        self.assertNotIn("fixture-secret", json.dumps(result))
        client.status()
        self.assertEqual(opener.open.call_count, 1)

    def test_failure_never_preserves_old_healthy_capacity(self):
        client, opener, _ = self.client()
        self.assertEqual(client.status()["status"], "ready")
        client._expires = 0
        opener.open.side_effect = HTTPError("fixture", 403, "denied", {}, None)
        result = client.status()
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("capacity", result)

    def test_plaintext_external_origin_redirect_and_stale_contract_rejected(self):
        client, opener, settings = self.client()
        settings["platform_url"] = "http://platform.example.test"
        self.assertEqual(client.status()["status"], "unavailable")
        opener.open.assert_not_called()
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test"))
        client, _, _ = self.client({"schema_version": 1, "source": "proxy-control", "valid_until": "2000-01-01T00:00:00Z"})
        self.assertEqual(client.status()["status"], "unavailable")

    def test_denial_is_scoped_without_raw_details_and_never_switches_session(self):
        client, opener, settings = self.client({"accepted": True})
        self.assertTrue(client.report_denial(settings["proxy_url"], status_code=429, detail="secret-do-not-send"))
        req = opener.open.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual(payload["session_id"], "session-one")
        self.assertEqual(payload["code"], "target_rate_limited")
        self.assertNotIn("secret-do-not-send", req.data.decode())
        self.assertNotIn("proxy", payload)

    def test_unconfigured_client_does_not_connect(self):
        client, opener, settings = self.client()
        settings["platform_url"] = ""
        self.assertEqual(client.status()["status"], "not_configured")
        self.assertFalse(client.report_denial(settings["proxy_url"], status_code=403))
        opener.open.assert_not_called()

    def test_feedback_preserves_scenario_stage_and_rejects_invalid_inputs(self):
        client, opener, settings = self.client({"accepted": True})
        self.assertTrue(client.report_denial(settings["proxy_url"], status_code=401, scenario="image", stage="download"))
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual((payload["scenario"], payload["stage"], payload["code"]), ("image", "download", "account_invalid"))
        opener.open.reset_mock()
        self.assertFalse(client.report_denial(settings["proxy_url"], status_code="invalid"))
        self.assertFalse(client.report_denial(settings["proxy_url"], status_code=403, scenario="unknown"))
        opener.open.assert_not_called()


class ProxyProbeTests(unittest.TestCase):
    def test_target_denials_and_redirects_are_not_healthy(self):
        for status in (200, 302, 401, 403, 407, 429, 502):
            with self.subTest(status=status), patch.object(proxy_service, "Session") as factory:
                factory.return_value.get.return_value = SimpleNamespace(status_code=status)
                result = proxy_service.test_proxy("http://fixture.example.test:8080")
                self.assertEqual(result["ok"], status == 200)
                self.assertEqual(result["scope"], "project_target")
                self.assertEqual(result["transport_ok"], status != 407)
                factory.return_value.close.assert_called_once()

    def test_public_config_masks_credentials_and_preserves_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = "http://fixture-user:fixture-password@proxy.example.test:8080"
            path.write_text(json.dumps({"auth-key": "fixture-local-test-only", "proxy": original, "proxy_runtime": {"proxy_url": original}}))
            store = ConfigStore(path)
            public = store.get_public_proxy_runtime_settings()
            self.assertNotIn("fixture-user", json.dumps(public))
            self.assertNotIn("fixture-password", json.dumps(public))
            self.assertNotIn("fixture-password", json.dumps(store.get()))
            store.update({"proxy": store.get()["proxy"]})
            self.assertEqual(store.get_proxy_settings(), original)
            with patch.object(proxy_service, "config", store), patch.object(proxy_service, "Session") as factory:
                factory.return_value.get.return_value = SimpleNamespace(status_code=200)
                self.assertTrue(proxy_service.test_proxy(public["proxy_url"])["ok"])
                self.assertIn(original, str(factory.call_args))
                factory.reset_mock()
                result = proxy_service.test_proxy(public["proxy_url"].replace("proxy.example", "unknown.example"))
                self.assertFalse(result["ok"])
                factory.assert_not_called()
            store.update({"proxy_runtime": public})
            self.assertEqual(store.get_proxy_runtime_settings()["proxy_url"], original)
            public["proxy_url"] = public["proxy_url"].replace("proxy.example", "other.example")
            with self.assertRaises(ValueError):
                store.update({"proxy_runtime": public})


class ProxyAPITests(unittest.TestCase):
    def test_activation_batch_accepts_2000_and_rejects_2001(self):
        with patch.object(self.module, "require_admin"), patch.object(self.module.risk_control_service, "activate_proxies", return_value={"updated": 2000, "enabled": True}) as activate:
            self.assertEqual(self.client.post("/api/proxies/activate", json={"ids": [f"fixture-{i}" for i in range(2000)]}).status_code, 200)
            activate.assert_called_once()
            activate.reset_mock()
            self.assertEqual(self.client.post("/api/proxies/activate", json={"ids": [f"fixture-{i}" for i in range(2001)]}).status_code, 422)
            activate.assert_not_called()

    def test_bulk_proxy_validation_does_not_echo_credentials(self):
        from api.errors import install_exception_handlers
        install_exception_handlers(self.client.app)
        secret = "fixture-private-credential"
        with patch.object(self.module, "require_admin"):
            result = self.client.post("/api/proxies/import", json={"text": secret * (self.module.MAX_PROXY_IMPORT_CHARS // len(secret) + 1)})
        self.assertEqual(result.status_code, 422)
        self.assertNotIn(secret, result.text)
        self.assertNotIn('"input"', result.text)

    def setUp(self):
        from api import risk
        self.module = risk
        app = FastAPI()
        app.include_router(risk.create_router())
        self.client = TestClient(app)

    def test_event_uses_stable_id_and_missing_identity_is_400(self):
        with patch.object(self.module, "require_admin"), patch.object(self.module.risk_control_service, "report_proxy_event", return_value={"id": "px-test"}) as event:
            self.assertEqual(self.client.post("/api/proxies/events", json={}).status_code, 400)
            self.assertEqual(self.client.post("/api/proxies/events", json={"proxy_id": "px-test", "code": "success"}).status_code, 200)
            event.assert_called_once_with("", "success", None, proxy_id="px-test")

    def test_event_unknown_id_is_404(self):
        with patch.object(self.module, "require_admin"), patch.object(self.module.risk_control_service, "report_proxy_event", side_effect=KeyError()):
            self.assertEqual(self.client.post("/api/proxies/events", json={"proxy_id": "missing"}).status_code, 404)

    def test_invalid_masked_registration_update_is_400_without_starting(self):
        service = Mock()
        service.update.side_effect = ValueError("re-enter proxy credentials")
        path = Path(__file__).resolve().parents[1] / "api/register.py"
        spec = importlib.util.spec_from_file_location("register_api_fixture", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"services.register_service": SimpleNamespace(register_service=service)}):
            spec.loader.exec_module(module)
        app = FastAPI()
        app.include_router(module.create_router())
        with patch.object(module, "require_admin"), patch.object(module, "require_feature"):
            result = TestClient(app).post("/api/register", json={"proxy": "http://***:***@changed.test"})
        self.assertEqual(result.status_code, 400)
        service.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
