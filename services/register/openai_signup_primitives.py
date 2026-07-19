from __future__ import annotations

import random
import secrets
import string
import uuid

from services.register.openai_oauth_redirect import (
    extract_oauth_callback_from_response,
    extract_oauth_callback_params,
    is_openai_oauth_continue_url,
    openai_oauth_origin,
)
from services.register.openai_oauth_response import (
    authorize_continue_required,
    authorize_landed_page,
    extract_continue_state,
    is_cloudflare_challenge,
    response_debug_detail,
    response_json,
)
from services.register.openai_registration_policy import (
    is_retryable_registration_error,
    is_tls_transport_error,
    registration_max_attempts,
    registration_retry_delay_seconds,
)
from utils.pkce import generate_pkce


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
    "build_authorize_url",
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
    "random_birthdate",
    "random_name",
    "random_password",
    "registration_max_attempts",
    "registration_retry_delay_seconds",
    "response_debug_detail",
    "response_json",
    "secure_url_token",
]
