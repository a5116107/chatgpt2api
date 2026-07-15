from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PROXY = "http://127.0.0.1:10808"
DEFAULT_MAIL_PROVIDER = "tempmail_lol"
DEFAULT_STRATEGY = "platform_bridge"
AUTH_BASE = "https://auth.openai.com"
PLATFORM_BASE = "https://platform.openai.com"
PLATFORM_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"
PLATFORM_REDIRECT_URI = f"{PLATFORM_BASE}/auth/callback"
PLATFORM_AUDIENCE = "https://api.openai.com/v1"
PLATFORM_AUTH0_CLIENT = "eyJuYW1lIjoiYXV0aDAtc3BhLWpzIiwidmVyc2lvbiI6IjEuMjEuMCJ9"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)
DEFAULT_SEC_CH_UA = '"Google Chrome";v="145", "Chromium";v="145", "Not_A Brand";v="24"'
DEFAULT_SENTINEL_SV = "20260124ceb8"


def _json_load(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


@dataclass(frozen=True)
class SignupConfig:
    project_root: Path
    strategy: str = DEFAULT_STRATEGY
    mail_provider: str = DEFAULT_MAIL_PROVIDER
    proxy: str = DEFAULT_PROXY
    otp_timeout_seconds: int = 600
    otp_poll_interval_seconds: float = 5.0
    register_password: str = ""
    user_agent: str = DEFAULT_USER_AGENT
    sec_ch_ua: str = DEFAULT_SEC_CH_UA
    accept_language: str = "en-US,en;q=0.9"
    auth_base: str = AUTH_BASE
    platform_base: str = PLATFORM_BASE
    platform_client_id: str = PLATFORM_CLIENT_ID
    platform_redirect_uri: str = PLATFORM_REDIRECT_URI
    platform_audience: str = PLATFORM_AUDIENCE
    platform_auth0_client: str = PLATFORM_AUTH0_CLIENT
    sentinel_sv: str = DEFAULT_SENTINEL_SV
    mail_providers_file: str = "mail_providers.json"
    account_profile_enabled: bool = True
    account_profile_dir: str = ".account_profiles"
    account_profile_rotate_on_unusable: bool = True
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def mail_providers_path(self) -> Path:
        return self.project_root / self.mail_providers_file

    @property
    def sentinel_dir(self) -> Path:
        return self.project_root / "sentinel"

    def proxy_or_none(self) -> str | None:
        value = str(self.proxy or "").strip()
        return value or None


def load_config(project_root: str | Path | None = None, **overrides: Any) -> SignupConfig:
    root = Path(project_root or Path.cwd()).resolve()
    cfg_path = root / "openai_signup_config.json"
    local = _json_load(cfg_path)
    mail_cfg = _json_load(root / "mail_providers.json")
    merged = _deep_merge(
        {
            "default_strategy": DEFAULT_STRATEGY,
            "default_mail_provider": DEFAULT_MAIL_PROVIDER,
            "default_proxy": DEFAULT_PROXY,
            "otp_timeout_seconds": 600,
            "otp_poll_interval_seconds": 5,
            "register_password": "",
            "mail_providers_file": "mail_providers.json",
            "platform_bridge": {},
        },
        local,
    )
    provider = (
        _first_env("MAIL_PROVIDER", "EMAIL_PROVIDER")
        or str(overrides.get("mail_provider") or overrides.get("provider") or merged.get("default_mail_provider") or mail_cfg.get("provider") or DEFAULT_MAIL_PROVIDER)
    )
    proxy = (
        _first_env("PROXY", "HTTPS_PROXY", "HTTP_PROXY")
        or str(overrides.get("proxy") or merged.get("default_proxy") or DEFAULT_PROXY)
    )
    strategy = (
        _first_env("OPENAI_SIGNUP_STRATEGY", "SIGNUP_STRATEGY")
        or str(overrides.get("strategy") or merged.get("default_strategy") or DEFAULT_STRATEGY)
    )
    bridge = merged.get("platform_bridge") if isinstance(merged.get("platform_bridge"), dict) else {}
    account_profile = merged.get("account_profile") if isinstance(merged.get("account_profile"), dict) else {}
    profile_enabled_raw = _first_env("OPENAI_ACCOUNT_PROFILE_ENABLED", "ACCOUNT_PROFILE_ENABLED") or str(account_profile.get("enabled", "true"))
    profile_enabled = str(profile_enabled_raw).strip().lower() not in {"0", "false", "no", "off"}
    return SignupConfig(
        project_root=root,
        strategy=str(strategy or DEFAULT_STRATEGY).strip().lower().replace("-", "_"),
        mail_provider=str(provider or DEFAULT_MAIL_PROVIDER).strip().lower().replace("-", "_"),
        proxy=str(proxy or "").strip(),
        otp_timeout_seconds=int(overrides.get("otp_timeout_seconds") or merged.get("otp_timeout_seconds") or 600),
        otp_poll_interval_seconds=float(overrides.get("otp_poll_interval_seconds") or merged.get("otp_poll_interval_seconds") or 5),
        register_password=str(overrides.get("password") or merged.get("register_password") or ""),
        user_agent=str(bridge.get("user_agent") or DEFAULT_USER_AGENT),
        sec_ch_ua=str(bridge.get("sec_ch_ua") or DEFAULT_SEC_CH_UA),
        accept_language=str(bridge.get("accept_language") or "en-US,en;q=0.9"),
        auth_base=str(bridge.get("auth_base") or AUTH_BASE).rstrip("/"),
        platform_base=str(bridge.get("platform_base") or PLATFORM_BASE).rstrip("/"),
        platform_client_id=str(bridge.get("client_id") or PLATFORM_CLIENT_ID),
        platform_redirect_uri=str(bridge.get("redirect_uri") or PLATFORM_REDIRECT_URI),
        platform_audience=str(bridge.get("audience") or PLATFORM_AUDIENCE),
        platform_auth0_client=str(bridge.get("auth0_client") or PLATFORM_AUTH0_CLIENT),
        sentinel_sv=str(bridge.get("sentinel_sv") or DEFAULT_SENTINEL_SV),
        mail_providers_file=str(merged.get("mail_providers_file") or "mail_providers.json"),
        account_profile_enabled=profile_enabled,
        account_profile_dir=str(_first_env("OPENAI_ACCOUNT_PROFILE_DIR", "ACCOUNT_PROFILE_DIR") or account_profile.get("dir") or ".account_profiles"),
        account_profile_rotate_on_unusable=str(account_profile.get("rotate_on_unusable", "true")).strip().lower() not in {"0", "false", "no", "off"},
        raw=merged,
    )


def write_default_config(project_root: str | Path) -> Path:
    root = Path(project_root).resolve()
    path = root / "openai_signup_config.json"
    payload = {
        "default_strategy": DEFAULT_STRATEGY,
        "default_mail_provider": DEFAULT_MAIL_PROVIDER,
        "default_proxy": DEFAULT_PROXY,
        "otp_timeout_seconds": 600,
        "otp_poll_interval_seconds": 5,
        "mail_providers_file": "mail_providers.json",
        "platform_bridge": {
            "client_id": PLATFORM_CLIENT_ID,
            "platform_base": PLATFORM_BASE,
            "auth_base": AUTH_BASE,
            "redirect_uri": PLATFORM_REDIRECT_URI,
            "audience": PLATFORM_AUDIENCE,
            "auth0_client": PLATFORM_AUTH0_CLIENT,
            "screen_hint": "signup",
            "sentinel_sv": DEFAULT_SENTINEL_SV,
        },
        "account_profile": {
            "enabled": True,
            "dir": ".account_profiles",
            "rotate_on_unusable": True,
            "notes": "账号级绑定画像：同一账号复用同一 device_id/user-agent/TLS/sentinel 屏幕画像；账号失效可清空画像。",
        },
    }
    if not path.exists():
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
