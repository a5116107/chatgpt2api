from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit


_RETRYABLE_REGISTRATION_MARKERS = (
    "invalid_state",
    "account_creation_failed",
    "registration_disallowed",
    "cloudflare",
    "http_403",
    "curl: (28)",
    "curl: (35)",
    "curl: (55)",
    "curl: (56)",
    "tls connect error",
    "timed out",
    "unsupported_email",
    "not supported",
    "user_already_exists",
    "clearance",
    "existing_account_password_login_failed",
    "about_you",
    "no_auth_code",
    "about_you_no_auth_code",
    "about_you_create_account_failed",
    "need_verification_code",
    "rate_limit_exceeded",
    "429",
    "超时",
    "验证码超时",
    "等待注册验证码",
    "wait_for_code",
    "otp",
)
_DOMAIN_REJECTION_MARKERS = (
    "account_creation_failed",
    "registration_disallowed",
    "unsupported_email",
    "not supported",
)
_TLS_MARKERS = ("curl: (28)", "curl: (35)", "curl: (55)", "curl: (56)", "tls connect")
_PROXY_TTL_RE = re.compile(r"-ttl-(?P<seconds>\d+)$", flags=re.IGNORECASE)
_PROXY_REGION_RE = re.compile(
    r"-(?P<kind>country|region)-RAND(?=-sid-|$)",
    flags=re.IGNORECASE,
)


def normalize_proxy_settings(lease_seconds: Any, region: Any) -> tuple[int, str]:
    try:
        normalized_lease = min(3600, max(60, int(lease_seconds or 900)))
    except (TypeError, ValueError, OverflowError):
        normalized_lease = 900
    return normalized_lease, str(region or "").strip().upper()


def normalize_registration_proxy(
    proxy: str,
    *,
    region: str = "",
    lease_seconds: int = 900,
) -> str:
    """Return a proxy URL with a stable region and sufficient session lease."""
    candidate = str(proxy or "").strip()
    if not candidate:
        return ""
    try:
        parsed = urlsplit(candidate if "://" in candidate else f"http://{candidate}")
    except ValueError:
        return candidate
    if not parsed.username:
        return candidate

    username = unquote(parsed.username)
    normalized_region = str(region or "").strip()
    if normalized_region:
        username = _PROXY_REGION_RE.sub(
            lambda match: f"-{match.group('kind')}-{normalized_region}",
            username,
            count=1,
        )
    bounded_lease = max(60, min(3600, int(lease_seconds)))
    username = _PROXY_TTL_RE.sub(
        lambda match: f"-ttl-{max(bounded_lease, int(match.group('seconds')))}",
        username,
    )
    if username == unquote(parsed.username):
        return candidate

    userinfo, separator, host_port = parsed.netloc.rpartition("@")
    if not separator:
        return candidate
    _, password_separator, encoded_password = userinfo.partition(":")
    encoded_userinfo = quote(username, safe="")
    if password_separator:
        encoded_userinfo = f"{encoded_userinfo}:{encoded_password}"
    rebuilt = urlunsplit(
        (parsed.scheme, f"{encoded_userinfo}@{host_port}", parsed.path, parsed.query, parsed.fragment)
    )
    return rebuilt if "://" in candidate else rebuilt.removeprefix("http://")


def clearance_target_url(value: str) -> str:
    """Keep the challenged origin and path while dropping one-time query values."""
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw
    if not parsed.scheme or not parsed.netloc:
        return raw
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))


def short_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:12]


def is_retryable_registration_error(error: Exception | str) -> bool:
    return any(marker in str(error or "").lower() for marker in _RETRYABLE_REGISTRATION_MARKERS)


def registration_retry_delay_seconds(error: Exception | str, attempt: int) -> float:
    """Return bounded backoff for a fresh registration session."""
    text = str(error or "").lower()
    ordinal = max(1, int(attempt or 1))
    if "429" in text or "rate_limit" in text or "rate limit" in text:
        return float(min(15 + 5 * ordinal, 35))
    if (
        "cloudflare" in text
        or "clearance" in text
        or "status=403" in text
        or "http_403" in text
    ):
        return float(min(10 + 5 * ordinal, 30))
    if any(marker in text for marker in _DOMAIN_REJECTION_MARKERS):
        return float(min(3 + 3 * ordinal, 15))
    if any(marker in text for marker in _TLS_MARKERS):
        return float(min(1.5 + 1.5 * ordinal, 8))
    return float(min(2 * ordinal, 8))


def registration_max_attempts(value: object, default: int = 6) -> int:
    """Normalize the per-account registration attempt budget."""
    try:
        attempts = int(value)
    except (TypeError, ValueError):
        attempts = int(default)
    return min(20, max(1, attempts))


def is_tls_transport_error(error: Exception | str) -> bool:
    text = str(error or "").lower()
    return any(marker in text for marker in ("curl: (35)", "tls connect error"))
