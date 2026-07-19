from __future__ import annotations

import copy
from dataclasses import dataclass
import json
import os
import sys
from pathlib import Path
import time

from services.storage.base import StorageBackend

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
CONFIG_FILE = BASE_DIR / "config.json"
VERSION_FILE = BASE_DIR / "VERSION"
BACKUP_STATE_FILE = DATA_DIR / "backup_state.json"

DEFAULT_BACKUP_INCLUDE = {
    "config": True,
    "register": True,
    "cpa": True,
    "sub2api": True,
    "logs": True,
    "image_tasks": True,
    "accounts_snapshot": True,
    "auth_keys_snapshot": True,
    "images": False,
}

DEFAULT_IMAGE_STORAGE = {
    "enabled": False,
    "mode": "local",
    "webdav_url": "",
    "webdav_username": "",
    "webdav_password": "",
    "webdav_root_path": "chatgpt2api/images",
    "public_base_url": "",
}

DEFAULT_CHAT_COMPLETION_CACHE = {
    "enabled": True,
    "ttl_seconds": 60,
    "max_entries": 256,
    "dedupe_inflight": True,
    "stream_cache": True,
    "normalize_messages": True,
    "drop_adjacent_duplicates": True,
    "drop_assistant_history": False,
}

DEFAULT_CHAT_RUNTIME = {
    "connect_timeout_secs": 10,
    "response_timeout_secs": 60,
    "max_account_rotates": 8,
    "rotate_on_timeout": True,
}

DEFAULT_PROXY_RUNTIME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)

DEFAULT_PROXY_RUNTIME = {
    "enabled": False,
    "egress_mode": "direct",
    "proxy_url": "",
    "resource_proxy_url": "",
    "skip_ssl_verify": False,
    "reset_session_status_codes": [403],
    "clearance": {
        "enabled": False,
        "mode": "none",
        "cf_cookies": "",
        "cf_clearance": "",
        "user_agent": DEFAULT_PROXY_RUNTIME_USER_AGENT,
        "browser": "chrome",
        "flaresolverr_url": "",
        "timeout_sec": 60,
        "refresh_interval": 3600,
        "warm_up_on_start": False,
    },
}

DEFAULT_THIRD_PARTY_APPS = {
    "infinite_canvas": {
        "enabled": False,
        "url": "https://canvas.best",
    },
}

DEFAULT_FEATURE_FLAGS = {
    "chat": True,
    "image": True,
    "video": True,
    "register": True,
}

DEFAULT_VIDEO_SETTINGS = {
    "enabled": False,
    "provider": "local",
    "fallback_provider": "local",
    "base_url": "",
    "api_key": "",
    "poll_interval_secs": 5,
    "poll_timeout_secs": 600,
    "storage_dir": "videos",
    "supported_providers": ["local", "mock", "openai_compatible"],
}

DEFAULT_ACCOUNT_WATCHER_SETTINGS = {
    "enabled": True,
    "check_limited": True,
    "check_normal": False,
    "check_expiring": True,
    "keepalive_refresh_tokens": True,
    "max_batch": 64,
    "initial_delay_seconds": 120,
    "require_proxy_ready": True,
    "proxy_ready_timeout_secs": 8,
    "proxy_status_url": "http://dynamic-proxy:17286/_dynamic_proxy/status",
    "min_proxy_available": 8,
    "dynamic_batch_by_proxy": True,
}


def _normalize_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return default
    if value is None:
        return default
    return bool(value)


def _normalize_positive_int(value: object, default: int, minimum: int = 0) -> int:
    try:
        normalized = int(value)
    except (OverflowError, TypeError, ValueError):
        normalized = default
    return max(minimum, normalized)


def _normalize_backup_include(value: object) -> dict[str, bool]:
    source = value if isinstance(value, dict) else {}
    normalized = dict(DEFAULT_BACKUP_INCLUDE)
    for key in normalized:
        normalized[key] = _normalize_bool(source.get(key), normalized[key])
    return normalized


def _normalize_backup_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    return {
        "enabled": _normalize_bool(source.get("enabled"), False),
        "provider": "cloudflare_r2",
        "account_id": str(source.get("account_id") or "").strip(),
        "access_key_id": str(source.get("access_key_id") or "").strip(),
        "secret_access_key": str(source.get("secret_access_key") or "").strip(),
        "bucket": str(source.get("bucket") or "").strip(),
        "prefix": str(source.get("prefix") or "backups").strip().strip("/") or "backups",
        "interval_minutes": _normalize_positive_int(source.get("interval_minutes"), 360, 1),
        "rotation_keep": _normalize_positive_int(source.get("rotation_keep"), 10, 0),
        "encrypt": _normalize_bool(source.get("encrypt"), False),
        "passphrase": str(source.get("passphrase") or "").strip(),
        "include": _normalize_backup_include(source.get("include")),
    }


def _normalize_backup_state(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    return {
        "last_started_at": str(source.get("last_started_at") or "").strip() or None,
        "last_finished_at": str(source.get("last_finished_at") or "").strip() or None,
        "last_status": str(source.get("last_status") or "idle").strip() or "idle",
        "last_error": str(source.get("last_error") or "").strip() or None,
        "last_object_key": str(source.get("last_object_key") or "").strip() or None,
    }


def _normalize_image_storage_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    mode = str(source.get("mode") or "local").strip().lower()
    if mode not in {"local", "webdav", "both"}:
        mode = "local"
    enabled = _normalize_bool(source.get("enabled"), False)
    if not enabled:
        mode = "local"
    root_path = str(source.get("webdav_root_path") or DEFAULT_IMAGE_STORAGE["webdav_root_path"]).strip().strip("/")
    return {
        "enabled": enabled,
        "mode": mode,
        "webdav_url": str(source.get("webdav_url") or "").strip().rstrip("/"),
        "webdav_username": str(source.get("webdav_username") or "").strip(),
        "webdav_password": str(source.get("webdav_password") or "").strip(),
        "webdav_root_path": root_path or str(DEFAULT_IMAGE_STORAGE["webdav_root_path"]),
        "public_base_url": str(source.get("public_base_url") or "").strip().rstrip("/"),
    }


def _normalize_chat_completion_cache_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    return {
        "enabled": _normalize_bool(source.get("enabled"), DEFAULT_CHAT_COMPLETION_CACHE["enabled"]),
        "ttl_seconds": _normalize_positive_int(
            source.get("ttl_seconds"),
            int(DEFAULT_CHAT_COMPLETION_CACHE["ttl_seconds"]),
            0,
        ),
        "max_entries": _normalize_positive_int(
            source.get("max_entries"),
            int(DEFAULT_CHAT_COMPLETION_CACHE["max_entries"]),
            1,
        ),
        "dedupe_inflight": _normalize_bool(
            source.get("dedupe_inflight"),
            bool(DEFAULT_CHAT_COMPLETION_CACHE["dedupe_inflight"]),
        ),
        "stream_cache": _normalize_bool(
            source.get("stream_cache"),
            bool(DEFAULT_CHAT_COMPLETION_CACHE["stream_cache"]),
        ),
        "normalize_messages": _normalize_bool(
            source.get("normalize_messages"),
            bool(DEFAULT_CHAT_COMPLETION_CACHE["normalize_messages"]),
        ),
        "drop_adjacent_duplicates": _normalize_bool(
            source.get("drop_adjacent_duplicates"),
            bool(DEFAULT_CHAT_COMPLETION_CACHE["drop_adjacent_duplicates"]),
        ),
        "drop_assistant_history": _normalize_bool(
            source.get("drop_assistant_history"),
            bool(DEFAULT_CHAT_COMPLETION_CACHE["drop_assistant_history"]),
        ),
    }


def _normalize_chat_runtime_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    return {
        "connect_timeout_secs": _normalize_positive_int(
            source.get("connect_timeout_secs"),
            int(DEFAULT_CHAT_RUNTIME["connect_timeout_secs"]),
            1,
        ),
        "response_timeout_secs": _normalize_positive_int(
            source.get("response_timeout_secs"),
            int(DEFAULT_CHAT_RUNTIME["response_timeout_secs"]),
            10,
        ),
        "max_account_rotates": _normalize_positive_int(
            source.get("max_account_rotates"),
            int(DEFAULT_CHAT_RUNTIME["max_account_rotates"]),
            0,
        ),
        "rotate_on_timeout": _normalize_bool(
            source.get("rotate_on_timeout"),
            bool(DEFAULT_CHAT_RUNTIME["rotate_on_timeout"]),
        ),
    }


def _normalize_status_codes(value: object) -> list[int]:
    items = value if isinstance(value, list) else DEFAULT_PROXY_RUNTIME["reset_session_status_codes"]
    normalized: list[int] = []
    for item in items:
        if isinstance(item, bool):
            continue
        try:
            status = int(item)
        except (OverflowError, TypeError, ValueError):
            continue
        if 100 <= status <= 599 and status not in normalized:
            normalized.append(status)
    if not normalized:
        return list(DEFAULT_PROXY_RUNTIME["reset_session_status_codes"])
    return normalized


def _normalize_proxy_runtime_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    default_clearance = DEFAULT_PROXY_RUNTIME["clearance"]
    clearance_source = source.get("clearance") if isinstance(source.get("clearance"), dict) else {}

    egress_mode = str(source.get("egress_mode") or DEFAULT_PROXY_RUNTIME["egress_mode"]).strip().lower()
    if egress_mode not in {"direct", "single_proxy"}:
        egress_mode = str(DEFAULT_PROXY_RUNTIME["egress_mode"])

    clearance_mode = str(clearance_source.get("mode") or default_clearance["mode"]).strip().lower()
    if clearance_mode not in {"none", "manual", "flaresolverr"}:
        clearance_mode = str(default_clearance["mode"])

    user_agent = str(clearance_source.get("user_agent") or default_clearance["user_agent"]).strip()
    browser = str(clearance_source.get("browser") or default_clearance["browser"]).strip()

    existing_clearance_cookies = str(source.get("_existing_cf_cookies") or "").strip()
    existing_cf_clearance = str(source.get("_existing_cf_clearance") or "").strip()
    cf_cookies = str(clearance_source.get("cf_cookies") or "").strip()
    cf_clearance = str(clearance_source.get("cf_clearance") or "").strip()
    if not cf_cookies and _normalize_bool(clearance_source.get("has_cf_cookies"), False):
        cf_cookies = existing_clearance_cookies
    if not cf_clearance and _normalize_bool(clearance_source.get("has_cf_clearance"), False):
        cf_clearance = existing_cf_clearance

    return {
        "enabled": _normalize_bool(source.get("enabled"), bool(DEFAULT_PROXY_RUNTIME["enabled"])),
        "egress_mode": egress_mode,
        "proxy_url": str(source.get("proxy_url") or "").strip(),
        "resource_proxy_url": str(source.get("resource_proxy_url") or "").strip(),
        "skip_ssl_verify": _normalize_bool(
            source.get("skip_ssl_verify"),
            bool(DEFAULT_PROXY_RUNTIME["skip_ssl_verify"]),
        ),
        "reset_session_status_codes": _normalize_status_codes(source.get("reset_session_status_codes")),
        "clearance": {
            "enabled": _normalize_bool(clearance_source.get("enabled"), bool(default_clearance["enabled"])),
            "mode": clearance_mode,
            "cf_cookies": cf_cookies,
            "cf_clearance": cf_clearance,
            "user_agent": user_agent or str(default_clearance["user_agent"]),
            "browser": browser or str(default_clearance["browser"]),
            "flaresolverr_url": str(clearance_source.get("flaresolverr_url") or "").strip(),
            "timeout_sec": _normalize_positive_int(
                clearance_source.get("timeout_sec"),
                int(default_clearance["timeout_sec"]),
                1,
            ),
            "refresh_interval": _normalize_positive_int(
                clearance_source.get("refresh_interval"),
                int(default_clearance["refresh_interval"]),
                60,
            ),
            "warm_up_on_start": _normalize_bool(
                clearance_source.get("warm_up_on_start"),
                bool(default_clearance["warm_up_on_start"]),
            ),
        },
    }


def _normalize_third_party_apps_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    canvas_source = source.get("infinite_canvas") if isinstance(source.get("infinite_canvas"), dict) else {}
    return {
        "infinite_canvas": {
            "enabled": _normalize_bool(canvas_source.get("enabled"), False),
            "url": str(canvas_source.get("url") or DEFAULT_THIRD_PARTY_APPS["infinite_canvas"]["url"]).strip(),
        },
    }


def _normalize_feature_flags(value: object) -> dict[str, bool]:
    source = value if isinstance(value, dict) else {}
    return {key: _normalize_bool(source.get(key), default) for key, default in DEFAULT_FEATURE_FLAGS.items()}


def _normalize_video_settings(value: object) -> dict[str, object]:
    # PATCH_MARKER video_provider_settings_r29
    source = value if isinstance(value, dict) else {}
    provider = str(source.get("provider") or DEFAULT_VIDEO_SETTINGS["provider"] or "local").strip().lower() or "local"
    fallback = str(source.get("fallback_provider") or DEFAULT_VIDEO_SETTINGS["fallback_provider"] or "local").strip().lower() or "local"
    supported = source.get("supported_providers")
    if not isinstance(supported, list) or not supported:
        supported = list(DEFAULT_VIDEO_SETTINGS["supported_providers"])
    supported = [str(item).strip().lower() for item in supported if str(item).strip()]
    # PATCH_MARKER video_upstream_ready_r33
    base_url = str(source.get("base_url") or "").strip().rstrip("/")
    api_key = str(source.get("api_key") or "").strip()
    upstream_ready = bool(base_url) and provider in {"openai_compatible", "openai", "sora_compatible"}
    mode = "upstream" if upstream_ready else ("local" if provider in {"local", "mock"} else "fallback_local")
    return {
        "enabled": _normalize_bool(source.get("enabled"), bool(DEFAULT_VIDEO_SETTINGS["enabled"])),
        "provider": provider,
        "fallback_provider": fallback,
        "base_url": base_url,
        "api_key": api_key,
        "poll_interval_secs": _normalize_positive_int(source.get("poll_interval_secs"), int(DEFAULT_VIDEO_SETTINGS["poll_interval_secs"]), 1),
        "poll_timeout_secs": _normalize_positive_int(source.get("poll_timeout_secs"), int(DEFAULT_VIDEO_SETTINGS["poll_timeout_secs"]), 30),
        "storage_dir": str(source.get("storage_dir") or DEFAULT_VIDEO_SETTINGS["storage_dir"]).strip().strip("/") or "videos",
        "supported_providers": supported,
        "upstream_ready": upstream_ready,
        "mode": mode,
    }


def _normalize_account_watcher_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    proxy_status_url = source.get("proxy_status_url")
    if proxy_status_url is None:
        proxy_status_url = DEFAULT_ACCOUNT_WATCHER_SETTINGS["proxy_status_url"]
    return {
        "enabled": _normalize_bool(source.get("enabled"), bool(DEFAULT_ACCOUNT_WATCHER_SETTINGS["enabled"])),
        "check_limited": _normalize_bool(source.get("check_limited"), bool(DEFAULT_ACCOUNT_WATCHER_SETTINGS["check_limited"])),
        "check_normal": _normalize_bool(source.get("check_normal"), bool(DEFAULT_ACCOUNT_WATCHER_SETTINGS["check_normal"])),
        "check_expiring": _normalize_bool(source.get("check_expiring"), bool(DEFAULT_ACCOUNT_WATCHER_SETTINGS["check_expiring"])),
        "keepalive_refresh_tokens": _normalize_bool(source.get("keepalive_refresh_tokens"), bool(DEFAULT_ACCOUNT_WATCHER_SETTINGS["keepalive_refresh_tokens"])),
        "max_batch": _normalize_positive_int(source.get("max_batch"), int(DEFAULT_ACCOUNT_WATCHER_SETTINGS["max_batch"]), 1),
        "initial_delay_seconds": _normalize_positive_int(source.get("initial_delay_seconds"), int(DEFAULT_ACCOUNT_WATCHER_SETTINGS["initial_delay_seconds"]), 0),
        "require_proxy_ready": _normalize_bool(source.get("require_proxy_ready"), bool(DEFAULT_ACCOUNT_WATCHER_SETTINGS["require_proxy_ready"])),
        "proxy_ready_timeout_secs": _normalize_positive_int(source.get("proxy_ready_timeout_secs"), int(DEFAULT_ACCOUNT_WATCHER_SETTINGS["proxy_ready_timeout_secs"]), 1),
        "proxy_status_url": str(proxy_status_url).strip(),
        "min_proxy_available": _normalize_positive_int(source.get("min_proxy_available"), int(DEFAULT_ACCOUNT_WATCHER_SETTINGS["min_proxy_available"]), 1),
        "dynamic_batch_by_proxy": _normalize_bool(source.get("dynamic_batch_by_proxy"), bool(DEFAULT_ACCOUNT_WATCHER_SETTINGS["dynamic_batch_by_proxy"])),
    }


def _validate_image_storage_settings(settings: dict[str, object]) -> None:
    if not _normalize_bool(settings.get("enabled"), False):
        return
    if not str(settings.get("webdav_url") or "").strip():
        raise ValueError("启用 WebDAV 图片存储后必须填写 WebDAV URL")
    if not str(settings.get("webdav_password") or "").strip():
        raise ValueError("启用 WebDAV 图片存储后必须填写 WebDAV 密码")


@dataclass(frozen=True)
class LoadedSettings:
    auth_key: str
    refresh_account_interval_minute: int


def _normalize_auth_key(value: object) -> str:
    return str(value or "").strip()


def _is_invalid_auth_key(value: object) -> bool:
    return _normalize_auth_key(value) == ""


def _read_json_object(path: Path, *, name: str) -> dict[str, object]:
    if not path.exists():
        return {}
    if path.is_dir():
        print(
            f"Warning: {name} at '{path}' is a directory, ignoring it and falling back to other configuration sources.",
            file=sys.stderr,
        )
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _load_settings() -> LoadedSettings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw_config = _read_json_object(CONFIG_FILE, name="config.json")
    auth_key = _normalize_auth_key(os.getenv("CHATGPT2API_AUTH_KEY") or raw_config.get("auth-key"))
    if _is_invalid_auth_key(auth_key):
        raise ValueError(
            "❌ auth-key 未设置！\n"
            "请在环境变量 CHATGPT2API_AUTH_KEY 中设置，或者在 config.json 中填写 auth-key。"
        )

    try:
        refresh_interval = int(raw_config.get("refresh_account_interval_minute", 5))
    except (TypeError, ValueError):
        refresh_interval = 5

    return LoadedSettings(
        auth_key=auth_key,
        refresh_account_interval_minute=refresh_interval,
    )


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.data = self._load()
        self._storage_backend: StorageBackend | None = None
        if _is_invalid_auth_key(self.auth_key):
            raise ValueError(
                "❌ auth-key 未设置！\n"
                "请按以下任意一种方式解决：\n"
                "1. 在 Render 的 Environment 变量中添加：\n"
                "   CHATGPT2API_AUTH_KEY = your_real_auth_key\n"
                "2. 或者在 config.json 中填写：\n"
                '   "auth-key": "your_real_auth_key"'
            )

    def _load(self) -> dict[str, object]:
        return _read_json_object(self.path, name="config.json")

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @property
    def auth_key(self) -> str:
        return _normalize_auth_key(os.getenv("CHATGPT2API_AUTH_KEY") or self.data.get("auth-key"))

    @property
    def accounts_file(self) -> Path:
        return DATA_DIR / "accounts.json"

    @property
    def refresh_account_interval_minute(self) -> int:
        try:
            return int(self.data.get("refresh_account_interval_minute", 5))
        except (TypeError, ValueError):
            return 5

    @property
    def image_retention_days(self) -> int:
        try:
            return max(1, int(self.data.get("image_retention_days", 30)))
        except (TypeError, ValueError):
            return 30

    @property
    def image_poll_timeout_secs(self) -> int:
        try:
            return max(1, int(self.data.get("image_poll_timeout_secs", 120)))
        except (TypeError, ValueError):
            return 120

    @property
    def image_request_deadline_secs(self) -> float:
        """Hard wall-clock budget for one image request, including retries and download."""
        try:
            return max(15.0, float(self.data.get("image_request_deadline_secs", 90.0)))
        except (TypeError, ValueError):
            return 90.0

    @property
    def image_attempt_timeout_secs(self) -> float:
        """Per-account image budget when another healthy account can take over."""
        try:
            return max(15.0, float(self.data.get("image_attempt_timeout_secs", 55.0)))
        except (TypeError, ValueError):
            return 55.0

    @property
    def image_min_retry_budget_secs(self) -> float:
        """Minimum remaining wall-clock budget required to start another upstream task."""
        try:
            return max(5.0, float(self.data.get("image_min_retry_budget_secs", 20.0)))
        except (TypeError, ValueError):
            return 20.0

    @property
    def image_sse_idle_timeout_secs(self) -> float:
        try:
            return max(5.0, float(self.data.get("image_sse_idle_timeout_secs", 20.0)))
        except (TypeError, ValueError):
            return 20.0

    @property
    def image_stream_close_timeout_secs(self) -> float:
        """Maximum wait per stream cleanup thread after an image result is available."""
        try:
            return min(5.0, max(0.1, float(self.data.get("image_stream_close_timeout_secs", 0.5))))
        except (TypeError, ValueError):
            return 0.5

    @property
    def image_poll_interval_secs(self) -> float:
        try:
            return max(0.5, float(self.data.get("image_poll_interval_secs", 0.5)))
        except (TypeError, ValueError):
            return 0.5

    @property
    def image_poll_initial_wait_secs(self) -> float:
        """Short commit grace before the first conversation poll."""
        try:
            return max(0.0, float(self.data.get("image_poll_initial_wait_secs", 0.25)))
        except (TypeError, ValueError):
            return 0.25

    @property
    def image_poll_request_timeout_secs(self) -> float:
        """Per-request timeout for low-latency conversation polling."""
        try:
            return min(5.0, max(0.5, float(self.data.get("image_poll_request_timeout_secs", 2.0))))
        except (TypeError, ValueError):
            return 2.0

    @property
    def image_poll_rate_limit_failover_threshold(self) -> int:
        """Consecutive poll 429s required before switching to a healthy alternative account."""
        try:
            return min(10, max(1, int(self.data.get("image_poll_rate_limit_failover_threshold", 2))))
        except (TypeError, ValueError):
            return 2

    @property
    def image_poll_rate_limit_retry_delay_secs(self) -> float:
        """Short retry delay for a first poll 429 when account failover is available."""
        try:
            return min(
                10.0,
                max(0.25, float(self.data.get("image_poll_rate_limit_retry_delay_secs", 1.0))),
            )
        except (TypeError, ValueError):
            return 1.0

    @property
    def image_poll_rate_limit_failover_min_elapsed_secs(self) -> float:
        """Minimum poll age before repeated 429s may abandon a still-viable SSE stream."""
        try:
            return min(
                60.0,
                max(0.0, float(self.data.get("image_poll_rate_limit_failover_min_elapsed_secs", 10.0))),
            )
        except (TypeError, ValueError):
            return 10.0

    @property
    def image_invalid_token_rotate_limit(self) -> int:
        """Maximum revoked image accounts skipped before failing one request."""
        try:
            return min(64, max(1, int(self.data.get("image_invalid_token_rotate_limit", 24))))
        except (TypeError, ValueError):
            return 24

    @property
    def image_poll_progress_persist_interval_secs(self) -> float:
        """Minimum interval between durable polling-progress updates."""
        try:
            return min(
                30.0,
                max(0.5, float(self.data.get("image_poll_progress_persist_interval_secs", 2.0))),
            )
        except (TypeError, ValueError):
            return 2.0

    @property
    def image_poll_fast_window_secs(self) -> float:
        try:
            return max(0.0, float(self.data.get("image_poll_fast_window_secs", 60.0)))
        except (TypeError, ValueError):
            return 60.0

    @property
    def image_poll_slow_interval_secs(self) -> float:
        try:
            return max(1.0, float(self.data.get("image_poll_slow_interval_secs", 3.0)))
        except (TypeError, ValueError):
            return 3.0

    @property
    def image_tasks_check_every(self) -> int:
        try:
            return max(1, int(self.data.get("image_tasks_check_every", 6)))
        except (TypeError, ValueError):
            return 6

    @property
    def image_tasks_timeout_secs(self) -> float:
        try:
            return max(0.5, float(self.data.get("image_tasks_timeout_secs", 0.75)))
        except (TypeError, ValueError):
            return 0.75

    @property
    def image_png_compress_level(self) -> int:
        """PNG compression is lossless; lower levels trade file size for latency."""
        try:
            return min(9, max(0, int(self.data.get("image_png_compress_level", 1))))
        except (TypeError, ValueError):
            return 1

    @property
    def image_fetch_direct_hosts(self) -> list[str]:
        """Exact image URL hosts that bypass the upstream proxy, for example the app's own CDN."""
        raw = self.data.get("image_fetch_direct_hosts", [])
        values = raw if isinstance(raw, (list, tuple, set)) else str(raw or "").split(",")
        hosts: list[str] = []
        for value in values:
            host = str(value or "").strip().lower().rstrip(".")
            if host and host not in hosts:
                hosts.append(host)
        return hosts

    @property
    def image_heartbeat_interval_secs(self) -> float:
        try:
            return max(1.0, float(self.data.get("image_heartbeat_interval_secs", 10.0)))
        except (TypeError, ValueError):
            return 10.0

    @property
    def image_account_probe_enabled(self) -> bool:
        value = self.data.get("image_account_probe_enabled", True)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def image_account_probe_interval_secs(self) -> float:
        try:
            return max(60.0, float(self.data.get("image_account_probe_interval_secs", 300.0)))
        except (TypeError, ValueError):
            return 300.0

    @property
    def image_account_probe_batch_size(self) -> int:
        try:
            return max(1, min(20, int(self.data.get("image_account_probe_batch_size", 3))))
        except (TypeError, ValueError):
            return 3

    @property
    def image_account_probe_parallelism(self) -> int:
        try:
            return max(1, min(10, int(self.data.get("image_account_probe_parallelism", 4))))
        except (TypeError, ValueError):
            return 4

    @property
    def image_account_probe_healthy_interval_secs(self) -> int:
        try:
            return max(300, min(86400, int(self.data.get("image_account_probe_healthy_interval_secs", 1800))))
        except (TypeError, ValueError):
            return 1800

    @property
    def image_account_quota_refresh_interval_secs(self) -> int:
        try:
            return max(300, min(86400, int(self.data.get("image_account_quota_refresh_interval_secs", 1800))))
        except (TypeError, ValueError):
            return 1800

    @property
    def image_account_probe_probation_interval_secs(self) -> int:
        try:
            return max(30, min(3600, int(self.data.get("image_account_probe_probation_interval_secs", 60))))
        except (TypeError, ValueError):
            return 60

    @property
    def image_account_rate_limit_cooldown_secs(self) -> int:
        try:
            return max(60, min(3600, int(self.data.get("image_account_rate_limit_cooldown_secs", 600))))
        except (TypeError, ValueError):
            return 600

    @property
    def image_account_timeout_cooldown_secs(self) -> int:
        try:
            return max(30, min(1800, int(self.data.get("image_account_timeout_cooldown_secs", 90))))
        except (TypeError, ValueError):
            return 90

    @property
    def image_account_max_cooldown_secs(self) -> int:
        try:
            return max(300, min(21600, int(self.data.get("image_account_max_cooldown_secs", 3600))))
        except (TypeError, ValueError):
            return 3600

    @property
    def image_account_failure_threshold(self) -> int:
        try:
            return max(1, min(10, int(self.data.get("image_account_failure_threshold", 2))))
        except (TypeError, ValueError):
            return 2

    @property
    def image_account_concurrency(self) -> int:
        try:
            return max(1, int(self.data.get("image_account_concurrency", 3)))
        except (TypeError, ValueError):
            return 3

    @property
    def image_parallel_generation(self) -> bool:
        value = self.data.get("image_parallel_generation", True)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def image_settle_enabled(self) -> bool:
        """图片二次确认机制：找到 file_ids 后等待一段时间再次确认。"""
        value = self.data.get("image_settle_enabled", True)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def image_check_before_hit_enabled(self) -> bool:
        """先check再hit：通过轮询确认 file_ids 存在后再返回，而非仅依赖 SSE 事件。"""
        value = self.data.get("image_check_before_hit_enabled", True)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def image_settle_secs(self) -> float:
        """二次确认等待时间（秒）。"""
        try:
            return max(0.5, float(self.data.get("image_settle_secs", 2.0)))
        except (TypeError, ValueError):
            return 2.0

    @property
    def auto_remove_invalid_accounts(self) -> bool:
        value = self.data.get("auto_remove_invalid_accounts", False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def auto_relogin_after_refresh(self) -> bool:
        value = self.data.get("auto_relogin_after_refresh", False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def log_levels(self) -> list[str]:
        levels = self.data.get("log_levels")
        if not isinstance(levels, list):
            return []
        allowed = {"debug", "info", "warning", "error"}
        return [level for item in levels if (level := str(item or "").strip().lower()) in allowed]

    @property
    def sensitive_words(self) -> list[str]:
        words = self.data.get("sensitive_words")
        return [word for item in words if (word := str(item or "").strip())] if isinstance(words, list) else []

    @property
    def ai_review(self) -> dict[str, object]:
        value = self.data.get("ai_review")
        return value if isinstance(value, dict) else {}

    @property
    def global_system_prompt(self) -> str:
        return str(self.data.get("global_system_prompt") or "").strip()

    @property
    def images_dir(self) -> Path:
        path = DATA_DIR / "images"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def image_thumbnails_dir(self) -> Path:
        path = DATA_DIR / "image_thumbnails"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cleanup_old_images(self) -> int:
        cutoff = time.time() - self.image_retention_days * 86400
        removed = 0
        for path in self.images_dir.rglob("*"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        for path in sorted((p for p in self.images_dir.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                path.rmdir()
            except OSError:
                pass
        return removed

    @property
    def base_url(self) -> str:
        return str(
            os.getenv("CHATGPT2API_BASE_URL")
            or self.data.get("base_url")
            or ""
        ).strip().rstrip("/")

    @property
    def app_version(self) -> str:
        try:
            value = VERSION_FILE.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return "0.0.0"
        return value or "0.0.0"

    def get(self) -> dict[str, object]:
        data = dict(self.data)
        data["refresh_account_interval_minute"] = self.refresh_account_interval_minute
        data["image_retention_days"] = self.image_retention_days
        data["image_poll_timeout_secs"] = self.image_poll_timeout_secs
        data["image_request_deadline_secs"] = self.image_request_deadline_secs
        data["image_attempt_timeout_secs"] = self.image_attempt_timeout_secs
        data["image_min_retry_budget_secs"] = self.image_min_retry_budget_secs
        data["image_sse_idle_timeout_secs"] = self.image_sse_idle_timeout_secs
        data["image_stream_close_timeout_secs"] = self.image_stream_close_timeout_secs
        data["image_poll_interval_secs"] = self.image_poll_interval_secs
        data["image_poll_initial_wait_secs"] = self.image_poll_initial_wait_secs
        data["image_poll_request_timeout_secs"] = self.image_poll_request_timeout_secs
        data["image_poll_rate_limit_failover_threshold"] = self.image_poll_rate_limit_failover_threshold
        data["image_poll_rate_limit_retry_delay_secs"] = self.image_poll_rate_limit_retry_delay_secs
        data["image_poll_rate_limit_failover_min_elapsed_secs"] = self.image_poll_rate_limit_failover_min_elapsed_secs
        data["image_invalid_token_rotate_limit"] = self.image_invalid_token_rotate_limit
        data["image_poll_progress_persist_interval_secs"] = self.image_poll_progress_persist_interval_secs
        data["image_poll_fast_window_secs"] = self.image_poll_fast_window_secs
        data["image_poll_slow_interval_secs"] = self.image_poll_slow_interval_secs
        data["image_tasks_check_every"] = self.image_tasks_check_every
        data["image_tasks_timeout_secs"] = self.image_tasks_timeout_secs
        data["image_png_compress_level"] = self.image_png_compress_level
        data["image_fetch_direct_hosts"] = self.image_fetch_direct_hosts
        data["image_heartbeat_interval_secs"] = self.image_heartbeat_interval_secs
        data["image_account_probe_enabled"] = self.image_account_probe_enabled
        data["image_account_probe_interval_secs"] = self.image_account_probe_interval_secs
        data["image_account_probe_batch_size"] = self.image_account_probe_batch_size
        data["image_account_probe_parallelism"] = self.image_account_probe_parallelism
        data["image_account_probe_healthy_interval_secs"] = self.image_account_probe_healthy_interval_secs
        data["image_account_quota_refresh_interval_secs"] = self.image_account_quota_refresh_interval_secs
        data["image_account_probe_probation_interval_secs"] = self.image_account_probe_probation_interval_secs
        data["image_account_rate_limit_cooldown_secs"] = self.image_account_rate_limit_cooldown_secs
        data["image_account_timeout_cooldown_secs"] = self.image_account_timeout_cooldown_secs
        data["image_account_max_cooldown_secs"] = self.image_account_max_cooldown_secs
        data["image_account_failure_threshold"] = self.image_account_failure_threshold
        data["image_account_concurrency"] = self.image_account_concurrency
        data["image_parallel_generation"] = self.image_parallel_generation
        data["auto_remove_invalid_accounts"] = self.auto_remove_invalid_accounts
        data.pop("auto_remove_rate_limited_accounts", None)
        data["auto_relogin_after_refresh"] = self.auto_relogin_after_refresh
        data["log_levels"] = self.log_levels
        data["sensitive_words"] = self.sensitive_words
        data["ai_review"] = self.ai_review
        data["global_system_prompt"] = self.global_system_prompt
        data["backup"] = self.get_backup_settings()
        data["image_storage"] = self.get_image_storage_settings()
        data["chat_completion_cache"] = self.get_chat_completion_cache_settings()
        data["chat_runtime"] = self.get_chat_runtime_settings()
        data["proxy_runtime"] = self.get_public_proxy_runtime_settings()
        data["third_party_apps"] = self.get_third_party_apps_settings()
        data["features"] = self.get_feature_flags()
        data["video"] = self.get_video_settings()
        data["account_watcher"] = self.get_account_watcher_settings()
        data.pop("auth-key", None)
        return data

    def get_proxy_settings(self) -> str:
        return str(self.data.get("proxy") or "").strip()

    def get_proxy_runtime_settings(self) -> dict[str, object]:
        return _normalize_proxy_runtime_settings(self.data.get("proxy_runtime"))

    def get_public_proxy_runtime_settings(self) -> dict[str, object]:
        runtime = copy.deepcopy(self.get_proxy_runtime_settings())
        clearance = runtime.get("clearance") if isinstance(runtime.get("clearance"), dict) else {}
        if isinstance(clearance, dict):
            cf_cookies = str(clearance.get("cf_cookies") or "").strip()
            cf_clearance = str(clearance.get("cf_clearance") or "").strip()
            clearance["cf_cookies"] = ""
            clearance["cf_clearance"] = ""
            clearance["has_cf_cookies"] = bool(cf_cookies)
            clearance["has_cf_clearance"] = bool(cf_clearance)
        return runtime

    def get_third_party_apps_settings(self) -> dict[str, object]:
        return _normalize_third_party_apps_settings(self.data.get("third_party_apps"))

    def update(self, data: dict[str, object]) -> dict[str, object]:
        next_data = dict(self.data)
        next_data.update(dict(data or {}))
        next_data.pop("auto_remove_rate_limited_accounts", None)
        if "backup" in next_data:
            next_data["backup"] = _normalize_backup_settings(next_data.get("backup"))
        if "image_storage" in next_data:
            next_data["image_storage"] = _normalize_image_storage_settings(next_data.get("image_storage"))
            _validate_image_storage_settings(next_data["image_storage"])
        if "chat_completion_cache" in next_data:
            next_data["chat_completion_cache"] = _normalize_chat_completion_cache_settings(
                next_data.get("chat_completion_cache")
            )
        if "chat_runtime" in next_data:
            next_data["chat_runtime"] = _normalize_chat_runtime_settings(next_data.get("chat_runtime"))
        if "third_party_apps" in next_data:
            next_data["third_party_apps"] = _normalize_third_party_apps_settings(next_data.get("third_party_apps"))
        if "features" in next_data:
            next_data["features"] = _normalize_feature_flags(next_data.get("features"))
        if "video" in next_data:
            next_data["video"] = _normalize_video_settings(next_data.get("video"))
        if "account_watcher" in next_data:
            next_data["account_watcher"] = _normalize_account_watcher_settings(next_data.get("account_watcher"))
        if "proxy_runtime" in next_data:
            incoming_runtime = next_data.get("proxy_runtime")
            if isinstance(incoming_runtime, dict):
                previous_clearance = self.get_proxy_runtime_settings().get("clearance")
                if isinstance(previous_clearance, dict):
                    incoming_runtime = dict(incoming_runtime)
                    incoming_runtime["_existing_cf_cookies"] = previous_clearance.get("cf_cookies")
                    incoming_runtime["_existing_cf_clearance"] = previous_clearance.get("cf_clearance")
            next_data["proxy_runtime"] = _normalize_proxy_runtime_settings(incoming_runtime)
        next_data.pop("backup_state", None)
        self.data = next_data
        self._save()
        return self.get()

    def get_backup_settings(self) -> dict[str, object]:
        return _normalize_backup_settings(self.data.get("backup"))

    def get_image_storage_settings(self) -> dict[str, object]:
        return _normalize_image_storage_settings(self.data.get("image_storage"))

    def get_chat_completion_cache_settings(self) -> dict[str, object]:
        return _normalize_chat_completion_cache_settings(self.data.get("chat_completion_cache"))

    def get_chat_runtime_settings(self) -> dict[str, object]:
        return _normalize_chat_runtime_settings(self.data.get("chat_runtime"))

    def get_feature_flags(self) -> dict[str, bool]:
        return _normalize_feature_flags(self.data.get("features"))

    def get_video_settings(self) -> dict[str, object]:
        return _normalize_video_settings(self.data.get("video"))

    def get_account_watcher_settings(self) -> dict[str, object]:
        return _normalize_account_watcher_settings(self.data.get("account_watcher"))

    def get_storage_backend(self) -> StorageBackend:
        """获取存储后端实例（单例）"""
        if self._storage_backend is None:
            from services.storage.factory import create_storage_backend
            self._storage_backend = create_storage_backend(DATA_DIR)
        return self._storage_backend


def load_backup_state() -> dict[str, object]:
    return _normalize_backup_state(_read_json_object(BACKUP_STATE_FILE, name="backup_state.json"))


def save_backup_state(state: dict[str, object]) -> dict[str, object]:
    normalized = _normalize_backup_state(state)
    BACKUP_STATE_FILE.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return normalized


config = ConfigStore(CONFIG_FILE)
