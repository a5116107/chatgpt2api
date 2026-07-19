from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from typing import Any

from services.config import DATA_DIR
from services.register.dropmail_domain_selection import select_dropmail_domains
from services.register.dropmail_token_store import get_dropmail_token
from services.register.mail_provider import (
    BaseMailProvider,
    _create_session,
    _message_matches_email,
    _parse_received_at,
)
from services.register.random_mail_domain_health import (
    RANDOM_MAIL_DOMAIN_ATTEMPTS,
    RANDOM_MAIL_DOMAIN_REJECT_COOLDOWN_SECONDS,
    bounded_provider_integer,
    random_mail_domain_family,
    random_mailbox_metadata,
)


DROPMAIL_TOKEN_CACHE_FILE = DATA_DIR / "dropmail_token_cache.json"

class DropMailProvider(BaseMailProvider):
    name = "dropmail"
    default_api_base = "https://dropmail.me"

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.api_base = str(entry.get("api_base") or self.default_api_base).strip().rstrip("/")
        self.token_lifetime = str(entry.get("token_lifetime") or "1d").strip() or "1d"
        self.has_provider_fallback = bool(entry.get("_has_provider_fallback"))
        self.random_domain_attempts = bounded_provider_integer(
            entry.get("random_domain_attempts"),
            RANDOM_MAIL_DOMAIN_ATTEMPTS,
            1,
            20,
        )
        self.domain_family_labels = bounded_provider_integer(
            entry.get("domain_family_labels"),
            2,
            2,
            4,
        )
        self.domain_cooldown_seconds = bounded_provider_integer(
            entry.get("domain_cooldown_seconds"),
            RANDOM_MAIL_DOMAIN_REJECT_COOLDOWN_SECONDS,
            300,
            24 * 3600,
        )
        self.denied_domains = {
            str(denied_domain or "").strip().lower().lstrip("@")
            for denied_domain in (entry.get("_denied_domains") or [])
            if str(denied_domain or "").strip()
        }
        self.session = _create_session(conf)
        self.session.headers.update(
            {
                "User-Agent": conf["user_agent"],
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        del username  # DropMail allocates the local part server-side.
        api_token = self._generate_token()
        domain_response = self._graphql(
            api_token,
            "query { domains { id name availableVia expiresAt } }",
        )
        current_domains = [
            dict(domain_entry)
            for domain_entry in (domain_response.get("domains") or [])
            if self._domain_is_current(domain_entry)
        ]
        eligible_domains, skipped_domains, half_open = select_dropmail_domains(
            current_domains,
            provider=self.name,
            provider_ref=self.provider_ref,
            denied_domains=self.denied_domains,
            family_labels=self.domain_family_labels,
            shuffle=random.shuffle,
        )

        if half_open and self.has_provider_fallback:
            cooling_domains = [
                *skipped_domains,
                *(str(entry.get("name") or "") for entry in eligible_domains),
            ]
            cooling_families = sorted(
                {
                    random_mail_domain_family(domain, self.domain_family_labels)
                    for domain in cooling_domains
                    if str(domain or "").strip()
                }
            )
            raise RuntimeError(
                f"DropMail 随机域名均处于冷却: {','.join(cooling_families)}"
            )

        if not eligible_domains:
            skipped_families = sorted(
                {
                    random_mail_domain_family(domain, self.domain_family_labels)
                    for domain in skipped_domains
                }
            )
            detail = ",".join(skipped_families) if skipped_families else "no dynamic domains returned"
            raise RuntimeError(f"DropMail 没有可用随机域名: {detail}")

        creation_errors: list[str] = []
        for domain_entry in eligible_domains[: self.random_domain_attempts]:
            domain = str(domain_entry.get("name") or "").strip().lower()
            try:
                mailbox = self._introduce_session(api_token, domain_entry)
            except Exception as exc:
                creation_errors.append(f"{domain}: {str(exc)[:180]}")
                continue
            return {
                **random_mailbox_metadata(
                    provider=self.name,
                    provider_ref=self.provider_ref,
                    address=mailbox["address"],
                    family_labels=self.domain_family_labels,
                    cooldown_seconds=self.domain_cooldown_seconds,
                    skipped_domains=skipped_domains,
                ),
                "api_base": self.api_base,
                "api_token": api_token,
                "session_id": mailbox["session_id"],
                "domain_health_half_open": half_open,
            }
        raise RuntimeError(f"DropMail 随机域名建箱失败: {' | '.join(creation_errors)}")

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        api_token = str(mailbox.get("api_token") or "").strip()
        session_id = str(mailbox.get("session_id") or "").strip()
        if not api_token or not session_id:
            raise RuntimeError("DropMail mailbox is missing api_token/session_id")
        query = """
            query Session($id: ID!) {
              session(id: $id) {
                mails { id fromAddr toAddr receivedAt text html headerSubject }
              }
            }
        """
        session_response = self._graphql(
            api_token,
            query,
            {"id": session_id},
            api_base=str(mailbox.get("api_base") or ""),
        )
        session = dict(session_response.get("session") or {})
        address = str(mailbox.get("address") or "")
        messages = [
            dict(message)
            for message in (session.get("mails") or [])
            if _message_matches_email(message, address)
        ]
        if not messages:
            return None
        latest_message = max(
            messages,
            key=lambda message: (
                (
                    _parse_received_at(message.get("receivedAt"))
                    or datetime.fromtimestamp(0, tz=timezone.utc)
                ).timestamp(),
                str(message.get("id") or ""),
            ),
        )
        return {
            "provider": self.name,
            "mailbox": address,
            "message_id": str(latest_message.get("id") or ""),
            "subject": str(latest_message.get("headerSubject") or ""),
            "sender": str(latest_message.get("fromAddr") or ""),
            "text_content": str(latest_message.get("text") or ""),
            "html_content": str(latest_message.get("html") or ""),
            "received_at": _parse_received_at(latest_message.get("receivedAt")),
            "raw": latest_message,
        }

    def close(self) -> None:
        self.session.close()

    def _generate_token(self) -> str:
        return get_dropmail_token(
            DROPMAIL_TOKEN_CACHE_FILE,
            api_base=self.api_base,
            token_lifetime=self.token_lifetime,
            generate=lambda: self._request_json(
                f"{self.api_base}/api/token/generate",
                {"type": "af", "lifetime": self.token_lifetime},
            ),
        )

    def _request_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        retries: int = 2,
    ) -> dict[str, Any]:
        last_error = ""
        for attempt in range(1, max(1, retries + 1) + 1):
            try:
                response = self.session.post(
                    url,
                    json=payload,
                    timeout=self.conf["request_timeout"],
                    verify=False,
                )
            except Exception as exc:
                last_error = f"DropMail request failed: {exc}"
                if attempt <= retries:
                    time.sleep(min(float(attempt), 3.0))
                    continue
                raise RuntimeError(last_error) from exc
            body = str(getattr(response, "text", "") or "")
            if response.status_code != 200:
                last_error = f"DropMail HTTP {response.status_code}: {body[:300]}"
                if attempt <= retries and response.status_code in {
                    408,
                    425,
                    429,
                    500,
                    502,
                    503,
                    504,
                }:
                    time.sleep(min(float(attempt), 3.0))
                    continue
                raise RuntimeError(last_error)
            try:
                response_data = response.json()
            except Exception as exc:
                last_error = f"DropMail non-json response: {exc}; body={body[:200]}"
                if attempt <= retries:
                    time.sleep(min(float(attempt), 3.0))
                    continue
                raise RuntimeError(last_error) from exc
            if not isinstance(response_data, dict):
                raise RuntimeError("DropMail response is not an object")
            return response_data
        raise RuntimeError(last_error or "DropMail request failed")

    def _graphql(
        self,
        token: str,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        api_base: str = "",
    ) -> dict[str, Any]:
        base = str(api_base or self.api_base).strip().rstrip("/")
        graph_response = self._request_json(
            f"{base}/api/graphql/{token}",
            {"query": query, "variables": variables or {}},
        )
        errors = graph_response.get("errors")
        if errors:
            raise RuntimeError(
                f"DropMail GraphQL error: {json.dumps(errors, ensure_ascii=False)[:500]}"
            )
        graph_payload = graph_response.get("data") or {}
        if not isinstance(graph_payload, dict):
            raise RuntimeError("DropMail GraphQL payload is not an object")
        return graph_payload

    def _introduce_session(
        self,
        api_token: str,
        domain_entry: dict[str, Any],
    ) -> dict[str, str]:
        mutation = """
            mutation IntroduceSession($input: IntroduceSessionInput) {
              introduceSession(input: $input) {
                id
                addresses { id address restoreKey }
              }
            }
        """
        creation_response = self._graphql(
            api_token,
            mutation,
            {
                "input": {
                    "withAddress": True,
                    "domainId": str(domain_entry.get("id") or ""),
                }
            },
        )
        session = dict(creation_response.get("introduceSession") or {})
        addresses = [dict(address) for address in (session.get("addresses") or [])]
        address = str((addresses[0] if addresses else {}).get("address") or "").strip()
        session_id = str(session.get("id") or "").strip()
        if not address or not session_id:
            raise RuntimeError("DropMail did not return session/address")
        return {"address": address, "session_id": session_id}

    @staticmethod
    def _domain_is_current(domain_entry: object) -> bool:
        try:
            domain = dict(domain_entry)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        if not str(domain.get("id") or "").strip() or not str(domain.get("name") or "").strip():
            return False
        expires_at = _parse_received_at(domain.get("expiresAt"))
        return not expires_at or expires_at > datetime.now(timezone.utc)
