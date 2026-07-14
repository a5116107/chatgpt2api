from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Callable

from .config import DEFAULT_SENTINEL_SV, DEFAULT_USER_AGENT, load_config
from .route_stats import record_route_event
from .sentinel import build_sentinel_request_body, generate_requirements_token
from .sentinel_runner import SentinelHeaders, generate_sentinel_headers

logger = logging.getLogger(__name__)

_CREATE_ACCOUNT_PATH = "/api/accounts/create_account"
_SENTINEL_REQ_URL = "https://sentinel.openai.com/backend-api/sentinel/req"
_DEFAULT_OBSERVER_WAIT_MS = 5000


def _module_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _safe_json(resp: Any) -> dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _error_code(resp: Any) -> str:
    data = _safe_json(resp)
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("code") or "")
    text = str(getattr(resp, "text", "") or "")
    if "registration_disallowed" in text:
        return "registration_disallowed"
    return ""


def _is_create_account_url(url: Any) -> bool:
    return _CREATE_ACCOUNT_PATH in str(url or "")


def _get_header(headers: dict[str, Any], name: str) -> str:
    lowered = name.lower()
    for key, value in (headers or {}).items():
        if str(key).lower() == lowered:
            return str(value or "")
    return ""


def _set_header(headers: dict[str, Any], name: str, value: str) -> None:
    lowered = name.lower()
    for key in list(headers.keys()):
        if str(key).lower() == lowered:
            headers[key] = value
            return
    headers[name] = value


def _owner_attr(owner: Any, *names: str) -> str:
    if owner is None:
        return ""
    for name in names:
        cur = owner
        ok = True
        for part in name.split("."):
            cur = getattr(cur, part, None)
            if cur is None:
                ok = False
                break
        if ok and str(cur or "").strip():
            return str(cur).strip()
    return ""


def _resolve_user_agent(owner: Any, headers: dict[str, Any]) -> str:
    return (
        _get_header(headers, "user-agent")
        or _owner_attr(owner, "user_agent", "ua", "config.user_agent")
        or DEFAULT_USER_AGENT
    )


def _resolve_device_id(owner: Any, headers: dict[str, Any], session: Any) -> str:
    cookie_did = ""
    try:
        cookie_did = str(session.cookies.get("oai-did") or "")
    except Exception:
        cookie_did = ""
    return (
        _get_header(headers, "oai-device-id")
        or _owner_attr(owner, "device_id", "did", "result.device_id", "config.device_id")
        or cookie_did
        or str(uuid.uuid4())
    )


def _resolve_sentinel_sv(owner: Any) -> str:
    return _owner_attr(owner, "sentinel_sv", "config.sentinel_sv") or DEFAULT_SENTINEL_SV


def _resolve_email(owner: Any) -> str:
    direct = _owner_attr(owner, "email", "mail", "account_email", "result.email", "config.email")
    if direct:
        return direct
    mailbox = getattr(owner, "mailbox", None) if owner is not None else None
    if isinstance(mailbox, dict):
        return str(mailbox.get("address") or mailbox.get("email") or "").strip()
    return ""


def _resolve_mail_provider(owner: Any) -> str:
    direct = _owner_attr(owner, "mail_provider", "provider", "email_provider", "config.mail_provider")
    if direct:
        return direct
    mailbox = getattr(owner, "mailbox", None) if owner is not None else None
    if isinstance(mailbox, dict):
        value = str(
            mailbox.get("provider")
            or mailbox.get("mail_provider")
            or str(mailbox.get("provider_ref") or "").split("#")[0]
            or ""
        ).strip()
        if value:
            return value
    return ""


def _resolve_mail_mode(owner: Any) -> str:
    direct = _owner_attr(owner, "mail_mode", "mode", "mailbox_mode")
    if direct:
        return direct
    mailbox = getattr(owner, "mailbox", None) if owner is not None else None
    if isinstance(mailbox, dict):
        value = str(mailbox.get("mode") or "").strip()
        if value:
            return value
    return _resolve_mail_provider(owner)


def _log(owner: Any, message: str, *args: Any) -> None:
    text = message % args if args else message
    for method_name in ("_log", "_print", "log"):
        method = getattr(owner, method_name, None) if owner is not None else None
        if callable(method):
            try:
                if method_name == "_log":
                    method("create_account_fallback", "INFO", "", 0, {"message": text})
                else:
                    method(text)
                return
            except Exception:
                pass
    logger.info(text)


def _request_sentinel_challenge(original_post: Callable[..., Any], *, device_id: str, user_agent: str, sentinel_sv: str, create_kwargs: dict[str, Any]) -> dict[str, Any]:
    p = generate_requirements_token(device_id, user_agent, sentinel_sv)
    body = build_sentinel_request_body(p, device_id, "oauth_create_account")
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "origin": "https://sentinel.openai.com",
        "referer": "https://auth.openai.com/",
        "user-agent": user_agent,
        "oai-device-id": device_id,
    }
    network_kwargs: dict[str, Any] = {"headers": headers, "data": body, "timeout": 30}
    for key in ("verify", "impersonate", "proxy", "proxies"):
        if key in create_kwargs:
            network_kwargs[key] = create_kwargs[key]
    resp = original_post(_SENTINEL_REQ_URL, **network_kwargs)
    if int(getattr(resp, "status_code", 0) or 0) != 200:
        raise RuntimeError(f"sentinel_req_http_{getattr(resp, 'status_code', '-')}: {str(getattr(resp, 'text', '') or '')[:200]}")
    data = _safe_json(resp)
    if not data.get("token"):
        raise RuntimeError("sentinel_req_missing_token")
    return data


def _record(project_root: Path, owner: Any, headers: SentinelHeaders, *, success: bool, error_code: str = "", sentinel_failure: bool = False) -> None:
    try:
        record_route_event(
            project_root,
            provider="openai",
            mail_provider=_resolve_mail_provider(owner),
            mail_mode=_resolve_mail_mode(owner),
            email=_resolve_email(owner),
            sentinel_route=headers.route or "unknown",
            stage="create_account",
            success=success,
            registration_disallowed=(error_code == "registration_disallowed"),
            sentinel_failure=sentinel_failure,
            error_code=error_code,
            token_len=headers.token_len or len(headers.token or ""),
            so_present=bool(headers.so_present or headers.so_token),
            so_len=headers.so_len or len(headers.so_token or ""),
        )
    except Exception as exc:
        logger.debug("route stats write skipped: %s", exc)


def _sdk_headers_for_retry(original_post: Callable[..., Any], *, project_root: Path, owner: Any, session: Any, base_headers: dict[str, Any], create_kwargs: dict[str, Any]) -> SentinelHeaders:
    user_agent = _resolve_user_agent(owner, base_headers)
    device_id = _resolve_device_id(owner, base_headers, session)
    sentinel_sv = _resolve_sentinel_sv(owner)
    challenge = _request_sentinel_challenge(
        original_post,
        device_id=device_id,
        user_agent=user_agent,
        sentinel_sv=sentinel_sv,
        create_kwargs=create_kwargs,
    )
    config = load_config(project_root)
    object.__setattr__(config, "user_agent", user_agent)
    object.__setattr__(config, "sentinel_sv", sentinel_sv)
    headers = generate_sentinel_headers(
        config,
        challenge,
        "oauth_create_account",
        device_id,
        user_agent=user_agent,
        page_url="https://auth.openai.com/about-you",
        route="sdk",
        observer_wait_ms=_DEFAULT_OBSERVER_WAIT_MS,
    )
    return headers


def install_create_account_fallback(session: Any, *, owner: Any = None, project_root: str | Path | None = None) -> Any:
    """Install a narrow create_account fallback on a requests/curl_cffi session.

    Prefer SDK+SO first for create_account (route_stats: sdk >> legacy). If the request
    already carries SO token, leave as-is. On non-200 we may fall back once to the
    original legacy headers; registration_disallowed without SO still gets one SDK retry.
    Token values are never logged; route_stats records only lengths and booleans.
    """

    if session is None:
        return session

    # 允许后续把 owner/project_root 绑定到同一 session（create_session 可能先装了无 owner 版本）
    if getattr(session, "_openai_create_account_fallback_installed", False):
        if owner is not None:
            setattr(session, "_openai_create_account_fallback_owner", owner)
        if project_root is not None:
            setattr(session, "_openai_create_account_fallback_root", Path(project_root).resolve())
        return session

    root = Path(project_root or _module_project_root()).resolve()
    setattr(session, "_openai_create_account_fallback_owner", owner)
    setattr(session, "_openai_create_account_fallback_root", root)
    original_post = session.post
    original_request = getattr(session, "request", None)

    def _handle(url: Any, resp: Any, req_headers: dict[str, Any], retry_send: Callable[..., Any], create_kwargs: dict[str, Any]) -> Any:
        if not _is_create_account_url(url):
            return resp

        active_owner = getattr(session, "_openai_create_account_fallback_owner", owner)
        active_root = Path(getattr(session, "_openai_create_account_fallback_root", root)).resolve()

        legacy_headers = SentinelHeaders(
            token=_get_header(req_headers, "openai-sentinel-token"),
            so_token=_get_header(req_headers, "openai-sentinel-so-token") or None,
            token_len=len(_get_header(req_headers, "openai-sentinel-token")),
            so_len=len(_get_header(req_headers, "openai-sentinel-so-token")),
            so_present=bool(_get_header(req_headers, "openai-sentinel-so-token")),
            route="sdk" if _get_header(req_headers, "openai-sentinel-so-token") else "legacy",
            flow="oauth_create_account",
            observer_wait_ms=_DEFAULT_OBSERVER_WAIT_MS if _get_header(req_headers, "openai-sentinel-so-token") else 0,
        )
        status = int(getattr(resp, "status_code", 0) or 0)
        if status == 200:
            _record(active_root, active_owner, legacy_headers, success=True)
            return resp

        code = _error_code(resp)
        _record(active_root, active_owner, legacy_headers, success=False, error_code=code)
        if code != "registration_disallowed" or legacy_headers.so_present:
            return resp

        try:
            sdk_headers = _sdk_headers_for_retry(
                original_post,
                project_root=active_root,
                owner=active_owner,
                session=session,
                base_headers=req_headers,
                create_kwargs=create_kwargs,
            )
        except Exception as exc:
            failed_headers = SentinelHeaders(route="sdk", flow="oauth_create_account", observer_wait_ms=_DEFAULT_OBSERVER_WAIT_MS)
            _record(active_root, active_owner, failed_headers, success=False, error_code="sentinel_failure", sentinel_failure=True)
            _log(active_owner, "create_account SDK+SO fallback sentinel failed: %s", str(exc)[:240])
            return resp

        retry_kwargs = dict(create_kwargs)
        retry_headers = dict(req_headers)
        _set_header(retry_headers, "openai-sentinel-token", sdk_headers.token)
        if sdk_headers.so_token:
            _set_header(retry_headers, "openai-sentinel-so-token", sdk_headers.so_token)
        device_id = _resolve_device_id(active_owner, retry_headers, session)
        if device_id:
            _set_header(retry_headers, "oai-device-id", device_id)
        retry_kwargs["headers"] = retry_headers
        _log(
            active_owner,
            "create_account SDK+SO fallback retry: token_len=%s so_present=%s so_len=%s sdk=%s observer_wait_ms=%s",
            sdk_headers.token_len,
            sdk_headers.so_present,
            sdk_headers.so_len,
            sdk_headers.sdk or "sdk.js",
            sdk_headers.observer_wait_ms,
        )
        retry_resp = retry_send(**retry_kwargs)
        retry_code = _error_code(retry_resp)
        _record(active_root, active_owner, sdk_headers, success=(int(getattr(retry_resp, "status_code", 0) or 0) == 200), error_code=retry_code)
        return retry_resp

    def wrapped_post(*args: Any, **kwargs: Any) -> Any:
        url = args[0] if args else kwargs.get("url", "")
        # PATCH_MARKER create_account_sdk_first_r25
        # route_stats: legacy create_account ~0% success; sdk ~100%. Prefer SDK+SO first.
        # If already carries SO token, leave as-is. On non-200 after SDK attempt, fall back once
        # to original headers (legacy path) so A/B remains measurable via route_stats.
        req_headers = dict(kwargs.get("headers") or {})
        create_kwargs = dict(kwargs)
        attempted_sdk_first = False
        if _is_create_account_url(url) and not _get_header(req_headers, "openai-sentinel-so-token"):
            active_owner = getattr(session, "_openai_create_account_fallback_owner", owner)
            active_root = Path(getattr(session, "_openai_create_account_fallback_root", root)).resolve()
            try:
                sdk_headers = _sdk_headers_for_retry(
                    original_post,
                    project_root=active_root,
                    owner=active_owner,
                    session=session,
                    base_headers=req_headers,
                    create_kwargs=create_kwargs,
                )
                sdk_req_headers = dict(req_headers)
                _set_header(sdk_req_headers, "openai-sentinel-token", sdk_headers.token)
                if sdk_headers.so_token:
                    _set_header(sdk_req_headers, "openai-sentinel-so-token", sdk_headers.so_token)
                device_id = _resolve_device_id(active_owner, sdk_req_headers, session)
                if device_id:
                    _set_header(sdk_req_headers, "oai-device-id", device_id)
                create_kwargs["headers"] = sdk_req_headers
                attempted_sdk_first = True
                _log(
                    active_owner,
                    "create_account SDK-first attempt: token_len=%s so_present=%s so_len=%s",
                    sdk_headers.token_len,
                    sdk_headers.so_present,
                    sdk_headers.so_len,
                )
            except Exception as exc:
                _log(active_owner, "create_account SDK-first prepare failed, use legacy: %s", str(exc)[:240])
                create_kwargs = dict(kwargs)

        if args:
            # positional url
            if "headers" in create_kwargs:
                resp = original_post(args[0], **{k: v for k, v in create_kwargs.items() if k != "url"})
            else:
                resp = original_post(*args, **kwargs)
        else:
            resp = original_post(**create_kwargs)

        # If SDK-first failed hard, optionally try original legacy headers once.
        # PATCH_MARKER legacy_demote_r28
        # route_stats: legacy create_account success_rate ~0; skip legacy on business denials
        # (registration_disallowed / 4xx body codes) and only keep it as transport fallback.
        if attempted_sdk_first and int(getattr(resp, "status_code", 0) or 0) != 200:
            status_code = int(getattr(resp, "status_code", 0) or 0)
            body_text = ""
            try:
                raw = getattr(resp, "text", None)
                if raw is None and getattr(resp, "content", None) is not None:
                    raw = resp.content
                if isinstance(raw, (bytes, bytearray)):
                    body_text = raw.decode("utf-8", "ignore")
                else:
                    body_text = str(raw or "")
            except Exception:
                body_text = ""
            low = body_text.lower()
            business_deny = (
                "registration_disallowed" in low
                or "invalid_request_error" in low
                or "string_below" in low
                or status_code in {400, 401, 403, 404, 409, 422}
            )
            transportish = status_code in {0, 408, 425, 429, 500, 502, 503, 504} or status_code >= 500
            if business_deny and not transportish:
                _log(
                    getattr(session, "_openai_create_account_fallback_owner", owner),
                    "create_account legacy demoted: skip legacy retry after SDK business deny status=%s",
                    status_code,
                )
            else:
                try:
                    if args:
                        legacy_resp = original_post(*args, **kwargs)
                    else:
                        legacy_resp = original_post(**kwargs)
                    # Prefer success; otherwise keep original SDK-first response for fallback handler.
                    if int(getattr(legacy_resp, "status_code", 0) or 0) == 200:
                        resp = legacy_resp
                        req_headers = dict(kwargs.get("headers") or {})
                    else:
                        # still let _handle record/maybe retry again if needed
                        pass
                except Exception:
                    pass

        final_headers = dict(create_kwargs.get("headers") or kwargs.get("headers") or {})

        def retry_send(**retry_kwargs: Any) -> Any:
            # preserve positional url if original used args
            if args:
                return original_post(args[0], **{k: v for k, v in retry_kwargs.items() if k != "url"})
            return original_post(**retry_kwargs)

        return _handle(url, resp, final_headers, retry_send, create_kwargs if attempted_sdk_first else kwargs)

    def wrapped_request(method: str, url: str, **kwargs: Any) -> Any:
        # openai_register uses session.request("POST", url, ...)
        if original_request is None:
            if str(method or "").upper() == "POST":
                return wrapped_post(url, **kwargs)
            raise AttributeError("session.request is unavailable")
        resp = original_request(method, url, **kwargs)
        req_headers = dict(kwargs.get("headers") or {})

        def retry_send(**retry_kwargs: Any) -> Any:
            return original_request(method, url, **retry_kwargs)

        return _handle(url, resp, req_headers, retry_send, kwargs)

    setattr(session, "_openai_create_account_fallback_original_post", original_post)
    if original_request is not None:
        setattr(session, "_openai_create_account_fallback_original_request", original_request)
    setattr(session, "post", wrapped_post)
    if original_request is not None:
        setattr(session, "request", wrapped_request)
    setattr(session, "_openai_create_account_fallback_installed", True)
    return session
# PATCH_MARKER create_account_sdk_first_r25
