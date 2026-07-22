from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from curl_cffi import requests
from curl_cffi.const import CurlHttpVersion

from services.account_service import account_service
from services.dynamic_proxy_feedback import report_dynamic_proxy_denial
from services.proxy_service import ClearanceBundle, proxy_settings
from services.register import mail_provider, openai_registration_policy, openai_signup_primitives
from services.runtime_profile_service import runtime_profile_service

try:
    from services.register.openai_signup_compat.legacy_create_account import (
        install_create_account_fallback as _install_create_account_fallback,
    )
except Exception:  # pragma: no cover - optional fallback path
    def _install_create_account_fallback(session, owner=None, project_root=None):  # type: ignore[misc]
        return session

base_dir = Path(__file__).resolve().parent
config = {
    "mail": {
        "request_timeout": 30,
        "wait_timeout": 30,
        "wait_interval": 2,
        "providers": [],
    },
    "proxy": "",
    "proxy_session_ttl_seconds": 900,
    "proxy_ttl_unit": "seconds",
    "proxy_region": "SG",
    "total": 10,
    "threads": 3,
    "max_attempts": 6,
}
register_config_file = base_dir.parents[1] / "data" / "register.json"
try:
    saved_config = json.loads(register_config_file.read_text(encoding="utf-8"))
    config.update(
        {
            key: saved_config[key]
            for key in (
                "mail",
                "proxy",
                "proxy_session_ttl_seconds",
                "proxy_ttl_unit",
                "proxy_region",
                "total",
                "threads",
                "max_attempts",
            )
            if key in saved_config
        }
    )
except Exception:
    pass

auth_base = "https://auth.openai.com"
platform_base = "https://platform.openai.com"
platform_oauth_client_id = "app_2SKx67EdpoN0G6j64rFvigXD"
platform_oauth_redirect_uri = f"{platform_base}/auth/callback"
platform_oauth_audience = "https://api.openai.com/v1"
platform_oauth_scope = "openid profile email offline_access"
platform_oauth_refresh_url = f"{auth_base}/oauth/token"
platform_auth0_client = "eyJuYW1lIjoiYXV0aDAtc3BhLWpzIiwidmVyc2lvbiI6IjEuMjEuMCJ9"
user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)
sec_ch_ua = '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"'
sec_ch_ua_full_version_list = '"Chromium";v="145.0.0.0", "Not:A-Brand";v="99.0.0.0", "Google Chrome";v="145.0.0.0"'
default_timeout = 45
register_request_timeout = 75
print_lock = threading.Lock()
stats_lock = threading.Lock()
stats = {"done": 0, "success": 0, "fail": 0, "start_time": 0.0}
register_log_sink = None

common_headers = {
    "accept": "application/json",
    "accept-encoding": "gzip, deflate, br",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "no-cache",
    "connection": "keep-alive",
    "content-type": "application/json",
    "dnt": "1",
    "origin": auth_base,
    "priority": "u=1, i",
    "sec-gpc": "1",
    "sec-ch-ua": sec_ch_ua,
    "sec-ch-ua-arch": '"x86_64"',
    "sec-ch-ua-bitness": '"64"',
    "sec-ch-ua-full-version-list": sec_ch_ua_full_version_list,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": '""',
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-platform-version": '"10.0.0"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": user_agent,
}

navigate_headers = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-encoding": "gzip, deflate, br",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "max-age=0",
    "connection": "keep-alive",
    "dnt": "1",
    "sec-gpc": "1",
    "sec-ch-ua": sec_ch_ua,
    "sec-ch-ua-arch": '"x86_64"',
    "sec-ch-ua-bitness": '"64"',
    "sec-ch-ua-full-version-list": sec_ch_ua_full_version_list,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": '""',
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-platform-version": '"10.0.0"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": user_agent,
}


def log(text: str, color: str = "") -> None:
    colors = {"red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m"}
    if register_log_sink:
        try:
            register_log_sink(text, color)
        except Exception:
            pass
    with print_lock:
        prefix = colors.get(color, "")
        suffix = "\033[0m" if prefix else ""
        print(f"{prefix}{datetime.now().strftime('%H:%M:%S')} {text}{suffix}")


def step(index: int, text: str, color: str = "") -> None:
    log(f"[任务{index}] {text}", color)


_response_json = openai_signup_primitives.response_json
_response_debug_detail = openai_signup_primitives.response_debug_detail
_is_cloudflare_challenge = openai_signup_primitives.is_cloudflare_challenge


def _mail_config(proxy_override: str | None = None) -> dict:
    """Build mail provider config.

    Important: empty string means explicit direct (no proxy). Do not fall back to
    register.proxy via falsy `or`, otherwise mailbox traffic reuses dynamic-proxy
    and tempmail returns non-json REMOTE_ADDR debug bodies.
    """
    if proxy_override is None:
        proxy = str(config.get("proxy") or "").strip()
    else:
        proxy = str(proxy_override or "").strip()
    mail_cfg = dict(config.get("mail") or {})
    mail_cfg["proxy"] = proxy
    return mail_cfg

def _is_sticky_proxy_url(proxy: str) -> bool:
    text = str(proxy or "").strip().lower()
    if not text:
        return False
    # dynamic-proxy sticky session username form: sess-...:x@host
    if "sess-" in text and "@" in text:
        return True
    return False


def _is_bad_mail_proxy(proxy: str) -> bool:
    """Proxies known to break mailbox providers (esp. tempmail.lol)."""
    text = str(proxy or "").strip().lower()
    if not text:
        return False
    if _is_sticky_proxy_url(text):
        return True
    # dynamic-proxy often returns empty/debug HTML-ish bodies for tempmail.lol
    if "dynamic-proxy" in text:
        return True
    return False


def _mail_proxy(preferred: str = "") -> str:
    """Mailbox/OTP traffic must avoid sticky/dynamic egress.

    OpenAI register keeps sticky egress for Auth0/Sentinel continuity, but
    tempmail providers break on dynamic-proxy (empty/non-json body). Prefer
    explicit mail.proxy when safe, otherwise direct.

    Important: if mail.proxy key exists and is an empty string, that is an
    explicit "direct" choice and must not fall back to register.proxy/glider.
    Local mail APIs (mail.acica.top) often reject proxied edge traffic with 403.
    """
    mail_cfg = config.get("mail") if isinstance(config.get("mail"), dict) else {}
    if isinstance(mail_cfg, dict) and "proxy" in mail_cfg:
        explicit = str(mail_cfg.get("proxy") or "").strip()
        if not explicit:
            return ""
        if not _is_bad_mail_proxy(explicit):
            return explicit
    candidates = [
        str(preferred or "").strip(),
        str(config.get("proxy") or "").strip(),
    ]
    for item in candidates:
        if item and not _is_bad_mail_proxy(item):
            return item
    return ""



_authorize_landed_page = openai_signup_primitives.authorize_landed_page
_authorize_continue_required = openai_signup_primitives.authorize_continue_required
_redact_url_secrets = openai_signup_primitives.redact_url_secrets


def _normalize_registration_proxy(proxy: str) -> str:
    try:
        lease_seconds = int(config.get("proxy_session_ttl_seconds") or 900)
    except (TypeError, ValueError, OverflowError):
        lease_seconds = 900
    return openai_registration_policy.normalize_registration_proxy(
        proxy,
        region=str(config.get("proxy_region") or ""),
        lease_seconds=lease_seconds,
        ttl_unit=str(config.get("proxy_ttl_unit") or "seconds"),
    )


_clearance_target_url = openai_registration_policy.clearance_target_url
_short_hash = openai_registration_policy.short_hash


def create_mailbox(
    username: str | None = None,
    proxy: str | None = None,
    *,
    excluded_domains: set[str] | None = None,
) -> dict:
    return mail_provider.create_mailbox(
        _mail_config(proxy),
        username,
        excluded_domains=excluded_domains,
    )


def wait_for_code(mailbox: dict, proxy: str | None = None) -> str | None:
    return mail_provider.wait_for_code(_mail_config(proxy), mailbox)


def _profile_fp(profile: dict | None) -> dict[str, str]:
    return openai_signup_primitives.profile_fingerprint(
        profile,
        user_agent=user_agent,
        sec_ch_ua=sec_ch_ua,
        sec_ch_ua_full_version_list=sec_ch_ua_full_version_list,
    )


_apply_fp_to_headers = openai_signup_primitives.apply_fingerprint_headers


def build_sentinel_token(session: requests.Session, device_id: str, flow: str, fp: dict[str, str] | None = None) -> str:
    """请求 sentinel token，返回 sentinel header 字符串（兼容旧接口）。"""
    return openai_signup_primitives.build_sentinel_header(
        session,
        device_id,
        flow,
        fp or {},
        default_user_agent=user_agent,
        default_sec_ch_ua=sec_ch_ua,
    )


def create_session(
    proxy: str = "",
    profile: dict | None = None,
    *,
    http_version: CurlHttpVersion | None = None,
) -> Any:
    fp = _profile_fp(profile)
    return openai_signup_primitives.create_upstream_session(
        requests.Session,
        proxy=proxy,
        profile=profile,
        fingerprint=fp,
        http_version=http_version,
    )


_apply_clearance_to_session = openai_signup_primitives.apply_clearance_to_session


def _headers_with_clearance(
    headers: dict[str, str],
    target_url: str,
    proxy: str = "",
    user_agent_override: str = "",
    profile: dict | None = None,
) -> dict[str, str]:
    fp = _profile_fp(profile)
    return openai_signup_primitives.headers_with_clearance(
        headers,
        target_url,
        proxy=proxy,
        user_agent_override=user_agent_override,
        profile=profile,
        fingerprint=fp,
    )


_cloudflare_block_message = openai_signup_primitives.cloudflare_block_message


def _login_existing_account_with_password(email: str, password: str) -> dict:
    result = account_service._login_with_password(email, password)
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"existing_account_password_login_failed: {json.dumps(result, ensure_ascii=False)[:800]}")
    return {
        "access_token": str(result.get("access_token") or "").strip(),
        "refresh_token": str(result.get("refresh_token") or "").strip(),
        "id_token": str(result.get("id_token") or "").strip(),
    }


_is_retryable_registration_error = openai_signup_primitives.is_retryable_registration_error


def request_with_local_retry(session: requests.Session, method: str, url: str, retry_attempts: int = 3, timeout: float | None = None, **kwargs):
    req_timeout = default_timeout if timeout is None else timeout
    return openai_signup_primitives.request_with_local_retry(
        session,
        method,
        url,
        retry_attempts=retry_attempts,
        timeout=req_timeout,
        sleep=time.sleep,
        **kwargs,
    )


def validate_otp(session: requests.Session, device_id: str, code: str, fp: dict[str, str] | None = None):
    fp = fp or {}
    headers = _apply_fp_to_headers(dict(common_headers), fp)
    headers["referer"] = f"{auth_base}/email-verification"
    headers["oai-device-id"] = device_id
    headers.update(openai_signup_primitives.make_trace_headers())
    resp, error = request_with_local_retry(session, "post", f"{auth_base}/api/accounts/email-otp/validate", json={"code": code}, headers=headers, verify=False, timeout=register_request_timeout, retry_attempts=4)
    if resp is not None and resp.status_code == 200:
        return resp, ""
    headers["openai-sentinel-token"] = build_sentinel_token(session, device_id, "authorize_continue", fp)
    resp, error = request_with_local_retry(session, "post", f"{auth_base}/api/accounts/email-otp/validate", json={"code": code}, headers=headers, verify=False, timeout=register_request_timeout, retry_attempts=4)
    return resp, error


def request_platform_oauth_token(
    session: requests.Session,
    code: str,
    code_verifier: str,
    fp: dict[str, str] | None = None,
) -> dict | None:
    return openai_signup_primitives.request_platform_oauth_token(
        session,
        code=code,
        code_verifier=code_verifier,
        fingerprint=fp or {},
        auth_base=auth_base,
        platform_base=platform_base,
        auth0_client=platform_auth0_client,
        oauth_client_id=platform_oauth_client_id,
        redirect_uri=platform_oauth_redirect_uri,
        sec_ch_ua=sec_ch_ua,
        user_agent=user_agent,
    )


class PlatformRegistrar:
    def __init__(
        self,
        proxy: str = "",
        *,
        excluded_mail_domains: set[str] | None = None,
    ) -> None:
        self.excluded_mail_domains = {
            str(domain or "").strip().lower().lstrip("@")
            for domain in (excluded_mail_domains or set())
            if str(domain or "").strip()
        }
        self.proxy = _normalize_registration_proxy(proxy)
        self.runtime_profile = runtime_profile_service.create_profile(
            {"proxy": self.proxy},
            proxy=self.proxy,
            source="register",
            save=True,
        )
        self.fp = _profile_fp(self.runtime_profile)
        proxy_account = {"runtime_profile_id": self.runtime_profile.get("id"), "profile_snapshot": self.runtime_profile, "fp": self.fp}
        self.egress_proxy = proxy_settings.get_profile(account=proxy_account, proxy=self.proxy, upstream=True).proxy_url or self.proxy
        self.proxy = self.egress_proxy
        self.session = create_session(self._active_proxy(), self.runtime_profile)
        self.session = _install_create_account_fallback(
            self.session,
            owner=self,
            project_root=str(base_dir),
        )
        self.clearance_user_agent = ""
        self.clearance_failure_reason = ""
        self.device_id = self.fp.get("oai-device-id") or openai_signup_primitives.new_uuid()
        self.code_verifier = ""
        self.oauth_state = ""
        self.platform_auth_code = ""
        self.account_already_exists = False
        self.authorize_landed = ""
        self.email = ""
        self.mail_provider = ""
        self.mail_mode = ""
        self.mailbox = {}
        self.started_at = time.monotonic()

    def _active_proxy(self) -> str:
        """Return the frozen egress, with a test/legacy fallback for direct use."""
        return str(getattr(self, "egress_proxy", "") or getattr(self, "proxy", "") or "")

    def _rebuild_session(
        self,
        *,
        http_version: CurlHttpVersion | None = None,
    ) -> None:
        try:
            self.session.close()
        except Exception:
            pass
        self.session = create_session(
            self._active_proxy(),
            self.runtime_profile,
            http_version=http_version,
        )
        self.session = _install_create_account_fallback(
            self.session,
            owner=self,
            project_root=str(base_dir),
        )
        self.session.cookies.set("oai-did", self.device_id, domain=".auth.openai.com")
        self.session.cookies.set("oai-did", self.device_id, domain="auth.openai.com")

    def close(self) -> None:
        self.session.close()

    def _report_dynamic_proxy_denial(self, resp: Any, stage: str) -> None:
        if resp is None:
            return
        status_code = int(getattr(resp, "status_code", 0) or 0)
        data = _response_json(resp)
        err = data.get("error", {}) if isinstance(data, dict) else {}
        code = str(err.get("code") or "") if isinstance(err, dict) else ""
        message = str(err.get("message") or "") if isinstance(err, dict) else ""
        reason = code or f"{stage}_http_{status_code}"
        response_url = str(getattr(resp, "url", "") or "")
        response_host = urlsplit(response_url).hostname or "auth.openai.com"
        report_dynamic_proxy_denial(
            self._active_proxy(),
            target=f"{response_host}:443",
            status_code=status_code,
            reason=reason,
            detail={"stage": stage, "message": message},
        )

    def _navigate_headers(self, referer: str = "") -> dict[str, str]:
        headers = _apply_fp_to_headers(dict(navigate_headers), self.fp)
        if referer:
            headers["referer"] = referer
        return headers

    def _json_headers(self, referer: str) -> dict[str, str]:
        headers = _apply_fp_to_headers(dict(common_headers), self.fp)
        headers["referer"] = referer
        headers["oai-device-id"] = self.device_id
        headers["oai-session-id"] = self.fp.get("oai-session-id", "")
        headers.update(openai_signup_primitives.make_trace_headers())
        return headers

    def _refresh_cloudflare_clearance(
        self,
        target_url: str,
        index: int,
        *,
        stage: str = "unknown",
    ) -> ClearanceBundle | None:
        self.clearance_failure_reason = ""
        clearance_url = _clearance_target_url(target_url)
        parsed_target = urlsplit(clearance_url)
        target_host = parsed_target.hostname or "unknown"
        account_context = {
            "runtime_profile_id": self.runtime_profile.get("id"),
            "profile_snapshot": self.runtime_profile,
            "fp": self.fp,
        }
        get_profile = getattr(proxy_settings, "get_profile", None)
        if callable(get_profile):
            profile = get_profile(account=account_context, proxy=self._active_proxy(), upstream=True)
            if not profile.clearance_enabled:
                self.clearance_failure_reason = (
                    "可尝试使用 FlareSolverr 清障方式，注意需要 Docker 部署 flaresolverr、privoxy、warp-proxy 等相关容器"
                )
                step(index, f"检测到 Cloudflare 拦截，{self.clearance_failure_reason}", "yellow")
                return None
        step(
            index,
            "检测到 Cloudflare 拦截，尝试刷新 clearance "
            f"stage={stage} host={target_host} path={parsed_target.path or '/'} "
            f"proxy_session={_short_hash(self._active_proxy())}",
            "yellow",
        )
        bundle = proxy_settings.refresh_clearance(
            target_url=clearance_url,
            account=account_context,
            proxy=self._active_proxy(),
            force=True,
            upstream=True,
        )
        if bundle is not None:
            _apply_clearance_to_session(self.session, bundle)
            self.clearance_user_agent = bundle.user_agent or self.clearance_user_agent
            step(
                index,
                "Cloudflare clearance 刷新完成，重试当前请求 "
                f"host={target_host} cookies={len(bundle.cookies)} "
                f"ua_hash={_short_hash(bundle.user_agent)}",
                "yellow",
            )
        else:
            self.clearance_failure_reason = "clearance 刷新未返回可用 Cookie，请检查 FlareSolverr URL、代理和出口 IP"
            step(index, f"Cloudflare clearance 刷新失败：{self.clearance_failure_reason}", "yellow")
        return bundle

    def _platform_authorize(self, email: str, index: int) -> bool:
        step(index, "开始 platform authorize")
        self.session.cookies.set("oai-did", self.device_id, domain=".auth.openai.com")
        self.session.cookies.set("oai-did", self.device_id, domain="auth.openai.com")
        self.code_verifier, code_challenge = openai_signup_primitives.create_pkce()
        self.oauth_state = openai_signup_primitives.secure_url_token(32)
        params = {
            "issuer": auth_base,
            "client_id": platform_oauth_client_id,
            "audience": platform_oauth_audience,
            "redirect_uri": platform_oauth_redirect_uri,
            "device_id": self.device_id,
            # 注册流程显式声明 signup：throwaway 域名 OpenAI 会自动当新账号走注册，
            # 但 @outlook.com/@hotmail.com 这类真实消费邮箱会被 login_or_signup 路由到登录分支，
            # 后续 user/register 落在错误的 auth step 上报 invalid_auth_step。
            "screen_hint": "signup",
            "max_age": "0",
            "login_hint": email,
            "scope": platform_oauth_scope,
            "response_type": "code",
            "response_mode": "query",
            "state": self.oauth_state,
            "nonce": openai_signup_primitives.secure_url_token(32),
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "auth0Client": platform_auth0_client,
        }
        target_url = openai_signup_primitives.build_authorize_url(auth_base, params)
        headers = self._navigate_headers(f"{platform_base}/")
        headers = _headers_with_clearance(headers, target_url, self._active_proxy(), self.clearance_user_agent, self.runtime_profile)
        # authorize may involve multi-hop redirects + CF; use register_request_timeout
        resp, error = request_with_local_retry(
            self.session,
            "get",
            target_url,
            headers=headers,
            allow_redirects=True,
            verify=False,
            timeout=register_request_timeout,
            retry_attempts=4,
        )
        if resp is None and openai_signup_primitives.is_tls_transport_error(error):
            step(
                index,
                "platform authorize TLS 连接重试耗尽，重建 Session 并使用 HTTP/1.1 恢复",
                "yellow",
            )
            self._rebuild_session(http_version=CurlHttpVersion.V1_1)
            recovery_headers = _headers_with_clearance(
                self._navigate_headers(f"{platform_base}/"),
                target_url,
                self._active_proxy(),
                self.clearance_user_agent,
                self.runtime_profile,
            )
            resp, error = request_with_local_retry(
                self.session,
                "get",
                target_url,
                headers=recovery_headers,
                allow_redirects=True,
                verify=False,
                timeout=register_request_timeout,
                retry_attempts=1,
            )
        if _is_cloudflare_challenge(resp):
            bundle = self._refresh_cloudflare_clearance(
                str(getattr(resp, "url", "") or target_url),
                index,
                stage="platform_authorize",
            )
            if bundle is None:
                self._report_dynamic_proxy_denial(resp, "platform_authorize_clearance_refresh_failed")
                raise RuntimeError(_cloudflare_block_message(resp, reason=self.clearance_failure_reason))
            retry_headers = _headers_with_clearance(
                self._navigate_headers(f"{platform_base}/"),
                target_url,
                self._active_proxy(),
                self.clearance_user_agent,
                self.runtime_profile,
            )
            resp, error = request_with_local_retry(
                self.session,
                "get",
                target_url,
                headers=retry_headers,
                allow_redirects=True,
                verify=False,
                timeout=register_request_timeout,
                retry_attempts=4,
            )
            if _is_cloudflare_challenge(resp):
                self._report_dynamic_proxy_denial(resp, "platform_authorize_clearance_retry")
                raise RuntimeError(_cloudflare_block_message(resp, "Cloudflare clearance 重试仍被拦截"))
        if resp is None or resp.status_code != 200:
            self._report_dynamic_proxy_denial(resp, "platform_authorize")
            err = _response_json(resp).get("error", {}) if resp is not None else {}
            detail = f": {err.get('code', '')} - {err.get('message', '')}".strip(" -") if err else ""
            debug = _response_debug_detail(resp)
            status = getattr(resp, "status_code", "unknown")
            raise RuntimeError(error or f"platform_authorize_http_{status}{detail}, {debug}")
        landed = _authorize_landed_page(resp)
        self.authorize_landed = landed
        continue_required = _authorize_continue_required(resp)
        step(index, f"platform authorize 完成[{landed or '?'}] url={_redact_url_secrets(str(getattr(resp, 'url', '') or ''))[:160]}")
        return continue_required

    def _authorize_continue(self, email: str, index: int) -> dict[str, str]:
        step(index, "开始提交 authorize/continue 邮箱")
        url = f"{auth_base}/api/accounts/authorize/continue"

        def send_request():
            headers = self._json_headers(
                f"{auth_base}/log-in-or-create-account?usernameKind=email"
            )
            headers["openai-sentinel-token"] = build_sentinel_token(
                self.session,
                self.device_id,
                "authorize_continue",
                self.fp,
            )
            headers = _headers_with_clearance(
                headers,
                url,
                self._active_proxy(),
                self.clearance_user_agent,
                self.runtime_profile,
            )
            return request_with_local_retry(
                self.session,
                "post",
                url,
                json={
                    "username": {"kind": "email", "value": email},
                    "screen_hint": "signup",
                },
                headers=headers,
                verify=False,
                timeout=register_request_timeout,
                retry_attempts=4,
            )

        resp, error = send_request()
        if _is_cloudflare_challenge(resp):
            bundle = self._refresh_cloudflare_clearance(
                str(getattr(resp, "url", "") or url),
                index,
                stage="authorize_continue",
            )
            if bundle is None:
                self._report_dynamic_proxy_denial(
                    resp,
                    "authorize_continue_clearance_refresh_failed",
                )
                raise RuntimeError(
                    _cloudflare_block_message(
                        resp,
                        reason=self.clearance_failure_reason,
                    )
                )
            resp, error = send_request()
        if resp is None or resp.status_code != 200:
            self._report_dynamic_proxy_denial(resp, "authorize_continue")
            data = _response_json(resp) if resp is not None else {}
            detail = f", detail={json.dumps(data, ensure_ascii=False)}" if data else ""
            raise RuntimeError(
                error
                or f"authorize_continue_http_{getattr(resp, 'status_code', 'unknown')}{detail}"
            )

        data = _response_json(resp)
        state = openai_signup_primitives.extract_continue_state(data)
        if not state["continue_url"]:
            raise RuntimeError(
                "authorize_continue_missing_continue_url: "
                f"page_type={state['page_type'] or 'unknown'}"
            )
        page_type = state["page_type"].lower()
        continue_path = str(state["continue_url"]).lower()
        if page_type in {"login", "password_verification"} or "/log-in" in continue_path:
            self.account_already_exists = True
            raise RuntimeError(f"user_already_exists: {email}")
        step(
            index,
            "authorize/continue 邮箱提交完成 "
            f"page={state['page_type'] or 'unknown'}",
        )
        return state

    def _follow_oauth_continue(
        self,
        continue_url: str,
        index: int,
        *,
        referer: str,
        require_code: bool,
    ) -> dict[str, str]:
        if not openai_signup_primitives.is_openai_oauth_continue_url(continue_url):
            raise RuntimeError("oauth_continue_untrusted_url")

        headers = _headers_with_clearance(
            self._navigate_headers(referer),
            continue_url,
            self._active_proxy(),
            self.clearance_user_agent,
            self.runtime_profile,
        )
        resp, error = request_with_local_retry(
            self.session,
            "get",
            continue_url,
            headers=headers,
            allow_redirects=True,
            max_redirects=12,
            verify=False,
            timeout=register_request_timeout,
            retry_attempts=4,
        )
        if _is_cloudflare_challenge(resp):
            challenge_url = str(getattr(resp, "url", "") or continue_url)
            bundle = self._refresh_cloudflare_clearance(
                challenge_url,
                index,
                stage="oauth_continue",
            )
            if bundle is None:
                self._report_dynamic_proxy_denial(
                    resp,
                    "oauth_continue_clearance_refresh_failed",
                )
                raise RuntimeError(
                    _cloudflare_block_message(
                        resp,
                        reason=self.clearance_failure_reason,
                    )
                )
            headers = _headers_with_clearance(
                self._navigate_headers(referer),
                continue_url,
                self._active_proxy(),
                self.clearance_user_agent,
                self.runtime_profile,
            )
            resp, error = request_with_local_retry(
                self.session,
                "get",
                continue_url,
                headers=headers,
                allow_redirects=True,
                max_redirects=12,
                verify=False,
                timeout=register_request_timeout,
                retry_attempts=4,
            )
        if resp is None or not 200 <= int(resp.status_code) < 400:
            self._report_dynamic_proxy_denial(resp, "oauth_continue")
            raise RuntimeError(
                error
                or f"oauth_continue_http_{getattr(resp, 'status_code', 'unknown')}"
            )

        try:
            callback = openai_signup_primitives.extract_oauth_callback_from_response(
                resp,
                initial_url=continue_url,
                expected_state=self.oauth_state,
            )
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        if callback:
            self.platform_auth_code = str(callback.get("code") or "").strip()
        if require_code and not self.platform_auth_code:
            redirect_count = len(list(getattr(resp, "history", None) or []))
            raise RuntimeError(
                "oauth_continue_missing_code: "
                f"redirect_count={redirect_count} final_status={resp.status_code}"
            )
        step(
            index,
            "OAuth continue 跟随完成 "
            f"redirects={len(list(getattr(resp, 'history', None) or []))} "
            f"code_present={bool(self.platform_auth_code)}",
        )
        return callback or {}

    def _register_user(self, email: str, password: str, index: int) -> dict[str, str]:
        step(index, "开始提交注册密码")
        url = f"{auth_base}/api/accounts/user/register"
        headers = self._json_headers(f"{auth_base}/create-account/password")
        headers["openai-sentinel-token"] = build_sentinel_token(self.session, self.device_id, "username_password_create", self.fp)
        headers = _headers_with_clearance(headers, url, self._active_proxy(), self.clearance_user_agent, self.runtime_profile)
        resp, error = request_with_local_retry(self.session, "post", url, json={"username": email, "password": password}, headers=headers, verify=False, timeout=register_request_timeout, retry_attempts=4)
        if _is_cloudflare_challenge(resp):
            bundle = self._refresh_cloudflare_clearance(
                str(getattr(resp, "url", "") or url),
                index,
                stage="user_register",
            )
            if bundle is None:
                self._report_dynamic_proxy_denial(resp, "user_register_clearance_refresh_failed")
                raise RuntimeError(_cloudflare_block_message(resp, reason=self.clearance_failure_reason))
            headers = self._json_headers(f"{auth_base}/create-account/password")
            headers["openai-sentinel-token"] = build_sentinel_token(self.session, self.device_id, "username_password_create", self.fp)
            headers = _headers_with_clearance(headers, url, self._active_proxy(), self.clearance_user_agent, self.runtime_profile)
            resp, error = request_with_local_retry(self.session, "post", url, json={"username": email, "password": password}, headers=headers, verify=False, timeout=register_request_timeout, retry_attempts=4)
            if _is_cloudflare_challenge(resp):
                self._report_dynamic_proxy_denial(resp, "user_register_clearance_retry")
                raise RuntimeError(_cloudflare_block_message(resp, "Cloudflare clearance 重试仍被拦截"))
        if resp is None or resp.status_code != 200:
            self._report_dynamic_proxy_denial(resp, "user_register")
            data = _response_json(resp) if resp is not None else {}
            if data.get("message") == "Failed to create account. Please try again.":
                step(index, "注册失败提示: 邮箱域名很可能因滥用被封禁，请更换邮箱域名", "yellow")
            detail = f", detail={json.dumps(data, ensure_ascii=False)}" if data else ""
            raise RuntimeError(error or f"user_register_http_{getattr(resp, 'status_code', 'unknown')}{detail}")
        state = openai_signup_primitives.extract_continue_state(_response_json(resp))
        step(
            index,
            f"提交注册密码完成 page={state['page_type'] or 'unknown'}",
        )
        return state

    def _send_otp(self, index: int) -> dict[str, str]:
        step(index, "开始发送验证码")
        url = f"{auth_base}/api/accounts/email-otp/send"
        headers = _headers_with_clearance(self._navigate_headers(f"{auth_base}/create-account/password"), url, self._active_proxy(), self.clearance_user_agent, self.runtime_profile)
        resp, error = request_with_local_retry(self.session, "get", url, headers=headers, allow_redirects=True, verify=False, timeout=register_request_timeout, retry_attempts=4)
        if _is_cloudflare_challenge(resp):
            bundle = self._refresh_cloudflare_clearance(
                str(getattr(resp, "url", "") or url),
                index,
                stage="send_otp",
            )
            if bundle is None:
                self._report_dynamic_proxy_denial(resp, "send_otp_clearance_refresh_failed")
                raise RuntimeError(_cloudflare_block_message(resp, reason=self.clearance_failure_reason))
            headers = _headers_with_clearance(self._navigate_headers(f"{auth_base}/create-account/password"), url, self._active_proxy(), self.clearance_user_agent, self.runtime_profile)
            resp, error = request_with_local_retry(self.session, "get", url, headers=headers, allow_redirects=True, verify=False, timeout=register_request_timeout, retry_attempts=4)
            if _is_cloudflare_challenge(resp):
                self._report_dynamic_proxy_denial(resp, "send_otp_clearance_retry")
                raise RuntimeError(_cloudflare_block_message(resp, "Cloudflare clearance 重试仍被拦截"))
        if resp is None or resp.status_code not in (200, 302):
            self._report_dynamic_proxy_denial(resp, "send_otp")
            raise RuntimeError(error or f"send_otp_http_{getattr(resp, 'status_code', 'unknown')}")
        state = openai_signup_primitives.extract_continue_state(_response_json(resp))
        step(index, f"发送验证码完成 page={state['page_type'] or 'unknown'}")
        return state

    def _validate_otp(self, code: str, index: int) -> dict[str, str]:
        step(index, f"开始校验验证码（长度={len(str(code))}）")
        resp, error = validate_otp(self.session, self.device_id, code, self.fp)
        if resp is None or resp.status_code != 200:
            self._report_dynamic_proxy_denial(resp, "validate_otp")
            body = ""
            try:
                body = (resp.text or "")[:500] if resp is not None else ""
            except Exception:
                pass
            raise RuntimeError(error or f"validate_otp_http_{getattr(resp, 'status_code', 'unknown')}_body={body}")
        state = openai_signup_primitives.extract_continue_state(_response_json(resp))
        if not state["continue_url"]:
            raise RuntimeError(
                "validate_otp_missing_continue_url: "
                f"page_type={state['page_type'] or 'unknown'}"
            )
        step(index, f"验证码校验完成 page={state['page_type'] or 'unknown'}")
        return state

    def _create_account(self, name: str, birthdate: str, index: int) -> dict[str, str]:
        step(index, "开始创建账号资料")
        url = f"{auth_base}/api/accounts/create_account"
        headers = self._json_headers(f"{auth_base}/about-you")
        headers["openai-sentinel-token"] = build_sentinel_token(self.session, self.device_id, "oauth_create_account", self.fp)
        headers = _headers_with_clearance(headers, url, self._active_proxy(), self.clearance_user_agent, self.runtime_profile)
        resp, error = request_with_local_retry(self.session, "post", url, json={"name": name, "birthdate": birthdate}, headers=headers, verify=False, timeout=register_request_timeout, retry_attempts=4)
        if _is_cloudflare_challenge(resp):
            bundle = self._refresh_cloudflare_clearance(
                str(getattr(resp, "url", "") or url),
                index,
                stage="create_account",
            )
            if bundle is None:
                self._report_dynamic_proxy_denial(resp, "create_account_clearance_refresh_failed")
                raise RuntimeError(_cloudflare_block_message(resp, reason=self.clearance_failure_reason))
            headers = self._json_headers(f"{auth_base}/about-you")
            headers["openai-sentinel-token"] = build_sentinel_token(self.session, self.device_id, "oauth_create_account", self.fp)
            headers = _headers_with_clearance(headers, url, self._active_proxy(), self.clearance_user_agent, self.runtime_profile)
            resp, error = request_with_local_retry(self.session, "post", url, json={"name": name, "birthdate": birthdate}, headers=headers, verify=False, timeout=register_request_timeout, retry_attempts=4)
            if _is_cloudflare_challenge(resp):
                self._report_dynamic_proxy_denial(resp, "create_account_clearance_retry")
                raise RuntimeError(_cloudflare_block_message(resp, "Cloudflare clearance 重试仍被拦截"))
        if resp is None or resp.status_code not in (200, 302):
            self._report_dynamic_proxy_denial(resp, "create_account")
            data = _response_json(resp) if resp is not None else {}
            err = data.get("error") if isinstance(data.get("error"), dict) else {}
            error_code = str(err.get("code") or "").strip()
            if error_code == "registration_disallowed":
                step(index, "create_account registration_disallowed（将由 SDK+SO fallback 自动重试，如已重试仍失败则按邮箱域名风控处理）", "yellow")
            if error_code == "user_already_exists":
                self.account_already_exists = True
                step(index, "检测到账号已存在，转入密码登录换 token", "yellow")
                return {"continue_url": "", "page_type": "user_already_exists"}
            if data.get("message") == "Failed to create account. Please try again.":
                step(index, "创建账号失败提示: 邮箱域名很可能因滥用被封禁，请更换邮箱域名", "yellow")
            detail = f", detail={json.dumps(data, ensure_ascii=False)}" if data else ""
            raise RuntimeError(error or f"create_account_http_{getattr(resp, 'status_code', 'unknown')}{detail}")
        data = _response_json(resp)
        state = openai_signup_primitives.extract_continue_state(data)
        if not state["continue_url"]:
            raise RuntimeError(
                "create_account_missing_continue_url: "
                f"page_type={state['page_type'] or 'unknown'}"
            )
        step(index, f"创建账号资料完成 page={state['page_type'] or 'unknown'}")
        return state

    def _exchange_registered_tokens(self, index: int) -> dict:
        step(index, "开始换 token")
        if not self.platform_auth_code:
            raise RuntimeError("token换取失败: oauth callback 缺少 authorization code")
        tokens = request_platform_oauth_token(self.session, self.platform_auth_code, self.code_verifier, self.fp)
        if not tokens:
            raise RuntimeError("token换取失败")
        step(index, "token 换取完成")
        return tokens

    def _resolve_mail_proxy(self) -> str:
        # Keep OpenAI sticky egress separate from mailbox provider proxy.
        resolved = _mail_proxy(self.proxy)
        return resolved

    def register(self, index: int) -> dict:
        mail_proxy = self._resolve_mail_proxy()
        step(index, f"开始创建邮箱（mail_proxy={'direct' if not mail_proxy else mail_proxy.split('@')[-1]}）")
        mailbox = create_mailbox(
            proxy=mail_proxy,
            excluded_domains=getattr(self, "excluded_mail_domains", set()),
        )
        email = str(mailbox.get("address") or "").strip()
        if not email:
            mail_provider.release_mailbox(mailbox)
            raise RuntimeError("邮箱服务未返回 address")
        label = str(mailbox.get("label") or mailbox.get("provider") or mailbox.get("provider_ref") or "")
        # 供 create_account fallback / route_stats 按邮箱线路归因
        self.mailbox = dict(mailbox) if isinstance(mailbox, dict) else {}
        self.email = email
        self.mail_provider = str(
            mailbox.get("provider")
            or mailbox.get("mail_provider")
            or str(mailbox.get("provider_ref") or "").split("#")[0]
            or ""
        ).strip()
        self.mail_mode = str(mailbox.get("mode") or self.mail_provider or "").strip()
        if mailbox.get("provider_failover") and mailbox.get("provider_failover_from"):
            trail = " -> ".join(str(x).split(":")[0] for x in mailbox.get("provider_failover_from") or [])
            step(
                index,
                f"邮箱 provider failover: {trail} -> {self.mail_provider or label}",
                "yellow",
            )
        step(index, f"邮箱创建完成[{label}]: {email}")
        # PATCH_MARKER denied_domains_log_r32
        if isinstance(mailbox, dict) and mailbox.get("denied_domains_checked"):
            step(index, f"denied_domains 已生效: {','.join(mailbox.get('denied_domains_checked') or [])}", "yellow")
        if isinstance(mailbox, dict) and mailbox.get("retry_excluded_domains_checked"):
            step(
                index,
                "本任务邮箱域名避让已生效: "
                + ",".join(mailbox.get("retry_excluded_domains_checked") or []),
                "yellow",
            )
        try:
            password = openai_signup_primitives.random_password()
            first_name, last_name = openai_signup_primitives.random_name()
            if self._platform_authorize(email, index):
                self._authorize_continue(email, index)
            else:
                step(index, "authorize 已接受邮箱，跳过重复 continue 提交")
            self._register_user(email, password, index)
            self._send_otp(index)
            step(index, "开始等待注册验证码")
            code = wait_for_code(mailbox, proxy=self._resolve_mail_proxy())
            if not code:
                raise RuntimeError("等待注册验证码超时")
            step(index, f"收到注册验证码（长度={len(str(code))}）")
            otp_state = self._validate_otp(code, index)
            otp_page = str(otp_state.get("page_type") or "").lower()
            otp_continue = str(otp_state.get("continue_url") or "").strip()
            needs_profile = (
                otp_page in {"about_you", "about-you", "create_account"}
                or "about-you" in otp_continue.lower()
                or "about_you" in otp_continue.lower()
            )
            if needs_profile:
                self._follow_oauth_continue(
                    otp_continue,
                    index,
                    referer=f"{auth_base}/email-verification",
                    require_code=False,
                )
                create_state = self._create_account(
                    f"{first_name} {last_name}",
                    openai_signup_primitives.random_birthdate(),
                    index,
                )
                if self.account_already_exists:
                    # mark_mailbox_result owns provider-specific retirement. For alias pools it
                    # retires the parent credential; for external pools it retires the resolved main.
                    step(index, f"邮箱已存在，已从当前 provider 可用池移除: {email}", "yellow")
                    raise RuntimeError(f"user_already_exists: {email}")
                self._follow_oauth_continue(
                    str(create_state.get("continue_url") or ""),
                    index,
                    referer=f"{auth_base}/about-you",
                    require_code=True,
                )
            else:
                self._follow_oauth_continue(
                    otp_continue,
                    index,
                    referer=f"{auth_base}/email-verification",
                    require_code=True,
                )
            tokens = self._exchange_registered_tokens(index)
        except Exception as error:
            mail_provider.mark_mailbox_result(mailbox, success=False, error=error)
            raise
        mail_provider.mark_mailbox_result(mailbox, success=True)
        account = {
            "email": email,
            "password": password,
            "access_token": str(tokens.get("access_token") or "").strip(),
            "refresh_token": str(tokens.get("refresh_token") or "").strip(),
            "id_token": str(tokens.get("id_token") or "").strip(),
            "proxy": self.proxy,
            "source_type": "web",
            "oauth_client_id": platform_oauth_client_id,
            "oauth_redirect_uri": platform_oauth_redirect_uri,
            "oauth_token_url": platform_oauth_refresh_url,
            "oauth_scope": platform_oauth_scope,
            "oauth_audience": platform_oauth_audience,
            "oauth_source": "platform_web_registration",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        account, self.runtime_profile = runtime_profile_service.bind_account(self.runtime_profile["id"], account)
        step(index, f"账号画像绑定完成 profile={account.get('runtime_profile_id')}", "yellow")
        return account


def worker(index: int) -> dict:
    start = time.time()
    last_error: Exception | None = None
    excluded_mail_domains: set[str] = set()
    job_mail_domain_failure_counts: dict[str, int] = {}
    max_attempts = openai_signup_primitives.registration_max_attempts(
        config.get("max_attempts")
    )
    for attempt in range(1, max_attempts + 1):
        registrar = PlatformRegistrar(
            config["proxy"],
            excluded_mail_domains=set(excluded_mail_domains),
        )
        try:
            step(index, "任务启动" if attempt == 1 else f"任务重试启动（第 {attempt}/{max_attempts} 次）", "yellow" if attempt > 1 else "")
            result = registrar.register(index)
            cost = time.time() - start
            access_token = str(result["access_token"])
            account_service.add_account_items([result])
            accepted_account = account_service.accept_registered_account(access_token)
            access_token = str(accepted_account.get("access_token") or access_token).strip()
            for token_field in ("access_token", "refresh_token", "id_token"):
                if accepted_account.get(token_field):
                    result[token_field] = accepted_account[token_field]
            step(
                index,
                "注册账号令牌验收完成 "
                f"access_len={len(str(result.get('access_token') or ''))} "
                f"refresh_len={len(str(result.get('refresh_token') or ''))} "
                f"rotated={bool(accepted_account.get('registration_token_rotated'))}",
                "yellow",
            )
            # PATCH_MARKER preserve_register_email_r26
            registered_email = str(result.get("email") or "").strip()
            # PATCH_MARKER register_refresh_retry_r25
            # Fresh tokens occasionally fail /backend-api/me with 403. Soft-heal in backend API
            # plus one delayed refresh reduces provisional-quota accounts after register.
            refresh_result = account_service.refresh_accounts([access_token])
            if refresh_result.get("errors"):
                try:
                    time.sleep(1.2)
                    refresh_result2 = account_service.refresh_accounts([access_token])
                    if not refresh_result2.get("errors"):
                        refresh_result = refresh_result2
                        step(index, "账号画像二次刷新成功", "yellow")
                    else:
                        # PATCH_MARKER register_refresh_retry_r26
                        time.sleep(2.4)
                        refresh_result3 = account_service.refresh_accounts([access_token])
                        if not refresh_result3.get("errors"):
                            refresh_result = refresh_result3
                            step(index, "账号画像三次刷新成功", "yellow")
                        else:
                            refresh_result = refresh_result3
                except Exception:
                    pass
            # Re-assert registration email if soft /me wiped it during hydrate.
            if registered_email:
                try:
                    cur = account_service.get_account(access_token) or {}
                    if not str(cur.get("email") or "").strip():
                        account_service.update_account(
                            access_token,
                            {"email": registered_email},
                            quiet=True,
                            sync_capabilities=False,
                        )
                except Exception:
                    pass
            if refresh_result.get("errors"):
                # Remote userinfo timeout must not leave a normal account unusable for image
                # selection (quota=0 filtered). Give provisional free quota; later image/chat rehydrate.
                try:
                    account_service.update_account(
                        access_token,
                        {
                            "status": "正常",
                            "quota": max(1, int((account_service.get_account(access_token) or {}).get("quota") or 0) or 5),
                            "image_quota_unknown": False,
                            "type": str((account_service.get_account(access_token) or {}).get("type") or "free"),
                            "last_refresh_error": "register_userinfo_timeout",
                            "last_refresh_error_at": datetime.now(timezone.utc).isoformat(),
                        },
                        quiet=True,
                        sync_capabilities=True,
                    )
                except Exception:
                    pass
                step(index, f"账号已保存，刷新状态暂未成功，稍后可重试: {refresh_result['errors']}", "yellow")
            with stats_lock:
                stats["done"] += 1
                stats["success"] += 1
                avg = (time.time() - stats["start_time"]) / stats["success"]
            log(f'{result["email"]} 注册成功，本次耗时{cost:.1f}s，全局平均每个号注册耗时{avg:.1f}s', "green")
            return {"ok": True, "index": index, "result": result}
        except Exception as e:
            last_error = e
            try:
                profile = getattr(registrar, "runtime_profile", None)
                if isinstance(profile, dict):
                    profile_id = str(profile.get("id") or "").strip()
                    profile_key = str(profile.get("account_key") or "").strip()
                    if profile_id and profile_key.startswith("register:"):
                        runtime_profile_service.delete_profile(profile_id)
            except Exception:
                pass
            if attempt < max_attempts and _is_retryable_registration_error(e):
                retry_exclusions = mail_provider.mailbox_retry_excluded_domains(
                    getattr(registrar, "mailbox", None),
                    e,
                    job_domain_failure_counts=job_mail_domain_failure_counts,
                )
                new_exclusions = retry_exclusions - excluded_mail_domains
                if new_exclusions:
                    excluded_mail_domains.update(new_exclusions)
                    step(
                        index,
                        "本任务后续尝试将避让邮箱域名："
                        + ",".join(sorted(new_exclusions)),
                        "yellow",
                    )
                elif (
                    mail_provider.random_mail_domain_health.registration_domain_penalty_reason(e)
                    == "account_creation_failed"
                    and isinstance(getattr(registrar, "mailbox", None), dict)
                    and registrar.mailbox.get("random_domain")
                ):
                    domain = str(
                        registrar.mailbox.get("domain_family")
                        or registrar.mailbox.get("domain")
                        or ""
                    ).strip()
                    step(
                        index,
                        "邮箱域名有成功历史，普通创建失败先换邮箱/会话复试一次"
                        + (f"：{domain}" if domain else ""),
                        "yellow",
                    )
                retry_delay = openai_signup_primitives.registration_retry_delay_seconds(
                    e, attempt
                )
                step(
                    index,
                    "检测到可重试错误，准备更换邮箱/会话后重试"
                    f"（退避 {retry_delay:.1f}s）：{e}",
                    "yellow",
                )
                registrar.close()
                time.sleep(retry_delay)
                continue
            cost = time.time() - start
            with stats_lock:
                stats["done"] += 1
                stats["fail"] += 1
            log(f"任务{index} 注册失败，本次耗时{cost:.1f}s，原因: {e}", "red")
            return {"ok": False, "index": index, "error": str(e)}
        finally:
            registrar.close()
    cost = time.time() - start
    with stats_lock:
        stats["done"] += 1
        stats["fail"] += 1
    log(f"任务{index} 注册失败，本次耗时{cost:.1f}s，原因: {last_error}", "red")
    return {"ok": False, "index": index, "error": str(last_error or 'unknown error')}


