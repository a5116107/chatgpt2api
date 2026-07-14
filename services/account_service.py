from __future__ import annotations

import base64
import json
import secrets
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Condition, Lock, Thread
from typing import Any
from urllib.parse import urlencode

from services.config import config
from services.dynamic_proxy_feedback import report_dynamic_proxy_denial
from services.log_service import (
    LOG_TYPE_ACCOUNT,
    log_service,
)
from services.storage.base import StorageBackend
from services.runtime_profile_service import runtime_profile_service
from utils.helper import anonymize_token


class AccountService:
    """账号池服务，使用 token -> account 的 dict 保存账号。"""

    _NEW_ACCOUNT_INVALID_GRACE_SECONDS = 30 * 60
    _INVALID_CONFIRM_SECONDS = 120
    _ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 24 * 60 * 60
    _REFRESH_TOKEN_KEEPALIVE_SECONDS = 3 * 24 * 60 * 60
    _REFRESH_TOKEN_KEEPALIVE_ERROR_BACKOFF_SECONDS = 6 * 60 * 60
    _REFRESH_TOKEN_KEEPALIVE_BATCH_SIZE = 3
    _TOKEN_REFRESH_ERROR_BACKOFF_SECONDS = 5 * 60
    _OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
    _OAUTH_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"
    _OAUTH_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )

    # 刷新进度追踪
    _refresh_progress: dict[str, dict] = {}
    _refresh_progress_lock = Lock()
    # 重新登录进度追踪
    _relogin_progress: dict[str, dict] = {}
    _relogin_progress_lock = Lock()

    def __init__(self, storage_backend: StorageBackend):
        self.storage = storage_backend
        self._lock = Lock()
        self._token_refresh_lock = Lock()
        self._image_slot_condition = Condition(self._lock)
        self._index = 0
        self._text_index = 0
        self._accounts = self._load_accounts()
        self._image_inflight: dict[str, int] = {}
        self._token_aliases: dict[str, str] = {}
        self._cumulative_total = self._load_cumulative_total()

    def _get_cumulative_file(self) -> Path:
        from services.config import DATA_DIR
        return DATA_DIR / ".cumulative_total"

    def _load_cumulative_total(self) -> int:
        try:
            f = self._get_cumulative_file()
            if f.exists():
                return int(f.read_text().strip())
        except Exception:
            pass
        return len(self._accounts)

    def _save_cumulative_total(self) -> None:
        try:
            self._get_cumulative_file().write_text(str(self._cumulative_total))
        except Exception:
            pass

    def _sync_risk_control_capabilities(self) -> None:
        try:
            from services.risk_control_service import risk_control_service
            risk_control_service.sync_account_capabilities()
        except Exception:
            pass

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _decode_jwt_payload(token: str) -> dict:
        try:
            payload = str(token or "").split(".")[1]
            payload += "=" * ((4 - len(payload) % 4) % 4)
            import base64
            import json
            data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _parse_time(value: object) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            try:
                parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _timestamp_to_iso(value: object) -> str:
        try:
            ts = int(value)
        except (TypeError, ValueError):
            return ""
        tz = timezone(timedelta(hours=8))
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz).isoformat()

    def _load_accounts(self) -> dict[str, dict]:
        accounts = self.storage.load_accounts()
        return {
            normalized["access_token"]: normalized
            for item in accounts
            if (normalized := self._normalize_account(item)) is not None
        }

    def _save_accounts(self) -> None:
        self.storage.save_accounts(list(self._accounts.values()))


    @staticmethod
    def _access_token_hard_dead(account: dict | None) -> bool:
        """True when access token itself is dead for API calls.

        Chat-only soft signals (e.g. text_stream:token_revoked) must remain image-eligible.
        Hard death from /backend-api/me or oauth invalidation must not burn image rotate budget.
        # PATCH_MARKER image_skip_hard_dead_soft_r12
        """
        if not isinstance(account, dict):
            return True
        blob = " ".join(
            str(account.get(k) or "")
            for k in (
                "last_token_refresh_error",
                "last_refresh_error",
                "last_error",
                "error",
                "note",
                "risk_status",
            )
        ).lower()
        if not blob.strip():
            return False
        hard_markers = (
            "token invalidated",
            "token_invalidated",
            "invalidated oauth",
            "account_deactivated",
            "refresh_token_invalidated",
            "session has ended",
            "invalid_grant",
            "app_session_terminated",
            "oauth_refresh_http_401",
            "oauth_refresh_http_403",
            # NOTE r25: bare "/backend-api/me" is NOT hard-dead; fresh free tokens can 403 me
            # while image/chat still work. Only explicit token invalidation markers hard-kill.
        )
        # PATCH_MARKER hard_dead_markers_r23
        # PATCH_MARKER me_not_hard_dead_r25
# PATCH_MARKER hard_dead_refresh_disable_r24
# PATCH_MARKER free_deactivated_disable_r24
        # text_stream:token_revoked alone is chat-scoped soft; keep image.
        if "text_stream:token_revoked" in blob and not any(m in blob for m in hard_markers if m != "token_revoked"):
            # only soft chat marker
            if "token invalidated" not in blob and "/backend-api/me" not in blob and "invalidated oauth" not in blob:
                return False
        return any(m in blob for m in hard_markers)

    @staticmethod
    def _is_image_account_available(account: dict) -> bool:
        if not isinstance(account, dict):
            return False
        status = str(account.get("status") or "").strip()
        # Hard blocks only. Soft 异常 (e.g. free text_stream token_revoked) must not
        # permanently kill image selection while quota remains.
        if status in {"禁用", "限流"}:
            return False
        if not str(account.get("access_token") or "").strip():
            return False
        if bool(account.get("image_quota_unknown")):
            return True
        if int(account.get("quota") or 0) > 0:
            return True
        # Fresh register may keep status=正常 while remote userinfo timed out (quota=0).
        # Exhausted accounts are marked 限流; allow normal tokens to pass acquire and rehydrate.
        return status == "正常"

    @staticmethod
    def _should_skip_remote_image_preflight(account: dict) -> bool:
        """本地快路径判断。

        对于已经有完整画像、状态正常且没有明显异常痕迹的账号，
        直接放行到生图链路，避免每次选号都同步打 /me + /conversation-init + /default-account。
        """
        if not isinstance(account, dict):
            return False
        if str(account.get("status") or "").strip() != "正常":
            return False
        if int(account.get("invalid_count") or 0) > 0:
            return False
        if account.get("last_refresh_error") or account.get("last_refresh_error_at"):
            return False
        profile_status = str(account.get("profile_status") or "").strip().lower()
        if profile_status and profile_status not in {"active"}:
            return False
        profile_snapshot = account.get("profile_snapshot")
        if not isinstance(profile_snapshot, dict):
            return False
        return True

    @classmethod
    def _account_matches_plan_type(cls, account: dict, plan_type: str | None = None) -> bool:
        if not plan_type:
            return True
        normalized_plan = cls._normalize_account_type(plan_type)
        normalized_account = cls._normalize_account_type(account.get("type"))
        if not normalized_plan or not normalized_account:
            return False
        return normalized_plan.lower() == normalized_account.lower()

    @classmethod
    def _account_matches_source_type(cls, account: dict, source_type: str | None = None) -> bool:
        if not source_type:
            return True
        return cls._normalize_source_type(account.get("source_type")) == cls._normalize_source_type(source_type)

    @classmethod
    def _account_matches_any_plan_type(cls, account: dict, plan_types: set[str] | tuple[str, ...] | None = None) -> bool:
        if not plan_types:
            return True
        normalized_account = cls._normalize_account_type(account.get("type"))
        normalized_plans = {
            normalized
            for plan_type in plan_types
            if (normalized := cls._normalize_account_type(plan_type))
        }
        return bool(normalized_account and normalized_account in normalized_plans)

    @staticmethod
    def _normalize_source_type(value: object) -> str:
        return str(value or "web").strip().lower() or "web"

    def _capability_allows(self, account: dict, capability: str) -> bool:
        try:
            from services.risk_control_service import risk_control_service
            return risk_control_service.capability_allows(account, capability)
        except Exception:
            return True

    @staticmethod
    @staticmethod
    def _is_free_account(account: dict | None) -> bool:
        """Free / unknown plan accounts die quickly under password rescue storms."""
        if not isinstance(account, dict):
            return True
        plan = str(account.get("type") or account.get("account_type") or "free").strip().lower()
        plan = plan.replace("-", "_").replace(" ", "_")
        if plan in {"", "free", "null", "none", "unknown"}:
            return True
        return plan not in {"plus", "pro", "prolite", "team", "business", "enterprise"}

    def _normalize_account_type(value: object) -> str | None:

        raw = str(value or "").strip()
        if not raw:
            return None
        key = raw.lower().replace("-", "_").replace(" ", "_")
        compact = key.replace("_", "")
        aliases = {
            "free": "free",
            "plus": "Plus",
            "pro": "Pro",
            "prolite": "ProLite",
            "team": "Team",
            "business": "Team",
            "enterprise": "Enterprise",
        }
        return aliases.get(compact) or aliases.get(key) or raw

    def _search_account_type(self, payload: object) -> str | None:
        if isinstance(payload, dict):
            for key in ("plan_type", "account_plan", "account_type", "subscription_type", "type"):
                plan = self._normalize_account_type(payload.get(key))
                if plan:
                    return plan
            for value in payload.values():
                plan = self._search_account_type(value)
                if plan:
                    return plan
        elif isinstance(payload, list):
            for value in payload:
                plan = self._search_account_type(value)
                if plan:
                    return plan
        return None

    def _normalize_account(self, item: dict) -> dict | None:
        if not isinstance(item, dict):
            return None
        access_token = item.get("access_token") or item.get("accessToken") or ""
        if not access_token:
            return None
        normalized = dict(item)
        normalized.pop("accessToken", None)
        normalized["access_token"] = access_token
        if str(normalized.get("type") or "").strip().lower() == "codex":
            normalized["export_type"] = "codex"
            normalized.pop("type", None)
        normalized["type"] = normalized.get("type") or "free"
        normalized["status"] = normalized.get("status") or "正常"
        normalized["quota"] = max(0, int(normalized.get("quota") if normalized.get("quota") is not None else 0))
        normalized["image_quota_unknown"] = bool(normalized.get("image_quota_unknown"))
        normalized["email"] = normalized.get("email") or None
        normalized["user_id"] = normalized.get("user_id") or None
        normalized["proxy"] = str(normalized.get("proxy") or "").strip()
        source_type = normalized.get("source_type")
        if not source_type and str(normalized.get("export_type") or "").strip().lower() == "codex":
            source_type = "codex"
        normalized["source_type"] = self._normalize_source_type(source_type)
        limits_progress = normalized.get("limits_progress")
        normalized["limits_progress"] = limits_progress if isinstance(limits_progress, list) else []
        normalized["default_model_slug"] = normalized.get("default_model_slug") or None
        normalized["restore_at"] = normalized.get("restore_at") or None
        normalized["success"] = int(normalized.get("success") or 0)
        normalized["fail"] = int(normalized.get("fail") or 0)
        normalized["invalid_count"] = int(normalized.get("invalid_count") or 0)
        normalized["last_used_at"] = normalized.get("last_used_at")
        normalized["last_invalid_at"] = normalized.get("last_invalid_at") or None
        normalized["last_refresh_error"] = normalized.get("last_refresh_error") or None
        normalized["last_refresh_error_at"] = normalized.get("last_refresh_error_at") or None
        normalized["last_token_refresh_at"] = normalized.get("last_token_refresh_at") or None
        normalized["last_token_refresh_error"] = normalized.get("last_token_refresh_error") or None
        normalized["last_token_refresh_error_at"] = normalized.get("last_token_refresh_error_at") or None
        normalized["created_at"] = normalized.get("created_at") or AccountService._now()
        normalized, _profile = runtime_profile_service.ensure_account_profile(normalized)
        return normalized

    @staticmethod
    def _profile_fp(account: dict | None) -> dict[str, str]:
        account = account if isinstance(account, dict) else {}
        fp = account.get("fp") if isinstance(account.get("fp"), dict) else {}
        result = {str(key).lower(): str(value) for key, value in fp.items() if str(value or "").strip()}
        result.setdefault("user-agent", AccountService._OAUTH_USER_AGENT)
        result.setdefault("impersonate", "chrome110")
        result.setdefault("sec-ch-ua", '"Microsoft Edge";v="143", "Chromium";v="143", "Not A(Brand";v="24"')
        result.setdefault("sec-ch-ua-mobile", "?0")
        result.setdefault("sec-ch-ua-platform", '"Windows"')
        return result

    @classmethod
    def _profile_session_kwargs(cls, account: dict | None = None) -> dict[str, object]:
        from services.proxy_service import proxy_settings

        fp = cls._profile_fp(account)
        return proxy_settings.build_session_kwargs(
            account=account,
            upstream=True,
            impersonate=fp.get("impersonate", "chrome110"),
            verify=True,
        )

    @classmethod
    def _profile_headers(cls, account: dict | None = None, *, content_type: str | None = None, accept: str | None = None) -> dict[str, str]:
        fp = cls._profile_fp(account)
        headers = {
            "Accept": accept or "application/json",
            "User-Agent": fp.get("user-agent", cls._OAUTH_USER_AGENT),
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    @staticmethod
    def _jwt_exp(access_token: str) -> int:
        try:
            return int(AccountService._decode_jwt_payload(access_token).get("exp") or 0)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _token_expires_in(cls, access_token: str) -> int | None:
        exp = cls._jwt_exp(access_token)
        if exp <= 0:
            return None
        return exp - int(time.time())

    @classmethod
    def _token_needs_refresh(cls, access_token: str, *, force: bool = False) -> bool:
        if force:
            return True
        remaining = cls._token_expires_in(access_token)
        return remaining is not None and remaining <= cls._ACCESS_TOKEN_REFRESH_SKEW_SECONDS

    @classmethod
    def _token_issued_at(cls, access_token: str) -> datetime | None:
        try:
            iat = int(cls._decode_jwt_payload(access_token).get("iat") or 0)
        except (TypeError, ValueError):
            return None
        if iat <= 0:
            return None
        return datetime.fromtimestamp(iat, tz=timezone.utc)

    @staticmethod
    def _safe_response_text(response: object, limit: int = 300) -> str:
        try:
            return str(getattr(response, "text", "") or "")[:limit]
        except Exception:
            return ""

    def _record_runtime_risk_event(
        self,
        message: str,
        *,
        account: dict | None = None,
        status_code: int | None = None,
        code: str | None = None,
        scope: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> None:
        try:
            from services.risk_control_service import risk_control_service
            risk_control_service.record_event(
                code=code,
                message=message,
                scope=scope,
                account=account or {},
                proxy=str((account or {}).get("proxy") or ""),
                profile_id=str((account or {}).get("runtime_profile_id") or ""),
                status_code=status_code,
                raw=raw or {},
            )
        except Exception:
            pass

    def _resolve_access_token_locked(self, access_token: str) -> str:
        token = str(access_token or "").strip()
        seen: set[str] = set()
        while token and token not in self._accounts and token in self._token_aliases and token not in seen:
            seen.add(token)
            token = self._token_aliases.get(token, token)
        return token

    def resolve_access_token(self, access_token: str) -> str:
        if not access_token:
            return ""
        with self._lock:
            return self._resolve_access_token_locked(access_token)

    def _get_account_for_token(self, access_token: str) -> tuple[str, dict | None]:
        with self._lock:
            resolved = self._resolve_access_token_locked(access_token)
            account = self._accounts.get(resolved)
            return resolved, dict(account) if account else None

    def _record_token_refresh_error(self, access_token: str, event: str, error: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            resolved = self._resolve_access_token_locked(access_token)
            current = self._accounts.get(resolved)
            if current is None:
                return
            next_item = dict(current)
            next_item["last_token_refresh_error"] = str(error or "refresh token failed")
            next_item["last_token_refresh_error_at"] = now
            account = self._normalize_account(next_item)
            if account is not None:
                self._accounts[resolved] = account
                self._save_accounts()
        log_service.add(
            LOG_TYPE_ACCOUNT,
            "refresh_token 刷新 access_token 失败",
            {"source": event, "token": anonymize_token(access_token), "error": str(error or "")},
        )

    def _recent_token_refresh_error(self, account: dict) -> bool:
        last_error_at = self._parse_time(account.get("last_token_refresh_error_at"))
        if last_error_at is None:
            return False
        return (datetime.now(timezone.utc) - last_error_at).total_seconds() < self._TOKEN_REFRESH_ERROR_BACKOFF_SECONDS

    def _recent_refresh_token_keepalive_error(self, account: dict, now: datetime) -> bool:
        last_error_at = self._parse_time(account.get("last_token_refresh_error_at"))
        if last_error_at is None:
            return False
        return (now - last_error_at).total_seconds() < self._REFRESH_TOKEN_KEEPALIVE_ERROR_BACKOFF_SECONDS

    def _refresh_token_keepalive_anchor(self, account: dict) -> datetime | None:
        return (
            self._parse_time(account.get("last_token_refresh_at"))
            or self._token_issued_at(str(account.get("access_token") or ""))
            or self._parse_time(account.get("created_at"))
        )

    def _refresh_token_keepalive_due_at(self, account: dict, now: datetime) -> datetime | None:
        if not str(account.get("refresh_token") or "").strip():
            return None
        if account.get("status") == "禁用":
            return None
        if self._recent_refresh_token_keepalive_error(account, now):
            return None
        anchor = self._refresh_token_keepalive_anchor(account)
        if anchor is None:
            return now
        due_at = anchor + timedelta(seconds=self._REFRESH_TOKEN_KEEPALIVE_SECONDS)
        return due_at if due_at <= now else None

    def _request_access_token_refresh(self, refresh_token: str, account: dict | None = None) -> dict[str, str]:
        from curl_cffi import requests

        normalized_account, _profile = runtime_profile_service.ensure_account_profile(dict(account or {}))
        session = requests.Session(**self._profile_session_kwargs(normalized_account))
        try:
            response = session.post(
                self._OAUTH_TOKEN_URL,
                headers=self._profile_headers(
                    normalized_account,
                    content_type="application/x-www-form-urlencoded",
                    accept="application/json",
                ),
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self._OAUTH_CLIENT_ID,
                },
                timeout=60,
            )
            data = response.json() if response.text else {}
            if response.status_code != 200 or not isinstance(data, dict) or not data.get("access_token"):
                detail = ""
                if isinstance(data, dict):
                    detail = str(data.get("error_description") or data.get("error") or data.get("message") or "")
                detail = detail or self._safe_response_text(response)
                raise RuntimeError(f"oauth_refresh_http_{response.status_code}{': ' + detail if detail else ''}")
            return {
                "access_token": str(data.get("access_token") or "").strip(),
                "refresh_token": str(data.get("refresh_token") or refresh_token).strip(),
                "id_token": str(data.get("id_token") or "").strip(),
            }
        finally:
            session.close()

    def _apply_refreshed_tokens(self, old_access_token: str, token_data: dict, event: str) -> str:
        now = datetime.now(timezone.utc).isoformat()
        with self._image_slot_condition:
            old_token = self._resolve_access_token_locked(old_access_token)
            current = self._accounts.get(old_token)
            if current is None:
                return old_token
            new_token = str(token_data.get("access_token") or old_token).strip()
            if not new_token:
                return old_token

            next_item = dict(current)
            next_item["access_token"] = new_token
            if token_data.get("refresh_token"):
                next_item["refresh_token"] = str(token_data.get("refresh_token") or "").strip()
            if token_data.get("id_token"):
                next_item["id_token"] = str(token_data.get("id_token") or "").strip()
            next_item["last_token_refresh_at"] = now
            next_item["last_token_refresh_error"] = None
            next_item["last_token_refresh_error_at"] = None
            next_item["invalid_count"] = 0
            next_item["last_invalid_at"] = None
            next_item["last_refresh_error"] = None
            next_item["last_refresh_error_at"] = None

            account = self._normalize_account(next_item)
            if account is None:
                return old_token

            rotated = new_token != old_token
            if rotated:
                self._accounts.pop(old_token, None)
                self._token_aliases[old_token] = new_token
                old_inflight = int(self._image_inflight.pop(old_token, 0))
                if old_inflight:
                    self._image_inflight[new_token] = int(self._image_inflight.get(new_token, 0)) + old_inflight
            self._accounts[new_token] = account
            self._save_accounts()
            self._image_slot_condition.notify_all()

        log_service.add(
            LOG_TYPE_ACCOUNT,
            "refresh_token 已刷新 access_token",
            {"source": event, "token": anonymize_token(new_token), "rotated": rotated},
        )
        return new_token

    def refresh_access_token(self, access_token: str, *, force: bool = False, event: str = "refresh_access_token") -> str:
        if not access_token:
            return ""
        with self._token_refresh_lock:
            resolved_token, account = self._get_account_for_token(access_token)
            if not account:
                return access_token
            active_token = str(account.get("access_token") or resolved_token or access_token)
            if not self._token_needs_refresh(active_token, force=force):
                return active_token
            refresh_token = str(account.get("refresh_token") or "").strip()
            if not refresh_token:
                return active_token
            if not force and self._recent_token_refresh_error(account):
                return active_token
            try:
                token_data = self._request_access_token_refresh(refresh_token, account)
            except Exception as exc:
                error_str = str(exc or "")
                self._record_token_refresh_error(active_token, event, error_str)
                low = error_str.lower()
                # 会话/刷新令牌永久失效时，优先用邮箱密码抢救，避免号池被 watcher 直接清空
                permanent = any(
                    marker in low
                    for marker in (
                        "app_session_terminated",
                        "refresh_token_invalidated",
                        "session has ended",
                        "invalid_grant",
                        "token has been revoked",
                        "token_revoked",
                    )
                )
                if permanent:
                    email = str(account.get("email") or "").strip()
                    password = str(account.get("password") or "").strip()
                    # PATCH_MARKER hard_dead_refresh_disable_r24
                    # Permanent refresh death (session ended / invalid_grant / etc.) must not re-stack
                    # as soft「异常」with fake quota. Route free/automation through remove_invalid_token
                    # so hard_dead_free_disable_r23 marks 禁用; paid may still attempt password rescue.
                    try:
                        self.remove_invalid_token(
                            active_token,
                            f"{event}:{error_str[:180]}",
                            quiet=True,
                            sync_capabilities=False,
                        )
                    except Exception:
                        try:
                            self.update_account(
                                active_token,
                                {
                                    "status": "禁用" if self._is_free_account(account) else "异常",
                                    "quota": 0 if self._is_free_account(account) else int(account.get("quota") or 0),
                                    "last_token_refresh_error": error_str[:500],
                                    "last_token_refresh_error_at": datetime.now(timezone.utc).isoformat(),
                                    "last_refresh_error": "refresh_token_invalidated",
                                    "last_refresh_error_at": datetime.now(timezone.utc).isoformat(),
                                },
                                quiet=True,
                                sync_capabilities=False,
                            )
                        except Exception:
                            pass
                    # Password rescue is best-effort and can itself trip OpenAI OTP / deactivation.
                    # Skip free-plan auto rescue; register refill remains the recovery path.
                    if (
                        email
                        and password
                        and bool(getattr(config, "auto_relogin_after_refresh", False))
                        and not self._is_free_account(account)
                    ):
                        t = Thread(
                            target=self._password_re_login_thread,
                            args=(active_token, email, password, f"{event}:refresh_invalidated"),
                            daemon=True,
                        )
                        t.start()
                return active_token
            return self._apply_refreshed_tokens(active_token, token_data, event)

    def _password_re_login_thread(self, access_token: str, email: str, password: str, event: str, progress_id: str | None = None) -> None:
        """密码重新登录线程入口"""
        try:
            result = self._login_with_password(email, password)
            if result.get("ok"):
                # 登录成功，更新账号
                new_access_token = result.get("access_token", "")
                new_refresh_token = result.get("refresh_token", "")
                new_id_token = result.get("id_token", "")
                new_expires_at = result.get("expires_at")

                # 构建 token_data 供 _apply_refreshed_tokens 使用
                token_data = {
                    "access_token": new_access_token,
                    "refresh_token": new_refresh_token,
                    "id_token": new_id_token,
                }

                # 使用 _apply_refreshed_tokens 更新账号（处理 token 别名）
                new_token = self._apply_refreshed_tokens(access_token, token_data, f"{event}:password_relogin")

                # 额外更新 source_type 和 status（静默，避免重复日志）
                self.update_account(new_token, {
                    "source_type": result.get("source_type", "password"),
                    "status": "正常",
                }, quiet=True, sync_capabilities=False)

                log_service.add(
                    LOG_TYPE_ACCOUNT,
                    "更新账号",
                    {
                        "source": event,
                        "old_token": anonymize_token(access_token),
                        "new_token": anonymize_token(new_access_token),
                        "email": email,
                        "status": "成功",
                    },
                )
                if progress_id:
                    self.update_relogin_progress(progress_id, access_token, "成功")
            else:
                # 登录失败
                error_type = result.get("error", "")
                if error_type == "password_verify_failed_403" and isinstance(result.get("detail"), dict):
                    log_service.add(
                        LOG_TYPE_ACCOUNT,
                        "更新账号",
                        {
                            "source": event,
                            "token": anonymize_token(access_token),
                            "email": email,
                            "status": "失败",
                            "error": error_type,
                            "detail": result.get("detail", {}),
                        },
                    )
                    detail_error = result["detail"].get("error", {})
                    if isinstance(detail_error, dict) and detail_error.get("code") == "account_deactivated":
                        # Free automation accounts frequently surface account_deactivated during
                        # password rescue after token revoke. Prefer soft 异常 so pool stats stay honest
                        # and register refill remains the recovery path. Paid plans still hard-disable.
                        acc_now = self.get_account(access_token) or {}
                        if self._is_free_account(acc_now):
                            # PATCH_MARKER free_deactivated_disable_r24
                            self.update_account(
                                access_token,
                                {
                                    "status": "禁用",
                                    "quota": 0,
                                    "last_refresh_error": "account_deactivated_free",
                                    "last_refresh_error_at": datetime.now(timezone.utc).isoformat(),
                                },
                                quiet=True,
                                sync_capabilities=False,
                            )
                            log_service.add(
                                LOG_TYPE_ACCOUNT,
                                "免费号停用信号-已禁用",
                                {
                                    "source": event,
                                    "token": anonymize_token(access_token),
                                    "email": email,
                                    "detail": result.get("detail", {}),
                                },
                            )
                            if progress_id:
                                self.update_relogin_progress(progress_id, access_token, "禁用", "account_deactivated_free")
                            self._sync_risk_control_capabilities()
                            return
                        self.update_account(
                            access_token,
                            {
                                "status": "禁用",
                                "quota": 0,
                                "last_refresh_error": "account_deactivated",
                                "last_refresh_error_at": datetime.now(timezone.utc).isoformat(),
                            },
                            quiet=True,
                            sync_capabilities=False,
                        )
                        account = self.get_account(access_token) or {}
                        log_service.add(
                            LOG_TYPE_ACCOUNT,
                            "账号已停用-标记禁用",
                            {
                                "source": event,
                                "token": anonymize_token(access_token),
                                "email": email,
                                "detail": result.get("detail", {}),
                            },
                        )
                        if progress_id:
                            self.update_relogin_progress(progress_id, access_token, "禁用")
                    else:
                        # 永久故障：将账号标记为异常（或自动移除）
                        self.remove_invalid_token(
                            access_token,
                            f"{event}:password_relogin_failed",
                            quiet=True,
                            sync_capabilities=False,
                        )
                        if progress_id:
                            self.update_relogin_progress(progress_id, access_token, "异常", error_type)
                else:
                    log_service.add(
                        LOG_TYPE_ACCOUNT,
                        "更新账号",
                        {
                            "source": event,
                            "token": anonymize_token(access_token),
                            "email": email,
                            "status": "失败",
                            "error": error_type,
                            "detail": result.get("detail", {}),
                        },
                    )
                    # OTP / incomplete signup rescue cannot finish without mailbox code.
                    # Keep soft 异常 + reason; avoid hard-disable and avoid delete thrash.
                    soft_only = error_type in {
                        "need_verification_code",
                        "password_verify_failed_403",
                        "unsupported_country_region_territory",
                    }
                    if soft_only:
                        try:
                            self.update_account(
                                access_token,
                                {
                                    "status": "异常",
                                    "last_refresh_error": str(error_type)[:200],
                                    "last_refresh_error_at": datetime.now(timezone.utc).isoformat(),
                                },
                                quiet=True,
                                sync_capabilities=False,
                            )
                        except Exception:
                            pass
                        if progress_id:
                            self.update_relogin_progress(progress_id, access_token, "异常", error_type)
                    else:
                        # 永久故障：将账号标记为异常（或自动移除）
                        self.remove_invalid_token(
                            access_token,
                            f"{event}:password_relogin_failed",
                            quiet=True,
                            sync_capabilities=False,
                        )
                        if progress_id:
                            self.update_relogin_progress(progress_id, access_token, "异常", error_type)
        except Exception as exc:
            log_service.add(
                LOG_TYPE_ACCOUNT,
                "更新账号",
                {
                    "source": event,
                    "token": anonymize_token(access_token),
                    "email": email,
                    "status": "异常",
                    "error": str(exc),
                },
            )
            # 将账号标记为异常（或自动移除）
            self.remove_invalid_token(
                access_token,
                f"{event}:password_relogin_exception",
                quiet=True,
                sync_capabilities=False,
            )
            if progress_id:
                self.update_relogin_progress(progress_id, access_token, "异常", str(exc))
        self._sync_risk_control_capabilities()

    def _login_with_password(self, email: str, password: str) -> dict:
        """通过邮箱+密码登录，返回 {access_token, refresh_token, id_token, ...}"""
        from curl_cffi import requests

        # 常量
        auth_base = "https://auth.openai.com"
        platform_oauth_audience = "https://api.openai.com/v1"
        platform_auth0_client = "eyJuYW1lIjoiYXV0aDAtc3BhLWpzIiwidmVyc2lvbiI6IjEuMjEuMCJ9"
        platform_oauth_client_id = self._OAUTH_CLIENT_ID
        platform_oauth_redirect_uri = "https://platform.openai.com/auth/callback"
        user_agent = self._OAUTH_USER_AGENT

        # 创建 session
        session_kwargs = {"impersonate": "chrome110", "verify": False}
        proxy = config.get_proxy_settings()
        if proxy:
            session_kwargs["proxy"] = proxy
        session = requests.Session(**session_kwargs)

        try:
            device_id = str(uuid.uuid4())

            # ─── 方式2: OAuth authorize 流程 ──────────────────────────
            # 使用 Platform Client + PKCE（与注册流程相同）

            from utils.pkce import generate_pkce
            code_verifier, code_challenge = generate_pkce()

            # ② 发起 OAuth authorize 请求 (使用 Platform Client + PKCE)
            session.cookies.set("oai-did", device_id, domain=".auth.openai.com")
            session.cookies.set("oai-did", device_id, domain="auth.openai.com")
            params = {
                "issuer": auth_base,
                "client_id": platform_oauth_client_id,
                "audience": platform_oauth_audience,
                "redirect_uri": platform_oauth_redirect_uri,
                "device_id": device_id,
                "screen_hint": "login_or_signup",
                "max_age": "0",
                "login_hint": email,
                "scope": "openid profile email offline_access",
                "response_type": "code",
                "response_mode": "query",
                "state": secrets.token_urlsafe(32),
                "nonce": secrets.token_urlsafe(32),
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "auth0Client": platform_auth0_client,
            }
            authorize_url = f"{auth_base}/api/accounts/authorize?{urlencode(params)}"
            resp = session.get(
                authorize_url,
                headers={
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "user-agent": user_agent,
                    "sec-ch-ua": '"Chromium";v="145", "Google Chrome";v="145", "Not/A)Brand";v="99"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                    "sec-fetch-dest": "document",
                    "sec-fetch-mode": "navigate",
                    "sec-fetch-site": "cross-site",
                    "sec-fetch-user": "?1",
                    "upgrade-insecure-requests": "1",
                    "referer": "https://platform.openai.com/",
                },
                allow_redirects=True,
                timeout=30,
            )

            if resp.status_code not in (200, 302):
                return {"ok": False, "error": f"authorize_failed_{resp.status_code}", "detail": {"url": resp.url, "text": resp.text[:500]}}

            # 检测最终 URL 是否指向错误页面
            final_url = str(resp.url)
            if "/error" in final_url and "payload=" in final_url:
                from urllib.parse import parse_qs, urlparse
                try:
                    parsed_query = parse_qs(urlparse(final_url).query)
                    error_payload_b64 = parsed_query.get("payload", [""])[0]
                    error_payload_b64 += "=" * ((4 - len(error_payload_b64) % 4) % 4)
                    error_payload = json.loads(base64.b64decode(error_payload_b64))
                    error_code = error_payload.get("errorCode", "")
                    if error_code == "rate_limit_exceeded":
                        return {"ok": False, "error": "rate_limit_exceeded", "detail": error_payload}
                    else:
                        return {"ok": False, "error": f"authorize_error_{error_code}", "detail": error_payload}
                except Exception as e:
                    return {"ok": False, "error": "authorize_redirect_error", "detail": {"url": final_url, "parse_error": str(e)}}

            # ③ 提交密码验证
            login_headers = {
                "accept": "application/json",
                "accept-language": "zh-CN,zh;q=0.9",
                "content-type": "application/json",
                "origin": auth_base,
                "priority": "u=1, i",
                "user-agent": user_agent,
                "sec-ch-ua": '"Chromium";v="145", "Google Chrome";v="145", "Not/A)Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "referer": f"{auth_base}/email-verification",
                "oai-device-id": device_id,
            }

            # 添加 sentinel token
            try:
                from utils.sentinel import build_sentinel_token
                sentinel_val, oai_sc_val = build_sentinel_token(session, device_id, "password_verify")
                login_headers["openai-sentinel-token"] = sentinel_val
                if oai_sc_val:
                    session.cookies.set("oai-sc", oai_sc_val, domain=".openai.com")
            except Exception:
                pass

            login_resp = session.post(
                f"{auth_base}/api/accounts/password/verify",
                headers=login_headers,
                json={"password": password},
                timeout=30,
            )

            login_data = {}
            try:
                login_data = login_resp.json() if login_resp.text else {}
            except Exception:
                pass

            if login_resp.status_code != 200:
                error_code = login_data.get("error", {}).get("code", "")
                error_msg = login_data.get("error", {}).get("message", "")
                if login_resp.status_code == 403 or error_code == "unsupported_country_region_territory":
                    report_dynamic_proxy_denial(
                        proxy,
                        target="auth.openai.com:443",
                        status_code=login_resp.status_code,
                        reason=error_code or f"password_verify_http_{login_resp.status_code}",
                        detail={"stage": "password_verify", "message": error_msg},
                    )
                if error_code == "unsupported_country_region_territory":
                    return {"ok": False, "error": "unsupported_country_region_territory", "detail": login_data}
                elif error_code == "invalid_state":
                    return {"ok": False, "error": "invalid_state", "detail": login_data}
                elif "Invalid credentials" in error_msg or "wrong password" in error_msg.lower():
                    return {"ok": False, "error": "invalid_password", "detail": login_data}
                return {"ok": False, "error": f"password_verify_failed_{login_resp.status_code}", "detail": login_data}

            # 获取 authorization code
            continue_url = str(login_data.get("continue_url") or "").strip()
            auth_code = ""
            if continue_url:
                from urllib.parse import parse_qs, urlparse
                parsed_params = parse_qs(urlparse(continue_url).query)
                auth_code = str((parsed_params.get("code") or [""])[0]).strip()

            # ─── 处理 about-you / 邮箱 OTP 等中间页 ──────────────────────────
            if not auth_code:
                page_type = ""
                page_info = login_data.get("page")
                if isinstance(page_info, dict):
                    page_type = str(page_info.get("type") or "")
                continue_path = ""
                try:
                    from urllib.parse import urlparse
                    continue_path = (urlparse(continue_url).path or "").lower()
                except Exception:
                    continue_path = str(continue_url or "").lower()

                needs_about_you = (
                    page_type in {"about_you", "about-you", "create_account"}
                    or "about-you" in continue_path
                    or "about_you" in continue_path
                    or "create-account" in continue_path
                )
                if page_type == "email_otp_verification":
                    # 需要验证码才能登录，直接标记为账号异常
                    return {"ok": False, "error": "need_verification_code", "detail": login_data}
                if needs_about_you:
                    # 账号已创建但未完成资料页：补 create_account 以换取 auth code
                    import random
                    first_names = [
                        "James", "Mary", "John", "Patricia", "Robert", "Jennifer",
                        "Michael", "Linda", "William", "Elizabeth", "David", "Barbara",
                        "Olivia", "Emma", "Noah", "Ava", "Liam", "Sophia",
                    ]
                    last_names = [
                        "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia",
                        "Miller", "Davis", "Wilson", "Moore", "Taylor", "Anderson",
                        "Thomas", "Jackson", "White", "Harris", "Martin", "Thompson",
                    ]
                    name = f"{random.choice(first_names)} {random.choice(last_names)}"
                    year = random.randint(1985, 2002)
                    month = random.randint(1, 12)
                    day = random.randint(1, 28)
                    birthdate = f"{year:04d}-{month:02d}-{day:02d}"
                    create_headers = dict(login_headers)
                    create_headers["referer"] = f"{auth_base}/about-you"
                    try:
                        from utils.sentinel import build_sentinel_token
                        sentinel_val, oai_sc_val = build_sentinel_token(session, device_id, "oauth_create_account")
                        create_headers["openai-sentinel-token"] = sentinel_val
                        if oai_sc_val:
                            session.cookies.set("oai-sc", oai_sc_val, domain=".openai.com")
                    except Exception:
                        pass
                    create_resp = session.post(
                        f"{auth_base}/api/accounts/create_account",
                        headers=create_headers,
                        json={"name": name, "birthdate": birthdate},
                        timeout=30,
                    )
                    create_data = {}
                    try:
                        create_data = create_resp.json() if create_resp.text else {}
                    except Exception:
                        pass
                    if create_resp.status_code not in (200, 302):
                        err = create_data.get("error") if isinstance(create_data.get("error"), dict) else {}
                        code = str((err or {}).get("code") or "")
                        return {
                            "ok": False,
                            "error": f"about_you_create_account_failed_{create_resp.status_code}",
                            "detail": {
                                "code": code,
                                "create_data": create_data,
                                "login_data": login_data,
                            },
                        }
                    continue_url = str(create_data.get("continue_url") or "").strip()
                    if continue_url:
                        from urllib.parse import parse_qs, urlparse
                        parsed_params = parse_qs(urlparse(continue_url).query)
                        auth_code = str((parsed_params.get("code") or [""])[0]).strip()
                    if not auth_code:
                        return {
                            "ok": False,
                            "error": "about_you_no_auth_code",
                            "detail": {"create_data": create_data, "login_data": login_data},
                        }
                else:
                    return {"ok": False, "error": "no_auth_code", "detail": login_data}

            # ④ 用 code 换 token (使用 Platform Client + code_verifier，与注册流程相同)
            platform_base = "https://platform.openai.com"
            token_resp = session.post(
                f"{auth_base}/api/accounts/oauth/token",
                headers={
                    "accept": "*/*",
                    "accept-language": "zh-CN,zh;q=0.9",
                    "auth0-client": platform_auth0_client,
                    "cache-control": "no-cache",
                    "content-type": "application/json",
                    "origin": platform_base,
                    "pragma": "no-cache",
                    "priority": "u=1, i",
                    "referer": f"{platform_base}/",
                    "sec-ch-ua": '"Chromium";v="145", "Google Chrome";v="145", "Not/A)Brand";v="99"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                    "sec-fetch-dest": "empty",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-site",
                    "user-agent": user_agent,
                },
                json={
                    "client_id": platform_oauth_client_id,
                    "code_verifier": code_verifier,
                    "grant_type": "authorization_code",
                    "code": auth_code,
                    "redirect_uri": platform_oauth_redirect_uri,
                },
                verify=False,
                timeout=60,
            )

            token_data = {}
            try:
                token_data = token_resp.json() if token_resp.text else {}
            except Exception:
                pass

            if token_resp.status_code != 200 or not token_data.get("access_token"):
                return {"ok": False, "error": "token_exchange_failed", "detail": token_data}

            access_token = str(token_data.get("access_token") or "").strip()
            refresh_token = str(token_data.get("refresh_token") or "").strip()
            id_token = str(token_data.get("id_token") or "").strip()

            # ⑤ 用 access_token 获取用户信息
            user_info = {}
            try:
                me_resp = session.get(
                    "https://chatgpt.com/backend-api/me",
                    headers={
                        "accept": "application/json",
                        "authorization": f"Bearer {access_token}",
                        "user-agent": user_agent,
                    },
                    timeout=30,
                )
                if me_resp.status_code == 200:
                    user_info = me_resp.json() if me_resp.text else {}
            except Exception:
                pass

            # 解析 JWT payload
            jwt_payload = self._decode_jwt_payload(access_token)

            email_from_jwt = str(jwt_payload.get("https://api.openai.com/profile", {}).get("email") or "").strip()
            account_id_from_jwt = str(
                jwt_payload.get("https://api.openai.com/auth", {}).get("chatgpt_account_id") or ""
            ).strip()

            account_info = user_info.get("account") if isinstance(user_info.get("account"), dict) else {}
            result = {
                "ok": True,
                "email": email_from_jwt or email,
                "account_id": account_id_from_jwt or account_info.get("account_id", ""),
                "access_token": access_token,
                "refresh_token": refresh_token,
                "id_token": id_token,
                "expires_at": jwt_payload.get("exp"),
                "source_type": "password",
            }

            return result

        finally:
            session.close()

    def list_expiring_access_tokens(self) -> list[str]:
        with self._lock:
            return [
                token
                for account in self._accounts.values()
                if str(account.get("refresh_token") or "").strip()
                and (token := str(account.get("access_token") or "").strip())
                and self._token_needs_refresh(token)
            ]

    def list_refresh_token_keepalive_tokens(self) -> list[str]:
        now = datetime.now(timezone.utc)
        due_items: list[tuple[datetime, str]] = []
        with self._lock:
            for account in self._accounts.values():
                status = str(account.get("status") or "").strip()
                if status in {"禁用", "异常", "限流"}:
                    continue
                # avoid repeatedly force-refreshing permanently invalidated sessions
                err = str(account.get("last_token_refresh_error") or account.get("last_refresh_error") or "").lower()
                if any(m in err for m in ("refresh_token_invalidated", "session has ended", "invalid_grant", "token_revoked", "app_session_terminated")):
                    continue
                due_at = self._refresh_token_keepalive_due_at(account, now)
                token = str(account.get("access_token") or "").strip()
                if due_at is not None and token:
                    due_items.append((due_at, token))
        due_items.sort(key=lambda item: item[0])
        return [token for _, token in due_items[: self._REFRESH_TOKEN_KEEPALIVE_BATCH_SIZE]]

    def keepalive_refresh_tokens(self, access_tokens: list[str]) -> dict[str, Any]:
        access_tokens = list(dict.fromkeys(token for token in access_tokens if token))
        if not access_tokens:
            return {"refreshed": 0, "errors": [], "items": self.list_accounts()}

        refreshed = 0
        errors = []
        for access_token in access_tokens:
            before = self.resolve_access_token(access_token)
            after = self.refresh_access_token(before, force=True, event="refresh_token_keepalive")
            account = self.get_account(after)
            if account and str(account.get("last_token_refresh_error") or "").strip():
                errors.append({
                    "token": anonymize_token(before),
                    "error": str(account.get("last_token_refresh_error") or "refresh token failed"),
                })
                continue
            if account:
                refreshed += 1

        return {
            "refreshed": refreshed,
            "errors": errors,
            "items": self.list_accounts(),
            "relogined": 0,
        }

    def list_tokens(self) -> list[str]:
        with self._lock:
            return list(self._accounts)

    def _list_ready_candidate_tokens(
            self,
            excluded_tokens: set[str] | None = None,
            plan_type: str | None = None,
            source_type: str | None = None,
            plan_types: set[str] | tuple[str, ...] | None = None,
    ) -> list[str]:
        excluded = set(excluded_tokens or set())
        def _image_ready(item: dict) -> bool:
            if not self._is_image_account_available(item):
                return False
            # Soft 异常 with hard-dead access tokens are not image-ready; keep them in pool
            # for stats/relogin policy, but do not select them for generations.
            if self._access_token_hard_dead(item):
                return False
            if not self._account_matches_plan_type(item, plan_type):
                return False
            if not self._account_matches_any_plan_type(item, plan_types):
                return False
            if not self._account_matches_source_type(item, source_type):
                return False
            if self._capability_allows(item, "image"):
                return True
            # Belt-and-suspenders: free/unknown soft abnormal with quota still image-eligible
            # even if stale capability snapshot fully wiped image=false.
            status = str(item.get("status") or "").strip()
            plan = str(item.get("type") or item.get("account_type") or item.get("plan") or "free").strip().lower()
            quota = int(item.get("quota") or 0)
            if status in {"正常", "异常"} and quota > 0 and (plan in {"", "free", "unknown", "null", "none"} or "free" in plan):
                return True
            return False

        ready = []
        for item in self._accounts.values():
            if not _image_ready(item):
                continue
            token = item.get("access_token") or ""
            if not token or token in excluded:
                continue
            ready.append((token, item))
        if not ready:
            return []
        # Hard preference: if any healthy 正常 candidates remain, do not waste rotate budget
        # on soft 异常 tokens that are usually already token_revoked for both chat and image.
        # Soft remains available only as fallback when no 正常 image candidate is left.
        # PATCH_MARKER image_select_normal_only_when_available_r11b
        normals = [tok for tok, item in ready if str(item.get("status") or "").strip() == "正常"]
        if normals:
            return normals
        return [tok for tok, _ in ready]

    def _list_available_candidate_tokens(
            self,
            excluded_tokens: set[str] | None = None,
            plan_type: str | None = None,
            source_type: str | None = None,
            plan_types: set[str] | tuple[str, ...] | None = None,
    ) -> list[str]:
        max_concurrency = max(1, int(config.image_account_concurrency or 1))
        tokens = [
            token
            for token in self._list_ready_candidate_tokens(excluded_tokens, plan_type, source_type, plan_types)
            if int(self._image_inflight.get(token, 0)) < max_concurrency
        ]
        # Prefer healthy 正常 first, then soft 异常 as fallback.
        # Within each tier: no refresh/token error, lower invalid_count, newer created_at.
        # PATCH_MARKER image_select_normal_first_r11
# PATCH_MARKER hard_dead_free_disable_r23
# PATCH_MARKER hard_dead_markers_r23
# PATCH_MARKER image_skip_hard_dead_soft_r12
        def _score(tok: str):
            acc = self._accounts.get(tok) or {}
            status = str(acc.get("status") or "").strip()
            # 0 = 正常 (best), 1 = soft 异常 fallback, 2 = other
            if status == "正常":
                status_rank = 0
            elif status == "异常":
                status_rank = 1
            else:
                status_rank = 2
            err_blob = " ".join(
                str(acc.get(k) or "")
                for k in (
                    "last_token_refresh_error",
                    "last_refresh_error",
                    "last_error",
                    "error",
                    "note",
                )
            ).lower()
            token_dead = 1 if any(
                x in err_blob
                for x in (
                    "token_revoked",
                    "token_invalidated",
                    "token_invalid",
                    "invalidated oauth",
                    "account_deactivated",
                )
            ) else 0
            refresh_err = 1 if err_blob.strip() else 0
            created = self._parse_time(acc.get("created_at"))
            created_ts = created.timestamp() if created is not None else 0.0
            invalid = int(acc.get("invalid_count") or 0)
            # sort ascending: smaller is better
            return (status_rank, token_dead, refresh_err, invalid, -created_ts)
        tokens.sort(key=_score)
        return tokens

    def _acquire_next_candidate_token(
            self,
            excluded_tokens: set[str] | None = None,
            plan_type: str | None = None,
            source_type: str | None = None,
            plan_types: set[str] | tuple[str, ...] | None = None,
    ) -> str:
        # 有候选但并发槽被占满时，有界等待；避免无限阻塞导致 /v1/images 客户端超时后服务端仍占坑
        max_wait_secs = 12.0
        stale_hold_secs = 45.0
        started = time.time()
        with self._image_slot_condition:
            while True:
                # 清理长时间未释放的 inflight（进程中断/上游 hang）
                now = time.time()
                stale_tokens = []
                for tok, meta in list(getattr(self, "_image_inflight_meta", {}).items()):
                    acquired_at = float(meta.get("acquired_at") or 0)
                    if acquired_at and now - acquired_at >= stale_hold_secs:
                        stale_tokens.append(tok)
                for tok in stale_tokens:
                    self._image_inflight.pop(tok, None)
                    if hasattr(self, "_image_inflight_meta"):
                        self._image_inflight_meta.pop(tok, None)
                    log_service.add(
                        LOG_TYPE_ACCOUNT,
                        "释放过期图片并发槽",
                        {"token": anonymize_token(tok), "stale_hold_secs": stale_hold_secs},
                    )
                if stale_tokens:
                    self._image_slot_condition.notify_all()

                if not self._list_ready_candidate_tokens(excluded_tokens, plan_type, source_type, plan_types):
                    raise RuntimeError(
                        f"no available {plan_type or source_type or ''} image quota".replace("  ", " ").strip()
                        if plan_type or source_type else "no available image quota"
                    )
                tokens = self._list_available_candidate_tokens(excluded_tokens, plan_type, source_type, plan_types)
                if tokens:
                    # 新号优先；仍用 index 在前几名里轮转，避免总打同一号
                    top_n = min(3, len(tokens))
                    access_token = tokens[self._index % top_n]
                    self._index += 1
                    self._image_inflight[access_token] = int(self._image_inflight.get(access_token, 0)) + 1
                    if not hasattr(self, "_image_inflight_meta"):
                        self._image_inflight_meta = {}
                    self._image_inflight_meta[access_token] = {"acquired_at": time.time()}
                    return access_token
                if time.time() - started >= max_wait_secs:
                    raise RuntimeError("image account slots busy; retry later")
                self._image_slot_condition.wait(timeout=1.0)

    def release_image_slot(self, access_token: str) -> None:
        if not access_token:
            return
        with self._image_slot_condition:
            access_token = self._resolve_access_token_locked(access_token)
            current_inflight = int(self._image_inflight.get(access_token, 0))
            if current_inflight <= 1:
                self._image_inflight.pop(access_token, None)
                if hasattr(self, "_image_inflight_meta"):
                    self._image_inflight_meta.pop(access_token, None)
            else:
                self._image_inflight[access_token] = current_inflight - 1
                # 保留最近一次 acquire 时间，避免过早判定 stale
            self._image_slot_condition.notify_all()

    def get_available_access_token(
            self,
            plan_type: str | None = None,
            source_type: str | None = None,
            plan_types: set[str] | tuple[str, ...] | None = None,
    ) -> str:
        """从候选池中获取一个可用的图片生图 token。

        先走本地快路径：对画像完整、状态正常且没有异常痕迹的账号直接放行。
        只有在本地状态不够干净时，才同步调用 fetch_remote_info 做远程修复/验证。
        限制最大尝试次数防止 token rotation 导致无限循环。
        """
        max_attempts = 20  # 防止无限循环
        attempted_tokens: set[str] = set()
        for _attempt in range(max_attempts):
            access_token = self._acquire_next_candidate_token(
                excluded_tokens=attempted_tokens,
                plan_type=plan_type,
                source_type=source_type,
                plan_types=plan_types,
            )
            attempted_tokens.add(access_token)
            account = self.get_account(access_token) or {}
            if not account:
                self.release_image_slot(access_token)
                continue
            if self._is_image_account_available(account) and self._should_skip_remote_image_preflight(account):
                return access_token
            try:
                account = self.fetch_remote_info(
                    access_token,
                    "get_available_access_token",
                    # 始终暂缓删除：auto_remove 只影响最终 remove_invalid_token 行为
                    defer_invalid_removal=True,
                )
            except Exception:
                self.release_image_slot(access_token)
                continue
            # fetch_remote_info 内部可能因 token rotation 导致 access_token 变化，
            # 把新 token 也加入排除列表，防止重复尝试
            resolved = str((account or {}).get("access_token") or "")
            if resolved and resolved != access_token:
                attempted_tokens.add(resolved)
            if (
                    self._is_image_account_available(account or {})
                    and self._account_matches_plan_type(account or {}, plan_type)
                    and self._account_matches_any_plan_type(account or {}, plan_types)
                    and self._account_matches_source_type(account or {}, source_type)
            ):
                return str((account or {}).get("access_token") or access_token)
            self.release_image_slot(access_token)
        raise RuntimeError(
            f"no available {plan_type or source_type or ''} image quota (tried {len(attempted_tokens)} tokens)".replace("  ", " ").strip()
            if plan_type or source_type else f"no available image quota (tried {len(attempted_tokens)} tokens)"
        )

    @staticmethod
    def _text_candidate_score(account: dict) -> tuple:
        status_penalty = 0 if account.get("status") == "正常" else 1
        refresh_error_penalty = 1 if str(account.get("last_token_refresh_error") or account.get("last_refresh_error") or "").strip() else 0
        invalid_count = int(account.get("invalid_count") or 0)
        fail_count = int(account.get("fail") or 0)
        success_bonus = -int(account.get("success") or 0)
        created = AccountService._parse_time(account.get("created_at"))
        created_ts = created.timestamp() if created else 0.0
        # 越小越好；新号优先（-created_ts）
        return (status_penalty, refresh_error_penalty, invalid_count, fail_count, success_bonus, -created_ts)

    def get_text_access_token(self, excluded_tokens: set[str] | None = None) -> str:
        """Pick a token for chat/text only.

        Soft-abnormal free accounts (text_stream:token_revoked with remaining image quota)
        must stay eligible for image selection, but they must NOT be selected for chat.
        Otherwise rotate budgets are wasted on already-revoked text tokens and soft_chat
        fails even when healthy 正常 accounts remain.
        """
        excluded = set(excluded_tokens or set())
        with self._lock:
            candidates = []
            for account in self._accounts.values():
                status = str(account.get("status") or "").strip()
                token = str(account.get("access_token") or "").strip()
                if not token or token in excluded:
                    continue
                # Chat strictly uses healthy accounts. Soft 异常 free keep image only.
                if status != "正常":
                    continue
                if not self._capability_allows(account, "chat"):
                    continue
                candidates.append(account)
            if not candidates:
                return ""
            candidates.sort(key=self._text_candidate_score)
            account = candidates[self._text_index % len(candidates)]
            self._text_index += 1
            access_token = str(account.get("access_token") or "").strip()
        return self.refresh_access_token(access_token, event="get_text_access_token") or access_token

    def mark_text_used(self, access_token: str) -> None:
        if not access_token:
            return
        with self._lock:
            access_token = self._resolve_access_token_locked(access_token)
            current = self._accounts.get(access_token)
            if current is None:
                return
            next_item = dict(current)
            next_item["last_used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            next_item["success"] = int(next_item.get("success") or 0) + 1
            account = self._normalize_account(next_item)
            if account is None:
                return
            self._accounts[access_token] = account
            self._save_accounts()

    def remove_invalid_token(
        self,
        access_token: str,
        event: str,
        quiet: bool = False,
        sync_capabilities: bool = True,
    ) -> bool:
        account = self.get_account(access_token) or {}
        email = str(account.get("email") or "").strip()
        password = str(account.get("password") or "").strip()
        low_event = str(event or "").lower()
        # Merge event + account error fields so hard-dead detection is consistent with selection.
        probe = dict(account)
        probe["last_refresh_error"] = str(event or account.get("last_refresh_error") or "")[:500]
        probe["last_token_refresh_error"] = str(
            account.get("last_token_refresh_error") or event or ""
        )[:500]
        hard_dead = self._access_token_hard_dead(probe) or any(
            m in low_event
            for m in (
                "token invalidated",
                "token_invalidated",
                "invalidated oauth",
                "account_deactivated",
                "refresh_token_invalidated",
                "session has ended",
                "invalid_grant",
                "app_session_terminated",
                "oauth_refresh_http_401",
                "oauth_refresh_http_403",
            )
        )
        chat_soft_only = (
            ("text_stream:token_revoked" in low_event or low_event.strip() == "token_revoked")
            and not hard_dead
        )
        # PATCH_MARKER hard_dead_free_disable_r23
        # Hard-dead accounts must not stay as soft「异常」with fake quota.
        # Disable them (keep row for audit). Chat-only soft revoke continues soft-keep path.
        if hard_dead and not chat_soft_only:
            try:
                self.release_image_slot(access_token)
            except Exception:
                pass
            self.update_account(
                access_token,
                {
                    "status": "禁用",
                    "quota": 0,
                    "last_refresh_error": str(event or "hard_dead")[:200],
                    "last_refresh_error_at": datetime.now(timezone.utc).isoformat(),
                    "invalid_count": int(account.get("invalid_count") or 0) + 1,
                },
                quiet=quiet,
                sync_capabilities=sync_capabilities,
            )
            if not quiet:
                log_service.add(
                    LOG_TYPE_ACCOUNT,
                    "hard-dead账号已禁用",
                    {
                        "source": event,
                        "token": anonymize_token(access_token),
                        "email": email,
                        "free": bool(self._is_free_account(account)),
                    },
                )
            return False
        # 有密码的账号：优先标记异常 + 异步密码重登，而不是直接删号
        if email and password and any(
            m in low_event
            for m in (
                "invalid_access_token",
                "refresh_accounts",
                "refresh_invalidated",
                "password_relogin_failed",
                "image_stream",
                "text_stream",
                "token_revoked",
                "refresh_token_keepalive",
                "get_available_access_token",
            )
        ):
            if config.auto_relogin_after_refresh or "password_relogin_failed" not in low_event:
                # 避免 password_relogin_failed 死循环；其余路径允许抢救一次
                if "password_relogin_failed" not in low_event:
                    if not self._is_free_account(account):
                        t = Thread(
                            target=self._password_re_login_thread,
                            args=(access_token, email, password, f"{event}:pre_remove_relogin"),
                            daemon=True,
                        )
                        t.start()
                        log_service.add(
                            LOG_TYPE_ACCOUNT,
                            "异常账号暂缓删除-尝试密码重登",
                            {"source": event, "token": anonymize_token(access_token), "email": email},
                        )
                    else:
                        log_service.add(
                            LOG_TYPE_ACCOUNT,
                            "异常账号暂缓删除-跳过免费号密码重登",
                            {"source": event, "token": anonymize_token(access_token), "email": email},
                        )
            # Free automation accounts frequently see temporary token_revoked on chat.
            # Keep them soft-flagged but do not wipe image capability via risk sync when
            # remaining quota is still positive; selection paths can rehydrate later.
            soft_free_keep = self._is_free_account(account) and int(account.get("quota") or 0) > 0
            self.update_account(
                access_token,
                {
                    "status": "异常",
                    "quota": int(account.get("quota") or 0),
                    "last_refresh_error": str(event or "invalid_token")[:200],
                    "last_refresh_error_at": datetime.now(timezone.utc).isoformat(),
                },
                quiet=quiet,
                # Avoid capability full-disable storm for free soft revoke.
                sync_capabilities=(False if soft_free_keep else sync_capabilities),
            )
            # 失效号立刻让出生图槽，避免 inflight 占坑导致全局 no available image quota
            try:
                self.release_image_slot(access_token)
            except Exception:
                pass
            return False

        # Prefer soft-disable over hard delete. Hard delete is too aggressive under
        # token/proxy jitter and can empty the account pool quickly.
        # Even when auto_remove_invalid_accounts=true, keep password accounts for relogin.
        if (not config.auto_remove_invalid_accounts) or (email and password):
            self.update_account(
                access_token,
                {
                    "status": "异常",
                    "quota": int(account.get("quota") or 0),
                    "invalid_count": int(account.get("invalid_count") or 0) + 1,
                },
                quiet=quiet,
                sync_capabilities=sync_capabilities,
            )
            try:
                self.release_image_slot(access_token)
            except Exception:
                pass
            if not quiet:
                log_service.add(
                    LOG_TYPE_ACCOUNT,
                    "标记异常账号-暂不删除",
                    {"source": event, "token": anonymize_token(access_token), "email": email},
                )
            return False
        removed = bool(self.delete_accounts([access_token], sync_capabilities=sync_capabilities)["removed"])
        if removed:
            log_service.add(LOG_TYPE_ACCOUNT, "自动移除异常账号",
                            {"source": event, "token": anonymize_token(access_token)})
        elif access_token:
            self.update_account(
                access_token,
                {"status": "异常", "quota": 0},
                quiet=quiet,
                sync_capabilities=sync_capabilities,
            )
        return removed

    def get_account(self, access_token: str) -> dict | None:
        if not access_token:
            return None
        with self._lock:
            access_token = self._resolve_access_token_locked(access_token)
            account = self._accounts.get(access_token)
            return dict(account) if account else None

    def list_accounts(self) -> list[dict]:
        """返回所有账号的副本，并为每个账号附加当前图片在途数 image_inflight。

        image_inflight 为内存态并发计数(账号正在生成、尚未结束的图片数)。号池空闲时
        若某账号该值持续 > 0，说明其并发槽位泄漏、已被静默排除出调度，可借此在 UI 上诊断。
        """
        with self._lock:
            result = []
            for item in self._accounts.values():
                account = dict(item)
                token = account.get("access_token") or ""
                account["image_inflight"] = int(self._image_inflight.get(token, 0))
                result.append(account)
            return result

    def list_limited_tokens(self) -> list[str]:
        with self._lock:
            return [
                token
                for item in self._accounts.values()
                if item.get("status") == "限流"
                   and (token := item.get("access_token") or "")
            ]

    def list_normal_tokens(self) -> list[str]:
        with self._lock:
            return [
                token
                for item in self._accounts.values()
                if item.get("status") == "正常"
                   and (token := item.get("access_token") or "")
            ]

    @staticmethod
    def _account_payload_token(item: dict) -> str:
        return str(item.get("access_token") or item.get("accessToken") or "").strip()

    @staticmethod
    def _prepare_account_payload(item: dict) -> dict | None:
        if not isinstance(item, dict):
            return None
        access_token = AccountService._account_payload_token(item)
        if not access_token:
            return None
        payload = dict(item)
        payload.pop("accessToken", None)
        payload["access_token"] = access_token
        # CPA/Codex 导出文件里的 `type=codex` 是导出格式，不是号池套餐类型。
        if str(payload.get("type") or "").strip().lower() == "codex":
            payload["export_type"] = "codex"
            payload["source_type"] = "codex"
            payload.pop("type", None)
        if str(payload.get("export_type") or "").strip().lower() == "codex":
            payload["source_type"] = "codex"
        if payload.get("plan_type") and not payload.get("type"):
            payload["type"] = str(payload.get("plan_type") or "").strip()
        return payload

    def add_account_items(self, items: list[dict]) -> dict:
        payloads = [
            payload
            for item in items
            if (payload := self._prepare_account_payload(item)) is not None
        ]
        return self._add_account_payloads(payloads)

    def add_accounts(self, tokens: list[str], source_type: str = "web") -> dict:
        tokens = list(dict.fromkeys(token for token in tokens if token))
        if not tokens:
            return {"added": 0, "skipped": 0, "items": self.list_accounts()}
        return self._add_account_payloads([
            {"access_token": token, "source_type": self._normalize_source_type(source_type)}
            for token in tokens
        ])

    def _add_account_payloads(self, payloads: list[dict]) -> dict:
        deduped: dict[str, dict] = {}
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            access_token = self._account_payload_token(payload)
            if not access_token:
                continue
            current = deduped.get(access_token, {})
            deduped[access_token] = {**current, **payload, "access_token": access_token}

        if not deduped:
            return {"added": 0, "skipped": 0, "items": self.list_accounts()}

        with self._lock:
            added = 0
            skipped = 0
            for access_token, payload in deduped.items():
                current = self._accounts.get(access_token)
                if current is None:
                    added += 1
                    self._cumulative_total += 1
                    self._save_cumulative_total()
                    current = {"created_at": self._now()}
                else:
                    skipped += 1
                incoming = dict(payload)
                if not incoming.get("created_at"):
                    incoming.pop("created_at", None)
                account = self._normalize_account(
                    {
                        **current,
                        **incoming,
                        "access_token": access_token,
                        "type": str(incoming.get("type") or current.get("type") or "free"),
                    }
                )
                if account is not None:
                    self._accounts[access_token] = account
            self._save_accounts()
            items = [dict(item) for item in self._accounts.values()]
            log_service.add(LOG_TYPE_ACCOUNT, f"新增 {added} 个账号，跳过 {skipped} 个",
                            {"added": added, "skipped": skipped})
        self._sync_risk_control_capabilities()
        return {"added": added, "skipped": skipped, "items": items}

    def delete_accounts(self, tokens: list[str], sync_capabilities: bool = True) -> dict:
        target_set = set(token for token in tokens if token)
        if not target_set:
            return {"removed": 0, "items": self.list_accounts()}
        with self._lock:
            target_set = {self._resolve_access_token_locked(token) for token in target_set if token}
            for token in target_set:
                current = self._accounts.get(token)
                if current is not None:
                    runtime_profile_service.delete_by_account(current)
            removed = sum(self._accounts.pop(token, None) is not None for token in target_set)
            for token in target_set:
                self._image_inflight.pop(token, None)
            self._token_aliases = {
                old: new
                for old, new in self._token_aliases.items()
                if old not in target_set and new not in target_set
            }
            if removed:
                if self._accounts:
                    self._index %= len(self._accounts)
                    self._text_index %= len(self._accounts)
                else:
                    self._index = 0
                    self._text_index = 0
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, f"删除 {removed} 个账号", {"removed": removed})
            items = [dict(item) for item in self._accounts.values()]
        if removed and sync_capabilities:
            self._sync_risk_control_capabilities()
        return {"removed": removed, "items": items}

    def update_account(
        self,
        access_token: str,
        updates: dict,
        quiet: bool = False,
        sync_capabilities: bool = True,
    ) -> dict | None:
        if not access_token:
            return None
        updated_account: dict | None = None
        with self._lock:
            access_token = self._resolve_access_token_locked(access_token)
            current = self._accounts.get(access_token)
            if current is None:
                return None
            account = self._normalize_account({**current, **updates, "access_token": access_token})
            if account is None:
                return None
            if account.get("status") == "限流" and config.auto_remove_rate_limited_accounts:
                runtime_profile_service.delete_by_account(current)
                self._accounts.pop(access_token, None)
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, "自动移除限流账号", {"token": anonymize_token(access_token)})
                updated_account = None
            else:
                self._accounts[access_token] = account
                self._save_accounts()
                updated_account = dict(account)
                if not quiet:
                    log_service.add(LOG_TYPE_ACCOUNT, "更新账号",
                                    {"token": anonymize_token(access_token), "status": account.get("status")})
        if not quiet and sync_capabilities:
            self._sync_risk_control_capabilities()
        return updated_account

    def get_runtime_profile(self, profile_id: str) -> dict | None:
        profile = runtime_profile_service.get(profile_id)
        return runtime_profile_service.public_profile(profile) if profile else None

    def list_runtime_profiles(self) -> list[dict[str, Any]]:
        return [runtime_profile_service.public_profile(profile) for profile in runtime_profile_service.list_profiles()]

    def cleanup_orphan_runtime_profiles(self) -> dict[str, Any]:
        with self._lock:
            accounts = [dict(item) for item in self._accounts.values()]
        result = runtime_profile_service.cleanup_orphan_profiles(accounts)
        log_service.add(
            LOG_TYPE_ACCOUNT,
            "清理孤儿画像",
            {"removed": result.get("removed", 0), "kept": result.get("kept", 0)},
        )
        return result

    def audit_runtime_profiles(self) -> dict[str, Any]:
        with self._lock:
            accounts = [dict(item) for item in self._accounts.values()]
        return runtime_profile_service.audit_accounts(accounts)

    def backfill_runtime_profiles(self, access_tokens: list[str] | None = None) -> dict[str, Any]:
        target_tokens = list(dict.fromkeys(token for token in (access_tokens or []) if token))
        with self._lock:
            if target_tokens:
                target_tokens = [self._resolve_access_token_locked(token) for token in target_tokens]
                accounts = [(token, dict(self._accounts.get(token) or {})) for token in target_tokens if self._accounts.get(token)]
            else:
                accounts = [(token, dict(item)) for token, item in self._accounts.items()]

            repaired = 0
            rebounded = 0
            updated_items: list[dict[str, Any]] = []
            for token, current in accounts:
                normalized, profile = runtime_profile_service.ensure_account_profile(current)
                if not normalized:
                    continue
                changed = normalized != current
                if changed:
                    repaired += 1
                if current.get("runtime_profile_id") != normalized.get("runtime_profile_id"):
                    rebounded += 1
                self._accounts[token] = normalized
                updated_items.append(dict(normalized))

            if updated_items:
                self._save_accounts()

        return {
            "repaired": repaired,
            "rebounded": rebounded,
            "items": updated_items if target_tokens else self.list_accounts(),
            "profiles": self.list_runtime_profiles(),
            "audit": self.audit_runtime_profiles(),
        }

    def repair_runtime_profile(self, access_token: str, profile_id: str | None = None) -> dict | None:
        access_token = str(access_token or "").strip()
        if not access_token:
            return None
        with self._lock:
            access_token = self._resolve_access_token_locked(access_token)
            current = self._accounts.get(access_token)
            if current is None:
                return None
            if profile_id:
                normalized_account, profile = runtime_profile_service.bind_account(profile_id, current)
            else:
                normalized_account, profile = runtime_profile_service.ensure_account_profile(current)
            if not normalized_account:
                return None
            self._accounts[access_token] = normalized_account
            self._save_accounts()
        return {
            "item": dict(normalized_account),
            "profile": runtime_profile_service.public_profile(profile),
            "audit": runtime_profile_service.audit_account(normalized_account),
        }

    def rebind_runtime_profile(self, access_token: str, profile_id: str | None = None) -> dict | None:
        access_token = str(access_token or "").strip()
        if not access_token:
            return None
        with self._lock:
            access_token = self._resolve_access_token_locked(access_token)
            current = self._accounts.get(access_token)
            if current is None:
                return None
            normalized_account, profile = runtime_profile_service.rebind_account(current, profile_id)
            if not normalized_account:
                return None
            self._accounts[access_token] = normalized_account
            self._save_accounts()
        return {
            "item": dict(normalized_account),
            "profile": runtime_profile_service.public_profile(profile),
            "audit": runtime_profile_service.audit_account(normalized_account),
        }

    def _record_refresh_success(self, access_token: str) -> None:
        with self._lock:
            access_token = self._resolve_access_token_locked(access_token)
            current = self._accounts.get(access_token)
            if current is None:
                return
            next_item = dict(current)
            next_item["invalid_count"] = 0
            next_item["last_invalid_at"] = None
            next_item["last_refresh_error"] = None
            next_item["last_refresh_error_at"] = None
            account = self._normalize_account(next_item)
            if account is not None:
                self._accounts[access_token] = account

    def _should_defer_invalid_token(self, account: dict | None, now: datetime) -> bool:
        if not isinstance(account, dict):
            return False
        created_at = self._parse_time(account.get("created_at"))
        if created_at is not None and (now - created_at).total_seconds() < self._NEW_ACCOUNT_INVALID_GRACE_SECONDS:
            return True
        last_invalid_at = self._parse_time(account.get("last_invalid_at"))
        invalid_count = int(account.get("invalid_count") or 0)
        if invalid_count <= 1:
            return True
        if last_invalid_at is not None and (now - last_invalid_at).total_seconds() < self._INVALID_CONFIRM_SECONDS:
            return True
        return False

    def _record_invalid_token_seen(
        self,
        access_token: str,
        event: str,
        error: str,
        defer_invalid_removal: bool = True,
    ) -> bool:
        now = datetime.now(timezone.utc)
        with self._lock:
            access_token = self._resolve_access_token_locked(access_token)
            current = self._accounts.get(access_token)
            if current is None:
                return True
            should_defer = defer_invalid_removal and self._should_defer_invalid_token(current, now)
            next_item = dict(current)
            next_item["invalid_count"] = int(next_item.get("invalid_count") or 0) + 1
            next_item["last_invalid_at"] = now.isoformat()
            next_item["last_refresh_error"] = str(error or "invalid access token")
            next_item["last_refresh_error_at"] = now.isoformat()
            account = self._normalize_account(next_item)
            if account is not None:
                self._accounts[access_token] = account
                self._save_accounts()
            if should_defer:
                log_service.add(
                    LOG_TYPE_ACCOUNT,
                    "暂缓标记异常账号",
                    {"source": event, "token": anonymize_token(access_token), "error": str(error or "")},
                )
                return False
        return True

    def mark_image_result(self, access_token: str, success: bool) -> dict | None:
        if not access_token:
            return None
        self.release_image_slot(access_token)
        with self._lock:
            access_token = self._resolve_access_token_locked(access_token)
            current = self._accounts.get(access_token)
            if current is None:
                return None
            next_item = dict(current)
            next_item["last_used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            image_quota_unknown = bool(next_item.get("image_quota_unknown"))
            if success:
                next_item["success"] = int(next_item.get("success") or 0) + 1
                if not image_quota_unknown:
                    next_item["quota"] = max(0, int(next_item.get("quota") or 0) - 1)
                if not image_quota_unknown and next_item["quota"] == 0:
                    next_item["status"] = "限流"
                    next_item["restore_at"] = next_item.get("restore_at") or None
                elif next_item.get("status") == "限流":
                    next_item["status"] = "正常"
            else:
                next_item["fail"] = int(next_item.get("fail") or 0) + 1
            account = self._normalize_account(next_item)
            if account is None:
                return None
            if account.get("status") == "限流" and config.auto_remove_rate_limited_accounts:
                runtime_profile_service.delete_by_account(current)
                self._accounts.pop(access_token, None)
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, "自动移除限流账号", {"token": anonymize_token(access_token)})
                return None
            self._accounts[access_token] = account
            self._save_accounts()
            return dict(account)
        return None

    def fetch_remote_info(
        self,
        access_token: str,
        event: str = "fetch_remote_info",
        defer_invalid_removal: bool = True,
    ) -> dict[str, Any] | None:
        if not access_token:
            raise ValueError("access_token is required")

        active_token = self.refresh_access_token(access_token, event=f"{event}:preflight") or access_token
        try:
            from services.openai_backend_api import InvalidAccessTokenError, OpenAIBackendAPI
            with OpenAIBackendAPI(active_token) as backend:
                result = backend.get_user_info()
        except InvalidAccessTokenError as exc:
            refreshed_token = self.refresh_access_token(active_token, force=True, event=f"{event}:invalid_access_token")
            if refreshed_token and refreshed_token != active_token:
                try:
                    with OpenAIBackendAPI(refreshed_token) as backend:
                        result = backend.get_user_info()
                except InvalidAccessTokenError as retry_exc:
                    retry_account = self.get_account(refreshed_token) or {"access_token": refreshed_token}
                    self._record_runtime_risk_event(str(retry_exc), account=retry_account, code="token_invalid", scope="account", raw={"phase": event, "kind": "invalid_access_token"})
                    if self._record_invalid_token_seen(
                        refreshed_token,
                        event,
                        str(retry_exc),
                        defer_invalid_removal=defer_invalid_removal,
                    ):
                        self.remove_invalid_token(
                            refreshed_token,
                            event,
                            quiet=True,
                            sync_capabilities=False,
                        )
                    raise
                active_token = refreshed_token
            else:
                active_account = self.get_account(active_token) or {"access_token": active_token}
                self._record_runtime_risk_event(str(exc), account=active_account, code="token_invalid", scope="account", raw={"phase": event, "kind": "invalid_access_token"})
                if self._record_invalid_token_seen(
                    active_token,
                    event,
                    str(exc),
                    defer_invalid_removal=defer_invalid_removal,
                ):
                    self.remove_invalid_token(
                        active_token,
                        event,
                        quiet=True,
                        sync_capabilities=False,
                    )
                raise
        except Exception as exc:
            active_account = self.get_account(active_token) or {"access_token": active_token}
            self._record_runtime_risk_event(str(exc), account=active_account, raw={"phase": event, "kind": "fetch_remote_info_exception"})
            raise
        self._record_refresh_success(active_token)
        return self.update_account(active_token, result, quiet=True, sync_capabilities=False)

    # ---- 刷新进度追踪 ----

    def init_refresh_progress(self, progress_id: str, total: int) -> None:
        """初始化刷新进度记录。"""
        with self._refresh_progress_lock:
            self._refresh_progress[progress_id] = {
                "total": total,
                "processed": 0,
                "done": False,
                "error": None,
                "status_counts": {"正常": 0, "限流": 0, "异常": 0, "禁用": 0},
                "total_quota": 0,
            }

    def update_refresh_progress(self, progress_id: str, token: str) -> None:
        """刷新单个账号后，更新进度计数。"""
        account = self.get_account(token)
        status = str(account.get("status") or "正常").strip() if account else "正常"
        quota = max(0, int(account.get("quota") or 0)) if account else 0

        with self._refresh_progress_lock:
            progress = self._refresh_progress.get(progress_id)
            if progress is None:
                return
            progress["processed"] += 1
            progress["status_counts"][status] = progress["status_counts"].get(status, 0) + 1
            progress["total_quota"] += quota

    def finish_refresh_progress(self, progress_id: str, result: dict | None = None, error: str | None = None) -> None:
        """标记刷新完成。"""
        with self._refresh_progress_lock:
            progress = self._refresh_progress.get(progress_id)
            if progress is None:
                return
            progress["done"] = True
            progress["result"] = result
            if error:
                progress["error"] = error

    def get_refresh_progress(self, progress_id: str) -> dict | None:
        """查询刷新进度。"""
        with self._refresh_progress_lock:
            progress = self._refresh_progress.get(progress_id)
            return dict(progress) if progress else None

    def clean_refresh_progress(self, progress_id: str) -> None:
        """清理过期进度记录。"""
        with self._refresh_progress_lock:
            self._refresh_progress.pop(progress_id, None)

    # ---- 重新登录进度追踪 ----

    def init_relogin_progress(self, progress_id: str, total: int) -> None:
        """初始化重新登录进度记录。"""
        with self._relogin_progress_lock:
            self._relogin_progress[progress_id] = {
                "total": total,
                "processed": 0,
                "done": False,
                "error": None,
                "results": [],
            }

    def update_relogin_progress(self, progress_id: str, token: str, status: str, error: str | None = None) -> None:
        """更新单个重新登录进度。当所有账号处理完毕时自动标记完成。"""
        with self._relogin_progress_lock:
            progress = self._relogin_progress.get(progress_id)
            if progress is None:
                return
            progress["processed"] += 1
            progress["results"].append({
                "token": anonymize_token(token),
                "status": status,
                "error": error,
            })
            if progress["processed"] >= progress["total"]:
                progress["done"] = True

    def finish_relogin_progress(self, progress_id: str, result: dict | None = None, error: str | None = None) -> None:
        """标记重新登录完成。"""
        with self._relogin_progress_lock:
            progress = self._relogin_progress.get(progress_id)
            if progress is None:
                return
            progress["done"] = True
            progress["result"] = result
            if error:
                progress["error"] = error

    def get_relogin_progress(self, progress_id: str) -> dict | None:
        """查询重新登录进度。"""
        with self._relogin_progress_lock:
            progress = self._relogin_progress.get(progress_id)
            return dict(progress) if progress else None

    def clean_relogin_progress(self, progress_id: str) -> None:
        """清理过期进度记录。"""
        with self._relogin_progress_lock:
            self._relogin_progress.pop(progress_id, None)

    def refresh_accounts(
        self,
        access_tokens: list[str],
        progress_id: str | None = None,
        defer_invalid_removal: bool = True,
    ) -> dict[str, Any]:
        access_tokens = list(dict.fromkeys(token for token in access_tokens if token))
        # 即使开启 auto_remove，也保留暂缓/宽限：避免代理抖动/瞬时 invalid 直接清空号池。
        # auto_remove 只决定 remove_invalid_token 是“删除”还是“标记异常”。
        if not access_tokens:
            items = self.list_accounts()
            result = {"refreshed": 0, "errors": [], "items": items, "relogined": 0}
            if progress_id:
                self.finish_refresh_progress(progress_id, result)
            return result

        refreshed = 0
        errors = []
        max_workers = min(10, len(access_tokens))

        if progress_id:
            self.init_refresh_progress(progress_id, len(access_tokens))

        executor = ThreadPoolExecutor(max_workers=max_workers)
        try:
            futures = {
                executor.submit(self.fetch_remote_info, token, "refresh_accounts", defer_invalid_removal): token
                for token in access_tokens
            }
            for future in as_completed(futures):
                token = futures[future]
                try:
                    account = future.result()
                except (KeyboardInterrupt, SystemExit):
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise
                except Exception as exc:
                    error_str = str(exc)
                    # TLS/代理连接错误是网络问题，不计入账号失败
                    from services.protocol.conversation import is_tls_connection_error
                    if not is_tls_connection_error(error_str):
                        errors.append({"token": anonymize_token(token), "error": error_str})
                else:
                    if account is not None:
                        refreshed += 1

                if progress_id:
                    self.update_refresh_progress(progress_id, token)
        except (KeyboardInterrupt, SystemExit):
            if progress_id:
                self.finish_refresh_progress(progress_id, error="cancelled")
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True, cancel_futures=True)

        # 自动重新登录异常账号（仅当配置开启时）
        relogined = 0
        if config.auto_relogin_after_refresh:
            for token in access_tokens:
                account = self.get_account(token)
                if not account:
                    continue
                status = str(account.get("status") or "").strip()
                if status != "异常":
                    continue
                email = str(account.get("email") or "").strip()
                password = str(account.get("password") or "").strip()
                if not email or not password:
                    continue
                t = Thread(
                    target=self._password_re_login_thread,
                    args=(token, email, password, "auto_relogin_after_refresh"),
                    daemon=True,
                )
                t.start()
                relogined += 1

        result = {
            "refreshed": refreshed,
            "errors": errors,
            "items": self.list_accounts(),
            "relogined": relogined,
        }
        self._sync_risk_control_capabilities()

        if progress_id:
            self.finish_refresh_progress(progress_id, result)

        return result

    def re_login_accounts(self, access_tokens: list[str], progress_id: str | None = None) -> dict[str, Any]:
        """对选中账号执行密码重新登录流程。

        仅对包含 email + password 的账号有效。
        登录成功后自动将状态设为"正常"。
        """
        access_tokens = list(dict.fromkeys(token for token in access_tokens if token))
        if not access_tokens:
            result = {"relogined": 0, "skipped": 0, "errors": [], "items": self.list_accounts()}
            if progress_id:
                self.finish_relogin_progress(progress_id, result)
            return result

        if progress_id:
            self.init_relogin_progress(progress_id, len(access_tokens))

        relogined = 0
        skipped = 0
        errors = []

        for token in access_tokens:
            account = self.get_account(token)
            if not account:
                errors.append({"token": anonymize_token(token), "error": "账号不存在"})
                if progress_id:
                    self.update_relogin_progress(progress_id, token, "跳过", "账号不存在")
                continue

            email = str(account.get("email") or "").strip()
            password = str(account.get("password") or "").strip()
            if not email or not password:
                skipped += 1
                if progress_id:
                    self.update_relogin_progress(progress_id, token, "跳过", "无邮箱密码")
                continue

            # 在新线程中执行密码重新登录
            t = Thread(
                target=self._password_re_login_thread,
                args=(token, email, password, "manual_relogin", progress_id),
                daemon=True,
            )
            t.start()
            relogined += 1

        result = {
            "relogined": relogined,
            "skipped": skipped,
            "errors": errors,
            "items": self.list_accounts(),
        }
        if progress_id:
            # 如果所有账号都已同步处理完毕（没有启动线程），直接标记完成
            if relogined == 0:
                self.finish_relogin_progress(progress_id, result)
            else:
                # 有线程在运行，等线程结束后再完成
                pass
        return result

    def build_export_items(self, access_tokens: list[str] | None = None) -> list[dict[str, str]]:
        target_tokens = set(token for token in (access_tokens or []) if token)
        with self._lock:
            accounts = [
                dict(item)
                for item in self._accounts.values()
                if not target_tokens or str(item.get("access_token") or "") in target_tokens
            ]

        items: list[dict[str, str]] = []
        for account in accounts:
            access_token = str(account.get("access_token") or "").strip()
            refresh_token = str(account.get("refresh_token") or "").strip()
            id_token = str(account.get("id_token") or "").strip()
            if not access_token or not refresh_token or not id_token:
                continue

            access_payload = self._decode_jwt_payload(access_token)
            id_payload = self._decode_jwt_payload(id_token)
            auth_claim = access_payload.get("https://api.openai.com/auth")
            auth_claim = auth_claim if isinstance(auth_claim, dict) else {}
            profile_claim = access_payload.get("https://api.openai.com/profile")
            profile_claim = profile_claim if isinstance(profile_claim, dict) else {}

            email = (
                str(account.get("email") or "").strip()
                or str(profile_claim.get("email") or "").strip()
                or str(id_payload.get("email") or "").strip()
            )
            account_id = (
                str(account.get("account_id") or "").strip()
                or str(auth_claim.get("chatgpt_account_id") or "").strip()
                or str(account.get("user_id") or "").strip()
            )
            item = {
                "type": str(account.get("export_type") or "codex"),
                "email": email,
                "account_id": account_id,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "id_token": id_token,
                "expired": self._timestamp_to_iso(access_payload.get("exp")),
                "last_refresh": self._timestamp_to_iso(access_payload.get("iat")),
            }
            password = str(account.get("password") or "").strip()
            if password:
                item["password"] = password
            items.append(item)
        return items

    def get_stats(self) -> dict:
        with self._lock:
            items = list(self._accounts.values())
        total = len(items)
        active = sum(1 for a in items if a.get("status") == "正常")
        limited = sum(1 for a in items if a.get("status") == "限流")
        abnormal = sum(1 for a in items if a.get("status") == "异常")
        disabled = sum(1 for a in items if a.get("status") == "禁用")
        total_quota = sum(max(0, int(a.get("quota") or 0)) for a in items if a.get("status") == "正常")
        unlimited = sum(1 for a in items if a.get("status") == "正常" and bool(a.get("image_quota_unknown")))
        total_success = sum(int(a.get("success") or 0) for a in items)
        total_fail = sum(int(a.get("fail") or 0) for a in items)
        by_type = {}
        for a in items:
            t = a.get("type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
        return {
            "total": total,
            "cumulative_total": self._cumulative_total,
            "active": active,
            "limited": limited,
            "abnormal": abnormal,
            "disabled": disabled,
            "total_quota": total_quota,
            "unlimited_quota_count": unlimited,
            "total_success": total_success,
            "total_fail": total_fail,
            "by_type": by_type,
        }

    def account_health(self) -> dict:
        stats = self.get_stats()
        return {
            "healthy": stats["active"] > 0 or stats["unlimited_quota_count"] > 0,
            "status": "ok" if stats["active"] > 0 else "degraded",
            **stats,
        }


account_service = AccountService(config.get_storage_backend())


# PATCH_MARKER free_soft_revoked_pool_keep_r8
# PATCH_MARKER chat_select_normal_only_r9
# PATCH_MARKER soft_image_select_heal_r10
# PATCH_MARKER image_select_normal_first_r11
# PATCH_MARKER hard_dead_free_disable_r23
# PATCH_MARKER hard_dead_markers_r23
# PATCH_MARKER me_403_soft_heal_r25
# PATCH_MARKER create_account_sdk_first_r25
# PATCH_MARKER register_refresh_retry_r25
