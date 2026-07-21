from __future__ import annotations

import random
import secrets
import string
import uuid
from typing import Any, Callable

from services.proxy_service import proxy_settings
from services.register.openai_oauth_redirect import (
    extract_oauth_callback_from_response,
    extract_oauth_callback_params,
    is_openai_oauth_continue_url,
    openai_oauth_origin,
)
from services.register.openai_oauth_response import (
    authorize_continue_required,
    authorize_landed_page,
    cloudflare_block_message,
    extract_continue_state,
    is_cloudflare_challenge,
    response_debug_detail,
    response_json,
    redact_url_secrets,
)
from services.register.openai_registration_policy import (
    is_retryable_registration_error,
    is_tls_transport_error,
    registration_max_attempts,
    registration_retry_delay_seconds,
)
from utils.pkce import generate_pkce
from utils.sentinel import build_sentinel_token as _build_sentinel_token_tuple


def profile_fingerprint(
    profile: dict | None,
    *,
    user_agent: str,
    sec_ch_ua: str,
    sec_ch_ua_full_version_list: str,
) -> dict[str, str]:
    if not isinstance(profile, dict):
        return {}
    headers = profile.get("headers") if isinstance(profile.get("headers"), dict) else {}
    tls = profile.get("tls") if isinstance(profile.get("tls"), dict) else {}
    openai = profile.get("openai") if isinstance(profile.get("openai"), dict) else {}
    return {
        "user-agent": str(headers.get("user-agent") or user_agent),
        "impersonate": str(tls.get("impersonate") or "chrome146"),
        "oai-device-id": str(openai.get("oai-device-id") or uuid.uuid4()),
        "oai-session-id": str(openai.get("oai-session-id") or uuid.uuid4()),
        "sec-ch-ua": str(headers.get("sec-ch-ua") or sec_ch_ua),
        "sec-ch-ua-mobile": str(headers.get("sec-ch-ua-mobile") or "?0"),
        "sec-ch-ua-platform": str(headers.get("sec-ch-ua-platform") or '"Windows"'),
        "sec-ch-ua-arch": str(headers.get("sec-ch-ua-arch") or '"x86_64"'),
        "sec-ch-ua-bitness": str(headers.get("sec-ch-ua-bitness") or '"64"'),
        "sec-ch-ua-full-version-list": str(
            headers.get("sec-ch-ua-full-version-list") or sec_ch_ua_full_version_list
        ),
        "accept-language": str(headers.get("accept-language") or "en-US,en;q=0.9"),
    }


def apply_fingerprint_headers(
    headers: dict[str, str],
    fingerprint: dict[str, str],
) -> dict[str, str]:
    next_headers = dict(headers)
    for key in (
        "user-agent",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
        "sec-ch-ua-arch",
        "sec-ch-ua-bitness",
        "sec-ch-ua-full-version-list",
        "accept-language",
    ):
        value = fingerprint.get(key)
        if value:
            existing = next((name for name in next_headers if name.lower() == key), key)
            next_headers[existing] = str(value)
    return next_headers


def build_sentinel_header(
    session: Any,
    device_id: str,
    flow: str,
    fingerprint: dict[str, str],
    *,
    default_user_agent: str,
    default_sec_ch_ua: str,
) -> str:
    sentinel_value, _ = _build_sentinel_token_tuple(
        session,
        device_id,
        flow,
        user_agent=fingerprint.get("user-agent", default_user_agent),
        sec_ch_ua=fingerprint.get("sec-ch-ua", default_sec_ch_ua),
    )
    return sentinel_value


def apply_clearance_to_session(session: Any, bundle: Any | None) -> None:
    if bundle is None:
        return
    if bundle.user_agent:
        session.headers["User-Agent"] = bundle.user_agent
        session.headers["user-agent"] = bundle.user_agent
    for name, value in bundle.cookies.items():
        try:
            session.cookies.set(name, value, domain=f".{bundle.target_host or 'openai.com'}")
            session.cookies.set(name, value, domain=bundle.target_host or "auth.openai.com")
        except Exception:
            continue


def headers_with_clearance(
    headers: dict[str, str],
    target_url: str,
    *,
    proxy: str,
    user_agent_override: str,
    profile: dict | None,
    fingerprint: dict[str, str],
) -> dict[str, str]:
    merged = proxy_settings.build_headers(
        headers=apply_fingerprint_headers(headers, fingerprint),
        target_url=target_url,
        account={
            "runtime_profile_id": (profile or {}).get("id"),
            "profile_snapshot": profile or {},
            "fp": fingerprint,
        },
        proxy=proxy,
        upstream=True,
    )
    normalized = {str(key): str(value) for key, value in merged.items()}
    if user_agent_override:
        ua_key = next((key for key in normalized if key.lower() == "user-agent"), "user-agent")
        normalized[ua_key] = user_agent_override
    return normalized


def create_upstream_session(
    session_factory: Callable[..., Any],
    *,
    proxy: str,
    profile: dict | None,
    fingerprint: dict[str, str],
    http_version: Any | None = None,
) -> Any:
    kwargs = proxy_settings.build_session_kwargs(
        account={
            "runtime_profile_id": (profile or {}).get("id"),
            "profile_snapshot": profile or {},
            "fp": fingerprint,
        },
        proxy=proxy,
        upstream=True,
        impersonate=fingerprint.get("impersonate", "chrome146"),
        verify=False,
    )
    if http_version is not None:
        kwargs["http_version"] = http_version
    return session_factory(**kwargs)


def request_with_local_retry(
    session: Any,
    method: str,
    url: str,
    *,
    retry_attempts: int,
    timeout: float,
    sleep: Callable[[float], None],
    **kwargs: Any,
) -> tuple[Any | None, str]:
    last_error = ""
    for attempt in range(max(1, retry_attempts)):
        try:
            return session.request(method.upper(), url, timeout=timeout, **kwargs), ""
        except Exception as error:
            last_error = str(error)
            if attempt + 1 < max(1, retry_attempts):
                sleep(min(1.5 * (attempt + 1), 4.0))
    return None, last_error


def request_platform_oauth_token(
    session: Any,
    *,
    code: str,
    code_verifier: str,
    fingerprint: dict[str, str],
    auth_base: str,
    platform_base: str,
    auth0_client: str,
    oauth_client_id: str,
    redirect_uri: str,
    sec_ch_ua: str,
    user_agent: str,
) -> dict[str, Any] | None:
    headers = apply_fingerprint_headers(
        {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9",
            "auth0-client": auth0_client,
            "cache-control": "no-cache",
            "content-type": "application/json",
            "origin": platform_base,
            "pragma": "no-cache",
            "priority": "u=1, i",
            "referer": f"{platform_base}/",
            "sec-ch-ua": sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": user_agent,
        },
        fingerprint,
    )
    response = session.post(
        f"{auth_base}/api/accounts/oauth/token",
        headers=headers,
        json={
            "client_id": oauth_client_id,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        verify=False,
        timeout=60,
    )
    if response.status_code != 200:
        return None
    return response_json(response)


def create_pkce() -> tuple[str, str]:
    return generate_pkce()


def make_trace_headers() -> dict[str, str]:
    trace_id = str(random.getrandbits(64))
    parent_id = str(random.getrandbits(64))
    return {
        "traceparent": f"00-{uuid.uuid4().hex}-{format(int(parent_id), '016x')}-01",
        "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum",
        "x-datadog-parent-id": parent_id,
        "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": trace_id,
    }


def random_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    password_characters = list(
        secrets.choice(string.ascii_uppercase)
        + secrets.choice(string.ascii_lowercase)
        + secrets.choice(string.digits)
        + secrets.choice("!@#$%")
        + "".join(secrets.choice(alphabet) for _ in range(max(0, length - 4)))
    )
    random.shuffle(password_characters)
    return "".join(password_characters)


def random_name() -> tuple[str, str]:
    first_name = random.choice(
        ["James", "Robert", "John", "Michael", "David", "Mary", "Emma", "Olivia"]
    )
    last_name = random.choice(
        ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller"]
    )
    return first_name, last_name


def random_birthdate() -> str:
    return (
        f"{random.randint(1996, 2006):04d}-"
        f"{random.randint(1, 12):02d}-"
        f"{random.randint(1, 28):02d}"
    )


def new_uuid() -> str:
    return str(uuid.uuid4())


def secure_url_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def build_authorize_url(auth_base: str, params: dict[str, str]) -> str:
    from urllib.parse import urlencode

    return f"{str(auth_base).rstrip('/')}/api/accounts/authorize?{urlencode(params)}"


__all__ = [
    "authorize_continue_required",
    "authorize_landed_page",
    "apply_clearance_to_session",
    "apply_fingerprint_headers",
    "build_authorize_url",
    "build_sentinel_header",
    "cloudflare_block_message",
    "create_upstream_session",
    "create_pkce",
    "extract_continue_state",
    "extract_oauth_callback_from_response",
    "extract_oauth_callback_params",
    "is_cloudflare_challenge",
    "is_openai_oauth_continue_url",
    "is_retryable_registration_error",
    "is_tls_transport_error",
    "make_trace_headers",
    "new_uuid",
    "openai_oauth_origin",
    "headers_with_clearance",
    "profile_fingerprint",
    "random_birthdate",
    "random_name",
    "random_password",
    "registration_max_attempts",
    "registration_retry_delay_seconds",
    "response_debug_detail",
    "response_json",
    "request_platform_oauth_token",
    "request_with_local_retry",
    "redact_url_secrets",
    "secure_url_token",
]
