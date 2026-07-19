from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any


health_file_lock = Lock()


def provider_scope(provider: str, provider_ref: object) -> str:
    normalized_provider = str(provider or "").strip().lower()
    normalized_ref = str(provider_ref or "").strip().lower()
    prefix, separator, suffix = normalized_ref.rpartition("#")
    if separator and suffix.isdigit() and prefix == normalized_provider:
        return normalized_provider
    return normalized_ref or normalized_provider


def domain_families_overlap(recorded_family: str, requested_family: str) -> bool:
    if not recorded_family or not requested_family:
        return False
    return (
        recorded_family == requested_family
        or requested_family.endswith(f".{recorded_family}")
        or recorded_family.endswith(f".{requested_family}")
    )


def parse_health_timestamp(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    timestamp = str(value or "").strip()
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def next_health_event_time(health_entries: dict[str, dict[str, Any]]) -> datetime:
    now = datetime.now(timezone.utc)
    recorded_times = [
        parsed
        for entry in health_entries.values()
        for field in ("updated_at", "last_success_at", "last_failure_at")
        if (parsed := parse_health_timestamp(entry.get(field))) is not None
    ]
    latest = max(recorded_times, default=None)
    return latest + timedelta(microseconds=1) if latest is not None and now <= latest else now


def load_health_entries(path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_entries = payload.get("items", payload)
        return {str(key): dict(value) for key, value in raw_entries.items()}
    except (FileNotFoundError, OSError, json.JSONDecodeError, AttributeError, TypeError, ValueError):
        return {}


def matching_health_entries(
    path: Path,
    *,
    provider: str,
    provider_ref: str,
    domain_family: str = "",
) -> list[dict[str, Any]]:
    normalized_provider = str(provider or "").strip().lower()
    normalized_scope = provider_scope(normalized_provider, provider_ref)
    normalized_family = str(domain_family or "").strip().lower()
    matching_entries: list[dict[str, Any]] = []
    for health_entry in load_health_entries(path).values():
        if str(health_entry.get("provider") or "").strip().lower() != normalized_provider:
            continue
        if provider_scope(normalized_provider, health_entry.get("provider_ref")) != normalized_scope:
            continue
        recorded_family = str(health_entry.get("domain_family") or "").strip().lower()
        if normalized_family and not domain_families_overlap(recorded_family, normalized_family):
            continue
        matching_entries.append(health_entry)
    return matching_entries


def active_failure_entries(
    health_entries: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    return [
        health_entry
        for health_entry in health_entries
        if (parse_health_timestamp(health_entry.get("cooldown_until")) or _EPOCH) > now
    ]


def latest_success_at(health_entries: list[dict[str, Any]]) -> datetime | None:
    return max(
        (
            success_at
            for health_entry in health_entries
            if (success_at := parse_health_timestamp(health_entry.get("last_success_at")))
        ),
        default=None,
    )


def failures_after_success(
    health_entries: list[dict[str, Any]],
    latest_success: datetime | None,
) -> list[dict[str, Any]]:
    return [
        health_entry
        for health_entry in health_entries
        if latest_success is None
        or (parse_health_timestamp(health_entry.get("last_failure_at")) or _EPOCH)
        > latest_success
    ]


def failure_count_for_reasons(
    health_entries: list[dict[str, Any]],
    reasons: set[str],
) -> int:
    return sum(
        max(1, int(health_entry.get("consecutive_failures") or 0))
        for health_entry in health_entries
        if str(health_entry.get("last_error_reason") or "") in reasons
    )


def has_success(health_entries: list[dict[str, Any]]) -> bool:
    return any(int(health_entry.get("success_count") or 0) > 0 for health_entry in health_entries)


def domain_families_for_scope(
    path: Path,
    *,
    provider: str,
    provider_ref: str,
) -> list[str]:
    return sorted(
        {
            str(health_entry.get("domain_family") or "").strip().lower()
            for health_entry in matching_health_entries(
                path,
                provider=provider,
                provider_ref=provider_ref,
            )
            if str(health_entry.get("domain_family") or "").strip()
        }
    )


def save_health_entries(path: Path, health_entries: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items": {key: health_entries[key] for key in sorted(health_entries)},
    }
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


_EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)
