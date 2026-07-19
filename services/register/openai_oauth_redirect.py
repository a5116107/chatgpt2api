from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse


_OPENAI_OAUTH_HOSTS = frozenset({"auth.openai.com", "platform.openai.com"})


def extract_oauth_callback_params(url: str) -> dict[str, str] | None:
    if not url:
        return None
    try:
        params = parse_qs(urlparse(url).query)
    except (TypeError, ValueError):
        return None
    code = str((params.get("code") or [""])[0]).strip()
    if not code:
        return None
    return {
        "code": code,
        "state": str((params.get("state") or [""])[0]).strip(),
        "scope": str((params.get("scope") or [""])[0]).strip(),
    }


def extract_oauth_callback_from_response(
    response: Any,
    *,
    initial_url: str = "",
    expected_state: str = "",
) -> dict[str, str] | None:
    for candidate in _response_urls(response, initial_url):
        callback = extract_oauth_callback_params(candidate)
        if callback is None:
            continue
        actual_state = str(callback.get("state") or "").strip()
        if expected_state and actual_state != expected_state:
            raise ValueError(
                "oauth_state_mismatch: "
                f"expected_len={len(expected_state)} actual_len={len(actual_state)}"
            )
        callback["callback_url"] = candidate
        return callback
    return None


def is_openai_oauth_continue_url(url: str) -> bool:
    try:
        parsed = urlparse(str(url or "").strip())
    except (TypeError, ValueError):
        return False
    return parsed.scheme == "https" and str(parsed.hostname or "").lower() in _OPENAI_OAUTH_HOSTS


def openai_oauth_origin(url: str) -> str:
    """Return the trusted OAuth origin used for host-specific clearance."""
    try:
        parsed = urlparse(str(url or "").strip())
    except (TypeError, ValueError):
        return ""
    hostname = str(parsed.hostname or "").lower()
    if parsed.scheme != "https" or hostname not in _OPENAI_OAUTH_HOSTS:
        return ""
    return f"https://{hostname}/"


def _response_urls(response: Any, initial_url: str = "") -> list[str]:
    urls: list[str] = []

    def append(value: object, base_url: str = "") -> None:
        text = str(value or "").strip()
        if not text:
            return
        resolved = urljoin(base_url, text) if base_url else text
        if resolved not in urls:
            urls.append(resolved)

    append(initial_url)
    responses = list(getattr(response, "history", None) or [])
    if response is not None:
        responses.append(response)
    for item in responses:
        item_url = str(getattr(item, "url", "") or "").strip()
        append(item_url)
        headers = getattr(item, "headers", None) or {}
        location = str(headers.get("location") or headers.get("Location") or "") if hasattr(headers, "get") else ""
        append(location, item_url)
    return urls
