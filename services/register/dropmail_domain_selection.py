from __future__ import annotations

import random
from typing import Any

from services.register.random_mail_domain_health import (
    random_mail_domain_family,
    random_mail_domain_is_cooling,
    random_mail_domain_priority,
    select_random_mail_domain_half_open,
)


def select_dropmail_domains(
    domains: list[dict[str, Any]],
    *,
    provider: str,
    provider_ref: str,
    denied_domains: set[str],
    family_labels: int,
    shuffle: Any = random.shuffle,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    shuffled_domains = list(domains)
    shuffle(shuffled_domains)
    eligible, cooling, skipped = _partition_domains(
        shuffled_domains,
        provider=provider,
        provider_ref=provider_ref,
        denied_domains=denied_domains,
        family_labels=family_labels,
    )
    eligible.sort(
        key=lambda entry: random_mail_domain_priority(
            provider,
            provider_ref,
            random_mail_domain_family(_domain_name(entry), family_labels),
        ),
        reverse=True,
    )
    if eligible or not cooling:
        return eligible, skipped, False
    return _half_open_selection(
        cooling,
        skipped,
        provider=provider,
        provider_ref=provider_ref,
        family_labels=family_labels,
    )


def _partition_domains(
    domains: list[dict[str, Any]],
    *,
    provider: str,
    provider_ref: str,
    denied_domains: set[str],
    family_labels: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    eligible: list[dict[str, Any]] = []
    cooling: list[dict[str, Any]] = []
    skipped: list[str] = []
    for domain_entry in domains:
        domain = _domain_name(domain_entry)
        family = random_mail_domain_family(domain, family_labels)
        if domain in denied_domains or family in denied_domains:
            skipped.append(domain)
        elif random_mail_domain_is_cooling(provider, provider_ref, family):
            skipped.append(domain)
            cooling.append(domain_entry)
        else:
            eligible.append(domain_entry)
    return eligible, cooling, skipped


def _half_open_selection(
    cooling_domains: list[dict[str, Any]],
    skipped_domains: list[str],
    *,
    provider: str,
    provider_ref: str,
    family_labels: int,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    selected_domain = select_random_mail_domain_half_open(
        provider,
        provider_ref,
        [_domain_name(entry) for entry in cooling_domains],
        family_labels,
    )
    selected_entries = [
        entry for entry in cooling_domains if _domain_name(entry) == selected_domain
    ][:1]
    if not selected_entries:
        return [], skipped_domains, False
    selected_family = random_mail_domain_family(selected_domain, family_labels)
    remaining_skipped = [
        domain
        for domain in dict.fromkeys(skipped_domains)
        if random_mail_domain_family(domain, family_labels) != selected_family
    ]
    return selected_entries, remaining_skipped, True


def _domain_name(domain_entry: dict[str, Any]) -> str:
    return str(domain_entry.get("name") or "").strip().lower().lstrip("@")
