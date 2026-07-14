from __future__ import annotations

"""Minimal OpenAI create_account Sentinel SDK+SO compatibility package.

Only the create_account fallback path is vendored into chatgpt2api.
"""

from .legacy_create_account import install_create_account_fallback

__all__ = ["install_create_account_fallback"]
