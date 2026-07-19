from __future__ import annotations


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
