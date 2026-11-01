"""Thin, project-scoped proxy control client; never selects or expands exits."""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib import error, request
from urllib.parse import unquote, urlparse

from services.config import config


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ProxyPlatformClient:
    def __init__(self, config_store=None, opener=None):
        self.config = config_store or config
        self.opener = opener or request.build_opener(request.ProxyHandler({}), NoRedirect())
        self._lock = threading.Lock()
        self._cached = None
        self._cache_key = None
        self._expires = 0.0

    def _connection(self, proxy_url=""):
        runtime = self.config.get_proxy_runtime_settings()
        origin = str(runtime.get("platform_url") or "").strip().rstrip("/")
        if not origin:
            return None
        parsed = urlparse(origin)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("invalid_platform_origin")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError("platform_requires_https")
        proxy = urlparse(str(proxy_url or runtime.get("proxy_url") or ""))
        username, password = unquote(proxy.username or ""), unquote(proxy.password or "")
        if not username or not password or "***" in username or "***" in password:
            raise ValueError("project_credential_missing")
        return origin, username, password

    def _call(self, connection, path, payload=None, timeout=5.0):
        origin, username, password = connection
        authorization = base64.b64encode((username + ":" + password).encode()).decode()
        req = request.Request(origin + path, data=json.dumps(payload).encode() if payload is not None else None,
                              headers={"Authorization": "Basic " + authorization, "Content-Type": "application/json",
                                       "Accept": "application/json", "User-Agent": "chatgpt2api-proxy-platform/1.0"})
        with self.opener.open(req, timeout=timeout) as response:
            raw = response.read((2 << 20) + 1)
            if len(raw) > 2 << 20:
                raise ValueError("platform_response_too_large")
            return json.loads(raw)

    def status(self):
        try:
            connection = self._connection()
            if connection is None:
                return {"status": "not_configured", "source": "proxy-control", "nodes": []}
            with self._lock:
                if self._cache_key == connection and self._cached is not None and time.monotonic() < self._expires:
                    return self._cached
                result = self._call(connection, "/api/proxy/project/status")
                if not isinstance(result, dict) or result.get("schema_version") != 1 or result.get("source") != "proxy-control":
                    raise ValueError("invalid_platform_contract")
                expires = datetime.fromisoformat(str(result.get("valid_until") or "").replace("Z", "+00:00"))
                if expires <= datetime.now(timezone.utc) or expires > datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=60):
                    raise ValueError("stale_platform_response")
                # Whitelist the public contract instead of relaying arbitrary remote fields.
                fields = {"schema_version", "source", "project", "status", "revision", "runtime_revision", "observed_at", "received_at", "valid_until", "refresh_after_seconds", "policy", "capacity", "nodes"}
                result = {key: value for key, value in result.items() if key in fields}
                capacity = result.get("capacity")
                if isinstance(capacity, dict):
                    allowed = {"unit", "max_concurrency", "observed_active_connections", "observed_remaining_connections", "rate_limit_per_minute", "rate_unit", "active_sessions", "eligible_nodes", "runtime_routes", "observed_unique_exits", "live_gateways", "authoritative"}
                    result["capacity"] = {key: value for key, value in capacity.items() if key in allowed}
                policy = result.get("policy")
                if isinstance(policy, dict):
                    allowed = {"id", "selection_mode", "ttl_mode", "ttl_seconds", "countries", "requested_country"}
                    result["policy"] = {key: value for key, value in policy.items() if key in allowed}
                nodes = result.get("nodes")
                allowed = {"id", "country", "country_source", "geo_verified", "exit_country", "exit_asn", "geo_observed_at", "geo_valid_until", "health", "observed_at", "exit_ip", "latency_ms", "routes", "routable"}
                result["nodes"] = [{key: value for key, value in node.items() if key in allowed} for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []
                self._cached, self._cache_key = result, connection
                self._expires = time.monotonic() + min(10, max(0, (expires-datetime.now(timezone.utc)).total_seconds()))
                return result
        except (OSError, ValueError, TypeError, error.HTTPError) as failure:
            if isinstance(failure, error.HTTPError):
                failure.close()
            # Fail closed in the observation: never reuse stale remaining capacity.
            return {"status": "unavailable", "source": "proxy-control", "nodes": [], "error_code": "platform_status_unavailable"}

    def report_denial(self, proxy_url, *, target="", status_code=0, reason="", detail=None, timeout=2.0, scenario="api", stage=""):
        try:
            code = int(status_code or 0)
            text = str(reason).lower()
            if code not in {401, 403, 407, 429} and "timeout" not in text and "connect tunnel failed" not in text:
                return False
            if scenario not in {"api", "sse", "image", "video", "browser", "download"} or stage not in {"", "connect", "request", "stream", "submit", "poll", "download"}:
                return False
            connection = self._connection(proxy_url)
            if connection is None:
                return False
            username = connection[1]
            match = re.fullmatch(r".+-sid-(.+)-ttl-\d+", username)
            session = match.group(1) if match else next((item[4:] for item in username.split(";") if item.startswith("sid=")), "")
            if not session:
                return False
            outcome = "proxy_auth_required" if code == 407 else "target_rate_limited" if code == 429 else "account_invalid" if code == 401 else "network_timeout" if "timeout" in text else "proxy_policy_denied" if "connect tunnel failed" in text else "target_access_denied"
            payload = {"event_id": "app-" + uuid.uuid4().hex, "session_id": session,
                       "scenario": scenario, "stage": stage or ("connect" if outcome.startswith("proxy_") else "request"),
                       "code": outcome, "http_status": code, "observed_at": datetime.now(timezone.utc).isoformat()}
            result = self._call(connection, "/api/proxy/project/feedback", payload, timeout=min(max(float(timeout), 0.1), 3.0))
            return bool(result.get("accepted"))
        except (OSError, ValueError, TypeError, error.HTTPError) as failure:
            if isinstance(failure, error.HTTPError):
                failure.close()
            return False


proxy_platform = ProxyPlatformClient()


def report_proxy_denial(proxy_url: str, **kwargs) -> bool:
    return proxy_platform.report_denial(proxy_url, **kwargs)
