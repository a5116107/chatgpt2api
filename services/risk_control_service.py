from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from services.account_service import account_service
from services.config import DATA_DIR, config
from services.runtime_profile_service import runtime_profile_service
from services.proxy_service import normalize_proxy_url

RISK_DIR = DATA_DIR / "risk_control"
CAPABILITIES_FILE = RISK_DIR / "account_capabilities.json"
RISK_EVENTS_FILE = RISK_DIR / "risk_events.json"
PROXIES_FILE = RISK_DIR / "proxy_nodes.json"
TASKS_FILE = RISK_DIR / "task_center.json"
SCHEMA_VERSION = 1

TERMINAL_TASK_STATES = {"success", "succeeded", "failed", "error", "cancelled", "stale", "idle"}
TASK_STATUS_ALIASES = {
    "success": "succeeded",
    "done": "succeeded",
    "error": "failed",
}
CAPABILITY_NAMES = (
    "chat", "responses", "raw_conversation", "search",
    "image", "image_edit", "image_variation",
    "file", "audio_tts", "audio_stt", "audio_translation", "video",
)

EVENT_CODE_POLICIES = {
    "token_invalid": {"scope": "account", "retryable": False, "cooldown_secs": 0},
    "quota_exhausted": {"scope": "account", "retryable": False, "cooldown_secs": 0},
    "account_rate_limited": {"scope": "account", "retryable": True, "cooldown_secs": 600},
    "proxy_or_challenge_blocked": {"scope": "proxy", "retryable": True, "cooldown_secs": 900},
    "network_timeout": {"scope": "proxy", "retryable": True, "cooldown_secs": 180},
    "policy_rejected": {"scope": "request", "retryable": False, "cooldown_secs": 0},
}
PROXY_SCORING_EVENT_CODES = {"proxy_or_challenge_blocked", "network_timeout"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _safe_key(value: str) -> str:
    return str(value or "").strip()


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _atomic_save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{uuid.uuid4().hex}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _runtime_aggregate_proxy_urls() -> set[str]:
    runtime = config.get_proxy_runtime_settings()
    candidates = {
        str(runtime.get("proxy_url") or ""),
        str(runtime.get("resource_proxy_url") or ""),
    }
    return {normalize_proxy_url(item) for item in candidates if str(item or "").strip()}


def _task_tombstone_id(task_id: str) -> str:
    return f"deleted:{_sha(task_id, 24)}"


def _load_task_tombstones(tasks: dict[str, dict[str, Any]]) -> set[str]:
    return {
        str(item.get("deleted_task_id") or "")
        for item in tasks.values()
        if item.get("type") == "tombstone" and item.get("deleted_task_id")
    }


def account_key(account: dict[str, Any]) -> str:
    for key in ("key", "id", "email", "username", "account"):
        value = account.get(key)
        if value:
            return str(value)
    token = str(account.get("access_token") or account.get("token") or "")
    if token:
        return f"token:{_sha(token, 10)}"
    return f"account:{_sha(json.dumps(account, sort_keys=True, ensure_ascii=False), 10)}"


def _account_plan(account: dict[str, Any]) -> str:
    """Normalize plan for capability derivation.

    IMPORTANT: free automation accounts often carry positive image quota.
    Never promote free/unknown to plus solely because quota>0 — that causes
    token_invalid events to fully wipe image capability and empty the pool.
    """
    raw = str(
        account.get("plan")
        or account.get("account_plan")
        or account.get("type")
        or account.get("account_type")
        or account.get("subscription_plan")
        or ""
    ).strip().lower().replace("-", "_").replace(" ", "_")
    compact = raw.replace("_", "")
    if raw in {"free", "plus", "team", "enterprise", "pro", "prolite", "business"} or compact in {
        "free", "plus", "team", "enterprise", "pro", "prolite", "business"
    }:
        if compact in {"business"}:
            return "team"
        if compact in {"prolite"}:
            return "pro"
        return "free" if compact == "free" else (compact if compact in {"plus", "team", "enterprise", "pro"} else raw)
    # chatgptfreeplan / freeplan style
    if "free" in raw:
        return "free"
    if any(k in raw for k in ("plus", "team", "enterprise", "pro")):
        for k in ("enterprise", "team", "plus", "pro"):
            if k in raw:
                return k
    # Unknown plan: keep unknown. Do NOT upgrade to plus on quota alone.
    return "unknown"


def _default_capability(account: dict[str, Any]) -> dict[str, Any]:
    status = str(account.get("status") or "").strip()
    normal = status in {"正常", "active", "ok", "healthy", ""}
    plan = _account_plan(account)
    quota = int(account.get("quota") or 0) if str(account.get("quota") or "0").isdigit() else 0
    # free/unknown with status=正常 may temporarily carry quota=0 after register userinfo timeout.
    # Exhausted free accounts are expected to move to 限流 (normal=False).
    soft_abnormal = status == "异常" and plan in {"free", "unknown"} and quota > 0
    usable = normal or soft_abnormal
    image_ok = usable and (
        quota > 0
        or plan in {"plus", "team", "enterprise", "pro", "free", "unknown"}
    )
    return {
        "account_key": account_key(account),
        "runtime_profile_id": account.get("runtime_profile_id"),
        "plan": plan,
        "chat": usable,
        "responses": usable,
        "raw_conversation": usable,
        "search": usable,
        "image": image_ok,
        "image_edit": image_ok,
        "image_variation": image_ok,
        "file": usable,
        "audio_tts": image_ok,
        "audio_stt": usable,
        "audio_translation": usable,
        "video": False,
        "quota": quota,
        "source": "derived",
        "last_probe_at": _now(),
        "updated_at": _now(),
    }


def classify_error(message: str, status_code: int | None = None) -> dict[str, Any]:
    text = str(message or "").lower()
    code = "upstream_error"
    scope = "upstream"
    retryable = True
    cooldown_secs = 60
    if status_code == 429 or "rate limit" in text or "too many requests" in text or "429" in text:
        code, scope, cooldown_secs = "account_rate_limited", "account", 600
    elif status_code == 403 or "cloudflare" in text or "cf_clearance" in text or "captcha" in text or "forbidden" in text or "403" in text:
        code, scope, cooldown_secs = "proxy_or_challenge_blocked", "proxy", 900
    elif "invalid token" in text or "token invalidated" in text or "unauthorized" in text or status_code == 401:
        code, scope, retryable, cooldown_secs = "token_invalid", "account", False, 0
    elif "quota" in text or "insufficient" in text:
        code, scope, retryable, cooldown_secs = "quota_exhausted", "account", False, 0
    elif "timeout" in text or "timed out" in text or "connection" in text:
        code, scope, cooldown_secs = "network_timeout", "proxy", 180
    elif "policy" in text or "safety" in text:
        code, scope, retryable, cooldown_secs = "policy_rejected", "request", False, 0
    return {"code": code, "scope": scope, "retryable": retryable, "cooldown_secs": cooldown_secs}


def normalize_task_status(status: object) -> str:
    value = str(status or "queued").strip().lower()
    return TASK_STATUS_ALIASES.get(value, value or "queued")


def normalize_task_error(error: object = "", status_code: int | None = None) -> dict[str, Any]:
    message = str(error or "").strip()
    if not message:
        return {"error": "", "error_code": "", "retryable": True, "error_scope": ""}
    classified = classify_error(message, status_code=status_code)
    return {
        "error": message,
        "error_code": classified["code"],
        "retryable": bool(classified["retryable"]),
        "error_scope": classified["scope"],
        "cooldown_secs": int(classified.get("cooldown_secs") or 0),
    }


class RiskControlService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._capability_cache: dict[str, dict[str, Any]] | None = None
        self._capability_cache_mtime_ns: int | None = None
        RISK_DIR.mkdir(parents=True, exist_ok=True)

    def _load_items(self, path: Path) -> list[dict[str, Any]]:
        data = _load_json(path, {"schema_version": SCHEMA_VERSION, "items": []})
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        return [x for x in data.get("items", []) if isinstance(x, dict)] if isinstance(data, dict) else []

    def _save_items(self, path: Path, items: list[dict[str, Any]]) -> None:
        _atomic_save(path, {"schema_version": SCHEMA_VERSION, "updated_at": _now(), "items": items})
        if path == CAPABILITIES_FILE:
            with self._lock:
                self._capability_cache = {str(item.get("account_key")): item for item in items if item.get("account_key")}
                try:
                    self._capability_cache_mtime_ns = path.stat().st_mtime_ns
                except FileNotFoundError:
                    self._capability_cache_mtime_ns = None

    def _load_capability_index(self) -> dict[str, dict[str, Any]]:
        try:
            mtime_ns = CAPABILITIES_FILE.stat().st_mtime_ns
        except FileNotFoundError:
            with self._lock:
                self._capability_cache = {}
                self._capability_cache_mtime_ns = None
            return {}

        with self._lock:
            if self._capability_cache is not None and self._capability_cache_mtime_ns == mtime_ns:
                return self._capability_cache
            items = self._load_items(CAPABILITIES_FILE)
            cache = {str(item.get("account_key")): item for item in items if item.get("account_key")}
            self._capability_cache = cache
            self._capability_cache_mtime_ns = mtime_ns
            return cache

    def sync_account_capabilities(self) -> dict[str, Any]:
        with self._lock:
            accounts = account_service.list_accounts()
            existing = {str(item.get("account_key")): item for item in self._load_items(CAPABILITIES_FILE) if item.get("account_key")}
            items: list[dict[str, Any]] = []
            created = updated = removed = 0
            seen: set[str] = set()
            for account in accounts:
                key = account_key(account)
                seen.add(key)
                derived = _default_capability(account)
                old = existing.get(key)
                old_source = str((old or {}).get("source") or "").strip().lower()
                if old and old_source and old_source != "derived":
                    merged = {**derived, **{k: old[k] for k in old if k not in {"runtime_profile_id", "quota", "updated_at"}}}
                    merged["runtime_profile_id"] = derived["runtime_profile_id"]
                    merged["quota"] = derived["quota"]
                    merged["updated_at"] = _now()
                    items.append(merged)
                    updated += 1
                else:
                    items.append(derived)
                    if old:
                        updated += 1
                    else:
                        created += 1
            removed = len([k for k in existing if k not in seen])
            self._save_items(CAPABILITIES_FILE, items)
            return {"created": created, "updated": updated, "removed": removed, "total": len(items)}

    def list_capabilities(self) -> list[dict[str, Any]]:
        self.sync_account_capabilities()
        return self._load_items(CAPABILITIES_FILE)

    def get_capability(self, key: str) -> dict[str, Any] | None:
        for item in self.list_capabilities():
            if item.get("account_key") == key:
                return item
        return None

    def update_capability(self, key: str, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            items = self.list_capabilities()
            for item in items:
                if item.get("account_key") == key:
                    allowed = {"chat", "responses", "raw_conversation", "search", "image", "image_edit", "image_variation", "file", "audio_tts", "audio_stt", "audio_translation", "video", "plan", "source"}
                    for k, v in updates.items():
                        if k in allowed:
                            item[k] = bool(v) if k not in {"plan", "source"} else str(v)
                    item["updated_at"] = _now()
                    item["source"] = str(updates.get("source") or item.get("source") or "manual")
                    self._save_items(CAPABILITIES_FILE, items)
                    return item
        raise KeyError(key)

    def capability_allows(self, account: dict[str, Any], capability: str) -> bool:
        """Return whether the given account may use the named capability.

        Reads capability snapshot when present, but self-heals free/unknown soft
        revoke cases: positive image quota must keep image/* even if a stale
        risk_event previously wiped the snapshot (plan mislabeled as plus).
        """
        name = str(capability or "").strip()
        if name not in CAPABILITY_NAMES:
            return True
        try:
            key = account_key(account)
            capability_item = self._load_capability_index().get(key)
            if not isinstance(capability_item, dict):
                capability_item = _default_capability(account)
            allowed = bool(capability_item.get(name))
            if allowed:
                return True
            # Self-heal soft free image after stale full wipe snapshots.
            plan = _account_plan(account)
            status = str(account.get("status") or "").strip()
            quota = int(account.get("quota") or 0) if str(account.get("quota") or "0").isdigit() else 0
            soft_free = plan in {"free", "unknown"} and status in {"正常", "异常", "active", "ok", "healthy", ""} and quota > 0
            if soft_free and name in {"image", "image_edit", "image_variation", "audio_tts"}:
                return True
            # Chat stays strict: soft/abnormal free text tokens are selected only via
            # account_service.get_text_access_token (status=正常 gate).
            return False
        except Exception:
            return bool(_default_capability(account).get(name))

    def _apply_event_to_capabilities_locked(self, event: dict[str, Any]) -> None:
        key = str(event.get("account_key") or "").strip()
        if not key:
            return
        code = str(event.get("code") or "")
        if code not in {"token_invalid", "quota_exhausted", "account_rate_limited"}:
            return
        items = self._load_items(CAPABILITIES_FILE)
        changed = False
        for item in items:
            if item.get("account_key") != key:
                continue
            if code == "token_invalid":
                # Soft-disable chat first. Free/unknown accounts with remaining image quota
                # keep image/* — including snapshots historically mislabeled as plus.
                plan = str(item.get("plan") or "").strip().lower()
                quota = int(item.get("quota") or 0) if str(item.get("quota") or "0").isdigit() else 0
                msg = str(event.get("message") or "").lower()
                soft_free_like = (
                    plan in {"free", "unknown", ""}
                    or (quota > 0 and plan not in {"team", "enterprise", "pro"} and ("token_revoked" in msg or "invalidated oauth" in msg or "text_stream" in msg or "image_stream" in msg))
                )
                if soft_free_like and quota > 0:
                    for name in ("chat", "responses", "raw_conversation", "search", "file", "audio_stt", "audio_translation"):
                        item[name] = False
                    # ensure image remains usable
                    for name in ("image", "image_edit", "image_variation", "audio_tts"):
                        item[name] = True
                    item["plan"] = "free" if plan in {"", "unknown", "plus"} and quota > 0 else (plan or "free")
                    item["risk_status"] = "soft_token_invalid"
                else:
                    for name in CAPABILITY_NAMES:
                        item[name] = False
                    item["risk_status"] = "invalid_token"
            elif code == "quota_exhausted":
                for name in ("image", "image_edit", "image_variation", "audio_tts"):
                    item[name] = False
                item["risk_status"] = "quota_exhausted"
            elif code == "account_rate_limited":
                item["risk_status"] = "cooldown"
                item["cooldown_until"] = int(time.time()) + int(event.get("cooldown_secs") or 600)
            item["source"] = "risk_event"
            item["last_risk_event_id"] = event.get("id")
            item["updated_at"] = _now()
            changed = True
            break
        if changed:
            self._save_items(CAPABILITIES_FILE, items)

    def record_event(self, *, code: str | None = None, message: str = "", scope: str | None = None, account: dict[str, Any] | str | None = None, proxy: str = "", profile_id: str = "", task_id: str = "", status_code: int | None = None, raw: dict[str, Any] | None = None) -> dict[str, Any]:
        classified = classify_error(message, status_code)
        if code:
            policy = EVENT_CODE_POLICIES.get(code)
            if policy:
                classified = {**classified, **policy}
        event = {
            "id": f"re_{uuid.uuid4().hex[:18]}",
            "time": _now(),
            "code": code or classified["code"],
            "message": str(message or ""),
            "scope": scope or classified["scope"],
            "retryable": classified["retryable"],
            "cooldown_secs": classified["cooldown_secs"],
            "status_code": status_code,
            "account_key": account_key(account) if isinstance(account, dict) else str(account or ""),
            "proxy_id": self.proxy_id(proxy) if proxy else "",
            "proxy": self._redact_proxy(proxy),
            "runtime_profile_id": profile_id,
            "task_id": task_id,
            "raw": raw or {},
        }
        with self._lock:
            items = self._load_items(RISK_EVENTS_FILE)
            items.append(event)
            items = items[-5000:]
            self._save_items(RISK_EVENTS_FILE, items)
            self._apply_event_to_capabilities_locked(event)
            if proxy and (event["code"] in PROXY_SCORING_EVENT_CODES or int(status_code or 0) in {403, 429}):
                self.report_proxy_event(proxy, event["code"], status_code=status_code)
        return event

    def upsert_task(
        self,
        task_id: str,
        *,
        type_: str,
        status: str = "queued",
        progress: object = None,
        source: str = "runtime",
        owner_id: str = "",
        error: object = "",
        status_code: int | None = None,
        **updates: Any,
    ) -> dict[str, Any]:
        task_id = str(task_id or "").strip()
        if not task_id:
            raise ValueError("task_id is required")
        now = _now()
        with self._lock:
            items = self._load_items(TASKS_FILE)
            index = {str(item.get("id")): dict(item) for item in items if item.get("id")}
            current = index.get(task_id, {})
            error_fields = normalize_task_error(error, status_code=status_code)
            item = {
                **current,
                "id": task_id,
                "type": str(type_ or current.get("type") or "generic"),
                "status": normalize_task_status(status or current.get("status")),
                "progress": progress if progress is not None else current.get("progress"),
                "created_at": current.get("created_at") or now,
                "updated_at": now,
                "source": str(source or current.get("source") or "runtime"),
                **({"owner_id": owner_id} if owner_id else {}),
                **{k: v for k, v in updates.items() if v is not None},
                **error_fields,
            }
            index[task_id] = item
            merged = sorted(index.values(), key=lambda x: str(x.get("updated_at") or x.get("created_at") or ""))[-5000:]
            self._save_items(TASKS_FILE, merged)
            return item

    def finish_task(
        self,
        task_id: str,
        *,
        type_: str,
        status: str,
        error: object = "",
        status_code: int | None = None,
        duration_ms: int | None = None,
        **updates: Any,
    ) -> dict[str, Any]:
        return self.upsert_task(
            task_id,
            type_=type_,
            status=status,
            error=error,
            status_code=status_code,
            progress=updates.pop("progress", status),
            duration_ms=duration_ms,
            **updates,
        )

    def list_events(self, limit: int = 200, code: str = "", scope: str = "") -> list[dict[str, Any]]:
        items = self._load_items(RISK_EVENTS_FILE)
        if code:
            items = [x for x in items if x.get("code") == code]
        if scope:
            items = [x for x in items if x.get("scope") == scope]
        return list(reversed(items[-max(1, min(limit, 1000)):]))

    def proxy_id(self, proxy: str) -> str:
        return f"px_{_sha(proxy, 18)}"

    def _redact_proxy(self, proxy: str) -> str:
        value = str(proxy or "")
        parsed = urlparse(value)
        if parsed.username or parsed.password:
            host = parsed.hostname or ""
            port = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme}://***:***@{host}{port}"
        return value

    def _ensure_runtime_proxies_locked(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        existing_ids = {str(item.get("id") or "") for item in items}
        for proxy_url in sorted(_runtime_aggregate_proxy_urls()):
            pid = self.proxy_id(proxy_url)
            if pid in existing_ids:
                continue
            now = _now()
            items.append({
                "id": pid,
                "proxy": proxy_url,
                "status": "healthy",
                "score": 100,
                "success": 0,
                "fail": 0,
                "http_403": 0,
                "http_429": 0,
                "timeout": 0,
                "last_success_at": None,
                "last_failure_at": None,
                "cooldown_until": None,
                "first_seen_at": now,
                "updated_at": now,
                "provider": "runtime",
                "protected": True,
            })
            existing_ids.add(pid)
        return items

    def list_proxies(self) -> list[dict[str, Any]]:
        with self._lock:
            items = self._load_items(PROXIES_FILE)
            before = len(items)
            items = self._ensure_runtime_proxies_locked(items)
            if len(items) != before:
                self._save_items(PROXIES_FILE, items)
            return items

    def upsert_proxy(self, proxy: str, **updates: Any) -> dict[str, Any]:
        proxy = str(proxy or "").strip()
        if not proxy:
            raise ValueError("proxy is required")
        with self._lock:
            items = self._load_items(PROXIES_FILE)
            pid = self.proxy_id(proxy)
            now = _now()
            for item in items:
                if item.get("id") == pid:
                    item.update(updates)
                    item.setdefault("first_seen_at", now)
                    item["updated_at"] = now
                    item["proxy"] = self._redact_proxy(proxy)
                    self._save_items(PROXIES_FILE, items)
                    return item
            item = {
                "id": pid,
                "proxy": self._redact_proxy(proxy),
                "status": "healthy",
                "score": 100,
                "success": 0,
                "fail": 0,
                "http_403": 0,
                "http_429": 0,
                "timeout": 0,
                "last_success_at": None,
                "last_failure_at": None,
                "cooldown_until": None,
                "first_seen_at": now,
                "updated_at": now,
                **updates,
            }
            items.append(item)
            self._save_items(PROXIES_FILE, items)
            return item

    def report_proxy_event(self, proxy: str, code: str, status_code: int | None = None) -> dict[str, Any] | None:
        if not proxy:
            return None
        if normalize_proxy_url(proxy) in _runtime_aggregate_proxy_urls():
            return None
        item = self.upsert_proxy(proxy)
        now = _now()
        if code in {"success", "ok"}:
            item["success"] = int(item.get("success") or 0) + 1
            item["last_success_at"] = now
        else:
            item["fail"] = int(item.get("fail") or 0) + 1
            item["last_failure_at"] = now
            if status_code == 403 or "403" in code or "challenge" in code:
                item["http_403"] = int(item.get("http_403") or 0) + 1
            if status_code == 429 or "429" in code or "rate" in code:
                item["http_429"] = int(item.get("http_429") or 0) + 1
            if "timeout" in code:
                item["timeout"] = int(item.get("timeout") or 0) + 1
        total = int(item.get("success") or 0) + int(item.get("fail") or 0)
        score = 100 if total == 0 else round(100 * int(item.get("success") or 0) / total, 1)
        score -= min(50, int(item.get("http_403") or 0) * 10)
        score -= min(40, int(item.get("http_429") or 0) * 8)
        score -= min(30, int(item.get("timeout") or 0) * 4)
        item["score"] = max(0, score)
        item["status"] = "healthy" if item["score"] >= 70 else "cooldown" if item["score"] >= 30 else "dead"
        self.upsert_proxy(proxy, **{k: v for k, v in item.items() if k not in {"id", "proxy"}})
        return item

    def delete_proxies(self, ids: list[str] | None = None, statuses: list[str] | None = None) -> dict[str, Any]:
        idset = {str(item or "").strip() for item in (ids or []) if str(item or "").strip()}
        status_set = {str(item or "").strip().lower() for item in (statuses or []) if str(item or "").strip()}
        with self._lock:
            items = self._ensure_runtime_proxies_locked(self._load_items(PROXIES_FILE))
            runtime_proxies = _runtime_aggregate_proxy_urls()
            kept: list[dict[str, Any]] = []
            removed = 0
            for item in items:
                proxy_url = normalize_proxy_url(str(item.get("proxy") or ""))
                if proxy_url in runtime_proxies:
                    kept.append(item)
                    continue
                match = False
                if idset and str(item.get("id") or "") in idset:
                    match = True
                if status_set and str(item.get("status") or "").lower() in status_set:
                    match = True
                if not idset and not status_set:
                    match = str(item.get("status") or "").lower() in {"dead"}
                if match:
                    removed += 1
                else:
                    kept.append(item)
            self._save_items(PROXIES_FILE, kept)
            return {"removed": removed, "kept": len(kept)}

    def prune_proxies(self, *, min_score: float = 30.0, min_failures: int = 3, include_cooldown: bool = False) -> dict[str, Any]:
        with self._lock:
            items = self._ensure_runtime_proxies_locked(self._load_items(PROXIES_FILE))
            runtime_proxies = _runtime_aggregate_proxy_urls()
            kept: list[dict[str, Any]] = []
            removed_items: list[dict[str, Any]] = []
            for item in items:
                proxy_url = normalize_proxy_url(str(item.get("proxy") or ""))
                if proxy_url in runtime_proxies:
                    kept.append(item)
                    continue
                status = str(item.get("status") or "").lower()
                score = float(item.get("score") or 0)
                failures = int(item.get("fail") or 0)
                should_remove = status == "dead" or (failures >= min_failures and score < min_score)
                if include_cooldown and status == "cooldown" and score < min_score:
                    should_remove = True
                if should_remove:
                    removed_items.append(item)
                else:
                    kept.append(item)
            if removed_items:
                self._save_items(PROXIES_FILE, kept)
            return {
                "removed": len(removed_items),
                "kept": len(kept),
                "removed_ids": [str(item.get("id") or "") for item in removed_items],
            }

    def test_proxy_pool(self, *, limit: int = 50, include_dead: bool = False) -> dict[str, Any]:
        from services.proxy_service import test_proxy

        candidates = self.list_proxies()
        if not include_dead:
            candidates = [item for item in candidates if item.get("status") != "dead"]
        candidates = sorted(candidates, key=lambda item: (str(item.get("status") or ""), -float(item.get("score") or 0)))[: max(1, min(limit, 200))]
        tested = ok = failed = 0
        results: list[dict[str, Any]] = []
        for item in candidates:
            proxy = str(item.get("proxy") or "")
            result = test_proxy(proxy, timeout=12.0)
            tested += 1
            status_code = int(result.get("status") or 0)
            if result.get("ok"):
                ok += 1
                self.report_proxy_event(proxy, "success", status_code=status_code or None)
            else:
                failed += 1
                code = "network_timeout" if "timeout" in str(result.get("error") or "").lower() else "proxy_or_challenge_blocked"
                self.report_proxy_event(proxy, code, status_code=status_code or None)
            results.append({"id": item.get("id"), "proxy": item.get("proxy"), "result": result})
        return {"tested": tested, "ok": ok, "failed": failed, "items": results}

    def rebuild_proxy_scores(self) -> dict[str, Any]:
        """Rebuild proxy score file from persisted risk events using current policy."""
        with self._lock:
            self._save_items(PROXIES_FILE, [])
            count = 0
            for event in self._load_items(RISK_EVENTS_FILE):
                proxy = str(event.get("proxy") or "")
                code = str(event.get("code") or "")
                status_code = event.get("status_code")
                try:
                    status_int = int(status_code or 0)
                except Exception:
                    status_int = 0
                if not proxy or (code not in PROXY_SCORING_EVENT_CODES and status_int not in {403, 429}):
                    continue
                self.report_proxy_event(proxy, code, status_code=status_int or None)
                count += 1
            return {"rebuilt": count, "total": len(self.list_proxies())}

    def sync_task_center(self) -> dict[str, Any]:
        # PATCH_MARKER conversation_status_repair_r28
        try:
            from services.conversation_store import conversation_store
            conversation_store.repair_statuses()
        except Exception:
            pass
        with self._lock:
            tasks = {str(item.get("id")): item for item in self._load_items(TASKS_FILE) if item.get("id")}
            deleted_task_ids = _load_task_tombstones(tasks)
            image_data = _load_json(DATA_DIR / "image_tasks.json", {"tasks": {}})
            raw = image_data.get("tasks") if isinstance(image_data, dict) else image_data
            image_items = list(raw.values()) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
            upserted = 0
            for item in image_items:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                tid = str(item["id"])
                if tid in deleted_task_ids:
                    continue
                existing = tasks.get(tid, {})
                error_fields = normalize_task_error(item.get("error") or item.get("error_message"))
                tasks[tid] = {
                    **existing,
                    "id": tid,
                    "type": "image",
                    "status": normalize_task_status(item.get("status")),
                    "progress": item.get("progress"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "duration_ms": item.get("duration_ms"),
                    **error_fields,
                    "source": "image_tasks",
                }
                upserted += 1
            register_data = _load_json(DATA_DIR / "register.json", {})
            if isinstance(register_data, dict):
                stats = register_data.get("stats") if isinstance(register_data.get("stats"), dict) else {}
                if stats:
                    total = int(register_data.get("total") or stats.get("total") or 0)
                    done = int(stats.get("done") or 0)
                    running = int(stats.get("running") or 0)
                    enabled = bool(register_data.get("enabled"))
                    status = "running" if enabled and running else ("success" if total and done >= total else "idle")
                    job_id = str(stats.get("job_id") or "current")
                    register_task_id = f"register:{job_id}"
                    if register_task_id in deleted_task_ids:
                        register_task_id = ""
                    for previous in tasks.values():
                        if previous.get("type") == "register" and previous.get("id") != register_task_id and previous.get("status") == "running":
                            previous["status"] = "stale"
                            previous.update(normalize_task_error("新的注册任务已启动，旧任务已自动收口"))
                            previous["updated_at"] = stats.get("started_at") or previous.get("updated_at")
                    if not register_task_id:
                        pass
                    else:
                        existing = tasks.get(register_task_id, {})
                        tasks[register_task_id] = {
                            **existing,
                            "id": register_task_id,
                            "type": "register",
                            "status": normalize_task_status(status),
                            "progress": f"{done}/{total}" if total else str(done),
                            "created_at": stats.get("started_at"),
                            "updated_at": stats.get("updated_at"),
                            "duration_ms": int(float(stats.get("elapsed_seconds") or 0) * 1000),
                            **normalize_task_error(existing.get("error")),
                            "source": "register_json",
                            "success": int(stats.get("success") or 0),
                            "fail": int(stats.get("fail") or 0),
                            "total": total,
                            "threads": int(stats.get("threads") or register_data.get("threads") or 0),
                            "current_quota": int(stats.get("current_quota") or 0),
                            "current_available": int(stats.get("current_available") or 0),
                        }
            video_data = _load_json(DATA_DIR / "video_tasks.json", {"tasks": {}})
            video_raw = video_data.get("tasks") if isinstance(video_data, dict) else video_data
            video_items = list(video_raw.values()) if isinstance(video_raw, dict) else (video_raw if isinstance(video_raw, list) else [])
            for item in video_items:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                tid = str(item["id"])
                if tid in deleted_task_ids:
                    continue
                existing = tasks.get(tid, {})
                error_fields = normalize_task_error(item.get("error") or item.get("error_message"))
                tasks[tid] = {
                    **existing,
                    "id": tid,
                    "type": "video",
                    "status": normalize_task_status(item.get("status")),
                    "progress": item.get("progress"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "duration_ms": item.get("duration_ms"),
                    **error_fields,
                    "source": "video_tasks",
                }
                upserted += 1
            conversation_data = _load_json(DATA_DIR / "conversations.json", {"items": []})
            conversation_items = conversation_data.get("items") if isinstance(conversation_data, dict) else conversation_data
            if not isinstance(conversation_items, list):
                conversation_items = []
            for item in conversation_items:
                # PATCH_MARKER chat_task_terminal_r27
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                tid = str(item["id"])
                if tid in deleted_task_ids:
                    continue
                messages = item.get("messages") if isinstance(item.get("messages"), list) else []
                existing = tasks.get(tid, {})
                raw_status = str(item.get("status") or "").strip().lower()
                has_assistant = any(
                    isinstance(m, dict)
                    and str(m.get("role") or "").strip().lower() == "assistant"
                    and str(m.get("content") or "").strip()
                    for m in messages
                )
                has_failed = any(
                    isinstance(m, dict)
                    and str((m.get("metadata") or {}).get("status") or "").lower() == "failed"
                    for m in messages
                )
                if raw_status in {"succeeded", "success", "failed", "error", "cancelled", "stale", "idle"}:
                    derived = raw_status
                elif has_failed:
                    derived = "failed"
                elif has_assistant:
                    derived = "succeeded"
                else:
                    derived = raw_status or "active"
                tasks[tid] = {
                    **existing,
                    "id": tid,
                    "type": "chat",
                    "status": normalize_task_status(derived),
                    "progress": f"{len(messages)} messages",
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "duration_ms": 0,
                    **normalize_task_error(existing.get("error")),
                    "source": "conversations",
                    "model": item.get("model"),
                    "title": item.get("title"),
                    "owner_id": item.get("owner_id"),
                }
                upserted += 1
            normalized_items = []
            for item in tasks.values():
                current = dict(item)
                current["status"] = normalize_task_status(current.get("status"))
                if current.get("error_code") in (None, "") or current.get("retryable") is None:
                    current.update(normalize_task_error(current.get("error")))
                normalized_items.append(current)
            items = sorted(normalized_items, key=lambda x: str(x.get("updated_at") or x.get("created_at") or ""))[-5000:]
            self._save_items(TASKS_FILE, items)
            return {"upserted": upserted, "total": len(items)}

    def list_tasks(self, limit: int = 200, type_: str = "", status: str = "") -> list[dict[str, Any]]:
        self.sync_task_center()
        items = self._load_items(TASKS_FILE)
        items = [x for x in items if x.get("type") != "tombstone"]
        if type_:
            items = [x for x in items if x.get("type") == type_]
        if status:
            items = [x for x in items if x.get("status") == status]
        return list(reversed(items[-max(1, min(limit, 1000)):]))

    def delete_tasks(self, ids: list[str] | None = None, terminal_only: bool = True) -> dict[str, Any]:
        with self._lock:
            items = self._load_items(TASKS_FILE)
            idset = {str(x) for x in (ids or []) if str(x)}
            kept: list[dict[str, Any]] = []
            tombstones: list[dict[str, Any]] = []
            removed = 0
            now = _now()
            for item in items:
                task_id = str(item.get("id") or "")
                if item.get("type") == "tombstone":
                    kept.append(item)
                    continue
                status = normalize_task_status(item.get("status"))
                match = (task_id in idset) if idset else (not terminal_only or status in TERMINAL_TASK_STATES)
                if match and task_id:
                    removed += 1
                    tombstones.append({
                        "id": _task_tombstone_id(task_id),
                        "type": "tombstone",
                        "status": "deleted",
                        "deleted_task_id": task_id,
                        "deleted_task_type": item.get("type"),
                        "created_at": now,
                        "updated_at": now,
                        "source": "task_center_delete",
                    })
                else:
                    kept.append(item)
            self._save_items(TASKS_FILE, kept + tombstones)
            return {"removed": removed, "kept": len(kept), "tombstones": len(tombstones)}

    def profile_audit_summary(self) -> dict[str, Any]:
        """Return a lightweight profile health summary for dashboards.

        The full audit path intentionally repairs/binds accounts and may touch
        every account/profile.  Calling that from /api/risk/summary made the
        dashboard block behind account writes while registration was running.
        Summary is read-only and only checks the persisted profile snapshot.
        Use /api/runtime-profiles/audit or backfill for the heavier repair flow.
        """
        try:
            profiles = runtime_profile_service.list_profiles()
            required = (
                ("tls", "impersonate"),
                ("headers", "user-agent"),
                ("headers", "sec-ch-ua"),
                ("headers", "sec-ch-ua-mobile"),
                ("headers", "sec-ch-ua-platform"),
                ("openai", "oai-device-id"),
                ("openai", "oai-session-id"),
            )
            ok = 0
            for profile in profiles:
                healthy = True
                for section, key in required:
                    value = profile.get(section) if isinstance(profile.get(section), dict) else {}
                    if not str(value.get(key) or "").strip():
                        healthy = False
                        break
                if healthy:
                    ok += 1
            return {"total": len(profiles), "ok": ok, "failed": max(0, len(profiles) - ok)}
        except Exception as exc:
            return {"error": str(exc)}

    def summary(self) -> dict[str, Any]:
        caps = self.list_capabilities()
        events = self._load_items(RISK_EVENTS_FILE)
        proxies = self.list_proxies()
        tasks = self.list_tasks(limit=1000)
        cap_counts = {name: sum(1 for item in caps if item.get(name)) for name in ("chat", "image", "file", "audio_tts", "audio_stt", "video")}
        event_counts: dict[str, int] = {}
        for event in events[-1000:]:
            code = str(event.get("code") or "unknown")
            event_counts[code] = event_counts.get(code, 0) + 1
        task_counts: dict[str, int] = {}
        for task in tasks:
            status = str(task.get("status") or "unknown")
            task_counts[status] = task_counts.get(status, 0) + 1
        return {
            "settings": {
                "risk_engine_enabled": True,
                "proxy_scoring_enabled": True,
                "capability_probe_enabled": True,
            },
            "accounts": {"total": len(account_service.list_accounts()), "capabilities": cap_counts},
            "profiles": self.profile_audit_summary(),
            "proxies": {"total": len(proxies), "healthy": sum(1 for p in proxies if p.get("status") == "healthy"), "cooldown": sum(1 for p in proxies if p.get("status") == "cooldown"), "dead": sum(1 for p in proxies if p.get("status") == "dead")},
            "risk_events": {"total": len(events), "recent_by_code": event_counts},
            "tasks": {"total": len(tasks), "by_status": task_counts},
            "updated_at": _now(),
        }


risk_control_service = RiskControlService()


def record_runtime_risk(
    backend: Any,
    message: str,
    *,
    status_code: int | None = None,
    code: str | None = None,
    scope: str | None = None,
    raw: dict[str, Any] | None = None,
) -> None:
    if backend is None:
        return
    proxy = str(getattr(backend, "proxy_url", "") or "")
    account = getattr(backend, "account", {}) or {}
    try:
        risk_control_service.record_event(
            code=code,
            message=message,
            scope=scope,
            account=account,
            proxy=proxy,
            profile_id=str(account.get("runtime_profile_id") or ""),
            status_code=status_code,
            raw=raw or {},
        )
    except Exception:
        pass
    try:
        from services.dynamic_proxy_feedback import report_dynamic_proxy_denial

        report_dynamic_proxy_denial(
            proxy,
            target="chatgpt.com:443",
            status_code=int(status_code or 0),
            reason=code or "runtime_upstream_denial",
            detail={"message": str(message or "")[:500], "raw": raw or {}},
        )
    except Exception:
        pass


def record_runtime_success(backend: Any) -> None:
    if backend is None:
        return
    try:
        proxy = str(getattr(backend, "proxy_url", "") or "")
        if proxy:
            risk_control_service.report_proxy_event(proxy, "success")
    except Exception:
        pass

# PATCH_MARKER free_soft_revoked_capability_r8
# PATCH_MARKER soft_image_capability_heal_r10
