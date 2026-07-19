from __future__ import annotations

import json
from typing import Any


def response_json(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def response_debug_detail(response: Any, limit: int = 800) -> str:
    if response is None:
        return ""
    payload = response_json(response)
    parts = [
        f"url={str(getattr(response, 'url', '') or '')[:300]}",
        f"content_type={str(getattr(response, 'headers', {}).get('content-type') or '')}",
    ]
    for key in ("cf-ray", "x-request-id", "openai-processing-ms"):
        value = str(getattr(response, "headers", {}).get(key) or "").strip()
        if value:
            parts.append(f"{key}={value}")
    parts.append(
        f"json={json.dumps(payload, ensure_ascii=False)[:limit]}"
        if payload
        else f"body={str(getattr(response, 'text', '') or '')[:limit]}"
    )
    return ", ".join(parts)


def is_cloudflare_challenge(response: Any) -> bool:
    if response is None:
        return False
    try:
        status_code = int(getattr(response, "status_code", 0) or 0)
    except (TypeError, ValueError):
        return False
    if status_code not in (403, 503):
        return False
    body = str(getattr(response, "text", "") or "").lower()
    return any(
        marker in body
        for marker in (
            "<title>just a moment",
            "<title>attention required! | cloudflare",
            "cf-chl-",
            "__cf_chl_",
            "cf-browser-verification",
        )
    )


def authorize_landed_page(response: Any) -> str:
    """Classify the authorize landing page for diagnostics only."""
    if response is None:
        return ""
    final_url = str(getattr(response, "url", "") or "").lower()
    payload = response_json(response)
    page = payload.get("page") if isinstance(payload, dict) else None
    page_type = str(page.get("type") or "").lower() if isinstance(page, dict) else ""
    if any(marker in final_url for marker in ("create-account", "signup")) or page_type == "create_account":
        return "signup"
    if "/log-in" in final_url or "/login" in final_url or page_type in {
        "login",
        "password_verification",
    }:
        return "login"
    return ""


def authorize_continue_required(response: Any) -> bool:
    """Return whether the authorize response is still waiting for an email."""
    if response is None:
        return True
    final_url = str(getattr(response, "url", "") or "").lower()
    payload = response_json(response)
    page = payload.get("page") if isinstance(payload, dict) else None
    page_type = str(page.get("type") or "").lower() if isinstance(page, dict) else ""
    if page_type in {
        "create_account_password",
        "email_otp_send",
        "email_otp_verification",
        "about_you",
        "oauth_callback",
    }:
        return False
    return not any(
        marker in final_url
        for marker in ("create-account/password", "email-verification", "about-you", "authorize/continue")
    )


def extract_continue_state(payload: dict[str, Any] | None) -> dict[str, str]:
    data = payload if isinstance(payload, dict) else {}
    page = data.get("page") if isinstance(data.get("page"), dict) else {}
    page_payload = page.get("payload") if isinstance(page.get("payload"), dict) else {}
    return {
        "continue_url": str(page_payload.get("url") or data.get("continue_url") or "").strip(),
        "page_type": str(page.get("type") or "").strip(),
    }
