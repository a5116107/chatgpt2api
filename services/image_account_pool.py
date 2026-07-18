from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class ImagePoolState:
    READY = "ready"
    PROBATION = "probation"
    COOLDOWN = "cooldown"
    EXHAUSTED = "exhausted"
    QUARANTINED = "quarantined"
    DISABLED = "disabled"

    ALL = {
        READY,
        PROBATION,
        COOLDOWN,
        EXHAUSTED,
        QUARANTINED,
        DISABLED,
    }


class ImagePoolOutcome:
    SUCCESS = "success"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    QUOTA_EXHAUSTED = "quota_exhausted"
    TOKEN_INVALID = "token_invalid"
    POLICY_REJECTED = "policy_rejected"
    NO_IMAGE = "no_image"
    UPSTREAM_ERROR = "upstream_error"


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _bounded_ratio(value: object, default: float = 0.5) -> float:
    return min(1.0, max(0.0, _as_float(value, default)))


def _timestamp(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _iso_time(now_epoch: float) -> str:
    return datetime.fromtimestamp(now_epoch, timezone.utc).isoformat()


def _quota_recheck_at(
    account: dict[str, Any],
    *,
    now_epoch: float,
    fallback_interval_secs: int,
) -> int:
    restore_at = _timestamp(account.get("restore_at"))
    if restore_at is not None and restore_at > now_epoch:
        return int(max(now_epoch + 300, restore_at))
    return int(now_epoch + max(300, fallback_interval_secs))


def is_terminal_image_token(account: dict[str, Any]) -> bool:
    """Return whether persisted evidence proves the token is terminal.

    A manually disabled account and a chat-only ``text_stream:token_revoked``
    signal are intentionally not terminal for the image pool.
    """
    if bool(account.get("token_revoked")):
        return True
    if account.get("token_revoked_at"):
        return True
    if str(account.get("token_status") or "").strip().lower() in {
        "revoked",
        "invalidated",
    }:
        return True
    blob = " ".join(
        str(account.get(key) or "")
        for key in (
            "token_revoked_source",
            "last_refresh_error",
            "last_token_refresh_error",
            "image_last_probe_error",
        )
    ).lower()
    hard_markers = (
        "token invalidated",
        "token_invalidated",
        "invalidated oauth",
        "encountered invalidated oauth token",
        "account_deactivated",
        "refresh_token_invalidated",
        "session has ended",
        "invalid_grant",
        "app_session_terminated",
        "oauth_refresh_http_401",
    )
    if any(marker in blob for marker in hard_markers):
        return True
    without_chat_soft_revoke = blob.replace("text_stream:token_revoked", "")
    return "token_revoked" in without_chat_soft_revoke


def quota_refresh_is_due(
    account: dict[str, Any],
    *,
    now_epoch: float,
    interval_secs: int,
) -> bool:
    """Return whether the remote image quota should be reconciled now."""
    if str(account.get("image_pool_state") or "").strip().lower() == ImagePoolState.EXHAUSTED:
        return True
    updated_at = _timestamp(account.get("image_quota_updated_at"))
    if updated_at is None:
        return True
    return updated_at + max(300, int(interval_secs)) <= now_epoch


def _has_current_generation_success(account: dict[str, Any]) -> bool:
    """Return whether real image traffic last proved this account ready."""
    last_success = _timestamp(account.get("image_last_success_at"))
    if last_success is None:
        return False
    last_failure = _timestamp(account.get("image_last_failure_at"))
    if last_failure is not None and last_failure > last_success:
        return False
    last_outcome = str(account.get("image_last_outcome") or "").strip().lower()
    last_outcome_at = _timestamp(account.get("image_last_outcome_at"))
    return not (
        last_outcome
        and last_outcome != ImagePoolOutcome.SUCCESS
        and last_outcome_at is not None
        and last_outcome_at > last_success
    )


def normalize_image_pool_fields(
    account: dict[str, Any],
    *,
    now_epoch: float,
) -> dict[str, Any]:
    normalized = dict(account or {})
    normalized["image_rate_limit_streak"] = max(
        0, _as_int(normalized.get("image_rate_limit_streak"))
    )
    normalized["image_consecutive_failures"] = max(
        0, _as_int(normalized.get("image_consecutive_failures"))
    )
    normalized["image_health_samples"] = max(
        0, _as_int(normalized.get("image_health_samples"))
    )
    normalized["image_success_ema"] = _bounded_ratio(
        normalized.get("image_success_ema"), 0.5
    )
    normalized["image_latency_ema_ms"] = max(
        0.0, _as_float(normalized.get("image_latency_ema_ms"))
    )

    status = str(normalized.get("status") or "正常").strip()
    state = str(normalized.get("image_pool_state") or "").strip().lower()
    cooldown_until = _timestamp(normalized.get("image_cooldown_until"))
    probation_reason: str | None = None
    if is_terminal_image_token(normalized):
        state = ImagePoolState.QUARANTINED
    elif status == "禁用":
        state = ImagePoolState.DISABLED
    elif status == "限流":
        state = ImagePoolState.EXHAUSTED
    elif state == ImagePoolState.EXHAUSTED and (
        bool(normalized.get("image_quota_unknown"))
        or max(0, _as_int(normalized.get("quota"))) > 0
    ):
        state = ImagePoolState.PROBATION
        probation_reason = "quota_restored_awaiting_generation"
    elif state == ImagePoolState.COOLDOWN and cooldown_until is not None:
        if cooldown_until <= now_epoch:
            state = ImagePoolState.PROBATION
            probation_reason = "cooldown_elapsed_awaiting_generation"
    elif state == ImagePoolState.READY and not _has_current_generation_success(normalized):
        state = ImagePoolState.PROBATION
        probation_reason = "awaiting_generation_success"
    elif state not in ImagePoolState.ALL:
        state = (
            ImagePoolState.READY
            if _has_current_generation_success(normalized)
            else ImagePoolState.PROBATION
        )
        if state == ImagePoolState.PROBATION:
            probation_reason = "awaiting_generation_success"

    normalized["image_pool_state"] = state
    normalized["image_pool_reason"] = (
        probation_reason
        or str(normalized.get("image_pool_reason") or "").strip()
        or None
    )
    if cooldown_until is not None and state == ImagePoolState.COOLDOWN:
        normalized["image_cooldown_until"] = int(cooldown_until)
    elif state != ImagePoolState.COOLDOWN:
        normalized["image_cooldown_until"] = None

    confidence = str(normalized.get("image_quota_confidence") or "").strip().lower()
    if bool(normalized.get("image_quota_unknown")):
        confidence = "unknown"
    elif confidence not in {"unknown", "estimated", "verified"}:
        confidence = "estimated"
    normalized["image_quota_confidence"] = confidence
    normalized["image_quota_updated_at"] = normalized.get("image_quota_updated_at") or None

    next_probe_at = _timestamp(normalized.get("image_next_probe_at"))
    if state in {ImagePoolState.QUARANTINED, ImagePoolState.DISABLED}:
        normalized["image_next_probe_at"] = None
    elif state == ImagePoolState.COOLDOWN and cooldown_until is not None:
        normalized["image_next_probe_at"] = int(cooldown_until)
    elif next_probe_at is not None:
        normalized["image_next_probe_at"] = int(next_probe_at)
    else:
        normalized["image_next_probe_at"] = None
    return normalized


def classify_image_outcome(
    error: object = "",
    *,
    status_code: int | None = None,
    code: object = "",
) -> str:
    text = f"{code or ''} {error or ''}".strip().lower()
    if status_code == 401 or any(
        marker in text
        for marker in (
            "token_revoked",
            "token invalid",
            "token_invalidated",
            "invalidated oauth",
            "account_deactivated",
            "invalid_grant",
        )
    ):
        return ImagePoolOutcome.TOKEN_INVALID
    if status_code == 429 or any(
        marker in text for marker in ("rate limit", "rate_limit", "too many requests")
    ):
        return ImagePoolOutcome.RATE_LIMITED
    if any(
        marker in text
        for marker in (
            "insufficient_quota",
            "quota exhausted",
            "quota_exhausted",
            "no image quota",
        )
    ):
        return ImagePoolOutcome.QUOTA_EXHAUSTED
    if any(
        marker in text
        for marker in (
            "content policy",
            "content_policy",
            "policy violation",
            "policy_rejected",
        )
    ):
        return ImagePoolOutcome.POLICY_REJECTED
    if any(
        marker in text
        for marker in (
            "timeout",
            "timed out",
            "deadline exceeded",
            "deadline_exceeded",
        )
    ):
        return ImagePoolOutcome.TIMEOUT
    if any(marker in text for marker in ("no image", "no_image", "without generating")):
        return ImagePoolOutcome.NO_IMAGE
    return ImagePoolOutcome.UPSTREAM_ERROR


def _record_generation_health(
    account: dict[str, Any],
    *,
    success: bool,
    now_epoch: float,
    duration_ms: int | None,
) -> None:
    samples = max(0, _as_int(account.get("image_health_samples")))
    previous_success = _bounded_ratio(
        account.get("image_success_ema"), 0.5 if samples == 0 else 0.0
    )
    account["image_health_samples"] = samples + 1
    account["image_success_ema"] = round(
        previous_success * 0.8 + (1.0 if success else 0.0) * 0.2,
        6,
    )
    if duration_ms is not None and duration_ms >= 0:
        previous_latency = max(0.0, _as_float(account.get("image_latency_ema_ms")))
        account["image_latency_ema_ms"] = round(
            float(duration_ms)
            if previous_latency <= 0
            else previous_latency * 0.8 + float(duration_ms) * 0.2,
            2,
        )
    account["last_used_at"] = _iso_time(now_epoch)
    if success:
        account["success"] = max(0, _as_int(account.get("success"))) + 1
        account["image_consecutive_failures"] = 0
        account["image_last_success_at"] = _iso_time(now_epoch)
    else:
        account["fail"] = max(0, _as_int(account.get("fail"))) + 1
        account["image_consecutive_failures"] = max(
            0, _as_int(account.get("image_consecutive_failures"))
        ) + 1
        account["image_last_failure_at"] = _iso_time(now_epoch)


def apply_image_outcome(
    account: dict[str, Any],
    outcome: str,
    *,
    now_epoch: float,
    duration_ms: int | None = None,
    error: object = "",
    healthy_interval_secs: int = 1800,
    probation_interval_secs: int = 60,
    rate_limit_base_secs: int = 600,
    timeout_base_secs: int = 90,
    max_cooldown_secs: int = 3600,
    failure_threshold: int = 2,
) -> dict[str, Any]:
    updated = normalize_image_pool_fields(account, now_epoch=now_epoch)
    updated["image_last_outcome"] = str(outcome or ImagePoolOutcome.UPSTREAM_ERROR)
    updated["image_last_outcome_at"] = _iso_time(now_epoch)
    updated["image_last_outcome_error"] = str(error or "")[:240] or None

    if outcome == ImagePoolOutcome.POLICY_REJECTED:
        return updated

    success = outcome == ImagePoolOutcome.SUCCESS
    _record_generation_health(
        updated,
        success=success,
        now_epoch=now_epoch,
        duration_ms=duration_ms,
    )

    if success:
        updated["image_rate_limit_streak"] = 0
        updated["image_pool_state"] = ImagePoolState.READY
        updated["image_pool_reason"] = None
        updated["image_cooldown_until"] = None
        updated["image_next_probe_at"] = int(now_epoch + max(60, healthy_interval_secs))
        if not bool(updated.get("image_quota_unknown")):
            updated["quota"] = max(0, _as_int(updated.get("quota")) - 1)
            updated["image_quota_confidence"] = "estimated"
            updated["image_quota_updated_at"] = _iso_time(now_epoch)
            if updated["quota"] == 0:
                updated["status"] = "限流"
                updated["image_pool_state"] = ImagePoolState.EXHAUSTED
                updated["image_pool_reason"] = "local_quota_consumed"
                updated["image_next_probe_at"] = _quota_recheck_at(
                    updated,
                    now_epoch=now_epoch,
                    fallback_interval_secs=healthy_interval_secs,
                )
        elif str(updated.get("status") or "").strip() == "限流":
            updated["status"] = "正常"
        return updated

    if outcome == ImagePoolOutcome.TOKEN_INVALID:
        updated["status"] = "禁用"
        updated["quota"] = 0
        updated["token_status"] = "revoked"
        updated["token_revoked"] = True
        updated["token_revoked_at"] = updated.get("token_revoked_at") or _iso_time(now_epoch)
        updated["token_revoked_source"] = str(error or "image_runtime")[:200]
        updated["image_pool_state"] = ImagePoolState.QUARANTINED
        updated["image_pool_reason"] = "token_invalid"
        updated["image_cooldown_until"] = None
        updated["image_next_probe_at"] = None
        return updated

    if outcome == ImagePoolOutcome.QUOTA_EXHAUSTED:
        updated["status"] = "限流"
        updated["quota"] = 0
        updated["image_pool_state"] = ImagePoolState.EXHAUSTED
        updated["image_pool_reason"] = "quota_exhausted"
        updated["image_quota_confidence"] = "verified"
        updated["image_quota_updated_at"] = _iso_time(now_epoch)
        updated["image_cooldown_until"] = None
        updated["image_next_probe_at"] = _quota_recheck_at(
            updated,
            now_epoch=now_epoch,
            fallback_interval_secs=healthy_interval_secs,
        )
        return updated

    if outcome == ImagePoolOutcome.RATE_LIMITED:
        streak = max(0, _as_int(updated.get("image_rate_limit_streak"))) + 1
        cooldown = min(
            max(1, max_cooldown_secs),
            max(1, rate_limit_base_secs) * (2 ** min(streak - 1, 8)),
        )
        updated["image_rate_limit_streak"] = streak
        updated["image_pool_state"] = ImagePoolState.COOLDOWN
        updated["image_pool_reason"] = "rate_limited"
        updated["image_cooldown_until"] = int(now_epoch + cooldown)
        updated["image_next_probe_at"] = int(now_epoch + cooldown)
        return updated

    failures = max(0, _as_int(updated.get("image_consecutive_failures")))
    threshold = max(1, failure_threshold)
    if failures >= threshold:
        exponent = min(max(0, failures - threshold), 8)
        cooldown = min(
            max(1, max_cooldown_secs),
            max(1, timeout_base_secs) * (2 ** exponent),
        )
        updated["image_pool_state"] = ImagePoolState.COOLDOWN
        updated["image_pool_reason"] = str(outcome or ImagePoolOutcome.UPSTREAM_ERROR)
        updated["image_cooldown_until"] = int(now_epoch + cooldown)
        updated["image_next_probe_at"] = int(now_epoch + cooldown)
    else:
        updated["image_pool_state"] = ImagePoolState.PROBATION
        updated["image_pool_reason"] = str(outcome or ImagePoolOutcome.UPSTREAM_ERROR)
        updated["image_cooldown_until"] = None
        updated["image_next_probe_at"] = int(
            now_epoch + max(1, probation_interval_secs)
        )
    return updated


def apply_probe_result(
    account: dict[str, Any],
    *,
    success: bool,
    now_epoch: float,
    duration_ms: int,
    error: object = "",
    outcome: str | None = None,
    healthy_interval_secs: int = 1800,
    probation_interval_secs: int = 60,
    rate_limit_base_secs: int = 600,
    max_cooldown_secs: int = 3600,
) -> dict[str, Any]:
    updated = normalize_image_pool_fields(account, now_epoch=now_epoch)
    samples = max(0, _as_int(updated.get("image_probe_samples")))
    previous_success = _bounded_ratio(
        updated.get("image_probe_success_ema"), 0.5 if samples == 0 else 0.0
    )
    previous_latency = max(0.0, _as_float(updated.get("image_probe_latency_ema_ms")))
    updated["image_probe_samples"] = samples + 1
    updated["image_probe_success_ema"] = round(
        previous_success * 0.8 + (1.0 if success else 0.0) * 0.2,
        6,
    )
    updated["image_probe_latency_ema_ms"] = round(
        float(duration_ms)
        if previous_latency <= 0
        else previous_latency * 0.8 + float(duration_ms) * 0.2,
        2,
    )
    updated["image_last_probe_at"] = _iso_time(now_epoch)
    updated["image_last_probe_error"] = None if success else str(error or "probe failed")[:240]

    if success:
        if updated["image_pool_state"] == ImagePoolState.EXHAUSTED:
            updated["image_next_probe_at"] = _quota_recheck_at(
                updated,
                now_epoch=now_epoch,
                fallback_interval_secs=healthy_interval_secs,
            )
        elif updated["image_pool_state"] not in {
            ImagePoolState.QUARANTINED,
            ImagePoolState.DISABLED,
        }:
            generation_ready = _has_current_generation_success(updated)
            updated["image_pool_state"] = (
                ImagePoolState.READY
                if generation_ready
                else ImagePoolState.PROBATION
            )
            if generation_ready:
                updated["image_pool_reason"] = None
                updated["image_rate_limit_streak"] = 0
            else:
                updated["image_pool_reason"] = (
                    str(updated.get("image_pool_reason") or "").strip()
                    or "probe_passed_awaiting_generation"
                )
            updated["image_cooldown_until"] = None
            updated["image_next_probe_at"] = int(
                now_epoch + max(60, healthy_interval_secs)
            )
        return updated

    resolved_outcome = outcome or classify_image_outcome(error)
    if resolved_outcome == ImagePoolOutcome.TOKEN_INVALID:
        updated["status"] = "禁用"
        updated["quota"] = 0
        updated["token_status"] = "revoked"
        updated["token_revoked"] = True
        updated["token_revoked_at"] = updated.get("token_revoked_at") or _iso_time(now_epoch)
        updated["token_revoked_source"] = str(error or "image_probe")[:200]
        updated["image_pool_state"] = ImagePoolState.QUARANTINED
        updated["image_pool_reason"] = "token_invalid"
        updated["image_cooldown_until"] = None
        updated["image_next_probe_at"] = None
    elif resolved_outcome == ImagePoolOutcome.QUOTA_EXHAUSTED:
        updated["status"] = "限流"
        updated["quota"] = 0
        updated["image_pool_state"] = ImagePoolState.EXHAUSTED
        updated["image_pool_reason"] = "quota_exhausted"
        updated["image_quota_confidence"] = "verified"
        updated["image_quota_updated_at"] = _iso_time(now_epoch)
        updated["image_next_probe_at"] = _quota_recheck_at(
            updated,
            now_epoch=now_epoch,
            fallback_interval_secs=healthy_interval_secs,
        )
    elif resolved_outcome == ImagePoolOutcome.RATE_LIMITED:
        streak = max(0, _as_int(updated.get("image_rate_limit_streak"))) + 1
        cooldown = min(
            max(1, max_cooldown_secs),
            max(1, rate_limit_base_secs) * (2 ** min(streak - 1, 8)),
        )
        updated["image_rate_limit_streak"] = streak
        updated["image_pool_state"] = ImagePoolState.COOLDOWN
        updated["image_pool_reason"] = "rate_limited"
        updated["image_cooldown_until"] = int(now_epoch + cooldown)
        updated["image_next_probe_at"] = int(now_epoch + cooldown)
    else:
        updated["image_pool_state"] = ImagePoolState.PROBATION
        updated["image_pool_reason"] = resolved_outcome
        updated["image_cooldown_until"] = None
        updated["image_next_probe_at"] = int(
            now_epoch + max(1, probation_interval_secs)
        )
    return updated


def is_image_pool_schedulable(
    account: dict[str, Any],
    *,
    now_epoch: float,
) -> bool:
    normalized = normalize_image_pool_fields(account, now_epoch=now_epoch)
    return normalized["image_pool_state"] in {
        ImagePoolState.READY,
        ImagePoolState.PROBATION,
    }


def probe_is_due(account: dict[str, Any], *, now_epoch: float) -> bool:
    normalized = normalize_image_pool_fields(account, now_epoch=now_epoch)
    if normalized["image_pool_state"] in {
        ImagePoolState.QUARANTINED,
        ImagePoolState.DISABLED,
    }:
        return False
    next_probe_at = _timestamp(normalized.get("image_next_probe_at"))
    return next_probe_at is None or next_probe_at <= now_epoch


def image_pool_score(account: dict[str, Any], *, inflight: int) -> tuple[Any, ...]:
    state = str(account.get("image_pool_state") or "").strip().lower()
    if state not in ImagePoolState.ALL:
        state = (
            ImagePoolState.READY
            if _has_current_generation_success(account)
            else ImagePoolState.PROBATION
        )
    state_rank = 0 if state == ImagePoolState.READY else 1
    success_ema = _bounded_ratio(account.get("image_success_ema"), 0.5)
    latency_ms = max(0.0, _as_float(account.get("image_latency_ema_ms"), 60_000.0))
    if latency_ms <= 0:
        latency_ms = 60_000.0
    failures = max(0, _as_int(account.get("image_consecutive_failures")))
    probe_error = 1 if str(account.get("image_last_probe_error") or "").strip() else 0
    quota = max(0, _as_int(account.get("quota")))
    return (
        state_rank,
        max(0, int(inflight)),
        probe_error,
        min(failures, 10),
        1.0 - success_ema,
        latency_ms,
        -quota,
    )
