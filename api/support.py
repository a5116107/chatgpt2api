from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from threading import Event, Thread

from fastapi import HTTPException, Request

from services.account_service import account_service
from services.auth_service import auth_service
from services.config import config
from services.proxy_service import test_proxy

BASE_DIR = Path(__file__).resolve().parents[1]
WEB_DIST_DIR = BASE_DIR / "web_dist"


def extract_bearer_token(authorization: str | None) -> str:
    scheme, _, value = str(authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return ""
    return value.strip()


def _legacy_admin_identity(token: str) -> dict[str, object] | None:
    auth_key = str(config.auth_key or "").strip()
    if auth_key and token == auth_key:
        return {"id": "admin", "name": "管理员", "role": "admin"}
    return None


def require_identity(authorization: str | None) -> dict[str, object]:
    token = extract_bearer_token(authorization)
    identity = _legacy_admin_identity(token) or auth_service.authenticate(token)
    if identity is None:
        raise HTTPException(status_code=401, detail={"error": "密钥无效或已失效，请重新登录"})
    return identity


def require_auth_key(authorization: str | None) -> None:
    require_identity(authorization)


def require_admin(authorization: str | None) -> dict[str, object]:
    identity = require_identity(authorization)
    if identity.get("role") != "admin":
        raise HTTPException(status_code=403, detail={"error": "需要管理员权限才能执行这个操作"})
    return identity


def resolve_image_base_url(request: Request) -> str:
    return config.base_url or f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"


def raise_image_quota_error(exc: Exception) -> None:
    message = str(exc)
    if "no available image quota" in message.lower():
        raise HTTPException(status_code=429, detail={"error": "no available image quota"}) from exc
    raise HTTPException(status_code=502, detail={"error": message}) from exc


def sanitize_cpa_pool(pool: dict | None) -> dict | None:
    if not isinstance(pool, dict):
        return None
    return {key: value for key, value in pool.items() if key != "secret_key"}


def sanitize_cpa_pools(pools: list[dict]) -> list[dict]:
    return [sanitized for pool in pools if (sanitized := sanitize_cpa_pool(pool)) is not None]


def sanitize_sub2api_server(server: dict | None) -> dict | None:
    if not isinstance(server, dict):
        return None
    sanitized = {key: value for key, value in server.items() if key not in {"password", "api_key"}}
    sanitized["has_api_key"] = bool(str(server.get("api_key") or "").strip())
    return sanitized


def sanitize_sub2api_servers(servers: list[dict]) -> list[dict]:
    return [sanitized for server in servers if (sanitized := sanitize_sub2api_server(server)) is not None]




def _account_watcher_proxy_status(watcher: dict) -> dict | None:
    url = str(watcher.get("proxy_status_url") or "").strip()
    if not url:
        return None
    timeout = max(1, int(watcher.get("proxy_ready_timeout_secs") or 8))
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        pool = payload.get("pool") if isinstance(payload, dict) else None
        if not isinstance(pool, dict):
            return None
        return {
            "available": int(pool.get("available") or 0),
            "total": int(pool.get("total") or 0),
            "banned": int(pool.get("banned") or 0),
            "below_score": int(pool.get("below_score") or 0),
        }
    except Exception as exc:
        print(f"[account-watcher] proxy status unavailable; skip this round error={exc}")
        return {"available": 0, "total": 0, "banned": 0, "below_score": 0}

def _account_watcher_proxy_ready(watcher: dict) -> dict | None:
    """Proxy readiness for account-watcher.

    Compatible with:
    1) dynamic-proxy pool: use status URL available for batch sizing
    2) single-proxy/glider: if status URL is unavailable, continue when test_proxy is ok
    """
    if not watcher.get("require_proxy_ready"):
        return {"available": max(1, int(watcher.get("max_batch") or 1))}
    timeout = max(1, int(watcher.get("proxy_ready_timeout_secs") or 8))
    result = test_proxy(timeout=float(timeout))
    if not result.get("ok"):
        print(
            "[account-watcher] proxy not ready; skip this round "
            f"status={result.get('status')} error={result.get('error')} "
            f"latency_ms={result.get('latency_ms')}"
        )
        return None
    pool_status = _account_watcher_proxy_status(watcher)
    min_available = max(1, int(watcher.get("min_proxy_available") or 1))
    available = int((pool_status or {}).get("available") or 0)
    # When dynamic-proxy is down/unavailable, keep single-proxy path alive.
    if available <= 0:
        fallback = max(1, int(watcher.get("max_batch") or 1))
        print(
            "[account-watcher] proxy pool status unavailable; continue with single-proxy batch "
            f"fallback_available={fallback} status={pool_status}"
        )
        return {
            "available": fallback,
            "total": fallback,
            "banned": 0,
            "below_score": 0,
            "mode": "single_proxy",
        }
    if available < min_available:
        print(
            "[account-watcher] proxy pool below threshold; continuing with limited batch "
            f"available={available} min={min_available} status={pool_status}"
        )
    return pool_status or {"available": available}

def start_limited_account_watcher(stop_event: Event) -> Thread:
    interval_seconds = config.refresh_account_interval_minute * 60

    def worker() -> None:
        first_round = True
        while not stop_event.is_set():
            try:
                watcher = config.get_account_watcher_settings()
                if not watcher.get("enabled"):
                    stop_event.wait(interval_seconds)
                    first_round = False
                    continue
                if first_round:
                    initial_delay = max(0, int(watcher.get("initial_delay_seconds") or 0))
                    if initial_delay:
                        print(f"[account-watcher] initial delay {initial_delay}s before first check")
                        if stop_event.wait(initial_delay):
                            break
                    first_round = False
                proxy_status = _account_watcher_proxy_ready(watcher)
                if not proxy_status:
                    stop_event.wait(interval_seconds)
                    continue
                limited_tokens = account_service.list_limited_tokens() if watcher.get("check_limited") else []
                normal_tokens = account_service.list_normal_tokens() if watcher.get("check_normal") else []
                expiring_tokens = account_service.list_expiring_access_tokens() if watcher.get("check_expiring") else []
                keepalive_tokens = account_service.list_refresh_token_keepalive_tokens() if watcher.get("keepalive_refresh_tokens") else []
                tokens = list(dict.fromkeys([*limited_tokens, *normal_tokens, *expiring_tokens]))
                max_batch = max(1, int(watcher.get("max_batch") or 64))
                if watcher.get("dynamic_batch_by_proxy"):
                    available = max(1, int((proxy_status or {}).get("available") or 1))
                    max_batch = min(max_batch, available)
                if len(tokens) > max_batch:
                    tokens = tokens[:max_batch]
                expiring_token_set = set(expiring_tokens)
                keepalive_tokens = [token for token in keepalive_tokens if token not in expiring_token_set]
                if len(keepalive_tokens) > max_batch:
                    keepalive_tokens = keepalive_tokens[:max_batch]
                if tokens:
                    print(
                        "[account-watcher] checking "
                        f"{len(limited_tokens)} limited accounts, "
                        f"{len(normal_tokens)} normal accounts, "
                        f"{len(expiring_tokens)} expiring access tokens; "
                        f"batch={len(tokens)}/{max_batch}"
                    )
                    account_service.refresh_accounts(tokens)
                if keepalive_tokens:
                    print(f"[account-watcher] keepalive {len(keepalive_tokens)} refresh tokens")
                    result = account_service.keepalive_refresh_tokens(keepalive_tokens)
                    if result.get("errors"):
                        print(f"[account-watcher] keepalive errors: {result['errors']}")
            except Exception as exc:
                print(f"[account-watcher] fail {exc}")
            stop_event.wait(interval_seconds)

    thread = Thread(target=worker, name="account-watcher", daemon=True)
    thread.start()
    return thread


def start_image_account_probe(stop_event: Event) -> Thread:
    """Continuously refresh image-account readiness outside user requests."""
    def worker() -> None:
        if stop_event.wait(15.0):
            return
        while not stop_event.is_set():
            try:
                if config.image_account_probe_enabled:
                    result = account_service.probe_image_candidates(config.image_account_probe_batch_size)
                    print(
                        "[image-account-probe] "
                        f"checked={result['checked']} healthy={result['healthy']} "
                        f"quarantined={result.get('quarantined', 0)} failures={len(result['failures'])}"
                    )
            except Exception as exc:
                print(f"[image-account-probe] fail {exc}")
            stop_event.wait(config.image_account_probe_interval_secs)

    thread = Thread(target=worker, name="image-account-probe", daemon=True)
    thread.start()
    return thread


def resolve_web_asset(requested_path: str) -> Path | None:
    if not WEB_DIST_DIR.exists():
        return None
    clean_path = requested_path.strip("/")
    base_dir = WEB_DIST_DIR.resolve()
    candidates = [base_dir / "index.html"] if not clean_path else [
        base_dir / Path(clean_path),
        base_dir / clean_path / "index.html",
        base_dir / f"{clean_path}.html",
    ]
    for candidate in candidates:
        try:
            candidate.resolve().relative_to(base_dir)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None
