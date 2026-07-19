from __future__ import annotations

import hashlib
import imaplib
import json
import os
import random
import re
import string
import time
from datetime import datetime, timezone
from email import (
    header as email_header,
    message_from_bytes,
    message_from_string,
    policy,
    utils as email_utils,
)
from pathlib import Path
from threading import Lock
from typing import Any, Callable, TypeVar

from curl_cffi import requests


from services.config import DATA_DIR
from services.register import outlook_account_selection, random_mail_domain_health

DDG_ALIASES_FILE = DATA_DIR / "ddg_aliases.json"
_ddg_aliases_lock = Lock()

OUTLOOK_TOKEN_USED_FILE = DATA_DIR / "outlook_token_used.json"
_outlook_token_state_lock = Lock()
# in_use 超过该秒数视为陈旧（注册进程崩溃残留），可被重新领用
OUTLOOK_IN_USE_STALE_SECONDS = 3600
OUTLOOK_RECORDED_STATES = {
    "used",
    "in_use",
    "token_invalid",
    "already_registered",
    "invalid_email",
    "failed",
}
OUTLOOK_UNAVAILABLE_STATES = {
    "used",
    "token_invalid",
    "already_registered",
    "invalid_email",
    "failed",
}
MAILFREE_DOMAIN_HEALTH_FILE = DATA_DIR / "mailfree_domain_health.json"
_mailfree_domain_health_lock = Lock()
MAILFREE_DOMAIN_REJECT_COOLDOWN_SECONDS = 6 * 3600


def _load_ddg_aliases() -> set[str]:
    try:
        if DDG_ALIASES_FILE.exists():
            data = json.loads(DDG_ALIASES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return {str(item).strip().lower() for item in data if str(item).strip()}
    except Exception:
        pass
    return set()


def _save_ddg_aliases(aliases: set[str]) -> None:
    DDG_ALIASES_FILE.parent.mkdir(parents=True, exist_ok=True)
    DDG_ALIASES_FILE.write_text(json.dumps(sorted(aliases), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _is_ddg_alias_duplicate(address: str) -> bool:
    target = str(address or "").strip().lower()
    if not target:
        return False
    with _ddg_aliases_lock:
        used = _load_ddg_aliases()
        return target in used


def _record_ddg_alias(address: str) -> None:
    target = str(address or "").strip().lower()
    if not target:
        return
    with _ddg_aliases_lock:
        used = _load_ddg_aliases()
        used.add(target)
        _save_ddg_aliases(used)


def _load_outlook_token_state() -> dict[str, dict[str, Any]]:
    """读取邮箱池状态文件，返回 {email_lower: {state, reason, updated_at}}。

    兼容旧格式：纯字符串列表（历史的“已用邮箱”）会被解释为 used。
    """
    try:
        if not OUTLOOK_TOKEN_USED_FILE.exists():
            return {}
        data = json.loads(OUTLOOK_TOKEN_USED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    state: dict[str, dict[str, Any]] = {}
    if isinstance(data, list):
        for item in data:
            key = str(item).strip().lower()
            if key:
                state[key] = {"state": "used", "reason": "", "updated_at": ""}
    elif isinstance(data, dict):
        for key, value in data.items():
            email = str(key).strip().lower()
            if not email:
                continue
            if isinstance(value, dict):
                state[email] = {
                    "state": str(value.get("state") or "used").strip() or "used",
                    "reason": str(value.get("reason") or ""),
                    "updated_at": str(value.get("updated_at") or ""),
                }
            else:
                state[email] = {"state": str(value or "used").strip() or "used", "reason": "", "updated_at": ""}
    return state


def _save_outlook_token_state(state: dict[str, dict[str, Any]]) -> None:
    OUTLOOK_TOKEN_USED_FILE.parent.mkdir(parents=True, exist_ok=True)
    ordered = {key: state[key] for key in sorted(state)}
    OUTLOOK_TOKEN_USED_FILE.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _outlook_entry_available(entry: dict[str, Any] | None) -> bool:
    """该邮箱当前是否可领用：未记录、或 in_use 已陈旧、或非终态时可用。"""
    if not isinstance(entry, dict):
        return True
    current = str(entry.get("state") or "")
    if current in OUTLOOK_UNAVAILABLE_STATES:
        return False
    if current == "in_use":
        updated_at = str(entry.get("updated_at") or "")
        try:
            ts = datetime.fromisoformat(updated_at)
            age = (datetime.now(timezone.utc) - (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc))).total_seconds()
            return age >= OUTLOOK_IN_USE_STALE_SECONDS
        except Exception:
            return True
    return True


def _set_outlook_token_state(address: str, state: str, reason: str = "") -> None:
    target = str(address or "").strip().lower()
    if not target:
        return
    with _outlook_token_state_lock:
        store = _load_outlook_token_state()
        store[target] = {"state": str(state), "reason": str(reason or ""), "updated_at": datetime.now(timezone.utc).isoformat()}
        _save_outlook_token_state(store)


def _release_outlook_token_state(address: str) -> None:
    """把 in_use 释放回未使用（仅当当前确实是 in_use 时）。"""
    target = str(address or "").strip().lower()
    if not target:
        return
    with _outlook_token_state_lock:
        store = _load_outlook_token_state()
        entry = store.get(target)
        if isinstance(entry, dict) and str(entry.get("state") or "") == "in_use":
            store.pop(target, None)
            _save_outlook_token_state(store)


def _outlook_mailbox_state_key(mailbox: dict[str, Any]) -> str:
    """Return the parent credential key used to lock or retire an alias mailbox."""
    return str(
        mailbox.get("state_key")
        or mailbox.get("parent_email")
        or mailbox.get("resolved_email")
        or mailbox.get("address")
        or ""
    ).strip()



def _outlook_external_used_file() -> Path:
    return DATA_DIR / "outlook_external_used_mains.json"


def _load_outlook_external_used() -> set[str]:
    path = _outlook_external_used_file()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return {str(x).strip().lower() for x in data if str(x).strip()}
            if isinstance(data, dict):
                items = data.get("mains") or data.get("used") or []
                return {str(x).strip().lower() for x in items if str(x).strip()}
    except Exception:
        pass
    return set()


def _save_outlook_external_used(used: set[str]) -> None:
    path = _outlook_external_used_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(used), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def mark_outlook_external_main_used(main_email: str, *, reason: str = "") -> None:
    main = str(main_email or "").strip().lower()
    if not main:
        return
    with provider_lock:
        used = _load_outlook_external_used()
        if main not in used:
            used.add(main)
            _save_outlook_external_used(used)


def reset_outlook_external_used_mains() -> int:
    with provider_lock:
        used = _load_outlook_external_used()
        n = len(used)
        _save_outlook_external_used(set())
        return n


def reset_outlook_token_pool_state(scope: str = "all") -> int:
    """重置邮箱池状态文件。

    scope=all 清空所有记录；scope=failed 仅清除 failed/token_invalid/in_use（保留 used）。
    返回被清除的条目数。
    """
    with _outlook_token_state_lock:
        store = _load_outlook_token_state()
        if not store:
            return 0
        if str(scope) == "failed":
            remove = {
                key
                for key, value in store.items()
                if str(value.get("state") or "")
                in {"failed", "token_invalid", "already_registered", "invalid_email", "in_use"}
            }
            for key in remove:
                store.pop(key, None)
            _save_outlook_token_state(store)
            return len(remove)
        count = len(store)
        _save_outlook_token_state({})
        return count


def prune_outlook_unused_credentials(credentials: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    """Return credentials with recorded state, plus the number pruned as unused."""
    with _outlook_token_state_lock:
        store = _load_outlook_token_state()
    kept: list[dict[str, str]] = []
    removed = 0
    for credential in credentials:
        key = str(credential.get("email") or "").strip().lower()
        entry = store.get(key) if key else None
        state = str(entry.get("state") or "") if isinstance(entry, dict) else ""
        if state in OUTLOOK_RECORDED_STATES:
            kept.append(credential)
        else:
            removed += 1
    return kept, removed


def outlook_token_pool_stats(pool: list[dict[str, str]] | None = None) -> dict[str, int]:
    """统计邮箱池各状态数量。pool 为该 provider 当前导入的邮箱列表（用于算 unused）。"""
    store = _load_outlook_token_state()
    counts = {
        "unused": 0,
        "in_use": 0,
        "used": 0,
        "token_invalid": 0,
        "already_registered": 0,
        "invalid_email": 0,
        "failed": 0,
    }
    if pool:
        for credential in pool:
            entry = store.get(str(credential.get("email") or "").strip().lower())
            state = str(entry.get("state") or "") if isinstance(entry, dict) else ""
            if state in counts:
                counts[state] += 1
            else:
                counts["unused"] += 1
    else:
        for entry in store.values():
            state = str(entry.get("state") or "") if isinstance(entry, dict) else ""
            if state in counts:
                counts[state] += 1
    return counts


def _load_mailfree_domain_health() -> dict[str, dict[str, Any]]:
    try:
        if not MAILFREE_DOMAIN_HEALTH_FILE.exists():
            return {}
        data = json.loads(MAILFREE_DOMAIN_HEALTH_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, dict):
        return {}
    return {str(key): value for key, value in items.items() if isinstance(value, dict)}


def _save_mailfree_domain_health(state: dict[str, dict[str, Any]]) -> None:
    MAILFREE_DOMAIN_HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items": {key: state[key] for key in sorted(state)},
    }
    MAILFREE_DOMAIN_HEALTH_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _mailfree_domain_key(provider_ref: str, api_base: str, domain_index: int) -> str:
    return f"{str(provider_ref or '').strip()}|{str(api_base or '').strip().rstrip('/')}|{int(domain_index)}"


def _mailfree_parse_time(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _mailfree_penalty_reason(error: Exception | str | None) -> str:
    text = str(error or "").strip().lower()
    if not text:
        return ""
    markers = {
        "unsupported_email": "unsupported_email",
        "account_creation_failed": "account_creation_failed",
        "failed to create account": "account_creation_failed",
        "邮箱域名很可能因滥用被封禁": "domain_abuse_suspected",
        "not supported": "unsupported_email",
    }
    for marker, reason in markers.items():
        if marker in text:
            return reason
    return ""


def _record_mailfree_domain_result(mailbox: dict[str, Any], *, success: bool, error: Exception | str | None = None) -> None:
    provider_ref = str(mailbox.get("provider_ref") or "").strip()
    api_base = str(mailbox.get("api_base") or "").strip()
    try:
        domain_index = int(mailbox.get("domain_index"))
    except (TypeError, ValueError):
        return
    penalty_reason = _mailfree_penalty_reason(error)
    if not success and not penalty_reason:
        return
    key = _mailfree_domain_key(provider_ref, api_base, domain_index)
    now = datetime.now(timezone.utc)
    with _mailfree_domain_health_lock:
        state = _load_mailfree_domain_health()
        entry = dict(state.get(key) or {})
        entry.update(
            {
                "provider_ref": provider_ref,
                "api_base": api_base,
                "domain_index": domain_index,
                "updated_at": now.isoformat(),
            }
        )
        if success:
            entry["success_count"] = int(entry.get("success_count") or 0) + 1
            entry["last_success_at"] = now.isoformat()
            entry["cooldown_until"] = ""
            entry["last_error"] = ""
            entry["last_error_reason"] = ""
        else:
            entry["failure_count"] = int(entry.get("failure_count") or 0) + 1
            entry["last_failure_at"] = now.isoformat()
            entry["last_error"] = str(error or "")[:500]
            entry["last_error_reason"] = penalty_reason
            entry["cooldown_until"] = datetime.fromtimestamp(
                now.timestamp() + MAILFREE_DOMAIN_REJECT_COOLDOWN_SECONDS, tz=timezone.utc
            ).isoformat()
        state[key] = entry
        _save_mailfree_domain_health(state)


ResultT = TypeVar("ResultT")
domain_lock = Lock()
provider_lock = Lock()
domain_index = 0
provider_index = 0
cloudmail_token_lock = Lock()
cloudmail_token_cache: dict[str, tuple[str, float]] = {}


def _config(mail_config: dict) -> dict:
    return {
        "request_timeout": float(mail_config.get("request_timeout") or 30),
        "wait_timeout": float(mail_config.get("wait_timeout") or 30),
        "wait_interval": float(mail_config.get("wait_interval") or 2),
        "user_agent": str(mail_config.get("user_agent") or "Mozilla/5.0"),
        "proxy": str(mail_config.get("proxy") or "").strip(),
    }


def _random_mailbox_name() -> str:
    return f"{''.join(random.choices(string.ascii_lowercase, k=5))}{''.join(random.choices(string.digits, k=random.randint(1, 3)))}{''.join(random.choices(string.ascii_lowercase, k=random.randint(1, 3)))}"


def _random_subdomain_label() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=random.randint(4, 10)))


def _next_domain(domains: list[str]) -> str:
    global domain_index
    domains = [str(item).strip() for item in domains if str(item).strip()]
    if not domains:
        raise RuntimeError("mail.domain 不能为空")
    if len(domains) == 1:
        return domains[0]
    with domain_lock:
        value = domains[domain_index % len(domains)]
        domain_index = (domain_index + 1) % len(domains)
        return value


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _create_session(conf: dict):
    proxy = str(conf.get("proxy") or "").strip()
    kwargs = {"impersonate": "chrome", "verify": False}
    if proxy:
        kwargs["proxy"] = proxy
    return requests.Session(**kwargs)


def _parse_received_at(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        date = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        return date if date.tzinfo else date.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        date = email_utils.parsedate_to_datetime(text)
        return date if date.tzinfo else date.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _extract_content(data: dict[str, Any]) -> tuple[str, str]:
    text_content = str(data.get("text_content") or data.get("text") or data.get("body") or data.get("content") or "")
    html_content = str(data.get("html_content") or data.get("html") or data.get("html_body") or data.get("body_html") or "")
    if text_content or html_content:
        return text_content, html_content
    raw = data.get("raw")
    if not isinstance(raw, str) or not raw.strip():
        return "", ""
    try:
        parsed = message_from_string(raw, policy=policy.default)
    except Exception:
        return raw, ""
    plain: list[str] = []
    html: list[str] = []
    for part in parsed.walk() if parsed.is_multipart() else [parsed]:
        if part.get_content_maintype() == "multipart":
            continue
        try:
            payload = part.get_content()
        except Exception:
            payload = ""
        if not payload:
            continue
        if part.get_content_type() == "text/html":
            html.append(str(payload))
        else:
            plain.append(str(payload))
    return "\n".join(plain).strip(), "\n".join(html).strip()


def _extract_text_candidates(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for key in ("address", "email", "name", "value"):
            if value.get(key):
                out.extend(_extract_text_candidates(value.get(key)))
        return out
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_extract_text_candidates(item))
        return out
    return []


def _message_matches_email(data: dict[str, Any], email: str) -> bool:
    target = str(email or "").strip().lower()
    candidates: list[str] = []
    for key in ("to", "toAddr", "mailTo", "receiver", "receivers", "address", "email", "envelope_to"):
        if key in data:
            candidates.extend(_extract_text_candidates(data.get(key)))
    return not target or not candidates or any(target in str(item).strip().lower() for item in candidates if str(item).strip())


def _extract_code(message: dict[str, Any]) -> str | None:
    content = f"{message.get('subject', '')}\n{message.get('text_content', '')}\n{message.get('html_content', '')}".strip()
    if not content:
        return None
    match = re.search(r"background-color:\s*#F3F3F3[^>]*>[\s\S]*?(\d{6})[\s\S]*?</p>", content, re.I)
    if match:
        return match.group(1)
    match = re.search(r"(?:Verification code|code is|代码为|验证码)[:\s]*(\d{6})", content, re.I)
    if match and match.group(1) != "177010":
        return match.group(1)
    for code in re.findall(r">\s*(\d{6})\s*<|(?<![#&])\b(\d{6})\b", content):
        value = code[0] or code[1]
        if value and value != "177010":
            return value
    return None


def _message_tracking_ref(message: dict[str, Any]) -> str:
    provider = str(message.get("provider") or "").strip()
    mailbox = str(message.get("mailbox") or "").strip()
    message_id = str(message.get("message_id") or "").strip()
    if message_id:
        return f"id:{provider}:{mailbox}:{message_id}"
    received_at = message.get("received_at")
    received_value = received_at.isoformat() if isinstance(received_at, datetime) else str(received_at or "")
    content = "\n".join(str(message.get(key) or "") for key in ("subject", "sender", "text_content", "html_content"))
    digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
    return f"content:{provider}:{mailbox}:{received_value}:{digest}"


class BaseMailProvider:
    name = "unknown"

    def __init__(self, conf: dict, provider_ref: str = ""):
        self.conf = conf
        self.provider_ref = provider_ref

    def wait_for(self, mailbox: dict[str, Any], on_message: Callable[[dict[str, Any]], ResultT | None]) -> ResultT | None:
        deadline = time.monotonic() + self.conf["wait_timeout"]
        while time.monotonic() < deadline:
            message = self.fetch_latest_message(mailbox)
            if message:
                result = on_message(message)
                if result is not None:
                    return result
            time.sleep(max(0.2, self.conf["wait_interval"]))
        return None

    def wait_for_code(self, mailbox: dict[str, Any]) -> str | None:
        seen_value = mailbox.setdefault("_seen_code_message_refs", [])
        if not isinstance(seen_value, list):
            seen_value = []
            mailbox["_seen_code_message_refs"] = seen_value
        seen_refs = {str(item) for item in seen_value}

        def extract_unseen_code(message: dict[str, Any]) -> str | None:
            ref = _message_tracking_ref(message)
            if ref in seen_refs:
                return None
            code = _extract_code(message)
            if code:
                seen_value.append(ref)
                seen_refs.add(ref)
            return code

        return self.wait_for(mailbox, extract_unseen_code)

    def close(self) -> None:
        pass


class CloudflareTempMailProvider(BaseMailProvider):
    name = "cloudflare_temp_email"

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.api_base = str(entry["api_base"]).rstrip("/")
        self.admin_password = str(entry["admin_password"]).strip()
        self.domain = entry.get("domain") or []
        self.session = _create_session(conf)

    def _request(self, method: str, path: str, headers: dict | None = None, params: dict | None = None, payload: dict | None = None, expected: tuple[int, ...] = (200,)):
        resp = self.session.request(method.upper(), f"{self.api_base}{path}", headers={"Content-Type": "application/json", "User-Agent": self.conf["user_agent"], **(headers or {})}, params=params, json=payload, timeout=self.conf["request_timeout"], verify=False)
        if resp.status_code not in expected:
            raise RuntimeError(f"CloudflareTempMail 请求失败: {method} {path}, HTTP {resp.status_code}, body={resp.text[:300]}")
        return {} if resp.status_code == 204 else resp.json()

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        data = self._request("POST", "/admin/new_address", headers={"x-admin-auth": self.admin_password}, payload={"enablePrefix": True, "name": username or _random_mailbox_name(), "domain": _next_domain(self.domain)})
        address = str(data.get("address") or "").strip()
        token = str(data.get("jwt") or "").strip()
        if not address or not token:
            raise RuntimeError("CloudflareTempMail 缺少 address 或 jwt")
        return {"provider": self.name, "provider_ref": self.provider_ref, "address": address, "token": token}

    def get_existing_mailbox(self, email: str) -> dict[str, Any]:
        """通过管理员密码获取已有邮箱地址的 JWT，用于查询邮件。"""
        data = self._request("POST", "/admin/get_address", headers={"x-admin-auth": self.admin_password}, payload={"address": email})
        address = str(data.get("address") or "").strip()
        token = str(data.get("jwt") or "").strip()
        if not address or not token:
            raise RuntimeError(f"CloudflareTempMail 无法获取已有邮箱 {email} 的 JWT")
        return {"provider": self.name, "provider_ref": self.provider_ref, "address": address, "token": token}

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        data = self._request("GET", "/api/mails", headers={"Authorization": f"Bearer {mailbox['token']}"}, params={"limit": 10, "offset": 0})
        raw = list(data.get("results") or []) if isinstance(data, dict) else data if isinstance(data, list) else []
        messages = [item for item in raw if isinstance(item, dict) and _message_matches_email(item, str(mailbox.get("address") or ""))]
        if not messages:
            return None
        item = messages[0]
        text_content, html_content = _extract_content(item)
        sender = item.get("from") or item.get("sender") or ""
        if isinstance(sender, dict):
            sender = sender.get("address") or sender.get("email") or sender.get("name") or ""
        return {"provider": self.name, "mailbox": mailbox["address"], "message_id": str(item.get("id") or item.get("_id") or ""), "subject": str(item.get("subject") or ""), "sender": str(sender), "text_content": text_content, "html_content": html_content, "received_at": _parse_received_at(item.get("createdAt") or item.get("created_at") or item.get("receivedAt") or item.get("date") or item.get("timestamp")), "raw": item}

    def close(self) -> None:
        self.session.close()


class DDGMailProvider(BaseMailProvider):
    name = "ddg_mail"

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.label = str(entry.get("label") or self.provider_ref)
        self.ddg_token = str(entry["ddg_token"]).strip()
        self.cf_api_base = str(entry.get("api_base") or entry.get("cf_api_base") or "").rstrip("/")
        self.cf_inbox_jwt = str(entry.get("cf_inbox_jwt") or "").strip()
        self.cf_admin_password = str(entry.get("admin_password") or "").strip()
        self.cf_api_key = str(entry.get("cf_api_key") or "").strip()
        self.cf_auth_mode = str(entry.get("cf_auth_mode") or "none").strip().lower()
        self.cf_domain = entry.get("cf_domain") or []
        self.cf_create_path = str(entry.get("cf_create_path") or "/api/new_address").strip()
        self.cf_messages_path = str(entry.get("cf_messages_path") or "/api/mails").strip()
        self.session = _create_session(conf)

    def _cf_build_headers(self, content_type: bool = False) -> dict:
        headers = {"Content-Type": "application/json"} if content_type else {}
        if self.cf_api_key:
            if self.cf_auth_mode == "x-api-key":
                headers["X-API-Key"] = self.cf_api_key
            elif self.cf_auth_mode != "none":
                headers["Authorization"] = f"Bearer {self.cf_api_key}"
        return headers

    def _cf_request(self, method: str, path: str, headers: dict | None = None, params: dict | None = None, payload: dict | None = None, expected: tuple[int, ...] = (200,)) -> dict:
        merged_headers = {**self._cf_build_headers(True), **(headers or {}), "User-Agent": self.conf["user_agent"]}
        if self.cf_admin_password and method.upper() in ("POST",):
            merged_headers["x-admin-auth"] = self.cf_admin_password
        if self.cf_api_key and self.cf_auth_mode == "query-key":
            params = {**(params or {}), "key": self.cf_api_key}
        resp = self.session.request(method.upper(), f"{self.cf_api_base}{path}", headers=merged_headers, params=params, json=payload, timeout=self.conf["request_timeout"], verify=False)
        if resp.status_code not in expected:
            raise RuntimeError(f"DDGMail CF请求失败: {method} {path}, HTTP {resp.status_code}, body={resp.text[:300]}")
        return {} if resp.status_code == 204 else resp.json()

    def _ddg_request(self, method: str, path: str, payload: dict | None = None) -> dict:
        resp = self.session.request(method.upper(), f"https://quack.duckduckgo.com{path}", headers={"Authorization": f"Bearer {self.ddg_token}", "Content-Type": "application/json", "User-Agent": self.conf["user_agent"]}, json=payload, timeout=self.conf["request_timeout"], verify=False)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"DDG API请求失败: {method} {path}, HTTP {resp.status_code}, body={resp.text[:300]}")
        return resp.json()

    def _cf_list_payload(self, data: Any) -> list:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("results", "hydra:member", "data", "messages"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
                if isinstance(value, dict) and isinstance(value.get("messages"), list):
                    return value["messages"]
        return []

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        ddg_data = self._ddg_request("POST", "/api/email/addresses", payload={})
        ddg_address_part = str(ddg_data.get("address") or "").strip()
        if not ddg_address_part:
            raise RuntimeError("DDG API 返回无 address 字段")
        ddg_address = f"{ddg_address_part}@duck.com"

        if _is_ddg_alias_duplicate(ddg_address):
            raise RuntimeError(f"[{self.label}] DDG日上限已达，别名 {ddg_address} 已存在，自动切换邮箱提供商")

        _record_ddg_alias(ddg_address)

        if not self.cf_inbox_jwt:
            raise RuntimeError("DDGMail 需要 cf_inbox_jwt（DDG 转发目标的固定收件箱 JWT），请在邮箱配置中填写 CF Inbox JWT")

        return {"provider": self.name, "provider_ref": self.provider_ref, "address": ddg_address, "token": self.cf_inbox_jwt, "label": self.label}

    def _parse_raw_recipient(self, raw_text: str) -> str:
        if not raw_text:
            return ""
        match = re.search(r"^To:\s*(.+?)$", raw_text, re.MULTILINE | re.IGNORECASE)
        if match:
            addr = match.group(1).strip()
            addr = re.sub(r"\s*<[^>]*>", "", addr)
            return addr.strip().lower()
        try:
            parsed = message_from_string(raw_text, policy=policy.default)
            return str(parsed.get("To") or "").strip().lower()
        except Exception:
            return ""

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        target_address = str(mailbox.get("address") or "").strip().lower()
        data = self._cf_request("GET", self.cf_messages_path, headers={"Authorization": f"Bearer {mailbox['token']}"}, params={"limit": 30, "offset": 0})
        raw_list = self._cf_list_payload(data)
        messages = [item for item in raw_list if isinstance(item, dict)]
        if not messages:
            return None

        for item in messages:
            message_id = str(item.get("id") or item.get("msgid") or item.get("_id") or "")
            raw_text = str(item.get("raw") or "")
            raw_recipient = self._parse_raw_recipient(raw_text)
            if target_address and raw_recipient and target_address not in raw_recipient:
                continue
            text_content, html_content = _extract_content(item)
            subject = str(item.get("subject") or "")
            sender = item.get("from") or item.get("sender") or item.get("source") or ""
            if isinstance(sender, dict):
                sender = sender.get("address") or sender.get("email") or sender.get("name") or ""
            if raw_text and (not subject or not sender or subject == sender == ""):
                try:
                    parsed = message_from_string(raw_text, policy=policy.default)
                    if not subject:
                        subject = str(parsed.get("Subject") or "")
                    if not sender:
                        sender = str(parsed.get("From") or "")
                except Exception:
                    pass
            return {"provider": self.name, "mailbox": mailbox["address"], "message_id": message_id, "subject": subject, "sender": str(sender), "text_content": text_content, "html_content": html_content, "received_at": _parse_received_at(item.get("createdAt") or item.get("created_at") or item.get("receivedAt") or item.get("date") or item.get("timestamp")), "raw": item}

        return None

    def close(self) -> None:
        self.session.close()


class CloudMailGenProvider(BaseMailProvider):
    name = "cloudmail_gen"

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.api_base = str(entry["api_base"]).rstrip("/")
        self.admin_email = str(entry.get("admin_email") or "").strip()
        self.admin_password = str(entry.get("admin_password") or "").strip()
        self.domain = _normalize_string_list(entry.get("domain"))
        self.subdomain = _normalize_string_list(entry.get("subdomain"))
        self.email_prefix = str(entry.get("email_prefix") or "").strip()
        self.session = _create_session(conf)

    def _request(
        self,
        method: str,
        path: str,
        headers: dict | None = None,
        params: dict | None = None,
        payload: dict | None = None,
        expected: tuple[int, ...] = (200,),
    ):
        resp = self.session.request(
            method.upper(),
            f"{self.api_base}{path}",
            headers={
                "Content-Type": "application/json",
                "User-Agent": self.conf["user_agent"],
                **(headers or {}),
            },
            params=params,
            json=payload,
            timeout=self.conf["request_timeout"],
            verify=False,
        )
        if resp.status_code not in expected:
            raise RuntimeError(f"CloudMailGen 请求失败: {method} {path}, HTTP {resp.status_code}, body={resp.text[:300]}")
        return {} if resp.status_code == 204 else resp.json()

    def _cache_key(self) -> str:
        return f"{self.api_base}|{self.admin_email}"

    def _get_token(self) -> str:
        if not self.admin_email or not self.admin_password:
            raise RuntimeError("CloudMailGen 缺少 admin_email 或 admin_password")
        cache_key = self._cache_key()
        now = time.time()
        with cloudmail_token_lock:
            cached = cloudmail_token_cache.get(cache_key)
            if cached and now < cached[1] - 300:
                return cached[0]
        data = self._request(
            "POST",
            "/api/public/genToken",
            payload={"email": self.admin_email, "password": self.admin_password},
        )
        token = ""
        if isinstance(data, dict) and data.get("code") == 200:
            token = str((data.get("data") or {}).get("token") or "").strip()
        if not token:
            raise RuntimeError(f"CloudMailGen genToken 返回异常: {data}")
        with cloudmail_token_lock:
            cloudmail_token_cache[cache_key] = (token, now + 24 * 3600)
        return token

    def _resolve_address(self, username: str | None = None) -> str:
        domain = _next_domain(self.domain)
        if self.subdomain:
            domain = f"{random.choice(self.subdomain)}.{domain}"
        if username:
            local_part = username
        elif self.email_prefix:
            local_part = f"{self.email_prefix}_{''.join(random.choices(string.ascii_lowercase + string.digits, k=6))}"
        else:
            local_part = _random_mailbox_name()
        return f"{local_part}@{domain}"

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        if not self.domain:
            raise RuntimeError("CloudMailGen 需要至少配置一个 domain")
        address = self._resolve_address(username)
        return {"provider": self.name, "provider_ref": self.provider_ref, "address": address}

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        address = str(mailbox.get("address") or "").strip()
        if not address:
            raise RuntimeError("CloudMailGen 缺少 address")
        token = self._get_token()
        data = self._request(
            "POST",
            "/api/public/emailList",
            headers={"Authorization": token},
            payload={"toEmail": address, "size": 20, "timeSort": "desc"},
        )
        items = (data.get("data") or []) if isinstance(data, dict) and data.get("code") == 200 else []
        messages = [item for item in items if isinstance(item, dict) and _message_matches_email(item, address)]
        if not messages:
            return None
        item = messages[0]
        text_content, html_content = _extract_content(item)
        return {
            "provider": self.name,
            "mailbox": address,
            "message_id": str(item.get("id") or item.get("_id") or item.get("messageId") or ""),
            "subject": str(item.get("subject") or ""),
            "sender": str(item.get("from") or item.get("sender") or ""),
            "text_content": text_content,
            "html_content": html_content,
            "received_at": _parse_received_at(
                item.get("createdAt") or item.get("created_at") or item.get("receivedAt") or item.get("date") or item.get("timestamp")
            ),
            "to": item.get("to") or item.get("toEmail") or item.get("mailTo"),
            "raw": item,
        }

    def close(self) -> None:
        self.session.close()


class MailfreeProvider(BaseMailProvider):
    name = "mailfree"
    _domain_index_lock = Lock()
    _domain_index_cursor = 0

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.api_base = str(entry.get("api_base") or "").rstrip("/")
        if not self.api_base:
            raise RuntimeError("Mailfree 缺少 api_base")
        self.admin_token = str(
            entry.get("admin_token")
            or entry.get("admin_password")
            or entry.get("api_key")
            or os.getenv("MAILFREE_ADMIN_TOKEN")
            or os.getenv("MAIL_ADMIN_TOKEN")
            or ""
        ).strip()
        if not self.admin_token:
            raise RuntimeError("Mailfree 缺少 admin_password/admin_token，且未提供环境变量 MAILFREE_ADMIN_TOKEN/MAIL_ADMIN_TOKEN")
        self.domain_indices = self._parse_domain_indices(entry)
        self.session = _create_session(conf)
        self.session.headers.update({
            "User-Agent": conf["user_agent"],
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.admin_token}",
            "X-Admin-Token": self.admin_token,
        })

    @staticmethod
    def _parse_domain_indices(entry: dict) -> list[int]:
        raw = entry.get("domain_indices")
        if raw is None:
            raw = entry.get("domain_index") if entry.get("domain_index") is not None else entry.get("domainIndex")
        values: list[int] = []
        source = raw if isinstance(raw, list) else str(raw or "").replace(";", ",").split(",")
        for item in source:
            try:
                value = int(str(item).strip())
            except (TypeError, ValueError):
                continue
            if value >= 0:
                values.append(value)
        return values or [4]

    @classmethod
    def _ordered_domain_indices(cls, values: list[int]) -> list[int]:
        unique_values = list(dict.fromkeys(values))
        if len(unique_values) <= 1:
            return unique_values
        with cls._domain_index_lock:
            start = cls._domain_index_cursor % len(unique_values)
            cls._domain_index_cursor = (cls._domain_index_cursor + 1) % len(unique_values)
        return unique_values[start:] + unique_values[:start]

    def _next_domain_index(self, values: list[int]) -> int:
        ordered = self._ordered_domain_indices(values)
        if len(ordered) <= 1:
            return ordered[0]
        now = datetime.now(timezone.utc)
        with _mailfree_domain_health_lock:
            state = _load_mailfree_domain_health()
        available: list[int] = []
        cooling: list[tuple[datetime, int]] = []
        for value in ordered:
            entry = state.get(_mailfree_domain_key(self.provider_ref, self.api_base, value)) or {}
            cooldown_until = _mailfree_parse_time(entry.get("cooldown_until"))
            if cooldown_until and cooldown_until > now:
                cooling.append((cooldown_until, value))
                continue
            available.append(value)
        if available:
            return available[0]
        cooling.sort(key=lambda item: item[0])
        return cooling[0][1] if cooling else ordered[0]

    def _request(self, method: str, path: str, params: dict | None = None, payload: dict | None = None, expected: tuple[int, ...] = (200,)):
        resp = self.session.request(
            method.upper(),
            f"{self.api_base}{path}",
            params=params,
            json=payload,
            timeout=self.conf["request_timeout"],
            verify=False,
        )
        if resp.status_code not in expected:
            raise RuntimeError(f"Mailfree 请求失败: {method} {path}, HTTP {resp.status_code}, body={resp.text[:300]}")
        if resp.status_code == 204:
            return {}
        content_type = str(resp.headers.get("content-type") or "").lower()
        body_preview = resp.text[:300].replace("\r", " ").replace("\n", " ")
        looks_like_html = "<html" in body_preview.lower() or "<!doctype html" in body_preview.lower()
        if "json" not in content_type and looks_like_html:
            raise RuntimeError(
                f"Mailfree 返回 HTML 页面而不是 JSON: {method} {path}, content-type={content_type or 'unknown'}, "
                f"body={body_preview}. 请确认 provider 类型为 mailfree，且 admin_token / MAIL_ADMIN_TOKEN 配置正确"
            )
        try:
            data = resp.json()
        except Exception as error:
            raise RuntimeError(
                f"Mailfree 返回了非 JSON 响应: {method} {path}, content-type={content_type or 'unknown'}, "
                f"body={body_preview}. 请确认 provider 类型为 mailfree，且 admin_token / MAIL_ADMIN_TOKEN 配置正确"
            ) from error
        if not isinstance(data, (dict, list)):
            raise RuntimeError(f"Mailfree {method} {path} 返回结构不是对象/数组")
        return data

    @staticmethod
    def _items(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("emails", "messages", "results", "data", "items"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        local_part = username or _random_mailbox_name()
        domain_index_value = self._next_domain_index(self.domain_indices)
        last_error: Exception | None = None
        data = None
        for _ in range(2):
            try:
                data = self._request(
                    "POST",
                    "/api/create",
                    payload={"local": local_part, "domainIndex": domain_index_value},
                    expected=(200, 201),
                )
                break
            except Exception as error:
                last_error = error
                time.sleep(0.5)
        if data is None:
            raise RuntimeError(f"Mailfree 创建邮箱失败: {last_error}")
        if not isinstance(data, dict):
            raise RuntimeError("Mailfree 创建邮箱返回结构不是对象")
        address = str(data.get("email") or data.get("address") or "").strip()
        if not address:
            raise RuntimeError(f"Mailfree 创建邮箱缺少 email/address: {data}")
        return {
            "provider": self.name,
            "provider_ref": self.provider_ref,
            "address": address,
            "domain_index": domain_index_value,
            "api_base": self.api_base,
            "label": f"mailfree#{domain_index_value}",
            "expires": data.get("expires"),
        }

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        address = str(mailbox.get("address") or "").strip()
        if not address:
            raise RuntimeError("Mailfree 缺少 address")
        data = self._request("GET", "/api/emails", params={"mailbox": address})
        messages = [item for item in self._items(data) if _message_matches_email(item, address)]
        if not messages:
            return None

        def sort_key(value: dict[str, Any]):
            date = _parse_received_at(
                value.get("createdAt")
                or value.get("created_at")
                or value.get("receivedAt")
                or value.get("received_at")
                or value.get("date")
                or value.get("timestamp")
            )
            return ((date or datetime.fromtimestamp(0, tz=timezone.utc)).timestamp(), str(value.get("id") or value.get("message_id") or ""))

        item = max(messages, key=sort_key)
        detail = item
        message_id = str(item.get("id") or item.get("message_id") or "").strip()
        if message_id and not (item.get("verification_code") or item.get("content") or item.get("html_content") or item.get("text_content")):
            detail_data = self._request("GET", f"/api/email/{message_id}")
            if isinstance(detail_data, dict):
                detail = {**item, **detail_data}
        elif isinstance(item, dict):
            detail = item

        text_content, html_content = _extract_content(detail)
        code = str(detail.get("verification_code") or item.get("verification_code") or "").strip()
        if code and code not in f"{text_content}\n{html_content}":
            text_content = f"Verification code: {code}\n{text_content}".strip()
        sender = detail.get("from") or detail.get("from_address") or detail.get("sender") or ""
        if isinstance(sender, dict):
            sender = sender.get("address") or sender.get("email") or sender.get("name") or ""
        return {
            "provider": self.name,
            "mailbox": address,
            "message_id": message_id,
            "subject": str(detail.get("subject") or ""),
            "sender": str(sender),
            "text_content": text_content,
            "html_content": html_content,
            "received_at": _parse_received_at(
                detail.get("createdAt")
                or detail.get("created_at")
                or detail.get("receivedAt")
                or detail.get("received_at")
                or detail.get("date")
                or detail.get("timestamp")
            ),
            "raw": detail,
        }

    def close(self) -> None:
        self.session.close()


class TempMailLolProvider(BaseMailProvider):
    name = "tempmail_lol"

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.api_key = str(entry.get("api_key") or "").strip()
        self.domain = [str(item).strip() for item in (entry.get("domain") or []) if str(item).strip()]
        self.has_provider_fallback = bool(entry.get("_has_provider_fallback"))
        self.random_domain_attempts = random_mail_domain_health.bounded_provider_integer(
            entry.get("random_domain_attempts"),
            random_mail_domain_health.RANDOM_MAIL_DOMAIN_ATTEMPTS,
            1,
            20,
        )
        self.domain_family_labels = random_mail_domain_health.bounded_provider_integer(
            entry.get("domain_family_labels"), 2, 2, 4
        )
        self.domain_cooldown_seconds = random_mail_domain_health.bounded_provider_integer(
            entry.get("domain_cooldown_seconds"),
            random_mail_domain_health.RANDOM_MAIL_DOMAIN_REJECT_COOLDOWN_SECONDS,
            300,
            24 * 3600,
        )
        self.denied_domains = {
            str(denied_domain or "").strip().lower().lstrip("@")
            for denied_domain in (entry.get("_denied_domains") or [])
            if str(denied_domain or "").strip()
        }
        self.session = _create_session(conf)
        self.session.headers.update({"User-Agent": conf["user_agent"], "Accept": "application/json", "Content-Type": "application/json"})
        if self.api_key:
            self.session.headers["Authorization"] = f"Bearer {self.api_key}"

    @staticmethod
    def _resolve_domain(domain: str) -> tuple[str, bool]:
        text = str(domain or "").strip().lower()
        if text.startswith("*.") and len(text) > 2:
            return f"{_random_subdomain_label()}.{text[2:]}", True
        return text, False

    def _request(self, method: str, path: str, params: dict | None = None, payload: dict | None = None, expected: tuple[int, ...] = (200,), retries: int = 2):
        last_error = ""
        attempts = max(1, int(retries) + 1)
        for attempt in range(1, attempts + 1):
            try:
                resp = self.session.request(
                    method.upper(),
                    f"https://api.tempmail.lol/v2{path}",
                    params=params,
                    json=payload,
                    timeout=self.conf["request_timeout"],
                    verify=False,
                )
            except Exception as exc:  # noqa: BLE001 - transport failures are retryable.
                last_error = (
                    f"TempMail.lol {method} {path} transport error: "
                    f"{type(exc).__name__}: {str(exc)[:240]}"
                )
                if attempt < attempts:
                    time.sleep(min(1.5 * attempt, 4.0))
                    continue
                raise RuntimeError(last_error) from exc
            body = getattr(resp, "text", "") or ""
            if resp.status_code not in expected:
                last_error = f"TempMail.lol 请求失败: {method} {path}, HTTP {resp.status_code}, body={body[:300]}"
                if attempt < attempts and resp.status_code in {408, 425, 429, 500, 502, 503, 504}:
                    time.sleep(min(1.5 * attempt, 4.0))
                    continue
                raise RuntimeError(last_error)
            if not body.strip():
                last_error = f"TempMail.lol {method} {path} empty response body (HTTP {resp.status_code})"
                if attempt < attempts:
                    time.sleep(min(1.0 * attempt, 3.0))
                    continue
                raise RuntimeError(last_error)
            try:
                data = resp.json()
            except Exception as exc:
                last_error = f"TempMail.lol {method} {path} non-json response: {exc}; body={body[:200]}"
                if attempt < attempts:
                    time.sleep(min(1.0 * attempt, 3.0))
                    continue
                raise RuntimeError(last_error) from exc
            if not isinstance(data, dict):
                raise RuntimeError(f"TempMail.lol {method} {path} 返回结构不是对象")
            return data
        raise RuntimeError(last_error or f"TempMail.lol {method} {path} failed")

    def _random_mailbox_result(
        self,
        address: str,
        token: str,
        skipped_domains: list[str],
        *,
        half_open: bool = False,
    ) -> dict[str, Any]:
        effective_skipped = list(dict.fromkeys(skipped_domains))
        if half_open:
            selected_family = random_mail_domain_health.random_mail_domain_family(
                address.partition("@")[2], self.domain_family_labels
            )
            effective_skipped = [
                domain
                for domain in effective_skipped
                if random_mail_domain_health.random_mail_domain_family(
                    domain, self.domain_family_labels
                )
                != selected_family
            ]
        result = {
            **random_mail_domain_health.random_mailbox_metadata(
                provider=self.name,
                provider_ref=self.provider_ref,
                address=address,
                family_labels=self.domain_family_labels,
                cooldown_seconds=self.domain_cooldown_seconds,
                skipped_domains=effective_skipped,
            ),
            "token": token,
        }
        if half_open:
            result["domain_health_half_open"] = True
        return result

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        random_domain = not self.domain
        attempts = self.random_domain_attempts if random_domain else 1
        skipped_domains: list[str] = []
        cooling_mailboxes: dict[str, dict[str, str]] = {}
        known_cooling_families = (
            random_mail_domain_health.random_mail_domain_cooling_families(
                self.name, self.provider_ref
            )
            if random_domain
            else []
        )
        preferred_half_open = random_mail_domain_health.select_random_mail_domain_half_open(
            self.name,
            self.provider_ref,
            known_cooling_families,
            self.domain_family_labels,
        )
        for _ in range(attempts):
            payload: dict[str, Any] = {}
            if self.domain:
                domain, force_random_prefix = self._resolve_domain(random.choice(self.domain))
                payload["domain"] = domain
                if force_random_prefix:
                    payload["prefix"] = _random_mailbox_name()
            if username and "prefix" not in payload:
                payload["prefix"] = username
            data = self._request("POST", "/inbox/create", payload=payload, expected=(200, 201))
            address = str(data.get("address") or "").strip()
            token = str(data.get("token") or "").strip()
            if not address or not token:
                raise RuntimeError("TempMail.lol 缺少 address 或 token")
            address_domain = address.partition("@")[2].lower()
            domain_family = random_mail_domain_health.random_mail_domain_family(
                address_domain, self.domain_family_labels
            )
            if address_domain in self.denied_domains or domain_family in self.denied_domains:
                skipped_domains.append(address_domain)
                continue
            if random_domain and random_mail_domain_health.random_mail_domain_is_cooling(
                self.name, self.provider_ref, domain_family
            ):
                skipped_domains.append(address_domain)
                cooling_mailboxes.setdefault(
                    domain_family,
                    {"address": address, "token": token},
                )
                if (
                    len(cooling_mailboxes)
                    >= random_mail_domain_health.RANDOM_MAIL_DOMAIN_HALF_OPEN_MIN_FAMILIES
                    and preferred_half_open in cooling_mailboxes
                    and not self.has_provider_fallback
                ):
                    preferred = cooling_mailboxes[preferred_half_open]
                    return self._random_mailbox_result(
                        preferred["address"],
                        preferred["token"],
                        skipped_domains,
                        half_open=True,
                    )
                continue
            if random_domain:
                return self._random_mailbox_result(address, token, skipped_domains)
            return {
                "provider": self.name,
                "provider_ref": self.provider_ref,
                "address": address,
                "token": token,
                "domain": address_domain,
                "label": f"{self.name}:{address_domain}" if address_domain else self.name,
            }
        if random_domain and cooling_mailboxes and not self.has_provider_fallback:
            selected_family = random_mail_domain_health.select_random_mail_domain_half_open(
                self.name,
                self.provider_ref,
                list(cooling_mailboxes),
                self.domain_family_labels,
            )
            selected = cooling_mailboxes.get(selected_family)
            if selected:
                return self._random_mailbox_result(
                    selected["address"],
                    selected["token"],
                    skipped_domains,
                    half_open=True,
                )
        families = sorted(
            {
                random_mail_domain_health.random_mail_domain_family(
                    item, self.domain_family_labels
                )
                for item in skipped_domains
            }
        )
        raise RuntimeError(f"TempMail.lol 随机域名均处于冷却: {','.join(families)}")

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        data = self._request("GET", "/inbox", params={"token": mailbox["token"]})
        items = data.get("emails") or data.get("messages") or []
        messages = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
        if not messages:
            return None
        item = max(messages, key=lambda value: ((_parse_received_at(value.get("created_at") or value.get("createdAt") or value.get("date") or value.get("received_at") or value.get("timestamp")) or datetime.fromtimestamp(0, tz=timezone.utc)).timestamp(), str(value.get("id") or value.get("token") or "")))
        text_content, html_content = _extract_content(item)
        return {"provider": self.name, "mailbox": mailbox["address"], "message_id": str(item.get("id") or item.get("token") or ""), "subject": str(item.get("subject") or ""), "sender": str(item.get("from") or item.get("from_address") or ""), "text_content": text_content, "html_content": html_content, "received_at": _parse_received_at(item.get("created_at") or item.get("createdAt") or item.get("date") or item.get("received_at") or item.get("timestamp")), "raw": item}

    def close(self) -> None:
        self.session.close()


class DuckMailProvider(BaseMailProvider):
    name = "duckmail"

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.api_key = str(entry["api_key"]).strip()
        self.default_domain = str(entry.get("default_domain") or "duckmail.sbs").strip() or "duckmail.sbs"
        self.session = _create_session(conf)
        self.session.headers.update({"User-Agent": conf["user_agent"], "Accept": "application/json", "Content-Type": "application/json"})

    def _request(self, method: str, path: str, token: str = "", use_api_key: bool = False, params: dict | None = None, payload: dict | None = None, expected: tuple[int, ...] = (200, 201, 204)):
        headers = {"Authorization": f"Bearer {self.api_key if use_api_key else token}"} if use_api_key or token else {}
        resp = self.session.request(method.upper(), f"https://api.duckmail.sbs{path}", headers=headers, params=params, json=payload, timeout=self.conf["request_timeout"], verify=False)
        if resp.status_code not in expected:
            raise RuntimeError(f"DuckMail 请求失败: {method} {path}, HTTP {resp.status_code}, body={resp.text[:300]}")
        return {} if resp.status_code == 204 else resp.json()

    @staticmethod
    def _items(data):
        return data if isinstance(data, list) else data.get("hydra:member") or data.get("member") or data.get("data") or []

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        password = "".join(random.choices(string.ascii_letters + string.digits, k=12))
        address = f"{username or _random_mailbox_name()}@{self.default_domain}"
        payload = {"address": address, "password": password}
        account = self._request("POST", "/accounts", use_api_key=True, payload=payload)
        token_data = self._request("POST", "/token", use_api_key=True, payload=payload)
        return {"provider": self.name, "provider_ref": self.provider_ref, "address": address, "token": str(token_data.get("token") or ""), "password": password, "account_id": str(account.get("id") or "")}

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        data = self._request("GET", "/messages", token=str(mailbox.get("token") or ""), params={"page": 1})
        items = self._items(data)
        if not items:
            return None
        item = items[0]
        message_id = str(item.get("id") or item.get("@id") or "").replace("/messages/", "")
        if message_id:
            item = self._request("GET", f"/messages/{message_id}", token=str(mailbox.get("token") or ""))
        sender = item.get("from") or ""
        if isinstance(sender, dict):
            sender = sender.get("address") or sender.get("name") or ""
        html_content = item.get("html") or ""
        if isinstance(html_content, list):
            html_content = "".join(str(value) for value in html_content)
        return {"provider": self.name, "mailbox": mailbox["address"], "message_id": message_id, "subject": str(item.get("subject") or ""), "sender": str(sender), "text_content": str(item.get("text") or item.get("text_content") or ""), "html_content": str(html_content), "received_at": _parse_received_at(item.get("createdAt") or item.get("created_at") or item.get("receivedAt") or item.get("date")), "raw": item}

    def close(self) -> None:
        self.session.close()


class GptMailProvider(BaseMailProvider):
    name = "gptmail"

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.api_key = str(entry["api_key"]).strip()
        self.default_domain = str(entry.get("default_domain") or "").strip()
        self.session = _create_session(conf)
        self.session.headers.update({"User-Agent": conf["user_agent"], "Accept": "application/json", "Content-Type": "application/json", "X-API-Key": self.api_key})

    def _request(self, method: str, path: str, params: dict | None = None, payload: dict | None = None):
        query = dict(params or {})
        resp = self.session.request(method.upper(), f"https://mail.chatgpt.org.uk{path}", params=query, json=payload, timeout=self.conf["request_timeout"], verify=False)
        if resp.status_code != 200:
            raise RuntimeError(f"GPTMail 请求失败: {method} {path}, HTTP {resp.status_code}, body={resp.text[:300]}")
        data = resp.json()
        return data["data"] if isinstance(data, dict) and "data" in data else data

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        payload = {key: value for key, value in {"prefix": username, "domain": self.default_domain}.items() if value}
        data = self._request("POST" if payload else "GET", "/api/generate-email", payload=payload or None)
        return {"provider": self.name, "provider_ref": self.provider_ref, "address": str(data["email"])}

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        data = self._request("GET", "/api/emails", params={"email": mailbox["address"]})
        emails = data if isinstance(data, list) else data.get("emails") or []
        if not emails:
            return None
        item = max(emails, key=lambda value: (float(value.get("timestamp") or 0), str(value.get("id") or "")))
        if item.get("id"):
            item = self._request("GET", f"/api/email/{item['id']}")
        return {"provider": self.name, "mailbox": mailbox["address"], "message_id": str(item.get("id") or ""), "subject": str(item.get("subject") or ""), "sender": str(item.get("from_address") or ""), "text_content": str(item.get("content") or ""), "html_content": str(item.get("html_content") or ""), "received_at": _parse_received_at(item.get("timestamp") or item.get("created_at")), "raw": item}

    def close(self) -> None:
        self.session.close()


class MoEmailProvider(BaseMailProvider):
    name = "moemail"

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.api_base = str(entry["api_base"]).rstrip("/")
        self.api_key = str(entry["api_key"]).strip()
        raw_domains = entry.get("domain") or []
        if isinstance(raw_domains, list):
            self.domain = [str(item).strip() for item in raw_domains if str(item).strip()]
        else:
            self.domain = [str(raw_domains).strip()] if str(raw_domains).strip() else []
        self.expiry_time = int(entry.get("expiry_time") or 0)
        self.session = _create_session(conf)

    def _request(self, method: str, path: str, params: dict | None = None, payload: dict | None = None, expected: tuple[int, ...] = (200,)):
        resp = self.session.request(method.upper(), f"{self.api_base}{path}", headers={"X-API-Key": self.api_key, "Content-Type": "application/json", "User-Agent": self.conf["user_agent"]}, params=params, json=payload, timeout=self.conf["request_timeout"], verify=False)
        if resp.status_code not in expected:
            raise RuntimeError(f"MoEmail 请求失败: {method} {path}, HTTP {resp.status_code}, body={resp.text[:300]}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"MoEmail {method} {path} 返回结构不是对象")
        return data

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        data = self._request("POST", "/api/emails/generate", payload={"name": username or _random_mailbox_name(), "expiryTime": self.expiry_time, "domain": _next_domain(self.domain)}, expected=(200, 201))
        address = str(data.get("email") or "").strip()
        email_id = str(data.get("id") or data.get("email_id") or "").strip()
        if not address or not email_id:
            raise RuntimeError("MoEmail 缺少 email 或 id")
        return {"provider": self.name, "provider_ref": self.provider_ref, "address": address, "email_id": email_id}

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        email_id = str(mailbox.get("email_id") or "").strip()
        if not email_id:
            raise RuntimeError("MoEmail 缺少 email_id")
        data = self._request("GET", f"/api/emails/{email_id}")
        items = data.get("messages") or []
        messages = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
        if not messages:
            return None
        _, item = max(enumerate(messages), key=lambda pair: (((_parse_received_at(pair[1].get("createdAt") or pair[1].get("created_at") or pair[1].get("receivedAt") or pair[1].get("date") or pair[1].get("timestamp")) or datetime.fromtimestamp(0, tz=timezone.utc)).timestamp()), pair[0]))
        message_id = str(item.get("id") or item.get("message_id") or item.get("_id") or "").strip()
        detail = self._request("GET", f"/api/emails/{email_id}/{message_id}") if message_id else {"message": item}
        message = detail.get("message") if isinstance(detail.get("message"), dict) else detail
        text_content, html_content = _extract_content(message)
        sender = message.get("from") or message.get("sender") or ""
        if isinstance(sender, dict):
            sender = sender.get("address") or sender.get("email") or sender.get("name") or ""
        return {"provider": self.name, "mailbox": mailbox["address"], "message_id": message_id, "subject": str(message.get("subject") or item.get("subject") or ""), "sender": str(sender), "text_content": text_content, "html_content": html_content, "received_at": _parse_received_at(message.get("createdAt") or message.get("created_at") or message.get("receivedAt") or message.get("date") or message.get("timestamp") or item.get("createdAt") or item.get("created_at") or item.get("receivedAt") or item.get("date") or item.get("timestamp")), "raw": detail}

    def close(self) -> None:
        self.session.close()


class InbucketMailProvider(BaseMailProvider):
    name = "inbucket"

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.api_base = str(entry["api_base"]).rstrip("/")
        raw_domains = entry.get("domain") or []
        if isinstance(raw_domains, list):
            self.domain = [str(item).strip() for item in raw_domains if str(item).strip()]
        else:
            self.domain = [str(raw_domains).strip()] if str(raw_domains).strip() else []
        self.random_subdomain = bool(entry.get("random_subdomain", True))
        self.session = _create_session(conf)
        self.session.headers.update({
            "User-Agent": conf["user_agent"],
            "Accept": "application/json",
        })

    def _request(self, method: str, path: str, expected: tuple[int, ...] = (200,)):
        resp = self.session.request(
            method.upper(),
            f"{self.api_base}{path}",
            timeout=self.conf["request_timeout"],
            verify=False,
        )
        if resp.status_code not in expected:
            raise RuntimeError(f"Inbucket 请求失败: {method} {path}, HTTP {resp.status_code}, body={resp.text[:300]}")
        if resp.status_code == 204:
            return {}
        content_type = str(resp.headers.get("content-type") or "").lower()
        if "application/json" in content_type:
            return resp.json()
        return resp.text

    def _resolve_domain(self) -> str:
        if self.domain:
            return _next_domain(self.domain)
        raise RuntimeError("Inbucket 需要至少配置一个 domain")

    def _mailbox_name(self, address: str) -> str:
        local_part, _, _ = str(address or "").partition("@")
        return local_part.strip()

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        local_part = username or _random_mailbox_name()
        base_domain = self._resolve_domain()
        domain = f"{_random_subdomain_label()}.{base_domain}" if self.random_subdomain else base_domain
        address = f"{local_part}@{domain}"
        mailbox_name = self._mailbox_name(address)
        return {
            "provider": self.name,
            "provider_ref": self.provider_ref,
            "address": address,
            "base_domain": base_domain,
            "mailbox_name": mailbox_name,
        }

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        mailbox_name = str(mailbox.get("mailbox_name") or self._mailbox_name(str(mailbox.get("address") or ""))).strip()
        if not mailbox_name:
            raise RuntimeError("Inbucket 缺少 mailbox_name")
        data = self._request("GET", f"/api/v1/mailbox/{mailbox_name}")
        items = [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
        if not items:
            return None
        items.sort(
            key=lambda value: (
                (_parse_received_at(value.get("date")) or datetime.fromtimestamp(0, tz=timezone.utc)).timestamp(),
                str(value.get("id") or ""),
            ),
            reverse=True,
        )
        address = str(mailbox.get("address") or "").strip()
        for item in items:
            message_id = str(item.get("id") or "").strip()
            if not message_id:
                continue
            detail = self._request("GET", f"/api/v1/mailbox/{mailbox_name}/{message_id}")
            if not isinstance(detail, dict):
                continue
            header = detail.get("header") if isinstance(detail.get("header"), dict) else {}
            body = detail.get("body") if isinstance(detail.get("body"), dict) else {}
            normalized = {
                "provider": self.name,
                "mailbox": mailbox_name,
                "message_id": message_id,
                "subject": str(detail.get("subject") or item.get("subject") or ""),
                "sender": str(detail.get("from") or item.get("from") or ""),
                "text_content": str(body.get("text") or ""),
                "html_content": str(body.get("html") or ""),
                "received_at": _parse_received_at(detail.get("date") or item.get("date")),
                "to": header.get("To") if isinstance(header, dict) else None,
                "raw": detail,
            }
            if _message_matches_email(normalized, address):
                return normalized
        return None

    def close(self) -> None:
        self.session.close()


class YydsMailProvider(BaseMailProvider):
    name = "yyds_mail"

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.api_base = str(entry.get("api_base") or "https://maliapi.215.im/v1").rstrip("/")
        self.api_key = str(entry["api_key"]).strip()
        self.domain = [str(item).strip() for item in (entry.get("domain") or []) if str(item).strip()]
        self.subdomain = str(entry.get("subdomain") or "").strip()
        self.wildcard = bool(entry.get("wildcard"))
        self.session = _create_session(conf)
        self.session.headers.update({"User-Agent": conf["user_agent"], "Accept": "application/json", "Content-Type": "application/json"})

    def _request(self, method: str, path: str, token: str = "", params: dict | None = None, payload: dict | None = None, expected: tuple[int, ...] = (200, 201, 204)):
        headers = {"Authorization": f"Bearer {token}"} if token else {"X-API-Key": self.api_key}
        resp = self.session.request(method.upper(), f"{self.api_base}{path}", headers=headers, params=params, json=payload, timeout=self.conf["request_timeout"], verify=False)
        if resp.status_code not in expected:
            raise RuntimeError(f"YYDSMail 请求失败: {method} {path}, HTTP {resp.status_code}, body={resp.text[:300]}")
        if resp.status_code == 204:
            return {}
        data = resp.json()
        if isinstance(data, dict) and data.get("success") is False:
            raise RuntimeError(f"YYDSMail 请求失败: {data.get('errorCode') or data.get('error')}")
        return data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), (dict, list)) else data

    @staticmethod
    def _items(data):
        return data if isinstance(data, list) else data.get("items") or data.get("messages") or data.get("data") or []

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        payload = {"localPart": username or _random_mailbox_name()}
        if self.domain:
            payload["domain"] = _next_domain(self.domain)
        if self.subdomain:
            payload["subdomain"] = self.subdomain
        data = self._request("POST", "/accounts/wildcard" if self.wildcard else "/accounts", payload=payload)
        address = str(data.get("address") or data.get("email") or "").strip()
        token = str(data.get("token") or data.get("temp_token") or data.get("tempToken") or data.get("access_token") or "").strip()
        if not address or not token:
            raise RuntimeError("YYDSMail 缺少 address 或 token")
        return {"provider": self.name, "provider_ref": self.provider_ref, "address": address, "token": token, "account_id": str(data.get("id") or "")}

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        data = self._request("GET", "/messages", token=str(mailbox.get("token") or ""), params={"address": mailbox["address"]})
        messages = [item for item in self._items(data) if isinstance(item, dict)]
        if not messages:
            return None
        item = max(messages, key=lambda value: ((_parse_received_at(value.get("createdAt") or value.get("created_at") or value.get("receivedAt") or value.get("date") or value.get("timestamp")) or datetime.fromtimestamp(0, tz=timezone.utc)).timestamp(), str(value.get("id") or "")))
        message_id = str(item.get("id") or item.get("message_id") or "").strip()
        if message_id:
            item = self._request("GET", f"/messages/{message_id}", token=str(mailbox.get("token") or ""), params={"address": mailbox["address"]})
        text_content, html_content = _extract_content(item)
        sender = item.get("from") or item.get("sender") or ""
        if isinstance(sender, dict):
            sender = sender.get("address") or sender.get("email") or sender.get("name") or ""
        return {"provider": self.name, "mailbox": mailbox["address"], "message_id": message_id, "subject": str(item.get("subject") or ""), "sender": str(sender), "text_content": text_content, "html_content": html_content, "received_at": _parse_received_at(item.get("createdAt") or item.get("created_at") or item.get("receivedAt") or item.get("date") or item.get("timestamp")), "raw": item}

    def close(self) -> None:
        self.session.close()


OUTLOOK_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
OUTLOOK_GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"
OUTLOOK_GRAPH_SCOPE = "offline_access https://graph.microsoft.com/Mail.Read"
OUTLOOK_IMAP_SCOPE = "offline_access https://outlook.office.com/IMAP.AccessAsUser.All"
OUTLOOK_DEFAULT_IMAP_HOST = "outlook.office365.com"


class OutlookTokenError(RuntimeError):
    """refresh_token 换取 access_token 失败（凭据失效/权限不对），与“读邮件失败”区分。"""


def _clean_outlook_value(value: str) -> str:
    return str(value or "").replace("﻿", "").replace(" ", " ").strip()


def parse_outlook_credentials(text: str) -> list[dict[str, str]]:
    """解析邮箱池文本，每行格式：email----password----client_id----refresh_token。"""
    credentials: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_line in str(text or "").splitlines():
        line = _clean_outlook_value(raw_line)
        if not line or "----" not in line:
            continue
        parts = [_clean_outlook_value(part) for part in line.split("----", 3)]
        if len(parts) != 4:
            continue
        email, password, client_id, refresh_token = parts
        if "@" not in email or not client_id or not refresh_token:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        credentials.append({"email": email, "password": password, "client_id": client_id, "refresh_token": refresh_token})
    return credentials


def _normalize_outlook_pool(value: Any) -> list[dict[str, str]]:
    """邮箱池既支持纯文本（每行一条），也支持已解析的对象列表。"""
    if isinstance(value, str):
        return parse_outlook_credentials(value)
    if isinstance(value, list):
        items: list[dict[str, str]] = []
        for item in value:
            if isinstance(item, str):
                items.extend(parse_outlook_credentials(item))
            elif isinstance(item, dict):
                email = _clean_outlook_value(item.get("email") or item.get("address") or "")
                client_id = _clean_outlook_value(item.get("client_id") or "")
                refresh_token = _clean_outlook_value(item.get("refresh_token") or "")
                if "@" in email and client_id and refresh_token:
                    items.append({"email": email, "password": _clean_outlook_value(item.get("password") or ""), "client_id": client_id, "refresh_token": refresh_token})
        return items
    return []


class OutlookTokenProvider(BaseMailProvider):
    """使用 refresh_token 读取 Outlook/Hotmail 邮箱验证码。

    邮箱池在应用配置里维护（mailboxes 字段，每行 email----password----client_id----refresh_token），
    create_mailbox() 从池中取下一个未使用的邮箱，wait_for_code() 用 refresh_token 换取 access_token
    后通过 Graph/IMAP 读取最新邮件。
    """

    name = "outlook_token"

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.label = str(entry.get("label") or self.provider_ref)
        self.pool = _normalize_outlook_pool(entry.get("mailboxes") or entry.get("pool"))
        self.mode = str(entry.get("mode") or "graph").strip().lower() or "graph"
        if self.mode not in {"graph", "imap", "auto"}:
            self.mode = "graph"
        self.use_plus_alias = bool(entry.get("use_plus_alias", False))
        self.alias_prefix = re.sub(r"[^a-z0-9]", "", str(entry.get("alias_prefix") or "gpt").lower()) or "gpt"
        self.imap_host = str(entry.get("imap_host") or OUTLOOK_DEFAULT_IMAP_HOST).strip() or OUTLOOK_DEFAULT_IMAP_HOST
        self.message_limit = max(1, int(entry.get("message_limit") or 10))
        self.session = _create_session(conf)

    def close(self) -> None:
        self.session.close()

    def _exchange_refresh_token(self, client_id: str, refresh_token: str, scope: str) -> str:
        resp = self.session.post(
            OUTLOOK_TOKEN_URL,
            data={"client_id": client_id, "grant_type": "refresh_token", "refresh_token": refresh_token, "scope": scope},
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": self.conf["user_agent"]},
            timeout=self.conf["request_timeout"],
            verify=False,
        )
        try:
            data = resp.json()
        except Exception:
            data = {}
        if resp.status_code != 200:
            detail = data.get("error_description") or data.get("error") or resp.text[:300]
            raise OutlookTokenError(f"OutlookToken 刷新失败: HTTP {resp.status_code}, {detail}")
        access_token = str(data.get("access_token") or "").strip()
        if not access_token:
            raise OutlookTokenError("OutlookToken 刷新响应缺少 access_token")
        return access_token

    def _access_token(self, mailbox: dict[str, Any], client_id: str, refresh_token: str, scope: str) -> str:
        """缓存 access_token 复用：避免 wait_for_code 轮询时每次都换 token 触发限流。"""
        cache = mailbox.get("_outlook_token_cache")
        if not isinstance(cache, dict):
            cache = {}
            mailbox["_outlook_token_cache"] = cache
        cached = cache.get(scope)
        if isinstance(cached, tuple) and len(cached) == 2 and time.monotonic() < cached[1]:
            return str(cached[0])
        token = self._exchange_refresh_token(client_id, refresh_token, scope)
        cache[scope] = (token, time.monotonic() + 600)
        return token

    def _plus_alias(self, parent_email: str) -> str:
        local, separator, domain = str(parent_email or "").strip().partition("@")
        if not separator:
            raise RuntimeError("OutlookToken parent email is invalid")
        base_local = local.split("+", 1)[0]
        timestamp = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S%f")
        random_suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
        tag = f"{self.alias_prefix}{timestamp}{random_suffix}"
        max_base_length = max(1, 64 - len(tag) - 1)
        return f"{base_local[:max_base_length]}+{tag}@{domain}"

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        if not self.pool:
            raise RuntimeError("OutlookToken 邮箱池为空，请在邮箱配置中导入 email----password----client_id----refresh_token")
        with _outlook_token_state_lock:
            store = _load_outlook_token_state()
            credential = next((item for item in self.pool if _outlook_entry_available(store.get(item["email"].strip().lower()))), None)
            if credential is None:
                raise RuntimeError(f"[{self.label}] OutlookToken 邮箱池暂无可用邮箱（共 {len(self.pool)} 个，已用尽或全部占用/失效），请导入新邮箱或重置池状态")
            parent_email = credential["email"].strip()
            state_key = parent_email.lower()
            store[state_key] = {"state": "in_use", "reason": "", "updated_at": datetime.now(timezone.utc).isoformat()}
            _save_outlook_token_state(store)
        address = self._plus_alias(parent_email) if self.use_plus_alias else parent_email
        return {
            "provider": self.name,
            "provider_ref": self.provider_ref,
            "address": address,
            "parent_email": parent_email,
            "resolved_email": parent_email,
            "state_key": state_key,
            "mode": "plus_alias" if self.use_plus_alias else "main",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "label": self.label,
            "client_id": credential["client_id"],
            "refresh_token": credential["refresh_token"],
        }

    def _read_graph(self, access_token: str) -> list[dict[str, Any]]:
        resp = self.session.get(
            OUTLOOK_GRAPH_MESSAGES_URL,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json", "User-Agent": self.conf["user_agent"]},
            params={
                "$top": self.message_limit,
                "$orderby": "receivedDateTime desc",
                "$select": "subject,receivedDateTime,from,toRecipients,body,bodyPreview",
            },
            timeout=self.conf["request_timeout"],
            verify=False,
        )
        try:
            data = resp.json()
        except Exception:
            data = {}
        if resp.status_code != 200:
            detail = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else resp.text[:300]
            raise RuntimeError(f"OutlookToken Graph 失败: HTTP {resp.status_code}, {detail}")
        items = data.get("value") if isinstance(data, dict) else None
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    @staticmethod
    def _graph_sender(message: dict[str, Any]) -> str:
        sender = message.get("from") or {}
        if isinstance(sender, dict):
            address = sender.get("emailAddress") or {}
            if isinstance(address, dict):
                return str(address.get("address") or address.get("name") or "")
        return ""

    def _normalize_graph_item(self, mailbox: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        body = item.get("body") if isinstance(item.get("body"), dict) else {}
        content_type = str(body.get("contentType") or "").lower()
        content = str(body.get("content") or "")
        text_content = content if content_type != "html" else str(item.get("bodyPreview") or "")
        html_content = content if content_type == "html" else ""
        recipients = []
        for recipient in item.get("toRecipients") or []:
            if not isinstance(recipient, dict):
                continue
            email_address = recipient.get("emailAddress") or {}
            if isinstance(email_address, dict) and email_address.get("address"):
                recipients.append(str(email_address["address"]))
        return {
            "provider": self.name,
            "mailbox": mailbox["address"],
            "message_id": str(item.get("id") or ""),
            "subject": str(item.get("subject") or ""),
            "sender": self._graph_sender(item),
            "to": recipients,
            "text_content": text_content,
            "html_content": html_content,
            "received_at": _parse_received_at(item.get("receivedDateTime")),
            "raw": item,
        }

    def _graph_messages(self, mailbox: dict[str, Any], access_token: str) -> list[dict[str, Any]]:
        """返回最近 N 封邮件（Graph 已按 receivedDateTime desc 排序，最新在前）。"""
        return [self._normalize_graph_item(mailbox, item) for item in self._read_graph(access_token)]

    def _imap_messages(self, mailbox: dict[str, Any], access_token: str) -> list[dict[str, Any]]:
        """返回最近 N 封邮件，最新在前。"""
        login_email = str(mailbox.get("parent_email") or mailbox.get("resolved_email") or mailbox["address"])
        auth_string = f"user={login_email}\x01auth=Bearer {access_token}\x01\x01"
        imap = imaplib.IMAP4_SSL(self.imap_host)
        try:
            imap.authenticate("XOAUTH2", lambda _: auth_string.encode("utf-8"))
            status, _ = imap.select("INBOX", readonly=True)
            if status != "OK":
                raise RuntimeError("OutlookToken IMAP select INBOX 失败")
            status, data = imap.uid("search", None, "ALL")
            if status != "OK" or not data or not data[0]:
                return []
            uids = data[0].split()[-self.message_limit :]
            messages: list[dict[str, Any]] = []
            for uid in reversed(uids):  # 最新在前
                status, fetched = imap.uid("fetch", uid, "(RFC822)")
                if status != "OK":
                    continue
                raw_payload = next((part[1] for part in fetched if isinstance(part, tuple) and isinstance(part[1], bytes)), b"")
                if raw_payload:
                    messages.append(self._parse_imap_message(mailbox, raw_payload))
            return messages
        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def _parse_imap_message(self, mailbox: dict[str, Any], raw: bytes) -> dict[str, Any]:
        message = message_from_bytes(raw, policy=policy.default)
        try:
            received = _parse_received_at(
                email_utils.parsedate_to_datetime(str(message.get("Date") or ""))
            )
        except Exception:
            received = None
        plain: list[str] = []
        html: list[str] = []
        for part in (message.walk() if message.is_multipart() else [message]):
            if part.get_content_maintype() == "multipart":
                continue
            try:
                payload = part.get_content()
            except Exception:
                continue
            if not payload:
                continue
            if part.get_content_type() == "text/html":
                html.append(str(payload))
            else:
                plain.append(str(payload))

        def _decode(value: str | None) -> str:
            if not value:
                return ""
            try:
                return str(email_header.make_header(email_header.decode_header(value)))
            except Exception:
                return value

        return {
            "provider": self.name,
            "mailbox": mailbox["address"],
            "message_id": _decode(str(message.get("Message-ID") or "")),
            "subject": _decode(str(message.get("Subject") or "")),
            "sender": _decode(str(message.get("From") or "")),
            "to": [_decode(str(message.get("To") or ""))],
            "text_content": "\n".join(plain).strip(),
            "html_content": "\n".join(html).strip(),
            "received_at": received,
            "raw": None,
        }

    def fetch_recent_messages(self, mailbox: dict[str, Any]) -> list[dict[str, Any]]:
        """拉取最近 N 封邮件（最新在前），供 wait_for_code 逐封扫描验证码。"""
        client_id = str(mailbox.get("client_id") or "").strip()
        refresh_token = str(mailbox.get("refresh_token") or "").strip()
        if not client_id or not refresh_token:
            raise RuntimeError("OutlookToken mailbox 缺少 client_id 或 refresh_token")
        errors: list[str] = []
        if self.mode in {"graph", "auto"}:
            try:
                access_token = self._access_token(mailbox, client_id, refresh_token, OUTLOOK_GRAPH_SCOPE)
                return self._graph_messages(mailbox, access_token)
            except Exception as error:
                if self.mode == "graph":
                    raise
                errors.append(f"graph: {error}")
        if self.mode in {"imap", "auto"}:
            try:
                access_token = self._access_token(mailbox, client_id, refresh_token, OUTLOOK_IMAP_SCOPE)
                return self._imap_messages(mailbox, access_token)
            except Exception as error:
                if self.mode == "imap":
                    raise
                errors.append(f"imap: {error}")
        if errors:
            raise RuntimeError("; ".join(errors))
        return []

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        messages = self.fetch_recent_messages(mailbox)
        return messages[0] if messages else None

    def wait_for_code(self, mailbox: dict[str, Any]) -> str | None:
        """轮询时遍历最近 N 封邮件，逐封提取验证码，避免最新一封是广告/安全提醒时错过验证码。"""
        seen_value = mailbox.setdefault("_seen_code_message_refs", [])
        if not isinstance(seen_value, list):
            seen_value = []
            mailbox["_seen_code_message_refs"] = seen_value
        seen_refs = {str(item) for item in seen_value}
        target_address = str(mailbox.get("address") or "").strip()
        created_at = _parse_received_at(mailbox.get("created_at"))
        minimum_timestamp = (created_at.timestamp() - 30.0) if created_at else 0.0

        deadline = time.monotonic() + self.conf["wait_timeout"]
        while time.monotonic() < deadline:
            for message in self.fetch_recent_messages(mailbox):
                if not _message_matches_email(message, target_address):
                    continue
                received_at = _parse_received_at(message.get("received_at"))
                if received_at and received_at.timestamp() < minimum_timestamp:
                    continue
                ref = _message_tracking_ref(message)
                if ref in seen_refs:
                    continue
                code = _extract_code(message)
                if code:
                    seen_value.append(ref)
                    return code
                seen_refs.add(ref)
            time.sleep(max(0.2, self.conf["wait_interval"]))
        return None



class OutlookExternalApiProvider(BaseMailProvider):
    """mail.acica.top 风格 Outlook 外部 API（账号池 + 邮件拉取）。

    create_mailbox:
      - 从 GET /api/external/accounts 取可用主邮箱
      - 优先使用已有 alias；否则生成 plus 别名 local+gptXXXX@domain
    wait_for_code:
      - GET /api/external/emails?email=<requested>&folder=all&top=20
      - 从 subject/body_preview 提取验证码
    """

    name = "outlook_external"

    def __init__(self, entry: dict, conf: dict):
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        self.label = str(entry.get("label") or self.provider_ref or self.name)
        self.api_base = str(entry.get("api_base") or entry.get("base_url") or "https://mail.acica.top").rstrip("/")
        self.api_key = str(entry.get("api_key") or entry.get("external_api_key") or "").strip()
        self.group_id = entry.get("group_id")
        self.folder = str(entry.get("folder") or "all").strip() or "all"
        self.top = max(1, min(50, int(entry.get("top") or 20)))
        self.use_plus_alias = bool(entry.get("use_plus_alias", True))
        self.prefer_alias = bool(entry.get("prefer_alias", True))
        self.realtime_preflight = bool(entry.get("realtime_preflight", True))
        self.preflight_attempts = max(1, min(20, int(entry.get("preflight_attempts") or 8)))
        if not self.api_key:
            raise RuntimeError("outlook_external 缺少 api_key（mail.acica.top 对外 API Key）")
        self.session = _create_session(conf)
        self.session.headers.update(
            {
                "User-Agent": conf["user_agent"],
                "Accept": "application/json",
                "X-API-Key": self.api_key,
            }
        )

    def _request(self, method: str, path: str, params: dict | None = None, expected: tuple[int, ...] = (200,)):
        url = f"{self.api_base}{path}"
        last_error = ""
        for attempt in range(1, 4):
            resp = self.session.request(
                method.upper(),
                url,
                params=params,
                timeout=self.conf["request_timeout"],
                verify=False,
            )
            body = getattr(resp, "text", "") or ""
            if resp.status_code not in expected:
                last_error = f"outlook_external {method} {path} HTTP {resp.status_code}: {body[:240]}"
                # 403 is often intermittent WAF/rate on shared edge; retry a few times.
                if attempt < 3 and resp.status_code in {403, 408, 425, 429, 500, 502, 503, 504}:
                    time.sleep(min(1.5 * attempt, 4.0))
                    continue
                raise RuntimeError(last_error)
            if not body.strip():
                last_error = f"outlook_external {method} {path} empty body"
                if attempt < 3:
                    time.sleep(min(1.0 * attempt, 3.0))
                    continue
                raise RuntimeError(last_error)
            try:
                data = resp.json()
            except Exception as exc:
                last_error = f"outlook_external non-json: {exc}; body={body[:180]}"
                if attempt < 3:
                    time.sleep(min(1.0 * attempt, 3.0))
                    continue
                raise RuntimeError(last_error) from exc
            if isinstance(data, dict) and data.get("success") is False:
                raise RuntimeError(str(data.get("error") or data)[:300])
            return data
        raise RuntimeError(last_error or f"outlook_external {method} {path} failed")

    def _list_accounts(self) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if self.group_id not in (None, ""):
            params["group_id"] = int(self.group_id)
        data = self._request("GET", "/api/external/accounts", params=params or None)
        items = data.get("accounts") if isinstance(data, dict) else None
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    @staticmethod
    def _plus_alias(main_email: str) -> str:
        local, sep, domain = str(main_email or "").partition("@")
        if not sep:
            raise RuntimeError(f"非法邮箱: {main_email}")
        tag = f"gpt{datetime.now(timezone.utc).strftime('%y%m%d%H%M%S')}{random.randint(100,999)}"
        # Outlook 常见支持 plus 别名；下游取信时用主邮箱/别名都可
        return f"{local}+{tag}@{domain}"

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        accounts = self._list_accounts()
        if not accounts:
            raise RuntimeError("outlook_external 账号池为空")
        used = _load_outlook_external_used()
        # OpenAI 会把 local+tag@outlook.com 归一到主邮箱，主邮箱一旦注册过就不能再靠 plus 别名新建
        candidates, pool_stats = outlook_account_selection.eligible_outlook_accounts(
            accounts, used
        )
        if not candidates:
            raise RuntimeError(
                "outlook_external 可取信主邮箱已耗尽"
                f"（池={pool_stats['pool']} healthy={pool_stats['healthy']} "
                f"unverified={pool_stats['unverified']} used={pool_stats['used']}），"
                "请扩充 mail.acica.top 分组或重置 used 记录"
            )
        global provider_index
        with provider_lock:
            idx = provider_index % len(candidates)
            provider_index = (provider_index + 1) % len(candidates)
        candidates = outlook_account_selection.rotate_outlook_accounts(candidates, idx)
        account = candidates[0]
        preflight_errors: list[str] = []
        if self.realtime_preflight:
            def probe_account(candidate_email: str) -> None:
                self._request(
                    "GET",
                    "/api/external/emails",
                    params={"email": candidate_email, "folder": "inbox", "top": 1},
                )

            readable_account, preflight_errors = (
                outlook_account_selection.first_readable_outlook_account(
                    candidates,
                    self.preflight_attempts,
                    probe_account,
                )
            )
            account = readable_account or {}
            if not account:
                raise RuntimeError(
                    "outlook_external 实时取信预检失败"
                    f"（候选={min(len(candidates), self.preflight_attempts)}）: "
                    f"{preflight_errors[-1] if preflight_errors else 'no readable account'}"
                )
        main = str(account.get("email") or "").strip()
        if not main:
            raise RuntimeError("outlook_external 账号缺少 email")
        aliases = [str(x).strip() for x in (account.get("aliases") or []) if str(x).strip()]
        if self.prefer_alias and aliases:
            address = random.choice(aliases)
            mode = "alias"
        elif self.use_plus_alias:
            address = self._plus_alias(main)
            mode = "plus_alias"
        else:
            address = main
            mode = "main"
        return {
            "provider": self.name,
            "provider_ref": self.provider_ref,
            "address": address,
            "label": self.label,
            "resolved_email": main,
            "matched_alias": address if mode != "main" else "",
            "account_id": account.get("id"),
            "mode": mode,
            "api_base": self.api_base,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "preflight_checked": bool(self.realtime_preflight),
            "preflight_skipped": len(preflight_errors),
        }

    def fetch_recent_messages(self, mailbox: dict[str, Any]) -> list[dict[str, Any]]:
        requested = str(mailbox.get("address") or mailbox.get("matched_alias") or "").strip()
        main = str(mailbox.get("resolved_email") or "").strip()
        # plus/alias 注册时，OTP 经常落在主邮箱，所以主邮箱优先；再查请求地址
        candidates: list[str] = []
        for item in [main, requested]:
            if item and item not in candidates:
                candidates.append(item)
        if not candidates:
            return []
        last_error = ""
        merged: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for email in candidates:
            try:
                data = self._request(
                    "GET",
                    "/api/external/emails",
                    params={"email": email, "folder": self.folder, "top": self.top},
                )
            except Exception as exc:
                last_error = str(exc)
                continue
            emails = data.get("emails") if isinstance(data, dict) else None
            if not isinstance(emails, list):
                continue
            for item in emails:
                if not isinstance(item, dict):
                    continue
                mid = str(item.get("id") or "")
                if mid and mid in seen_ids:
                    continue
                if mid:
                    seen_ids.add(mid)
                preview = str(item.get("body_preview") or item.get("body") or item.get("text") or "")
                subject = str(item.get("subject") or "")
                sender = str(item.get("from") or "")
                blob = f"{subject}\n{sender}\n{preview}".lower()
                # 优先保留 OpenAI/ChatGPT 相关邮件，但不完全丢弃其他邮件
                score = 0
                if any(k in blob for k in ("openai", "chatgpt", "tm.openai", "verification code", "验证码")):
                    score = 1
                merged.append(
                    {
                        "provider": self.name,
                        "mailbox": str(data.get("resolved_email") or email),
                        "message_id": mid,
                        "subject": subject,
                        "sender": sender,
                        "text_content": preview,
                        "html_content": "",
                        "received_at": _parse_received_at(item.get("date")),
                        "raw": item,
                        "_score": score,
                    }
                )
        if not merged and last_error:
            raise RuntimeError(last_error)
        # 新邮件优先，OpenAI 相关优先
        def _sort_key(msg: dict[str, Any]):
            ts = msg.get("received_at")
            epoch = ts.timestamp() if isinstance(ts, datetime) else 0.0
            return (int(msg.get("_score") or 0), epoch)

        merged.sort(key=_sort_key, reverse=True)
        for msg in merged:
            msg.pop("_score", None)
        return merged

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        items = self.fetch_recent_messages(mailbox)
        return items[0] if items else None

    def wait_for_code(self, mailbox: dict[str, Any]) -> str | None:
        seen_value = mailbox.setdefault("_seen_code_message_refs", [])
        if not isinstance(seen_value, list):
            seen_value = []
            mailbox["_seen_code_message_refs"] = seen_value
        seen_refs = {str(item) for item in seen_value}
        created_at = _parse_received_at(mailbox.get("created_at"))
        # 允许少量时钟偏差
        min_ts = (created_at.timestamp() - 30) if created_at else 0.0
        deadline = time.monotonic() + self.conf["wait_timeout"]
        while time.monotonic() < deadline:
            for message in self.fetch_recent_messages(mailbox):
                ref = _message_tracking_ref(message)
                if ref in seen_refs:
                    continue
                received = message.get("received_at")
                if isinstance(received, datetime) and received.timestamp() < min_ts:
                    seen_refs.add(ref)
                    continue
                # 优先 OpenAI 相关
                blob = f"{message.get('subject') or ''}\n{message.get('sender') or ''}\n{message.get('text_content') or ''}".lower()
                if not any(k in blob for k in ("openai", "chatgpt", "tm.openai", "verification", "code", "验证")):
                    seen_refs.add(ref)
                    continue
                code = _extract_code(message)
                if code:
                    seen_value.append(ref)
                    return code
                seen_refs.add(ref)
            time.sleep(max(0.2, self.conf["wait_interval"]))
        return None

    def close(self) -> None:
        self.session.close()

def _entries(mail_config: dict) -> list[dict]:
    result: list[dict] = []
    counters: dict[str, int] = {}
    for item in mail_config["providers"]:
        idx = len(result) + 1
        t = item.get("type", "")
        cnt = counters.get(t, 0) + 1
        counters[t] = cnt
        label = f"DDG-{cnt}" if t == "ddg_mail" else f"{t}#{idx}"
        result.append({**item, "provider_ref": f"{item['type']}#{idx}", "label": label})
    return result


def _enabled_entries(mail_config: dict) -> list[dict]:
    items = [item for item in _entries(mail_config) if item.get("enable")]
    if not items:
        raise RuntimeError("mail.providers 没有启用的 provider")
    return items


# PATCH_MARKER denied_domains_filter_r32
def _email_domain(address: str) -> str:
    value = str(address or "").strip().lower()
    return value.rsplit("@", 1)[-1] if "@" in value else ""


def _denied_domains(mail_config: dict | None) -> set[str]:
    mail = mail_config if isinstance(mail_config, dict) else {}
    values = mail.get("denied_domains") or mail.get("blocked_domains") or []
    out: set[str] = set()
    if isinstance(values, list):
        for item in values:
            domain = str(item or "").strip().lower().lstrip("@")
            if domain:
                out.add(domain)
    extra = mail.get("domain_policy") if isinstance(mail.get("domain_policy"), dict) else {}
    for item in (extra.get("denied_domains") or []):
        domain = str(item or "").strip().lower().lstrip("@")
        if domain:
            out.add(domain)
    return out


def _filter_provider_domains(entry: dict, denied: set[str]) -> dict:
    if not denied:
        return entry
    next_entry = dict(entry)
    for key in ("domain", "domains", "cf_domain"):
        raw = next_entry.get(key)
        if isinstance(raw, list):
            kept = []
            for item in raw:
                text = str(item or "").strip()
                if not text:
                    continue
                base = text[2:] if text.startswith("*.") else text
                base = base.lower().lstrip("@")
                if base in denied or text.lower().lstrip("@") in denied:
                    continue
                kept.append(item)
            next_entry[key] = kept
        elif isinstance(raw, str) and raw.strip():
            text = raw.strip()
            base = text[2:] if text.startswith("*.") else text
            base = base.lower().lstrip("@")
            if base in denied or text.lower().lstrip("@") in denied:
                next_entry[key] = []
    return next_entry


def _is_denied_mailbox(mailbox: dict | None, denied: set[str]) -> str:
    if not denied or not isinstance(mailbox, dict):
        return ""
    address = str(mailbox.get("address") or mailbox.get("email") or "").strip()
    domain = _email_domain(address)
    if domain and domain in denied:
        return domain
    resolved = _email_domain(str(mailbox.get("resolved_email") or ""))
    if resolved and resolved in denied:
        return resolved
    return ""



def _next_entry(mail_config: dict) -> dict:
    global provider_index
    items = _enabled_entries(mail_config)
    if len(items) == 1:
        return dict(items[0])
    with provider_lock:
        value = dict(items[provider_index % len(items)])
        provider_index = (provider_index + 1) % len(items)
        return value


def _instantiate_provider(entry: dict[str, Any], conf: dict[str, Any]) -> BaseMailProvider:
    provider_type = str(entry.get("type") or "").strip()
    if provider_type == "dropmail":
        from services.register.dropmail_provider import DropMailProvider

        return DropMailProvider(entry, conf)
    provider_classes: dict[str, type[BaseMailProvider]] = {
        "cloudmail_gen": CloudMailGenProvider,
        "cloudflare_temp_email": CloudflareTempMailProvider,
        "ddg_mail": DDGMailProvider,
        "mailfree": MailfreeProvider,
        "tempmail_lol": TempMailLolProvider,
        "duckmail": DuckMailProvider,
        "gptmail": GptMailProvider,
        "moemail": MoEmailProvider,
        "inbucket": InbucketMailProvider,
        "yyds_mail": YydsMailProvider,
        "outlook_token": OutlookTokenProvider,
        "outlook_external": OutlookExternalApiProvider,
        "outlook_alias": OutlookExternalApiProvider,
        "outlook_api": OutlookExternalApiProvider,
    }
    provider_class = provider_classes.get(provider_type)
    if provider_class is None:
        raise RuntimeError(f"不支持的 mail.provider: {provider_type}")
    return provider_class(entry, conf)


def _create_provider(mail_config: dict, provider: str = "", provider_ref: str = "") -> BaseMailProvider:
    entry = next((dict(item) for item in _entries(mail_config) if provider_ref and item["provider_ref"] == provider_ref), None)
    entry = entry or next((dict(item) for item in _enabled_entries(mail_config) if provider and item["type"] == provider), None) or _next_entry(mail_config)
    entry["_denied_domains"] = sorted(_denied_domains(mail_config))
    return _instantiate_provider(entry, _config(mail_config))


def create_mailbox(mail_config: dict, username: str | None = None) -> dict:
    """Create a mailbox, rotating across enabled providers with failover.

    Strategy:
    1) Round-robin chooses the starting provider (fairness across jobs).
    2) From that start, walk every enabled provider exactly once in config order.
    3) Any create failure (pool exhausted / 403 / timeout) falls through to next.

    PATCH_MARKER denied_domains_filter_r32:
    - honor mail.denied_domains / blocked_domains
    - filter provider domain lists before create
    - if created address domain is denied, failover to next provider
    """
    enabled = _enabled_entries(mail_config)
    denied = _denied_domains(mail_config)
    global provider_index
    with provider_lock:
        start_idx = provider_index % len(enabled)
        provider_index = (provider_index + 1) % len(enabled)
    order = enabled[start_idx:] + enabled[:start_idx]

    tried: set[str] = set()
    errors: list[str] = []
    for position, entry in enumerate(order):
        conf = _config(mail_config)
        provider_type = str(entry.get("type") or "")
        entry = _filter_provider_domains(dict(entry), denied)
        entry["_denied_domains"] = sorted(denied)
        # A provider may half-open only when another provider remains after it
        # in this attempt's traversal order. A preceding provider has already
        # failed and is not a usable fallback for the current attempt.
        entry["_has_provider_fallback"] = position < len(order) - 1
        try:
            provider = _instantiate_provider(entry, conf)
        except Exception as error:  # noqa: BLE001
            errors.append(f"{provider_type}: init {error}"[:220])
            continue

        provider_key = f"{provider.name}#{getattr(provider, 'provider_ref', '') or entry.get('provider_ref') or ''}"
        if provider_key in tried:
            try:
                provider.close()
            except Exception:
                pass
            continue
        tried.add(provider_key)
        try:
            mailbox = provider.create_mailbox(username)
            if isinstance(mailbox, dict):
                denied_hit = _is_denied_mailbox(mailbox, denied)
                if denied_hit:
                    errors.append(f"{provider_key}: denied_domain={denied_hit}")
                    continue
                if errors:
                    mailbox["provider_failover_from"] = list(errors)
                    mailbox["provider_failover"] = True
                mailbox.setdefault("provider", getattr(provider, "name", "") or mailbox.get("provider") or provider_type)
                mailbox.setdefault(
                    "provider_ref",
                    getattr(provider, "provider_ref", "") or mailbox.get("provider_ref") or str(entry.get("provider_ref") or ""),
                )
                mailbox.setdefault("label", str(entry.get("label") or mailbox.get("provider") or provider_type))
                if denied:
                    mailbox["denied_domains_checked"] = sorted(denied)
            return mailbox
        except Exception as error:  # noqa: BLE001
            last_error = str(error)
            errors.append(f"{provider_key}: {last_error[:220]}")
            continue
        finally:
            try:
                provider.close()
            except Exception:
                pass
    detail = " | ".join(errors) if errors else "no provider attempted"
    raise RuntimeError(f"所有启用的邮箱提供商均无法创建邮箱: {detail}")

def wait_for_code(mail_config: dict, mailbox: dict) -> str | None:
    provider = _create_provider(mail_config, str(mailbox.get("provider") or ""), str(mailbox.get("provider_ref") or ""))
    try:
        return provider.wait_for_code(mailbox)
    finally:
        provider.close()


def mark_mailbox_result(mailbox: dict, *, success: bool, error: Exception | str | None = None) -> None:
    """注册流程结束后更新邮箱池状态。

    outlook_external：OpenAI 按主邮箱去重，成功/已存在都标记 main used。
    outlook_token：成功标记 used；token 失效标记 token_invalid，其余 failed。
    """
    provider = str(mailbox.get("provider") or "")
    if provider == MailfreeProvider.name:
        _record_mailfree_domain_result(mailbox, success=success, error=error)
        return
    if provider in {TempMailLolProvider.name, "dropmail"} and mailbox.get("random_domain"):
        random_mail_domain_health.record_random_mailbox_result(
            mailbox, success=success, error=error
        )
        return
    if provider in {OutlookExternalApiProvider.name, "outlook_external", "outlook_alias", "outlook_api"}:
        main = str(mailbox.get("resolved_email") or mailbox.get("address") or "").strip()
        reason = str(error or "").lower()
        if (
            success
            or "user_already_exists" in reason
            or "already exists" in reason
            or "existing_account" in reason
            or "invalid_auth_step" in reason
        ):
            mark_outlook_external_main_used(main, reason=str(error or ("success" if success else ""))[:200])
        return
    if provider != OutlookTokenProvider.name:
        return
    state_key = _outlook_mailbox_state_key(mailbox)
    if not state_key:
        return
    if success:
        _set_outlook_token_state(state_key, "used")
        return
    reason = str(error or "").strip()
    low_reason = reason.lower()
    if any(marker in low_reason for marker in ("user_already_exists", "already exists", "existing_account")):
        _set_outlook_token_state(state_key, "already_registered", reason[:300])
        return
    if isinstance(error, OutlookTokenError) or any(
        marker in low_reason
        for marker in (
            "outlooktoken 刷新失败",
            "outlooktoken refresh failed",
            "invalid_grant",
            "aadsts",
            "missing access_token",
            "缺少 access_token",
            "graph 失败: http 401",
            "graph 失败: http 403",
            "imap authenticate",
        )
    ):
        _set_outlook_token_state(state_key, "token_invalid", reason[:300])
        return
    if any(
        marker in low_reason
        for marker in (
            "unsupported_email",
            "invalid email",
            "email_invalid",
            "email_not_allowed",
            "邮箱无效",
            "邮箱不受支持",
        )
    ):
        _set_outlook_token_state(state_key, "invalid_email", reason[:300])
        return
    # Network, upstream and OTP timing failures do not prove the parent mailbox is bad.
    _release_outlook_token_state(state_key)


def release_mailbox(mailbox: dict) -> None:
    """把 outlook_token 邮箱从 in_use 释放回未使用（用于流程主动放弃且未消费验证码时）。"""
    if str(mailbox.get("provider") or "") != OutlookTokenProvider.name:
        return
    _release_outlook_token_state(_outlook_mailbox_state_key(mailbox))


def get_existing_mailbox(mail_config: dict, email: str) -> dict:
    """通过管理员密码获取已有邮箱地址的 JWT，用于查询邮件。"""
    enabled = _enabled_entries(mail_config)
    tried: set[str] = set()
    last_error = ""
    for _ in range(len(enabled)):
        provider = _create_provider(mail_config)
        provider_key = f"{provider.name}#{provider.provider_ref}"
        try:
            if provider_key in tried:
                continue
            tried.add(provider_key)
            if hasattr(provider, "get_existing_mailbox"):
                mailbox = provider.get_existing_mailbox(email)
                return mailbox
            else:
                raise RuntimeError(f"邮箱提供商 {provider.name} 不支持查询已有邮箱")
        except RuntimeError as error:
            last_error = str(error)
            if "DDG日上限已达" not in last_error:
                raise
        finally:
            provider.close()
    raise RuntimeError(last_error or "所有启用的邮箱提供商均无法查询已有邮箱")
