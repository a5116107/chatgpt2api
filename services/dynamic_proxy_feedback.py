from __future__ import annotations

import json
import os
import urllib.request
from typing import Any
from urllib.parse import urlparse

try:
    from curl_cffi import requests as curl_requests
except Exception:
    curl_requests = None  # type: ignore[assignment]

_FEEDBACK_PATH = "/_dynamic_proxy/report"
_SOCKS_TO_HTTP_PORT = {17283: 17285, 17284: 17286}
_DYNAMIC_PROXY_PORTS = {17283, 17284, 17285, 17286}


def _parse_proxy_url(proxy_url: str):
    proxy_url = str(proxy_url or "").strip()
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url if "://" in proxy_url else f"http://{proxy_url}")
    host = parsed.hostname or ""
    port = parsed.port
    if not host:
        return None
    return parsed, host, port


def _is_dynamic_proxy_addr(host: str, port: int | None) -> bool:
    host = str(host or "").strip().lower()
    return bool(host) and ("dynamic-proxy" in host or port in _DYNAMIC_PROXY_PORTS)


def _feedback_endpoint(proxy_url: str) -> str:
    configured = str(os.getenv("DYNAMIC_PROXY_FEEDBACK_URL") or "").strip()
    if configured:
        return configured
    parsed = _parse_proxy_url(proxy_url)
    if not parsed:
        return ""
    _, host, port = parsed
    is_dynamic_proxy = _is_dynamic_proxy_addr(host, port)
    if not is_dynamic_proxy:
        return ""
    feedback_port = _SOCKS_TO_HTTP_PORT.get(port, port or 17285)
    return f"http://{host}:{feedback_port}{_FEEDBACK_PATH}"


def _normalized_proxy_addr(proxy_url: str) -> str:
    parsed = _parse_proxy_url(proxy_url)
    if not parsed:
        return ""
    _, host, port = parsed
    if _is_dynamic_proxy_addr(host, port):
        return ""
    return f"{host}:{port}" if port else host


def _is_reportable_denial(status_code: int, reason: str, detail: Any = None) -> bool:
    text = f"{reason} {detail or ''}".lower()
    return (
        int(status_code or 0) == 403
        or int(status_code or 0) == 429
        or "rate limit" in text
        or "too many requests" in text
        or "unsupported_country_region_territory" in text
        or "country, region, or territory not supported" in text
        or "not supported" in text
    )


def report_dynamic_proxy_denial(
    proxy_url: str,
    *,
    target: str = "auth.openai.com:443",
    status_code: int = 0,
    reason: str = "",
    detail: Any = None,
    timeout: float = 2.0,
) -> bool:
    if not _is_reportable_denial(status_code, reason, detail):
        return False
    endpoint = _feedback_endpoint(proxy_url)
    if not endpoint:
        return False
    payload = {
        "proxy": _normalized_proxy_addr(proxy_url),
        "target": target,
        "status_code": int(status_code or 0),
        "reason": str(reason or f"upstream_http_{status_code}"),
        "detail": detail,
    }
    try:
        if curl_requests is not None:
            resp = curl_requests.post(endpoint, json=payload, timeout=timeout)
            status = int(getattr(resp, "status_code", 0) or 0)
            body = str(getattr(resp, "text", "") or "")[:200]
        else:
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - internal Docker endpoint
                status = int(getattr(resp, "status", 0) or 0)
                body = resp.read(200).decode("utf-8", errors="replace")
        ok = 200 <= status < 300
        if not ok:
            print(f"[dynamic-proxy-feedback] rejected status={status} target={target} reason={payload['reason']} body={body}")
        return ok
    except Exception as exc:
        print(f"[dynamic-proxy-feedback] failed target={target} reason={payload['reason']} error={exc}")
        return False
