from __future__ import annotations

import json
import threading
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def _domain(email: str) -> str:
    value = str(email or "").strip().lower()
    return value.rsplit("@", 1)[-1] if "@" in value else ""


def _default_data_stats_path(project_root: Path) -> Path:
    root = Path(project_root)
    # openai_register.base_dir = .../services/register
    if root.name == "register" and root.parent.name == "services":
        return root.parents[1] / "data" / "route_stats.jsonl"  # .../app/data
    if (root / "data").is_dir():
        return root / "data" / "route_stats.jsonl"
    if root.name == "app":
        return root / "data" / "route_stats.jsonl"
    return root / ".account_profiles" / "route_stats.jsonl"


def _stats_path(project_root: Path) -> Path:
    root = Path(project_root)
    preferred = _default_data_stats_path(root)
    candidates = [
        preferred,
        root / ".account_profiles" / "route_stats.jsonl",
        root / "data" / "route_stats.jsonl",
    ]
    for item in candidates:
        if item.exists():
            return item
    return preferred


def record_route_event(
    project_root: Path,
    *,
    provider: str = "openai",
    mail_provider: str = "",
    mail_mode: str = "",
    email: str = "",
    email_domain: str = "",
    sentinel_route: str = "",
    stage: str = "create_account",
    success: bool = False,
    registration_disallowed: bool = False,
    sentinel_failure: bool = False,
    error_code: str = "",
    token_len: int = 0,
    so_present: bool = False,
    so_len: int = 0,
    extra: dict[str, Any] | None = None,
) -> Path:
    path = _stats_path(Path(project_root))
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "mail_provider": mail_provider,
        "mail_mode": mail_mode or mail_provider,
        "email_domain": email_domain or _domain(email),
        "sentinel_route": sentinel_route or "unknown",
        "stage": stage,
        "success": bool(success),
        "registration_disallowed": bool(registration_disallowed),
        "sentinel_failure": bool(sentinel_failure),
        "error_code": str(error_code or ""),
        "token_len": int(token_len or 0),
        "so_present": bool(so_present),
        "so_len": int(so_len or 0),
    }
    if extra:
        event["extra"] = extra
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, sort_keys=True)
    with _LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return path


def summarize_route_stats(project_root: Path) -> dict[str, Any]:
    path = _stats_path(Path(project_root))
    summary: dict[tuple[str, str, str, str], Counter] = defaultdict(Counter)
    if not path.exists():
        return {"path": str(path), "total": 0, "groups": []}
    total = 0
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            total += 1
            key = (
                str(event.get("mail_provider") or ""),
                str(event.get("email_domain") or ""),
                str(event.get("sentinel_route") or "unknown"),
                str(event.get("stage") or ""),
            )
            bucket = summary[key]
            bucket["attempts"] += 1
            if event.get("success"):
                bucket["successes"] += 1
            if event.get("registration_disallowed"):
                bucket["registration_disallowed"] += 1
            if event.get("sentinel_failure"):
                bucket["sentinel_failures"] += 1
    groups = []
    for (mail_provider, email_domain, sentinel_route, stage), counts in sorted(summary.items()):
        attempts = int(counts["attempts"])
        successes = int(counts["successes"])
        groups.append({
            "mail_provider": mail_provider,
            "email_domain": email_domain,
            "sentinel_route": sentinel_route,
            "stage": stage,
            "attempts": attempts,
            "successes": successes,
            "success_rate": round(successes / attempts, 4) if attempts else 0.0,
            "registration_disallowed": int(counts["registration_disallowed"]),
            "sentinel_failures": int(counts["sentinel_failures"]),
        })
    return {"path": str(path), "total": total, "groups": groups}


def suggest_route_retirement(
    project_root: Path,
    *,
    min_attempts: int = 20,
    max_success_rate: float = 0.2,
    include_successful: bool = False,
) -> dict[str, Any]:
    """Return low-success route/provider/domain groups for later manual decommission.

    只做统计建议，不自动禁用 provider/域名/线路，避免误伤现有可用路径。
    """
    summary = summarize_route_stats(project_root)
    candidates: list[dict[str, Any]] = []
    for group in summary.get("groups", []):
        attempts = int(group.get("attempts") or 0)
        success_rate = float(group.get("success_rate") or 0)
        successes = int(group.get("successes") or 0)
        if attempts < int(min_attempts):
            continue
        if not include_successful and successes > 0 and success_rate > 0:
            continue
        if success_rate <= float(max_success_rate):
            candidates.append({
                **group,
                "reason": f"attempts>={int(min_attempts)} and success_rate<={float(max_success_rate):.4f}",
                "action": "review_then_disable_or_lower_priority",
            })
    return {
        "path": summary.get("path"),
        "total": summary.get("total", 0),
        "threshold": {"min_attempts": int(min_attempts), "max_success_rate": float(max_success_rate)},
        "candidates": candidates,
    }


# PATCH_MARKER route_stats_denied_helper_r31
def load_denied_domains(mail_config: dict | None) -> set[str]:
    """Return lowercase denied email domains from register mail config."""
    mail = mail_config if isinstance(mail_config, dict) else {}
    values = mail.get("denied_domains") or mail.get("blocked_domains") or []
    out: set[str] = set()
    if isinstance(values, list):
        for item in values:
            domain = str(item or "").strip().lower()
            if domain:
                out.add(domain)
    return out
