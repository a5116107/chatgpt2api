from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from services.config import DATA_DIR


PROFILE_FILE = DATA_DIR / "runtime_profiles.json"

DEFAULT_ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7"

PROFILE_TEMPLATES: dict[str, dict[str, Any]] = {
    "chrome_win_146": {
        "tls": {"impersonate": "chrome146"},
        "headers": {
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/146.0.0.0 Safari/537.36"
            ),
            "sec-ch-ua": '"Google Chrome";v="146", "Chromium";v="146", "Not A(Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua-arch": '"x86"',
            "sec-ch-ua-bitness": '"64"',
            "sec-ch-ua-full-version": '"146.0.0.0"',
            "sec-ch-ua-full-version-list": (
                '"Google Chrome";v="146.0.0.0", "Chromium";v="146.0.0.0", '
                '"Not A(Brand";v="24.0.0.0"'
            ),
            "accept-language": DEFAULT_ACCEPT_LANGUAGE,
        },
        "locale": {"timezone": "Asia/Shanghai", "language": "zh-CN"},
    },
    "chrome_win_145": {
        "tls": {"impersonate": "chrome145"},
        "headers": {
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/145.0.0.0 Safari/537.36"
            ),
            "sec-ch-ua": '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua-arch": '"x86"',
            "sec-ch-ua-bitness": '"64"',
            "sec-ch-ua-full-version": '"145.0.0.0"',
            "sec-ch-ua-full-version-list": (
                '"Chromium";v="145.0.0.0", "Not:A-Brand";v="99.0.0.0", '
                '"Google Chrome";v="145.0.0.0"'
            ),
            "accept-language": DEFAULT_ACCEPT_LANGUAGE,
        },
        "locale": {"timezone": "Asia/Shanghai", "language": "zh-CN"},
    },
    "chrome_win_144": {
        "tls": {"impersonate": "chrome142"},
        "headers": {
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/144.0.0.0 Safari/537.36"
            ),
            "sec-ch-ua": '"Google Chrome";v="144", "Not?A_Brand";v="8", "Chromium";v="144"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua-arch": '"x86"',
            "sec-ch-ua-bitness": '"64"',
            "sec-ch-ua-full-version": '"144.0.0.0"',
            "sec-ch-ua-full-version-list": (
                '"Chromium";v="144.0.0.0", "Not:A-Brand";v="99.0.0.0", '
                '"Google Chrome";v="144.0.0.0"'
            ),
            "accept-language": DEFAULT_ACCEPT_LANGUAGE,
        },
        "locale": {"timezone": "Asia/Shanghai", "language": "zh-CN"},
    },
    "chrome_win_143": {
        "tls": {"impersonate": "chrome142"},
        "headers": {
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/143.0.0.0 Safari/537.36"
            ),
            "sec-ch-ua": '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua-arch": '"x86"',
            "sec-ch-ua-bitness": '"64"',
            "sec-ch-ua-full-version": '"143.0.7499.147"',
            "sec-ch-ua-full-version-list": (
                '"Google Chrome";v="143.0.7499.147", "Chromium";v="143.0.7499.147", '
                '"Not A(Brand";v="24.0.0.0"'
            ),
            "accept-language": DEFAULT_ACCEPT_LANGUAGE,
        },
        "locale": {"timezone": "Asia/Shanghai", "language": "zh-CN"},
    },
    "edge_win_143": {
        "tls": {"impersonate": "edge101"},
        "headers": {
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
            ),
            "sec-ch-ua": '"Microsoft Edge";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua-arch": '"x86"',
            "sec-ch-ua-bitness": '"64"',
            "sec-ch-ua-full-version": '"143.0.3650.96"',
            "sec-ch-ua-full-version-list": (
                '"Microsoft Edge";v="143.0.3650.96", "Chromium";v="143.0.7499.147", '
                '"Not A(Brand";v="24.0.0.0"'
            ),
            "accept-language": DEFAULT_ACCEPT_LANGUAGE,
        },
        "locale": {"timezone": "Asia/Shanghai", "language": "zh-CN"},
    },
    "edge_win_144": {
        "tls": {"impersonate": "edge101"},
        "headers": {
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0"
            ),
            "sec-ch-ua": '"Microsoft Edge";v="144", "Chromium";v="144", "Not A(Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua-arch": '"x86"',
            "sec-ch-ua-bitness": '"64"',
            "sec-ch-ua-full-version": '"144.0.0.0"',
            "sec-ch-ua-full-version-list": (
                '"Microsoft Edge";v="144.0.0.0", "Chromium";v="144.0.0.0", '
                '"Not A(Brand";v="24.0.0.0"'
            ),
            "accept-language": DEFAULT_ACCEPT_LANGUAGE,
        },
        "locale": {"timezone": "Asia/Shanghai", "language": "zh-CN"},
    },
}

PROFILE_TEMPLATES["chrome_win"] = PROFILE_TEMPLATES["chrome_win_146"]
PROFILE_TEMPLATES["edge_win"] = PROFILE_TEMPLATES["edge_win_143"]

PROFILE_REQUIRED_FP_KEYS = (
    "user-agent",
    "impersonate",
    "oai-device-id",
    "oai-session-id",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: object) -> str:
    return str(value or "").strip()


def _token_hash(value: object) -> str:
    raw = _clean(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24] if raw else ""


def _account_key(account: dict[str, Any] | None, fallback: str = "") -> str:
    source = account if isinstance(account, dict) else {}
    for key in ("account_id", "user_id", "email"):
        value = _clean(source.get(key))
        if value:
            return f"{key}:{value.lower() if key == 'email' else value}"
    token_digest = _token_hash(source.get("access_token"))
    if token_digest:
        return f"token_sha256:{token_digest}"
    fallback = _clean(fallback)
    return fallback or f"pending:{uuid.uuid4().hex}"


def _template_name(seed: str = "") -> str:
    names = sorted(PROFILE_TEMPLATES)
    if not seed:
        return secrets.choice(names)
    digest = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16)
    return names[digest % len(names)]


def _profile_id(account_key: str) -> str:
    return "rp_" + hashlib.sha256(account_key.encode("utf-8")).hexdigest()[:20]


def _merge_dict(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _fp_from_profile(profile: dict[str, Any]) -> dict[str, str]:
    headers = profile.get("headers") if isinstance(profile.get("headers"), dict) else {}
    tls = profile.get("tls") if isinstance(profile.get("tls"), dict) else {}
    openai = profile.get("openai") if isinstance(profile.get("openai"), dict) else {}
    return {
        "user-agent": _clean(headers.get("user-agent")),
        "impersonate": _clean(tls.get("impersonate")) or "chrome146",
        "oai-device-id": _clean(openai.get("oai-device-id")) or str(uuid.uuid4()),
        "oai-session-id": _clean(openai.get("oai-session-id")) or str(uuid.uuid4()),
        "sec-ch-ua": _clean(headers.get("sec-ch-ua")),
        "sec-ch-ua-mobile": _clean(headers.get("sec-ch-ua-mobile")) or "?0",
        "sec-ch-ua-platform": _clean(headers.get("sec-ch-ua-platform")) or '"Windows"',
        "sec-ch-ua-arch": _clean(headers.get("sec-ch-ua-arch")) or '"x86"',
        "sec-ch-ua-bitness": _clean(headers.get("sec-ch-ua-bitness")) or '"64"',
        "sec-ch-ua-full-version": _clean(headers.get("sec-ch-ua-full-version")),
        "sec-ch-ua-full-version-list": _clean(headers.get("sec-ch-ua-full-version-list")),
        "accept-language": _clean(headers.get("accept-language")) or DEFAULT_ACCEPT_LANGUAGE,
    }


def _profile_snapshot(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": profile.get("id"),
        "account_key": profile.get("account_key"),
        "tls": deepcopy(profile.get("tls") or {}),
        "headers": deepcopy(profile.get("headers") or {}),
        "openai": deepcopy(profile.get("openai") or {}),
        "locale": deepcopy(profile.get("locale") or {}),
        "proxy_policy": deepcopy(profile.get("proxy_policy") or {}),
        "clearance": deepcopy(profile.get("clearance") or {}),
        "version": int(profile.get("version") or 1),
        "updated_at": profile.get("updated_at"),
    }


class RuntimeProfileService:
    def __init__(self, path: Path = PROFILE_FILE) -> None:
        self.path = path
        self._lock = RLock()
        self._profiles = self._load()
        self._cleanup_stale_tmp_files()

    def _load(self) -> dict[str, dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        items = raw.get("items") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return {}
        profiles: dict[str, dict[str, Any]] = {}
        for item in items:
            profile = self.normalize_profile(item)
            if profile:
                profiles[str(profile["id"])] = profile
        return profiles

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cleanup_stale_tmp_files_locked()
        payload = {
            "schema_version": 1,
            "updated_at": _now(),
            "items": sorted(self._profiles.values(), key=lambda item: str(item.get("created_at") or "")),
        }
        tmp = self.path.with_suffix(self.path.suffix + f".tmp.{uuid.uuid4().hex}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def _cleanup_stale_tmp_files_locked(self) -> int:
        removed = 0
        for candidate in self.path.parent.glob(f"{self.path.name}.tmp.*"):
            if not candidate.is_file():
                continue
            try:
                candidate.unlink()
            except Exception:
                continue
            removed += 1
        return removed

    def _cleanup_stale_tmp_files(self) -> int:
        with self._lock:
            return self._cleanup_stale_tmp_files_locked()

    def normalize_profile(self, item: object) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        account_key = _clean(item.get("account_key")) or f"pending:{uuid.uuid4().hex}"
        template = item.get("template")
        template_name = str(template or _template_name(account_key)).strip()
        base = deepcopy(PROFILE_TEMPLATES.get(template_name) or PROFILE_TEMPLATES["chrome_win_145"])
        source = _merge_dict(base, item)
        profile_id = _clean(source.get("id")) or _profile_id(account_key)
        headers = source.get("headers") if isinstance(source.get("headers"), dict) else {}
        tls = source.get("tls") if isinstance(source.get("tls"), dict) else {}
        openai = source.get("openai") if isinstance(source.get("openai"), dict) else {}
        proxy_policy = source.get("proxy_policy") if isinstance(source.get("proxy_policy"), dict) else {}
        clearance = source.get("clearance") if isinstance(source.get("clearance"), dict) else {}
        locale = source.get("locale") if isinstance(source.get("locale"), dict) else {}
        normalized = {
            "id": profile_id,
            "account_key": account_key,
            "template": template_name,
            "status": _clean(source.get("status")) or "active",
            "tls": {"impersonate": _clean(tls.get("impersonate")) or _clean(base.get("tls", {}).get("impersonate")) or "chrome146"},
            "headers": {
                "user-agent": _clean(headers.get("user-agent")) or PROFILE_TEMPLATES["chrome_win_145"]["headers"]["user-agent"],
                "sec-ch-ua": _clean(headers.get("sec-ch-ua")) or PROFILE_TEMPLATES["chrome_win_145"]["headers"]["sec-ch-ua"],
                "sec-ch-ua-mobile": _clean(headers.get("sec-ch-ua-mobile")) or "?0",
                "sec-ch-ua-platform": _clean(headers.get("sec-ch-ua-platform")) or '"Windows"',
                "sec-ch-ua-arch": _clean(headers.get("sec-ch-ua-arch")) or '"x86"',
                "sec-ch-ua-bitness": _clean(headers.get("sec-ch-ua-bitness")) or '"64"',
                "sec-ch-ua-full-version": _clean(headers.get("sec-ch-ua-full-version")),
                "sec-ch-ua-full-version-list": _clean(headers.get("sec-ch-ua-full-version-list")),
                "accept-language": _clean(headers.get("accept-language")) or DEFAULT_ACCEPT_LANGUAGE,
            },
            "openai": {
                "oai-device-id": _clean(openai.get("oai-device-id")) or str(uuid.uuid4()),
                "oai-session-id": _clean(openai.get("oai-session-id")) or str(uuid.uuid4()),
            },
            "locale": {
                "timezone": _clean(locale.get("timezone")) or "Asia/Shanghai",
                "language": _clean(locale.get("language")) or "zh-CN",
            },
            "proxy_policy": {
                "mode": _clean(proxy_policy.get("mode")) or "account_or_runtime",
                "group": _clean(proxy_policy.get("group")),
                "register_proxy": _clean(proxy_policy.get("register_proxy")),
                "runtime_proxy": _clean(proxy_policy.get("runtime_proxy")),
                "allow_rotate": bool(proxy_policy.get("allow_rotate", True)),
            },
            "clearance": {
                "scope": _clean(clearance.get("scope")) or "profile",
                "last_refresh_at": clearance.get("last_refresh_at") or None,
            },
            "version": int(source.get("version") or 1),
            "created_at": source.get("created_at") or _now(),
            "updated_at": _now(),
        }
        return normalized

    def create_profile(
        self,
        account: dict[str, Any] | None = None,
        *,
        proxy: str = "",
        template: str = "",
        source: str = "account",
        profile_id: str = "",
        save: bool = True,
    ) -> dict[str, Any]:
        account_key = _account_key(account, fallback=f"{source}:{uuid.uuid4().hex}")
        template_name = template or _template_name(account_key)
        base = deepcopy(PROFILE_TEMPLATES.get(template_name) or PROFILE_TEMPLATES["chrome_win_145"])
        profile = {
            **base,
            "id": _clean(profile_id) or _profile_id(account_key),
            "account_key": account_key,
            "template": template_name,
            "status": "active",
            "proxy_policy": {
                "mode": "account_or_runtime",
                "group": "",
                "register_proxy": _clean(proxy or (account or {}).get("proxy")),
                "runtime_proxy": _clean((account or {}).get("proxy")),
                "allow_rotate": True,
            },
            "clearance": {"scope": "profile", "last_refresh_at": None},
            "source": source,
        }
        normalized = self.normalize_profile(profile)
        assert normalized is not None
        if save:
            with self._lock:
                self._profiles[str(normalized["id"])] = normalized
                self._save_locked()
        return normalized

    def ensure_account_profile(self, account: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(account, dict):
            account = {}
        account_key = _account_key(account)
        profile_id = _clean(account.get("runtime_profile_id"))
        raw_snapshot = account.get("profile_snapshot") if isinstance(account.get("profile_snapshot"), dict) else {}
        raw_fp = account.get("fp") if isinstance(account.get("fp"), dict) else {}
        seed_profile: dict[str, Any] = {
            "id": profile_id or _profile_id(account_key),
            "account_key": account_key,
            "template": raw_snapshot.get("template") or _template_name(account_key),
        }
        if raw_snapshot:
            seed_profile = _merge_dict(seed_profile, raw_snapshot)
            if profile_id:
                seed_profile["id"] = profile_id
        if raw_fp:
            seed_profile = _merge_dict(
                seed_profile,
                {
                    "tls": {"impersonate": raw_fp.get("impersonate")},
                    "headers": {
                        "user-agent": raw_fp.get("user-agent"),
                        "sec-ch-ua": raw_fp.get("sec-ch-ua"),
                        "sec-ch-ua-mobile": raw_fp.get("sec-ch-ua-mobile"),
                        "sec-ch-ua-platform": raw_fp.get("sec-ch-ua-platform"),
                        "sec-ch-ua-arch": raw_fp.get("sec-ch-ua-arch"),
                        "sec-ch-ua-bitness": raw_fp.get("sec-ch-ua-bitness"),
                        "sec-ch-ua-full-version": raw_fp.get("sec-ch-ua-full-version"),
                        "sec-ch-ua-full-version-list": raw_fp.get("sec-ch-ua-full-version-list"),
                        "accept-language": raw_fp.get("accept-language"),
                    },
                    "openai": {
                        "oai-device-id": raw_fp.get("oai-device-id"),
                        "oai-session-id": raw_fp.get("oai-session-id"),
                    },
                },
            )
        proxy = _clean(account.get("proxy"))
        proxy_policy = seed_profile.get("proxy_policy") if isinstance(seed_profile.get("proxy_policy"), dict) else {}
        if proxy:
            proxy_policy["runtime_proxy"] = proxy
            proxy_policy.setdefault("register_proxy", proxy)
        seed_profile["proxy_policy"] = proxy_policy

        with self._lock:
            existing = self._profiles.get(str(seed_profile["id"]))
            profile = self.normalize_profile(_merge_dict(existing or {}, seed_profile))
            assert profile is not None
            self._profiles[str(profile["id"])] = profile
            self._save_locked()

        normalized_account = dict(account)
        normalized_account["runtime_profile_id"] = str(profile["id"])
        normalized_account["profile_status"] = profile.get("status") or "active"
        normalized_account["profile_snapshot"] = _profile_snapshot(profile)
        normalized_account["proxy_policy"] = deepcopy(profile.get("proxy_policy") or {})
        normalized_account["fp"] = _fp_from_profile(profile)
        return normalized_account, profile

    def bind_account(self, profile_id: str, account: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        profile_id = _clean(profile_id)
        with self._lock:
            profile = self._profiles.get(profile_id)
            if profile is None:
                profile = self.create_profile(account, source="bind", profile_id=profile_id, save=False)
            profile = dict(profile)
            profile["account_key"] = _account_key(account)
            if _clean(account.get("proxy")):
                proxy_policy = dict(profile.get("proxy_policy") or {})
                proxy_policy["runtime_proxy"] = _clean(account.get("proxy"))
                profile["proxy_policy"] = proxy_policy
            normalized = self.normalize_profile(profile)
            assert normalized is not None
            self._profiles[str(normalized["id"])] = normalized
            if profile_id and profile_id != str(normalized["id"]):
                self._profiles.pop(profile_id, None)
            self._save_locked()
        account_with_profile = dict(account)
        account_with_profile["runtime_profile_id"] = str(normalized["id"])
        account_with_profile["profile_status"] = normalized.get("status") or "active"
        account_with_profile["profile_snapshot"] = _profile_snapshot(normalized)
        account_with_profile["proxy_policy"] = deepcopy(normalized.get("proxy_policy") or {})
        account_with_profile["fp"] = _fp_from_profile(normalized)
        return account_with_profile, normalized

    def repair_profile(self, profile_id: str, account: dict[str, Any] | None = None) -> dict[str, Any] | None:
        profile_id = _clean(profile_id)
        if not profile_id:
            return None
        with self._lock:
            current = self._profiles.get(profile_id)
            if current is None:
                if not isinstance(account, dict):
                    return None
                current = self.create_profile(account, source="repair", profile_id=profile_id, save=False)
            merged = dict(current)
            if isinstance(account, dict):
                merged["account_key"] = _account_key(account, fallback=profile_id)
                proxy = _clean(account.get("proxy"))
                if proxy:
                    proxy_policy = dict(merged.get("proxy_policy") or {})
                    proxy_policy["runtime_proxy"] = proxy
                    proxy_policy.setdefault("register_proxy", proxy)
                    merged["proxy_policy"] = proxy_policy
                profile_snapshot = account.get("profile_snapshot") if isinstance(account.get("profile_snapshot"), dict) else {}
                if profile_snapshot:
                    merged = _merge_dict(merged, profile_snapshot)
                    merged["id"] = profile_id
                fp = account.get("fp") if isinstance(account.get("fp"), dict) else {}
                if fp:
                    merged = _merge_dict(
                        merged,
                        {
                            "tls": {"impersonate": fp.get("impersonate")},
                            "headers": {
                                "user-agent": fp.get("user-agent"),
                                "sec-ch-ua": fp.get("sec-ch-ua"),
                                "sec-ch-ua-mobile": fp.get("sec-ch-ua-mobile"),
                                "sec-ch-ua-platform": fp.get("sec-ch-ua-platform"),
                                "sec-ch-ua-arch": fp.get("sec-ch-ua-arch"),
                                "sec-ch-ua-bitness": fp.get("sec-ch-ua-bitness"),
                                "sec-ch-ua-full-version": fp.get("sec-ch-ua-full-version"),
                                "sec-ch-ua-full-version-list": fp.get("sec-ch-ua-full-version-list"),
                                "accept-language": fp.get("accept-language"),
                            },
                            "openai": {
                                "oai-device-id": fp.get("oai-device-id"),
                                "oai-session-id": fp.get("oai-session-id"),
                            },
                        },
                    )
            normalized = self.normalize_profile(merged)
            assert normalized is not None
            self._profiles[str(normalized["id"])] = normalized
            self._save_locked()
            return deepcopy(normalized)

    def rebind_account(self, account: dict[str, Any], profile_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(account, dict):
            account = {}
        profile_id = _clean(profile_id or account.get("runtime_profile_id"))
        if profile_id:
            return self.bind_account(profile_id, account)
        return self.ensure_account_profile(account)

    def get(self, profile_id: str) -> dict[str, Any] | None:
        with self._lock:
            profile = self._profiles.get(_clean(profile_id))
            return deepcopy(profile) if profile else None

    def list_profiles(self) -> list[dict[str, Any]]:
        with self._lock:
            return [deepcopy(item) for item in sorted(self._profiles.values(), key=lambda item: str(item.get("created_at") or ""))]

    def delete_profile(self, profile_id: str) -> bool:
        profile_id = _clean(profile_id)
        if not profile_id:
            return False
        with self._lock:
            removed = self._profiles.pop(profile_id, None) is not None
            if removed:
                self._save_locked()
            return removed

    def delete_by_account(self, account: dict[str, Any]) -> bool:
        profile_id = _clean((account or {}).get("runtime_profile_id"))
        if not profile_id:
            return False
        return self.delete_profile(profile_id)

    def cleanup_orphan_profiles(self, accounts: list[dict[str, Any]]) -> dict[str, Any]:
        accounts = [item for item in accounts if isinstance(item, dict)]
        keep_ids: set[str] = set()
        for account in accounts:
            runtime_profile_id = _clean(account.get("runtime_profile_id"))
            if runtime_profile_id:
                keep_ids.add(runtime_profile_id)
            profile_snapshot = account.get("profile_snapshot") if isinstance(account.get("profile_snapshot"), dict) else {}
            snapshot_id = _clean(profile_snapshot.get("id"))
            if not runtime_profile_id and snapshot_id:
                keep_ids.add(snapshot_id)
        with self._lock:
            removed: list[dict[str, Any]] = []
            kept: list[dict[str, Any]] = []
            for profile in self._profiles.values():
                profile_id = _clean(profile.get("id"))
                if profile_id and profile_id in keep_ids:
                    kept.append(profile)
                else:
                    removed.append(profile)
            if removed:
                self._profiles = {str(item["id"]): item for item in kept}
                self._save_locked()
        return {
            "removed": len(removed),
            "kept": len(kept),
            "removed_ids": [str(item.get("id") or "") for item in removed],
        }

    def audit_account(self, account: dict[str, Any]) -> dict[str, Any]:
        account_with_profile, profile = self.ensure_account_profile(dict(account or {}))
        fp = account_with_profile.get("fp") if isinstance(account_with_profile.get("fp"), dict) else {}
        missing = [key for key in PROFILE_REQUIRED_FP_KEYS if not _clean(fp.get(key))]
        account_proxy = _clean(account_with_profile.get("proxy"))
        proxy_policy = profile.get("proxy_policy") if isinstance(profile.get("proxy_policy"), dict) else {}
        runtime_proxy = _clean(proxy_policy.get("runtime_proxy"))
        register_proxy = _clean(proxy_policy.get("register_proxy"))
        warnings: list[str] = []
        if account_proxy and runtime_proxy and account_proxy != runtime_proxy:
            warnings.append("account proxy differs from runtime profile proxy policy")
        if register_proxy and account_proxy and register_proxy != account_proxy and not bool(proxy_policy.get("allow_rotate", True)):
            warnings.append("register proxy differs from account proxy while rotate is disabled")
        return {
            "account_key": profile.get("account_key"),
            "runtime_profile_id": profile.get("id"),
            "ok": not missing and not warnings,
            "missing": missing,
            "warnings": warnings,
            "profile": self.public_profile(profile),
        }

    def audit_accounts(self, accounts: list[dict[str, Any]]) -> dict[str, Any]:
        results = [self.audit_account(account) for account in accounts if isinstance(account, dict)]
        return {
            "total": len(results),
            "ok": sum(1 for item in results if item.get("ok")),
            "failed": sum(1 for item in results if not item.get("ok")),
            "items": results,
        }

    def public_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        item = deepcopy(profile)
        headers = item.get("headers") if isinstance(item.get("headers"), dict) else {}
        ua = _clean(headers.get("user-agent"))
        if ua:
            headers["user-agent_preview"] = ua[:32] + ("..." if len(ua) > 32 else "")
        return item


runtime_profile_service = RuntimeProfileService()
