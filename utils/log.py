import base64
import binascii
import hashlib
import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class Logger:
    _DATA_URL_RE = re.compile(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+")
    _JSON_B64_RE = re.compile(r'("b64_json"\s*:\s*")([A-Za-z0-9+/=]+)(")')
    _URL_RE = re.compile(r"https?://[^\s\"'<>]+")
    _EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
    _BEARER_RE = re.compile(r"(?i)(\bBearer\s+)([A-Za-z0-9._~+/-]+)")
    _SECRET_KEY_PARTS = (
        "token",
        "secret",
        "password",
        "authorization",
        "cookie",
        "api_key",
        "apikey",
        "auth-key",
        "signature",
    )

    def __init__(self, name: str = "chatgpt2api") -> None:
        self._logger = logging.getLogger(name)
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            self._logger.addHandler(handler)
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False

    def _enabled(self, level: str) -> bool:
        try:
            from services.config import config
            levels = set(config.log_levels)
        except Exception:
            levels = set()
        return level in (levels or {"info", "warning", "error"})

    def _mask_string(self, value: str, keep: int = 10) -> str:
        if len(value) <= keep:
            return value
        return value[:keep] + "..."

    @staticmethod
    def _redact_secret(value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"[redacted len={len(value)} sha256={digest}]"

    @staticmethod
    def _strip_url_query(value: str) -> str:
        """Keep URL origin/path for diagnostics without logging signed query parameters."""
        try:
            parsed = urlsplit(value)
        except ValueError:
            return value
        if not parsed.scheme or not parsed.netloc:
            return value
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    def _mask_base64(self, value: str) -> str:
        if value.startswith("data:") and ";base64," in value:
            header, _, data = value.partition(",")
            return f"{header},{self._mask_string(data, 24)} (base64 len={len(data)})"
        return f"{self._mask_string(value, 24)} (base64 len={len(value)})"

    def _is_base64_string(self, value: str) -> bool:
        if len(value) < 64 or len(value) % 4 != 0:
            return False
        if not any(char in value for char in "+/="):
            return False
        try:
            base64.b64decode(value, validate=True)
            return True
        except (binascii.Error, ValueError):
            return False

    def _sanitize_string(self, value: str) -> str:
        stripped = value.strip()
        if stripped.startswith("data:") and ";base64," in stripped:
            return self._mask_base64(stripped)
        if self._is_base64_string(stripped):
            return self._mask_base64(stripped)
        sanitized = self._DATA_URL_RE.sub(lambda match: self._mask_base64(match.group(0)), value)
        sanitized = self._JSON_B64_RE.sub(
            lambda match: f'{match.group(1)}{self._mask_base64(match.group(2))}{match.group(3)}',
            sanitized,
        )
        sanitized = self._URL_RE.sub(lambda match: self._strip_url_query(match.group(0)), sanitized)
        sanitized = self._EMAIL_RE.sub("[redacted-email]", sanitized)
        sanitized = self._BEARER_RE.sub(
            lambda match: f"{match.group(1)}{self._redact_secret(match.group(2))}",
            sanitized,
        )
        if sanitized != value:
            return sanitized
        return value

    def _sanitize(self, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized = {}
            for key, item in value.items():
                lowered_key = key.lower()
                if isinstance(item, str) and "email" in lowered_key:
                    sanitized[key] = "[redacted-email]"
                elif isinstance(item, str) and (
                    lowered_key == "dx" or any(part in lowered_key for part in self._SECRET_KEY_PARTS)
                ):
                    sanitized[key] = self._redact_secret(item)
                elif isinstance(item, str) and ("base64" in lowered_key or lowered_key == "b64_json"):
                    sanitized[key] = self._mask_base64(item)
                else:
                    sanitized[key] = self._sanitize(item)
            return sanitized
        if isinstance(value, list):
            return [self._sanitize(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._sanitize(item) for item in value)
        if isinstance(value, str):
            return self._sanitize_string(value)
        return value

    def _message(self, value: Any) -> str:
        sanitized = self._sanitize(value)
        if isinstance(sanitized, str):
            return sanitized
        return json.dumps(sanitized, ensure_ascii=False, default=str)

    def debug(self, message: Any) -> None:
        if self._enabled("debug"):
            self._logger.debug(self._message(message))

    def info(self, message: Any) -> None:
        if self._enabled("info"):
            self._logger.info(self._message(message))

    def warning(self, message: Any) -> None:
        if self._enabled("warning"):
            self._logger.warning(self._message(message))

    def error(self, message: Any) -> None:
        if self._enabled("error"):
            self._logger.error(self._message(message))


logger = Logger()
