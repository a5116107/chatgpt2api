from __future__ import annotations

from fastapi import HTTPException

from services.config import config


def feature_enabled(name: str) -> bool:
    flags = config.get_feature_flags()
    return bool(flags.get(str(name or "").strip(), True))


def require_feature(name: str) -> None:
    if not feature_enabled(name):
        raise HTTPException(
            status_code=403,
            detail={"error": f"feature '{name}' is disabled by settings"},
        )
