from __future__ import annotations

import json
import re
import time
from pathlib import Path
from threading import Lock
from typing import Any, Callable


_token_cache_lock = Lock()


def get_dropmail_token(
    path: Path,
    *,
    api_base: str,
    token_lifetime: str,
    generate: Callable[[], dict[str, Any]],
) -> str:
    now = time.time()
    with _token_cache_lock:
        token_entries = _load_token_entries(path)
        cached_token = _find_usable_token(
            token_entries,
            api_base=api_base,
            token_lifetime=token_lifetime,
            now=now,
        )
        if cached_token:
            return cached_token

        token_response = generate()
        token = str(token_response.get("token") or "").strip()
        if not token:
            raise RuntimeError("DropMail token generation returned no token")

        lifetime_seconds = _token_lifetime_seconds(token_lifetime)
        new_token_entry = {
            "api_base": api_base,
            "token_lifetime": token_lifetime,
            "token": token,
            "created_at": now,
            "expires_at": now + lifetime_seconds - min(300, lifetime_seconds // 10),
        }
        active_other_tokens = [
            token_entry
            for token_entry in token_entries
            if float(token_entry.get("expires_at") or 0) > now
            and not _same_token_scope(
                token_entry,
                api_base=api_base,
                token_lifetime=token_lifetime,
            )
        ]
        _save_token_entries(path, [*active_other_tokens, new_token_entry])
        return token


def _find_usable_token(
    token_entries: list[dict[str, Any]],
    *,
    api_base: str,
    token_lifetime: str,
    now: float,
) -> str:
    for token_entry in token_entries:
        token = str(token_entry.get("token") or "").strip()
        if (
            _same_token_scope(
                token_entry,
                api_base=api_base,
                token_lifetime=token_lifetime,
            )
            and token
            and float(token_entry.get("expires_at") or 0) > now + 60
        ):
            return token
    return ""


def _same_token_scope(
    token_entry: dict[str, Any],
    *,
    api_base: str,
    token_lifetime: str,
) -> bool:
    return (
        str(token_entry.get("api_base") or "").rstrip("/") == api_base
        and str(token_entry.get("token_lifetime") or "") == token_lifetime
    )


def _token_lifetime_seconds(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", str(value or ""), re.IGNORECASE)
    if not match:
        return 24 * 3600
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 24 * 3600}[match.group(2).lower()]
    return max(300, min(int(match.group(1)) * multiplier, 7 * 24 * 3600))


def _load_token_entries(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [dict(token_entry) for token_entry in payload.get("items", [])]
    except (FileNotFoundError, OSError, json.JSONDecodeError, AttributeError, TypeError, ValueError):
        return []


def _save_token_entries(path: Path, token_entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "items": token_entries}
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
