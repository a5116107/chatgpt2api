from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from services.config import DATA_DIR


CONVERSATIONS_FILE = DATA_DIR / "conversations.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: object) -> str:
    return str(value or "").strip()


def _owner_id(identity: dict[str, object] | None) -> str:
    if not isinstance(identity, dict):
        return "anonymous"
    return _clean(identity.get("id")) or _clean(identity.get("key_id")) or _clean(identity.get("name")) or "anonymous"


def _title_from_text(text: str, fallback: str = "新会话") -> str:
    value = " ".join(_clean(text).split())
    if not value:
        return fallback
    return value[:40] + ("…" if len(value) > 40 else "")


def _extract_chat_assistant_text(response: object) -> str:
    if not isinstance(response, dict):
        return ""
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    if isinstance(message.get("content"), str):
        return message["content"]
    return ""


def _extract_response_text(response: object) -> str:
    if not isinstance(response, dict):
        return ""
    output = response.get("output")
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
    return "".join(parts)


def _message_text(message: object) -> str:
    if isinstance(message, str):
        return message
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("input_text") or item.get("output_text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _safe_messages_from_chat_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_messages = payload.get("messages")
    messages: list[dict[str, Any]] = []
    if isinstance(raw_messages, list):
        for raw in raw_messages:
            if not isinstance(raw, dict):
                continue
            role = _clean(raw.get("role")) or "user"
            text = _message_text(raw)
            if text:
                messages.append({"id": f"msg_{uuid.uuid4().hex}", "role": role, "content": text, "created_at": _now()})
    prompt = _clean(payload.get("prompt"))
    if prompt:
        messages.append({"id": f"msg_{uuid.uuid4().hex}", "role": "user", "content": prompt, "created_at": _now()})
    return messages


def _safe_messages_from_response_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("input")
    if isinstance(value, str):
        return [{"id": f"msg_{uuid.uuid4().hex}", "role": "user", "content": value, "created_at": _now()}] if value.strip() else []
    if isinstance(value, dict):
        role = _clean(value.get("role")) or "user"
        text = _message_text(value)
        return [{"id": f"msg_{uuid.uuid4().hex}", "role": role, "content": text, "created_at": _now()}] if text else []
    messages: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                messages.append({"id": f"msg_{uuid.uuid4().hex}", "role": "user", "content": item.strip(), "created_at": _now()})
            elif isinstance(item, dict):
                role = _clean(item.get("role")) or "user"
                text = _message_text(item)
                if text:
                    messages.append({"id": f"msg_{uuid.uuid4().hex}", "role": role, "content": text, "created_at": _now()})
    return messages



def _parse_iso_ts(value: object) -> float | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _derive_chat_task_status(
    conversation: dict[str, Any],
    *,
    now_ts: float | None = None,
    abandoned_idle_secs: int = 3600,
    draft_idle_secs: int = 600,
) -> str:
    # PATCH_MARKER chat_task_terminal_r27
    # PATCH_MARKER conversation_status_repair_r28
    # PATCH_MARKER conversation_draft_idle_r30
    status = _clean(conversation.get("status")).lower()
    if status in {"succeeded", "success", "failed", "error", "cancelled", "stale", "idle"}:
        return "succeeded" if status in {"success", "succeeded"} else ("failed" if status == "error" else status)
    messages = conversation.get("messages") if isinstance(conversation.get("messages"), list) else []
    has_assistant = any(
        isinstance(m, dict) and _clean(m.get("role")) == "assistant" and _message_text(m)
        for m in messages
    )
    has_failed = any(
        isinstance(m, dict) and str((m.get("metadata") or {}).get("status") or "").lower() == "failed"
        for m in messages
    )
    if has_failed:
        return "failed"
    if has_assistant:
        return "succeeded"
    # abandoned draft / smoke conversations should not stay active forever
    ts = _parse_iso_ts(conversation.get("updated_at")) or _parse_iso_ts(conversation.get("created_at"))
    current = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
    source = _clean(conversation.get("source")).lower()
    # pure drafts (no assistant): shorter idle for smoke/manual; general abandoned uses abandoned_idle_secs
    idle_limit = int(draft_idle_secs if source in {"smoke", "manual", "r28-manual", "r29-manual", "r30-manual"} or source.startswith("r") and "manual" in source else abandoned_idle_secs)
    # any conversation without assistant is a draft; prefer draft_idle_secs when only user messages exist
    user_only = bool(messages) and all(not (isinstance(m, dict) and _clean(m.get("role")) == "assistant") for m in messages)
    if user_only:
        idle_limit = min(idle_limit, int(draft_idle_secs))
    if ts is not None and (current - ts) >= max(60, idle_limit):
        return "idle"
    if status in {"running", "active", "queued"}:
        return status or "active"
    return status or "active"


def _sync_unified_chat_task(conversation: dict[str, Any], *, status: str | None = None, error: object = "") -> None:
    try:
        from services.risk_control_service import risk_control_service

        messages = conversation.get("messages") if isinstance(conversation.get("messages"), list) else []
        risk_control_service.upsert_task(
            str(conversation.get("id") or ""),
            type_="chat",
            status=status or _derive_chat_task_status(conversation),
            progress=f"{len(messages)} messages",
            source="conversation_store",
            owner_id=str(conversation.get("owner_id") or ""),
            error=error,
            model=conversation.get("model"),
            title=conversation.get("title"),
        )
    except Exception:
        pass


class ConversationStore:
    def __init__(self, path: Path = CONVERSATIONS_FILE) -> None:
        self.path = path
        self._lock = RLock()
        self._items = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        items = raw.get("items") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return {}
        output: dict[str, dict[str, Any]] = {}
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                normalized = self._normalize(item)
                output[str(normalized["id"])] = normalized
        return output

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "updated_at": _now(),
            "items": sorted(self._items.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True),
        }
        tmp = self.path.with_suffix(self.path.suffix + f".tmp.{uuid.uuid4().hex}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _normalize(item: dict[str, Any]) -> dict[str, Any]:
        messages = item.get("messages") if isinstance(item.get("messages"), list) else []
        normalized_messages = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = _message_text(message)
            normalized_messages.append({
                "id": _clean(message.get("id")) or f"msg_{uuid.uuid4().hex}",
                "role": _clean(message.get("role")) or "user",
                "content": content,
                "created_at": message.get("created_at") or _now(),
                "metadata": deepcopy(message.get("metadata") if isinstance(message.get("metadata"), dict) else {}),
            })
        created_at = item.get("created_at") or _now()
        updated_at = item.get("updated_at") or created_at
        return {
            "id": _clean(item.get("id")) or f"conv_{uuid.uuid4().hex}",
            "owner_id": _clean(item.get("owner_id")) or "anonymous",
            "title": _clean(item.get("title")) or "新会话",
            "model": _clean(item.get("model")) or "auto",
            "source": _clean(item.get("source")) or "api",
            "status": _clean(item.get("status")) or "active",
            "created_at": created_at,
            "updated_at": updated_at,
            "metadata": deepcopy(item.get("metadata") if isinstance(item.get("metadata"), dict) else {}),
            "messages": normalized_messages,
        }

    def create(self, identity: dict[str, object], *, title: str = "", model: str = "auto", source: str = "api", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        now = _now()
        item = {
            "id": f"conv_{uuid.uuid4().hex}",
            "owner_id": _owner_id(identity),
            "title": _clean(title) or "新会话",
            "model": _clean(model) or "auto",
            "source": _clean(source) or "api",
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "metadata": dict(metadata or {}),
            "messages": [],
        }
        with self._lock:
            self._items[item["id"]] = item
            self._save_locked()
        _sync_unified_chat_task(item)
        return deepcopy(item)

    def list(self, identity: dict[str, object], *, limit: int = 100, include_messages: bool = False) -> list[dict[str, Any]]:
        owner = _owner_id(identity)
        with self._lock:
            items = [deepcopy(item) for item in self._items.values() if item.get("owner_id") == owner or identity.get("role") == "admin"]
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        output = items[: max(1, min(int(limit or 100), 500))]
        if not include_messages:
            for item in output:
                item["message_count"] = len(item.get("messages") if isinstance(item.get("messages"), list) else [])
                item.pop("messages", None)
        return output

    def get(self, identity: dict[str, object], conversation_id: str) -> dict[str, Any] | None:
        owner = _owner_id(identity)
        with self._lock:
            item = self._items.get(_clean(conversation_id))
            if not item:
                return None
            if item.get("owner_id") != owner and identity.get("role") != "admin":
                return None
            return deepcopy(item)

    def update(self, identity: dict[str, object], conversation_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        owner = _owner_id(identity)
        with self._lock:
            item = self._items.get(_clean(conversation_id))
            if not item or (item.get("owner_id") != owner and identity.get("role") != "admin"):
                return None
            for key in ("title", "model", "status", "source"):
                if key in updates and updates[key] is not None:
                    item[key] = _clean(updates[key]) or item.get(key)
            metadata = updates.get("metadata")
            if isinstance(metadata, dict):
                item["metadata"] = {**(item.get("metadata") if isinstance(item.get("metadata"), dict) else {}), **metadata}
            item["updated_at"] = _now()
            self._save_locked()
            _sync_unified_chat_task(item)
            return deepcopy(item)

    def delete(self, identity: dict[str, object], conversation_id: str) -> bool:
        owner = _owner_id(identity)
        with self._lock:
            item = self._items.get(_clean(conversation_id))
            if not item or (item.get("owner_id") != owner and identity.get("role") != "admin"):
                return False
            self._items.pop(_clean(conversation_id), None)
            self._save_locked()
            return True

    def append_messages(self, identity: dict[str, object], conversation_id: str, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        owner = _owner_id(identity)
        with self._lock:
            item = self._items.get(_clean(conversation_id))
            if not item or (item.get("owner_id") != owner and identity.get("role") != "admin"):
                return None
            current = item.setdefault("messages", [])
            for message in messages:
                if isinstance(message, dict):
                    current.append(self._normalize({"id": "tmp", "messages": [message]})["messages"][0])
            if current and item.get("title") == "新会话":
                item["title"] = _title_from_text(str(current[0].get("content") or ""), "新会话")
            item["updated_at"] = _now()
            self._save_locked()
            _sync_unified_chat_task(item)
            return deepcopy(item)

    def ensure_from_chat_payload(self, identity: dict[str, object], payload: dict[str, Any], request_preview: str = "") -> tuple[str, list[dict[str, Any]]]:
        conversation_id = _clean(payload.get("conversation_id")) or _clean(payload.get("metadata", {}).get("conversation_id") if isinstance(payload.get("metadata"), dict) else "")
        model = _clean(payload.get("model")) or "auto"
        new_messages = _safe_messages_from_chat_payload(payload)
        with self._lock:
            if conversation_id and conversation_id in self._items:
                item = self._items[conversation_id]
            else:
                created = self.create(identity, title=_title_from_text(request_preview), model=model, source="chat")
                conversation_id = str(created["id"])
                item = self._items[conversation_id]
            item["messages"].extend(new_messages)
            item["model"] = model
            item["status"] = "running"
            item["updated_at"] = _now()
            self._save_locked()
            _sync_unified_chat_task(item, status="running")
        return conversation_id, new_messages

    def ensure_from_response_payload(self, identity: dict[str, object], payload: dict[str, Any], request_preview: str = "") -> tuple[str, list[dict[str, Any]]]:
        conversation_id = _clean(payload.get("conversation_id")) or _clean(payload.get("metadata", {}).get("conversation_id") if isinstance(payload.get("metadata"), dict) else "")
        model = _clean(payload.get("model")) or "auto"
        new_messages = _safe_messages_from_response_payload(payload)
        with self._lock:
            if conversation_id and conversation_id in self._items:
                item = self._items[conversation_id]
            else:
                created = self.create(identity, title=_title_from_text(request_preview), model=model, source="responses")
                conversation_id = str(created["id"])
                item = self._items[conversation_id]
            item["messages"].extend(new_messages)
            item["model"] = model
            item["status"] = "running"
            item["updated_at"] = _now()
            self._save_locked()
            _sync_unified_chat_task(item, status="running")
        return conversation_id, new_messages

    def record_chat_result(self, identity: dict[str, object], conversation_id: str, result: object, *, error: str = "") -> None:
        # PATCH_MARKER chat_task_terminal_r27
        if error:
            self.append_messages(identity, conversation_id, [{
                "role": "system",
                "content": f"调用失败：{error}",
                "metadata": {"status": "failed"},
            }])
            self.update(identity, conversation_id, {"status": "failed"})
            return
        text = _extract_chat_assistant_text(result)
        if text:
            self.append_messages(identity, conversation_id, [{
                "role": "assistant",
                "content": text,
                "metadata": {"status": "success"},
            }])
            self.update(identity, conversation_id, {"status": "succeeded"})
        else:
            self.update(identity, conversation_id, {"status": "succeeded"})

    def record_response_result(self, identity: dict[str, object], conversation_id: str, result: object, *, error: str = "") -> None:
        # PATCH_MARKER chat_task_terminal_r27
        if error:
            self.append_messages(identity, conversation_id, [{
                "role": "system",
                "content": f"调用失败：{error}",
                "metadata": {"status": "failed"},
            }])
            self.update(identity, conversation_id, {"status": "failed"})
            return
        text = _extract_response_text(result)
        if text:
            self.append_messages(identity, conversation_id, [{
                "role": "assistant",
                "content": text,
                "metadata": {"status": "success"},
            }])
            self.update(identity, conversation_id, {"status": "succeeded"})
        else:
            self.update(identity, conversation_id, {"status": "succeeded"})

    def record_stream_chunk(self, identity: dict[str, object], conversation_id: str, protocol: str, item: object) -> str:
        if protocol == "responses" and isinstance(item, dict):
            if item.get("type") == "response.output_text.delta":
                return str(item.get("delta") or "")
        if protocol == "chat" and isinstance(item, dict):
            choices = item.get("choices")
            first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
            return str(delta.get("content") or "")
        return ""

    def finalize_stream(self, identity: dict[str, object], conversation_id: str, text: str, *, error: str = "") -> None:
        # PATCH_MARKER chat_task_terminal_r27
        if error:
            self.record_chat_result(identity, conversation_id, {}, error=error)
        elif text:
            self.append_messages(identity, conversation_id, [{
                "role": "assistant",
                "content": text,
                "metadata": {"status": "success", "stream": True},
            }])
            self.update(identity, conversation_id, {"status": "succeeded"})
        else:
            self.update(identity, conversation_id, {"status": "succeeded"})

    def export_markdown(self, identity: dict[str, object], conversation_id: str) -> str | None:
        item = self.get(identity, conversation_id)
        if not item:
            return None
        lines = [f"# {item.get('title') or conversation_id}", ""]
        for message in item.get("messages") or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user")
            content = str(message.get("content") or "")
            lines.extend([f"## {role}", "", content, ""])
        return "\n".join(lines).rstrip() + "\n"


    def repair_statuses(self, *, abandoned_idle_secs: int = 3600, draft_idle_secs: int = 600) -> dict[str, Any]:
        """Backfill conversation/task status from message evidence.
        PATCH_MARKER conversation_status_repair_r28
        PATCH_MARKER conversation_draft_idle_r30
        """
        changed = 0
        by_status: dict[str, int] = {}
        now_ts = datetime.now(timezone.utc).timestamp()
        with self._lock:
            for item in self._items.values():
                derived = _derive_chat_task_status(
                    item,
                    now_ts=now_ts,
                    abandoned_idle_secs=int(abandoned_idle_secs),
                    draft_idle_secs=int(draft_idle_secs),
                )
                old = _clean(item.get("status")) or "active"
                if old != derived:
                    item["status"] = derived
                    changed += 1
                    _sync_unified_chat_task(item, status=derived)
                by_status[derived] = by_status.get(derived, 0) + 1
            if changed:
                self._save_locked()
        return {
            "repaired": changed,
            "total": len(self._items),
            "by_status": by_status,
            "abandoned_idle_secs": int(abandoned_idle_secs),
            "draft_idle_secs": int(draft_idle_secs),
        }

    def task_items(self, limit: int = 5000) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._items.values())
        output = []
        for item in items[-max(1, limit):]:
            output.append({
                "id": str(item.get("id")),
                "type": "chat",
                "status": _derive_chat_task_status(item),
                "progress": f"{len(item.get('messages') if isinstance(item.get('messages'), list) else [])} messages",
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "duration_ms": 0,
                "error": None,
                "source": "conversations_json",
            })
        return output


conversation_store = ConversationStore()
