from __future__ import annotations

import json
import base64
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request

from services.config import DATA_DIR, config
from services.log_service import LOG_TYPE_CALL, log_service


VIDEO_TASKS_FILE = DATA_DIR / "video_tasks.json"
VIDEO_DIR = DATA_DIR / "videos"
TERMINAL = {"success", "failed", "error", "cancelled"}
LOCAL_SAMPLE_MP4_B64 = (
    "AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAAGbW9vdgAAAGxtdmhkAAAAANrZ"
    "8rfa2fK3AAAD6AAAA+gAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAA"
    "AAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAnh0cmFrAAAAXHRr"
    "aGQAAAAD2tnyt9rZ8rcAAAABAAAAAAAAA+gAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAA"
    "AAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAQAAAAEAAAAAACRlZHRzAAAAHGVsc3QAAAAAAAAA"
    "AQAAA+gAAAAAAAEAAAAAAAGdbWRpYQAAACBtZGhkAAAAANrZ8rfa2fK3AAAAGQAAABkAVcQA"
    "AAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAABQG1pbmYA"
    "AAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAA"
    "AQAAAPxzdGJsAAAAsHN0c2QAAAAAAAAAAQAAAKBhdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAABAAEASAAAAEgAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAGP//AAAANmF2Y0MBAWQACv/hABlnZAAKreKQDwBE/LgIgAAADACAAAAMAUeJEVQ/"
    "AQAGaO48gA=="
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: object) -> str:
    return str(value or "").strip()


def _owner_id(identity: dict[str, object]) -> str:
    return _clean(identity.get("id")) or _clean(identity.get("name")) or "anonymous"


def _task_key(owner_id: str, task_id: str) -> str:
    return f"{owner_id}:{task_id}"


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in task.items() if key not in {"owner_id", "key"}}


def _sync_unified_task(task_id: str, *, status: str, owner_id: str = "", progress: object = None, error: object = "", duration_ms: int | None = None, **updates: Any) -> None:
    try:
        from services.risk_control_service import risk_control_service

        risk_control_service.upsert_task(
            task_id,
            type_="video",
            status=status,
            progress=progress,
            source="video_task_service",
            owner_id=owner_id,
            error=error,
            duration_ms=duration_ms,
            **updates,
        )
    except Exception:
        pass


def _write_local_sample_video(task: dict[str, Any]) -> dict[str, Any]:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    task_id = _clean(task.get("id"))
    path = VIDEO_DIR / f"{task_id}.mp4"
    if not path.exists():
        path.write_bytes(base64.b64decode(LOCAL_SAMPLE_MP4_B64))
    base_url = _clean(task.get("base_url")).rstrip("/")
    url = f"{base_url}/v1/videos/{task_id}/content" if base_url else f"/v1/videos/{task_id}/content"
    return {"data": [{"url": url, "mime_type": "video/mp4", "provider": "local", "bytes": path.stat().st_size}]}


class VideoTaskService:
    def __init__(self, path: Path = VIDEO_TASKS_FILE) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._tasks = self._load_locked()

    def _load_locked(self) -> dict[str, dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        items = raw.get("tasks") if isinstance(raw, dict) else raw
        if isinstance(items, dict):
            return {str(key): dict(value) for key, value in items.items() if isinstance(value, dict)}
        if isinstance(items, list):
            return {_task_key(_clean(item.get("owner_id")) or "anonymous", str(item.get("id"))): dict(item) for item in items if isinstance(item, dict) and item.get("id")}
        return {}

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "updated_at": _now(), "tasks": self._tasks}
        tmp = self.path.with_suffix(self.path.suffix + f".tmp.{uuid.uuid4().hex}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def _update_task(self, key: str, **updates: Any) -> None:
        with self._lock:
            task = self._tasks.get(key)
            if not task:
                return
            task.update(updates)
            task["updated_at"] = _now()
            self._save_locked()

    def submit_generation(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str = "",
        prompt: str,
        model: str = "sora",
        size: str = "",
        seconds: int | None = None,
        base_url: str = "",
    ) -> dict[str, Any]:
        prompt = _clean(prompt)
        if not prompt:
            raise ValueError("prompt is required")
        owner = _owner_id(identity)
        task_id = _clean(client_task_id) or f"video_{uuid.uuid4().hex}"
        key = _task_key(owner, task_id)
        now = _now()
        task = {
            "key": key,
            "id": task_id,
            "owner_id": owner,
            "status": "queued",
            "mode": "generate",
            "model": _clean(model) or "sora",
            "prompt": prompt,
            "size": _clean(size),
            "seconds": int(seconds or 0),
            "created_at": now,
            "updated_at": now,
            "progress": "queued",
            "data": [],
            "error": "",
            "base_url": _clean(base_url),
        }
        with self._lock:
            if key in self._tasks and self._tasks[key].get("status") not in TERMINAL:
                return _public_task(self._tasks[key])
            self._tasks[key] = task
            self._save_locked()
            _sync_unified_task(task_id, status="queued", owner_id=owner, progress="queued", model=task["model"], total=1)
        thread = threading.Thread(target=self._run_task, args=(key, dict(identity)), name=f"video-task-{task_id[:16]}", daemon=True)
        thread.start()
        return _public_task(task)

    def _run_task(self, key: str, identity: dict[str, object]) -> None:
        started = time.time()
        self._update_task(key, status="running", progress="checking_video_backend")
        task = self._tasks.get(key, {})
        task_id = _clean(task.get("id")) or key.rsplit(":", 1)[-1]
        owner_id = _clean(task.get("owner_id"))
        _sync_unified_task(task_id, status="running", owner_id=owner_id, progress="checking_video_backend", model=task.get("model"))
        try:
            # PATCH_MARKER video_provider_router_r29
            video_settings = config.get_video_settings() if hasattr(config, "get_video_settings") else {}
            if not isinstance(video_settings, dict):
                video_settings = {}
            enabled = bool(video_settings.get("enabled"))
            provider = _clean(video_settings.get("provider")).lower() or "local"
            fallback = _clean(video_settings.get("fallback_provider")).lower() or "local"
            if not enabled:
                raise RuntimeError("video backend not configured: set config.video.enabled=true and config.video.provider")
            used_provider, result = self._generate_with_provider(provider, task, video_settings)
            if result is None and fallback and fallback != provider:
                self._update_task(key, progress=f"{provider}:fallback->{fallback}")
                _sync_unified_task(task_id, status="running", owner_id=owner_id, progress=f"{provider}:fallback->{fallback}", model=task.get("model"))
                used_provider, result = self._generate_with_provider(fallback, task, video_settings)
            if result is None:
                raise RuntimeError(f"video provider '{provider}' adapter not implemented")
            duration_ms = int((time.time() - started) * 1000)
            self._update_task(
                key,
                status="success",
                progress="succeeded",
                data=result.get("data") or [],
                error="",
                duration_ms=duration_ms,
                provider=used_provider,
            )
            _sync_unified_task(task_id, status="success", owner_id=owner_id, progress="succeeded", duration_ms=duration_ms, model=task.get("model"), success=1, total=1, provider=used_provider)
            log_service.add(
                LOG_TYPE_CALL,
                "视频生成任务完成",
                {
                    "key_id": identity.get("id"),
                    "endpoint": "/v1/videos/generations",
                    "model": task.get("model"),
                    "status": "success",
                    "duration_ms": duration_ms,
                    "request_text": task.get("prompt"),
                    "provider": used_provider,
                },
            )
        except Exception as exc:
            error = str(exc) or "video task failed"
            duration_ms = int((time.time() - started) * 1000)
            error_code = "video_provider_not_configured" if "not configured" in error else "video_provider_not_implemented" if "not implemented" in error else "video_task_failed"
            self._update_task(key, status="failed", progress="failed", error=error, error_code=error_code, duration_ms=duration_ms)
            _sync_unified_task(task_id, status="failed", owner_id=owner_id, progress="failed", error=error, duration_ms=duration_ms, model=task.get("model"), fail=1, total=1)
            log_service.add(
                LOG_TYPE_CALL,
                "视频生成任务失败",
                {
                    "key_id": identity.get("id"),
                    "endpoint": "/v1/videos/generations",
                    "model": task.get("model"),
                    "status": "failed",
                    "duration_ms": duration_ms,
                    "request_text": task.get("prompt"),
                    "error": error,
                },
            )


    def _generate_with_provider(self, provider: str, task: dict[str, Any], video_settings: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
        # PATCH_MARKER video_provider_router_r29
        provider = _clean(provider).lower() or "local"
        task_key = _clean(task.get("key"))
        if task_key:
            self._update_task(task_key, progress=f"{provider}:generating")
        if provider in {"local", "mock"}:
            return provider, _write_local_sample_video(task)
        if provider in {"openai_compatible", "openai", "sora_compatible"}:
            return provider, self._generate_openai_compatible(task, video_settings)
        return provider, None

    def _generate_openai_compatible(self, task: dict[str, Any], video_settings: dict[str, Any]) -> dict[str, Any] | None:
        # PATCH_MARKER video_openai_compatible_r29
        base_url = _clean(video_settings.get("base_url")).rstrip("/")
        api_key = _clean(video_settings.get("api_key"))
        if not base_url:
            # No upstream configured: soft-miss so fallback_provider can take over.
            return None
        payload = {
            "prompt": task.get("prompt") or "",
            "model": task.get("model") or "sora",
        }
        if task.get("size"):
            payload["size"] = task.get("size")
        if task.get("seconds"):
            payload["seconds"] = task.get("seconds")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"{base_url}/v1/videos/generations", data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=int(video_settings.get("poll_timeout_secs") or 60)) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"openai_compatible create failed: HTTP {exc.code} {detail[:300]}") from exc
        except Exception as exc:
            raise RuntimeError(f"openai_compatible create failed: {exc}") from exc
        try:
            created = json.loads(raw) if raw else {}
        except Exception as exc:
            raise RuntimeError(f"openai_compatible invalid create response: {raw[:200]}") from exc
        remote_id = _clean(created.get("id") or ((created.get("data") or [{}])[0].get("id") if isinstance(created.get("data"), list) else ""))
        if not remote_id:
            # Some gateways return content immediately.
            if isinstance(created.get("data"), list) and created.get("data"):
                return {"data": created.get("data"), "provider": "openai_compatible"}
            raise RuntimeError("openai_compatible create missing task id")
        poll_interval = max(1, int(video_settings.get("poll_interval_secs") or 2))
        deadline = time.time() + max(5, int(video_settings.get("poll_timeout_secs") or 60))
        last = created
        while time.time() < deadline:
            get_req = urllib.request.Request(f"{base_url}/v1/videos/{remote_id}", headers=headers, method="GET")
            try:
                with urllib.request.urlopen(get_req, timeout=30) as resp:
                    last = json.loads(resp.read().decode("utf-8", "replace") or "{}")
            except Exception as exc:
                raise RuntimeError(f"openai_compatible poll failed: {exc}") from exc
            status = _clean(last.get("status")).lower()
            if status in {"success", "completed", "succeeded"}:
                data = last.get("data")
                if not isinstance(data, list) or not data:
                    content_url = f"{base_url}/v1/videos/{remote_id}/content"
                    data = [{"url": content_url, "mime_type": "video/mp4", "provider": "openai_compatible", "remote_id": remote_id}]
                # Prefer local mirror if content is downloadable bytes URL under same host.
                try:
                    content_req = urllib.request.Request(f"{base_url}/v1/videos/{remote_id}/content", headers=headers, method="GET")
                    with urllib.request.urlopen(content_req, timeout=60) as resp:
                        blob = resp.read()
                    if blob:
                        VIDEO_DIR.mkdir(parents=True, exist_ok=True)
                        path = VIDEO_DIR / f"{_clean(task.get('id'))}.mp4"
                        path.write_bytes(blob)
                        local_base = _clean(task.get("base_url")).rstrip("/")
                        url = f"{local_base}/v1/videos/{_clean(task.get('id'))}/content" if local_base else f"/v1/videos/{_clean(task.get('id'))}/content"
                        data = [{"url": url, "mime_type": "video/mp4", "provider": "openai_compatible", "remote_id": remote_id, "bytes": path.stat().st_size}]
                except Exception:
                    pass
                return {"data": data, "provider": "openai_compatible", "remote_id": remote_id}
            if status in {"failed", "error", "cancelled"}:
                raise RuntimeError(f"openai_compatible remote status={status} error={last.get('error') or last.get('message') or ''}")
            time.sleep(poll_interval)
        raise RuntimeError(f"openai_compatible poll timeout remote_id={remote_id}")

    def list_tasks(self, identity: dict[str, object], task_ids: list[str] | None = None) -> dict[str, Any]:
        owner = _owner_id(identity)
        requested = {_clean(item) for item in (task_ids or []) if _clean(item)}
        with self._lock:
            items = []
            missing = set(requested)
            for task in self._tasks.values():
                if task.get("owner_id") != owner and identity.get("role") != "admin":
                    continue
                if requested and str(task.get("id")) not in requested:
                    continue
                missing.discard(str(task.get("id")))
                items.append(_public_task(task))
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return {"items": items, "missing_ids": sorted(missing)}

    def get_task(self, identity: dict[str, object], task_id: str) -> dict[str, Any] | None:
        result = self.list_tasks(identity, [_clean(task_id)])
        return result["items"][0] if result.get("items") else None

    def cancel(self, identity: dict[str, object], task_id: str) -> dict[str, Any] | None:
        owner = _owner_id(identity)
        key = _task_key(owner, task_id)
        with self._lock:
            task = self._tasks.get(key)
            if not task and identity.get("role") == "admin":
                task = next((item for item in self._tasks.values() if item.get("id") == task_id), None)
                key = str(task.get("key")) if task else key
            if not task:
                return None
            if task.get("status") not in TERMINAL:
                task["status"] = "cancelled"
                task["progress"] = "cancelled"
                task["updated_at"] = _now()
                self._save_locked()
                _sync_unified_task(str(task.get("id") or task_id), status="cancelled", owner_id=owner, progress="cancelled")
            return _public_task(task)

    def content_path(self, identity: dict[str, object], task_id: str) -> Path | None:
        task = self.get_task(identity, task_id)
        if not task or task.get("status") != "success":
            return None
        path = VIDEO_DIR / f"{task_id}.mp4"
        return path if path.exists() else None

    def task_items(self, limit: int = 5000) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._tasks.values())[-max(1, limit):]
        return [
            {
                "id": str(item.get("id")),
                "type": "video",
                "status": item.get("status"),
                "progress": item.get("progress"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "duration_ms": item.get("duration_ms"),
                "error": item.get("error"),
                "error_code": item.get("error_code"),
                "source": "video_tasks_json",
            }
            for item in items
        ]


video_task_service = VideoTaskService()
