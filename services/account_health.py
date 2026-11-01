"""Bounded, non-generating login diagnostics; never refresh or rotate credentials."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, Thread
from typing import Any

from services.proxy_service import proxy_settings


HEALTH_TTL_SECONDS = 15 * 60
HEALTH_RECHECK_SECONDS = 60


def health_context(account: dict) -> str:
    # Hash private context; it is neither returned to clients nor logged.
    keys = ("access_token", "proxy", "fp", "runtime_profile_id", "profile_snapshot",
            "user-agent", "impersonate", "oai-device-id", "oai-session-id",
            "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform")
    value = {key: account.get(key) for key in keys}
    value["effective_proxy"] = proxy_settings.get_profile(account=account, upstream=True).proxy_url
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def health_view(account: dict, *, now: float | None = None) -> dict | None:
    saved = account.get("health_check")
    if not isinstance(saved, dict):
        return None
    view = dict(saved)
    context = view.pop("context_hash", None)
    current_time = time.time() if now is None else now
    view["stale"] = (
        context != health_context(account)
        or current_time - float(view.get("checked_at") or 0) >= HEALTH_TTL_SECONDS
    )
    return view


def classify_response(response: Any) -> str | None:
    status = int(response.status_code)
    text = str(getattr(response, "text", "") or "")[:16384].lower()
    headers = getattr(response, "headers", {})
    if (headers.get("cf-mitigated", "").lower() == "challenge"
            or "enable javascript and cookies to continue" in text
            or status in {403, 503} and any(marker in text for marker in ("cf-chl-", "challenge-platform"))):
        return "browser_verification_required"
    if status == 407:
        return "proxy_auth_required"
    if status == 401:
        return "login_expired"
    if status == 429:
        return "rate_limited"
    if status == 403:
        if "unsupported_country" in text or "region_not_supported" in text:
            return "region_restricted"
        return "access_denied"
    if 300 <= status < 400:
        return "login_redirect"  # Do not send Authorization to a redirect target.
    if status != 200:
        return "upstream_error"
    return None


def classify_transport(error: Exception) -> str:
    text = str(error).lower()
    code = int(getattr(error, "code", 0) or 0)
    if "connect tunnel failed" in text:
        return "proxy_auth_required" if re.search(r"\b407\b", text) else "proxy_tunnel_rejected"
    if code == 28 or "timed out" in text:
        return "network_timeout"
    if code in {35, 51, 58, 60, 77}:
        return "tls_error"
    return "network_error"


def check_login(account: dict) -> dict:
    """At most two GETs on the existing route. No retries, POSTs, or clearance."""
    from services.openai_backend_api import OpenAIBackendAPI

    started = time.monotonic()
    result: dict[str, Any] = {"state": "network_error", "phase": "connection", "http_status": None}
    try:
        with OpenAIBackendAPI(str(account["access_token"]), diagnostic_account=account) as backend:
            for phase, path in (("connection", "/"), ("login", "/backend-api/me")):
                result["phase"] = phase
                response = (
                    backend._get_homepage(timeout=15, allow_redirects=False)
                    if phase == "connection" else backend.session.get(
                        backend.base_url + path, headers=backend._headers(path),
                        timeout=15, allow_redirects=False,
                    )
                )
                result["http_status"] = int(response.status_code)
                denied = classify_response(response)
                if denied:
                    result["state"] = denied
                    break
                if phase == "login":
                    try:
                        identity = response.json()
                    except (TypeError, ValueError):
                        identity = None
                    # A 200 HTML page or an arbitrary JSON object is not proof of login.
                    result["state"] = (
                        "login_verified" if isinstance(identity, dict)
                        and bool(identity.get("id")) and not identity.get("error")
                        else "invalid_response"
                    )
    except Exception as error:
        # Raw errors may contain authenticated proxy URLs; never retain them.
        result["state"] = classify_transport(error)
        result["http_status"] = None
    result["checked_at"] = time.time()
    result["duration_ms"] = round((time.monotonic() - started) * 1000)
    return result


class AccountHealthService:
    """One bounded job at a time, with initialized progress and safe result merges."""

    def __init__(self, accounts):
        self.accounts = accounts
        self._lock = Lock()
        self._jobs: dict[str, dict] = {}
        self._active: str | None = None

    def start(self, tokens: list[str]) -> dict:
        tokens = list(dict.fromkeys(tokens or self.accounts.list_tokens()))
        if not tokens:
            raise ValueError("账号池为空，请先导入账号")
        if len(tokens) > 2000:
            raise ValueError("每次最多检测 2000 个账号，请分批选择")
        with self._lock:
            if self._active:
                return copy.deepcopy(self._jobs[self._active])
            # Keep bounded recent progress for reloads, without private account data.
            self._jobs = {key: job for key, job in self._jobs.items()
                          if time.time() - job["started_at"] < 3600}
            while len(self._jobs) >= 8:
                self._jobs.pop(next(iter(self._jobs)))
            job_id = str(uuid.uuid4())
            job = {"progress_id": job_id, "started_at": time.time(), "total": len(tokens),
                   "processed": 0, "done": False, "error": None, "counts": {}}
            self._jobs[job_id] = job
            self._active = job_id
        try:
            Thread(target=self._run, args=(job_id, tokens), daemon=True).start()
        except Exception:
            with self._lock:
                job.update(done=True, error="检测任务启动失败，请重试")
                self._active = None
            raise
        return self.progress(job_id)

    def progress(self, job_id: str) -> dict | None:
        with self._lock:
            return copy.deepcopy(self._jobs.get(job_id))

    def current(self) -> dict | None:
        with self._lock:
            return copy.deepcopy(self._jobs.get(self._active)) if self._active else None

    def _check_one(self, token: str) -> str:
        snapshot = self.accounts.health_snapshot(token)
        if snapshot is None:
            return "skipped"
        previous = health_view(snapshot)
        if previous and not previous["stale"] and time.time() - previous["checked_at"] < HEALTH_RECHECK_SECONDS:
            return "cached"
        context = health_context(snapshot)
        result = check_login(snapshot)
        if not self.accounts.save_health_check(token, context, result):
            return "changed"
        return result["state"]

    def _run(self, job_id: str, tokens: list[str]) -> None:
        try:
            with ThreadPoolExecutor(max_workers=3, thread_name_prefix="account-health") as pool:
                futures = [pool.submit(self._check_one, token) for token in tokens]
                for future in as_completed(futures):
                    try:
                        state = future.result()
                    except Exception:
                        state = "check_error"
                    with self._lock:
                        job = self._jobs[job_id]
                        job["processed"] += 1
                        job["counts"][state] = job["counts"].get(state, 0) + 1
        except Exception:
            with self._lock:
                self._jobs[job_id]["error"] = "检测任务异常结束，请刷新查看已完成结果"
        finally:
            with self._lock:
                self._jobs[job_id]["done"] = True
                self._active = None
