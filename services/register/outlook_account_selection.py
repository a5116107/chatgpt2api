from __future__ import annotations

from typing import Any, Callable


def eligible_outlook_accounts(
    accounts: list[dict[str, Any]],
    used_addresses: set[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    active_accounts = [
        account
        for account in accounts
        if str(account.get("status") or "active").strip().lower()
        in {"active", "ok", "normal", ""}
    ]
    account_pool = active_accounts
    normalized_used = {
        str(address or "").strip().lower()
        for address in used_addresses
        if str(address or "").strip()
    }

    healthy_accounts = _accounts_with_refresh_state(account_pool, {"success"})
    unverified_accounts = _accounts_without_refresh_state(
        account_pool,
        {"success", "failed", "error", "invalid"},
    )
    healthy_accounts.sort(key=_refresh_sort_key, reverse=True)
    unverified_accounts.sort(key=_refresh_sort_key, reverse=True)

    candidates = [
        account
        for account in [*healthy_accounts, *unverified_accounts]
        if (email := str(account.get("email") or "").strip().lower())
        and email not in normalized_used
    ]
    return candidates, {
        "pool": len(account_pool),
        "healthy": len(healthy_accounts),
        "unverified": len(unverified_accounts),
        "used": len(normalized_used),
    }


def rotate_outlook_accounts(
    accounts: list[dict[str, Any]],
    offset: int,
) -> list[dict[str, Any]]:
    if not accounts:
        return []
    start = max(0, int(offset)) % len(accounts)
    return [*accounts[start:], *accounts[:start]]


def first_readable_outlook_account(
    candidates: list[dict[str, Any]],
    attempts: int,
    probe: Callable[[str], None],
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    for candidate in candidates[: max(1, int(attempts))]:
        email = str(candidate.get("email") or "").strip()
        if not email:
            continue
        try:
            probe(email)
        except Exception as exc:  # noqa: BLE001 - provider failures are recorded for failover.
            errors.append(str(exc)[:160])
            continue
        return candidate, errors
    return None, errors


def _accounts_with_refresh_state(
    accounts: list[dict[str, Any]],
    accepted_states: set[str],
) -> list[dict[str, Any]]:
    return [
        account
        for account in accounts
        if str(account.get("last_refresh_status") or "").strip().lower()
        in accepted_states
    ]


def _accounts_without_refresh_state(
    accounts: list[dict[str, Any]],
    excluded_states: set[str],
) -> list[dict[str, Any]]:
    return [
        account
        for account in accounts
        if str(account.get("last_refresh_status") or "").strip().lower()
        not in excluded_states
    ]


def _refresh_sort_key(account: dict[str, Any]) -> str:
    return str(account.get("last_refresh_at") or account.get("updated_at") or "")
