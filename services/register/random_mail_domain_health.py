from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.config import DATA_DIR
from services.register import random_mail_domain_health_store as health_store


RANDOM_MAIL_DOMAIN_HEALTH_FILE = DATA_DIR / "random_mail_domain_health.json"
RANDOM_MAIL_DOMAIN_REJECT_COOLDOWN_SECONDS = 2 * 3600
RANDOM_MAIL_DOMAIN_ATTEMPTS = 8
RANDOM_MAIL_DOMAIN_HALF_OPEN_MIN_FAMILIES = 2
GENERIC_DOMAIN_FAILURE_THRESHOLD = 2
RANDOM_MAIL_DOMAIN_PERMANENT_FAILURE_THRESHOLD = 3
RANDOM_MAIL_DOMAIN_PERMANENT_STRONG_FAILURE_THRESHOLD = 2
RANDOM_MAIL_PROVIDER_UNPROVEN_FAILURE_THRESHOLD = 3

_STRONG_DOMAIN_FAILURE_REASONS = {
    "domain_abuse_suspected",
    "registration_disallowed",
    "unsupported_email",
}
_GENERIC_DOMAIN_FAILURE_REASONS = {"account_creation_failed"}

_health_file_lock = health_store.health_file_lock
_active_failure_entries = health_store.active_failure_entries
_domain_families_for_scope = health_store.domain_families_for_scope
_failure_count_for_reasons = health_store.failure_count_for_reasons
_failures_after_success = health_store.failures_after_success
_has_success = health_store.has_success
_latest_success_at = health_store.latest_success_at
_load_health_entries = health_store.load_health_entries
_matching_health_entries = health_store.matching_health_entries
_next_health_event_time = health_store.next_health_event_time
_save_health_entries = health_store.save_health_entries


def bounded_provider_integer(value: object, default: int, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(parsed, upper))


def random_mail_domain_family(domain: str, labels: int = 2) -> str:
    parts = [part for part in str(domain or "").strip().lower().strip(".").split(".") if part]
    if not parts:
        return ""
    family_size = bounded_provider_integer(labels, 2, 2, 4)
    return ".".join(parts[-family_size:])


def random_mail_domain_is_cooling(provider: str, provider_ref: str, domain_family: str) -> bool:
    normalized_provider = str(provider or "").strip().lower()
    normalized_family = str(domain_family or "").strip().lower()
    if not normalized_family:
        return False

    now = datetime.now(timezone.utc)
    with _health_file_lock:
        matching_entries = _matching_health_entries(
            RANDOM_MAIL_DOMAIN_HEALTH_FILE,
            provider=normalized_provider,
            provider_ref=provider_ref,
            domain_family=normalized_family,
        )

    if random_mail_domain_is_permanently_rejected(
        normalized_provider,
        provider_ref,
        normalized_family,
        matching_entries=matching_entries,
    ):
        return True

    active_failures = _active_failure_entries(matching_entries, now)
    if not active_failures:
        return False

    latest_success = _latest_success_at(matching_entries)
    relevant_failures = _failures_after_success(active_failures, latest_success)
    if not relevant_failures:
        return False
    if any(
        str(health_entry.get("last_error_reason") or "") in _STRONG_DOMAIN_FAILURE_REASONS
        for health_entry in relevant_failures
    ):
        return True

    generic_failures = _failure_count_for_reasons(
        relevant_failures,
        _GENERIC_DOMAIN_FAILURE_REASONS,
    )
    has_prior_success = _has_success(matching_entries)
    if generic_failures:
        return not has_prior_success or generic_failures >= GENERIC_DOMAIN_FAILURE_THRESHOLD

    # Preserve conservative handling for health files written by older versions.
    return True


def random_mail_domain_has_success(
    provider: str,
    provider_ref: str,
    domain_family: str,
) -> bool:
    """Return whether a domain family has ever completed registration."""
    normalized_provider = str(provider or "").strip().lower()
    normalized_family = str(domain_family or "").strip().lower()
    if not normalized_provider or not normalized_family:
        return False
    with _health_file_lock:
        matching_entries = _matching_health_entries(
            RANDOM_MAIL_DOMAIN_HEALTH_FILE,
            provider=normalized_provider,
            provider_ref=provider_ref,
            domain_family=normalized_family,
        )
    return _has_success(matching_entries)


def random_mail_provider_health_state(
    provider: str,
    provider_ref: str,
    *,
    unproven_failure_threshold: int = RANDOM_MAIL_PROVIDER_UNPROVEN_FAILURE_THRESHOLD,
) -> str:
    """Classify a random-domain provider without exposing individual domains.

    This is intentionally provider-scoped: domain circuit breakers still decide
    whether a specific family may be used, while routing can prefer providers
    that have demonstrated at least one successful registration.
    """
    normalized_provider = str(provider or "").strip().lower()
    if not normalized_provider:
        return "unknown"
    threshold = bounded_provider_integer(unproven_failure_threshold, 3, 1, 100)
    with _health_file_lock:
        entries = _matching_health_entries(
            RANDOM_MAIL_DOMAIN_HEALTH_FILE,
            provider=normalized_provider,
            provider_ref=provider_ref,
        )
    success_count = sum(int(entry.get("success_count") or 0) for entry in entries)
    failure_count = sum(int(entry.get("failure_count") or 0) for entry in entries)
    if success_count:
        return "proven"
    if failure_count >= threshold:
        return "unproven_failed"
    if failure_count:
        return "unproven"
    return "unknown"


def random_mail_domain_is_permanently_rejected(
    provider: str,
    provider_ref: str,
    domain_family: str,
    *,
    matching_entries: list[dict[str, Any]] | None = None,
) -> bool:
    """Keep a never-successful domain family out of rotation after repeated rejects.

    A cooldown is useful for transient upstream failures, but it is not enough for
    a domain family that has repeatedly been rejected and has never produced a
    successful registration. Strong rejection signals reach quarantine sooner;
    generic account-creation failures require a few independent observations.
    """
    normalized_provider = str(provider or "").strip().lower()
    normalized_family = str(domain_family or "").strip().lower()
    if not normalized_family:
        return False
    if matching_entries is None:
        with _health_file_lock:
            matching_entries = _matching_health_entries(
                RANDOM_MAIL_DOMAIN_HEALTH_FILE,
                provider=normalized_provider,
                provider_ref=provider_ref,
                domain_family=normalized_family,
            )
    if not matching_entries or _has_success(matching_entries):
        return False

    latest_success = _latest_success_at(matching_entries)
    relevant_failures = _failures_after_success(matching_entries, latest_success)
    strong_failures = _failure_count_for_reasons(
        relevant_failures,
        _STRONG_DOMAIN_FAILURE_REASONS,
    )
    generic_failures = _failure_count_for_reasons(
        relevant_failures,
        _GENERIC_DOMAIN_FAILURE_REASONS,
    )
    return (
        strong_failures >= RANDOM_MAIL_DOMAIN_PERMANENT_STRONG_FAILURE_THRESHOLD
        or generic_failures >= RANDOM_MAIL_DOMAIN_PERMANENT_FAILURE_THRESHOLD
    )


def random_mail_domain_priority(
    provider: str,
    provider_ref: str,
    domain_family: str,
) -> tuple[int, int, int]:
    """Rank a domain by prior results while keeping unseen domains equivalent.

    Auto-generated provider refs include the provider's list position. The position is
    intentionally removed from the lookup scope so config reordering does not discard
    domain health history.
    """
    normalized_provider = str(provider or "").strip().lower()
    normalized_family = str(domain_family or "").strip().lower()
    if not normalized_family:
        return (0, 0, 0)

    success_count = 0
    failure_count = 0
    consecutive_failures = 0
    with _health_file_lock:
        matching_entries = _matching_health_entries(
            RANDOM_MAIL_DOMAIN_HEALTH_FILE,
            provider=normalized_provider,
            provider_ref=provider_ref,
            domain_family=normalized_family,
        )
        for health_entry in matching_entries:
            success_count += int(health_entry.get("success_count") or 0)
            failure_count += int(health_entry.get("failure_count") or 0)
            consecutive_failures += int(health_entry.get("consecutive_failures") or 0)
    return (
        1 if success_count else 0,
        success_count - failure_count,
        -consecutive_failures,
    )


def select_random_mail_domain_half_open(
    provider: str,
    provider_ref: str,
    domains: list[str],
    family_labels: int = 2,
) -> str:
    """Select one cooling domain for a circuit-breaker half-open probe.

    This is only used after a provider confirms that every currently available
    random domain is cooling. It preserves liveness without clearing health
    history or reopening the whole rejected domain set.
    """
    candidates = list(
        dict.fromkeys(
            str(domain or "").strip().lower().lstrip("@")
            for domain in domains
            if str(domain or "").strip()
        )
    )
    if not candidates:
        return ""
    return max(
        candidates,
        key=lambda domain: random_mail_domain_priority(
            provider,
            provider_ref,
            random_mail_domain_family(domain, family_labels),
        ),
    )


def random_mail_domain_cooling_families(
    provider: str,
    provider_ref: str,
) -> list[str]:
    """Return active cooling families for a provider's stable health scope."""
    normalized_provider = str(provider or "").strip().lower()
    with _health_file_lock:
        families = _domain_families_for_scope(
            RANDOM_MAIL_DOMAIN_HEALTH_FILE,
            provider=normalized_provider,
            provider_ref=provider_ref,
        )
    return [
        family
        for family in sorted(families)
        if random_mail_domain_is_cooling(
            normalized_provider,
            provider_ref,
            family,
        )
    ]


def record_random_mailbox_result(
    mailbox: dict[str, Any],
    *,
    success: bool,
    error: Exception | str | None = None,
) -> None:
    address_domain = str(mailbox.get("address") or "").strip().lower().partition("@")[2]
    family_labels = bounded_provider_integer(mailbox.get("domain_family_labels"), 2, 2, 4)
    domain_family = str(
        mailbox.get("domain_family") or random_mail_domain_family(address_domain, family_labels)
    ).strip().lower()
    if not domain_family:
        return

    penalty_reason = registration_domain_penalty_reason(error)
    if not success and not penalty_reason:
        return

    provider = str(mailbox.get("provider") or "").strip().lower()
    provider_ref = str(mailbox.get("provider_ref") or "").strip()
    health_key = _random_mail_domain_key(provider, provider_ref, domain_family)
    cooldown_seconds = bounded_provider_integer(
        mailbox.get("domain_cooldown_seconds"),
        RANDOM_MAIL_DOMAIN_REJECT_COOLDOWN_SECONDS,
        300,
        24 * 3600,
    )

    with _health_file_lock:
        health_entries = _load_health_entries(RANDOM_MAIL_DOMAIN_HEALTH_FILE)
        # Windows can return the same wall-clock microsecond for two adjacent
        # provider results. Keep event ordering strict so a later rejection cannot
        # be hidden by an equal-timestamp success from another provider slot.
        now = _next_health_event_time(health_entries)
        health_entry = dict(health_entries.get(health_key) or {})
        health_entry.update(
            {
                "provider": provider,
                "provider_ref": provider_ref,
                "domain_family": domain_family,
                "last_domain": address_domain,
                "updated_at": now.isoformat(),
            }
        )
        if success:
            health_entry["success_count"] = int(health_entry.get("success_count") or 0) + 1
            health_entry["consecutive_failures"] = 0
            health_entry["last_success_at"] = now.isoformat()
            health_entry["cooldown_until"] = ""
            health_entry["last_error"] = ""
            health_entry["last_error_reason"] = ""
        else:
            health_entry["failure_count"] = int(health_entry.get("failure_count") or 0) + 1
            health_entry["consecutive_failures"] = int(
                health_entry.get("consecutive_failures") or 0
            ) + 1
            health_entry["last_failure_at"] = now.isoformat()
            health_entry["last_error"] = str(error or "")[:500]
            health_entry["last_error_reason"] = penalty_reason
            health_entry["cooldown_until"] = datetime.fromtimestamp(
                now.timestamp() + cooldown_seconds,
                tz=timezone.utc,
            ).isoformat()
        health_entries[health_key] = health_entry
        _save_health_entries(RANDOM_MAIL_DOMAIN_HEALTH_FILE, health_entries)


def random_mailbox_metadata(
    *,
    provider: str,
    provider_ref: str,
    address: str,
    family_labels: int,
    cooldown_seconds: int,
    skipped_domains: list[str] | None = None,
) -> dict[str, Any]:
    address_domain = str(address or "").strip().lower().partition("@")[2]
    return {
        "provider": provider,
        "provider_ref": provider_ref,
        "address": address,
        "random_domain": True,
        "domain": address_domain,
        "domain_family": random_mail_domain_family(address_domain, family_labels),
        "domain_family_labels": family_labels,
        "domain_cooldown_seconds": cooldown_seconds,
        "domain_health_skipped": list(skipped_domains or []),
        "label": f"{provider}:{address_domain}" if address_domain else provider,
    }


def registration_domain_penalty_reason(error: Exception | str | None) -> str:
    error_text = str(error or "").strip().lower()
    if not error_text:
        return ""
    markers = (
        ("unsupported_email", "unsupported_email"),
        ("email you provided is not supported", "unsupported_email"),
        ("account_creation_failed", "account_creation_failed"),
        ("failed to create account", "account_creation_failed"),
        ("registration_disallowed", "registration_disallowed"),
        ("邮箱域名很可能因滥用被封禁", "domain_abuse_suspected"),
    )
    for marker, reason in markers:
        if marker in error_text:
            return reason
    return ""


def _random_mail_domain_key(provider: str, provider_ref: str, domain_family: str) -> str:
    return "|".join(
        (
            str(provider or "").strip().lower(),
            str(provider_ref or "").strip().lower(),
            str(domain_family or "").strip().lower(),
        )
    )
