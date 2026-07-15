from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from services.config import DATA_DIR, config
from services.content_filter import request_text
from services.log_service import LOG_TYPE_CALL, log_service
from services.protocol import openai_v1_image_edit, openai_v1_image_generations
from utils.log import logger

TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_ERROR = "error"
TERMINAL_STATUSES = {TASK_STATUS_SUCCESS, TASK_STATUS_ERROR}
UNFINISHED_STATUSES = {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}

# A task can legitimately sit in upstream polling for image_poll_timeout_secs,
# but it must keep heartbeat updates while doing so.  If no heartbeat happens
# for this long, the worker thread was interrupted or wedged and the UI should
# not keep spinning forever.
DEFAULT_STALE_HEARTBEAT_SECS = 180.0


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _timestamp(value: object) -> float:
    if not isinstance(value, str) or not value.strip():
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:26], fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _clean(value: object, default: str = "") -> str:
    return str(value or default).strip()


def _owner_id(identity: dict[str, object]) -> str:
    return _clean(identity.get("id")) or "anonymous"


def _task_key(owner_id: str, task_id: str) -> str:
    return f"{owner_id}:{task_id}"


def _collect_image_urls(data: list[Any]) -> list[str]:
    urls: list[str] = []
    for item in data:
        if isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str) and url:
                urls.append(url)
    return urls


def _stale_heartbeat_secs() -> float:
    try:
        timeout = float(config.image_poll_timeout_secs)
    except Exception:
        timeout = 120.0
    return max(DEFAULT_STALE_HEARTBEAT_SECS, timeout + 60.0)


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    item = {
        "id": task.get("id"),
        "status": task.get("status"),
        "mode": task.get("mode"),
        "model": task.get("model"),
        "size": task.get("size"),
        "quality": task.get("quality"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
    }
    if task.get("conversation_id"):
        item["conversation_id"] = task.get("conversation_id")
    if task.get("data") is not None:
        item["data"] = task.get("data")
    if task.get("usage") is not None:
        item["usage"] = task.get("usage")
    if task.get("error"):
        item["error"] = task.get("error")
    if task.get("progress"):
        item["progress"] = task.get("progress")
    if task.get("duration_ms") is not None:
        item["duration_ms"] = task.get("duration_ms")
    if task.get("stage_metrics"):
        item["stage_metrics"] = task.get("stage_metrics")
    if task.get("account_hash"):
        item["account_hash"] = task.get("account_hash")
    if task.get("status") in (TASK_STATUS_RUNNING, TASK_STATUS_QUEUED):
        if task.get("status") == TASK_STATUS_RUNNING:
            # RUNNING 状态仅在 started_ts 被设置后（image_stream_resolve_start）才计时
            base_ts = task.get("started_ts")
        else:
            # QUEUED 状态从 created_ts 开始计时（排队等待中）
            base_ts = task.get("created_ts") or task.get("updated_ts")
        if base_ts:
            item["elapsed_secs"] = round(time.time() - base_ts, 1)
    return item


def _sync_unified_task(task_id: str, *, status: str, mode: str = "generate", owner_id: str = "", progress: object = None, error: object = "", duration_ms: int | None = None, **updates: Any) -> None:
    try:
        from services.risk_control_service import risk_control_service

        risk_control_service.upsert_task(
            task_id,
            type_="image",
            status=status,
            progress=progress,
            source="image_task_service",
            owner_id=owner_id,
            error=error,
            duration_ms=duration_ms,
            mode=mode,
            **updates,
        )
    except Exception:
        pass


class ImageTaskService:
    def __init__(
        self,
        path: Path,
        *,
        generation_handler: Callable[[dict[str, Any]], dict[str, Any]] = openai_v1_image_generations.handle,
        edit_handler: Callable[[dict[str, Any]], dict[str, Any]] = openai_v1_image_edit.handle,
        retention_days_getter: Callable[[], int] | None = None,
        heartbeat_interval_getter: Callable[[], float] | None = None,
    ):
        self.path = path
        self.generation_handler = generation_handler
        self.edit_handler = edit_handler
        self.retention_days_getter = retention_days_getter or (lambda: config.image_retention_days)
        self.heartbeat_interval_getter = heartbeat_interval_getter or (lambda: config.image_heartbeat_interval_secs)
        self._lock = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._tasks = self._load_locked()
            changed = self._recover_unfinished_locked()
            changed = self._cleanup_locked() or changed
            if changed:
                self._save_locked()

    def submit_generation(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        prompt: str,
        model: str,
        size: str | None,
        quality: str = "auto",
        base_url: str = "",
    ) -> dict[str, Any]:
        payload = {
            "prompt": prompt,
            "model": model,
            "n": 1,
            "size": size,
            "quality": quality,
            "response_format": "url",
            "base_url": base_url,
        }
        return self._submit(identity, client_task_id=client_task_id, mode="generate", payload=payload)

    def submit_edit(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        prompt: str,
        model: str,
        size: str | None,
        quality: str = "auto",
        base_url: str = "",
        images: list[tuple[bytes, str, str]] | None = None,
        masks: list[tuple[bytes, str, str]] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "prompt": prompt,
            "images": images or [],
            "mask": masks or [],
            "model": model,
            "n": 1,
            "size": size,
            "quality": quality,
            "response_format": "url",
            "base_url": base_url,
        }
        return self._submit(identity, client_task_id=client_task_id, mode="edit", payload=payload)

    def list_tasks(self, identity: dict[str, object], task_ids: list[str]) -> dict[str, Any]:
        owner = _owner_id(identity)
        requested_ids = [_clean(task_id) for task_id in task_ids if _clean(task_id)]
        with self._lock:
            if self._cleanup_locked() or self._mark_stale_unfinished_locked():
                self._save_locked()
            items = []
            missing_ids = []
            for task_id in requested_ids:
                task = self._tasks.get(_task_key(owner, task_id))
                if task is None:
                    missing_ids.append(task_id)
                else:
                    items.append(_public_task(task))
            if not requested_ids:
                items = [
                    _public_task(task)
                    for task in self._tasks.values()
                    if task.get("owner_id") == owner
                ]
                items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
                missing_ids = []
            return {"items": items, "missing_ids": missing_ids}

    def _submit(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        mode: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id = _clean(client_task_id)
        if not task_id:
            raise ValueError("client_task_id is required")
        owner = _owner_id(identity)
        key = _task_key(owner, task_id)
        now = _now_iso()
        should_start = False
        with self._lock:
            cleaned = self._cleanup_locked()
            cleaned = self._mark_stale_unfinished_locked() or cleaned
            task = self._tasks.get(key)
            if task is not None:
                if cleaned:
                    self._save_locked()
                return _public_task(task)
            task = {
                "id": task_id,
                "owner_id": owner,
                "status": TASK_STATUS_QUEUED,
                "mode": mode,
                "model": _clean(payload.get("model"), "gpt-image-2"),
                "size": _clean(payload.get("size")),
                "quality": _clean(payload.get("quality"), "auto"),
                "created_at": now,
                "updated_at": now,
                "created_ts": time.time(),
                "last_heartbeat_ts": time.time(),
                "stage_metrics": {},
            }
            self._tasks[key] = task
            self._save_locked()
            _sync_unified_task(task_id, status=TASK_STATUS_QUEUED, mode=mode, owner_id=owner, progress="queued")
            should_start = True

        if should_start:
            thread = threading.Thread(
                target=self._run_task,
                args=(key, mode, payload, dict(identity), _clean(payload.get("model"), "gpt-image-2")),
                name=f"image-task-{task_id[:16]}",
                daemon=True,
            )
            thread.start()
        return _public_task(task)

    def _run_task(
        self,
        key: str,
        mode: str,
        payload: dict[str, Any],
        identity: dict[str, object],
        model: str,
    ) -> None:
        started = time.time()
        started_monotonic = time.monotonic()
        deadline_monotonic = started_monotonic + config.image_request_deadline_secs
        deadline_ts = started + config.image_request_deadline_secs
        lease_id = uuid.uuid4().hex
        stop_heartbeat = threading.Event()
        if not self._transition_task(
            key,
            expected_statuses={TASK_STATUS_QUEUED},
            status=TASK_STATUS_RUNNING,
            error="",
            started_ts=started,
            last_heartbeat_ts=started,
            deadline_ts=deadline_ts,
            lease_expires_ts=deadline_ts + max(5.0, config.image_heartbeat_interval_secs),
            lease_id=lease_id,
        ):
            return
        task_id = key.rsplit(":", 1)[-1]
        owner_id = key.split(":", 1)[0]
        with self._lock:
            self._cancel_events[key] = stop_heartbeat
        _sync_unified_task(task_id, status=TASK_STATUS_RUNNING, mode=mode, owner_id=owner_id, progress="running")
        previous_stage_at = started_monotonic
        stage_metrics: dict[str, dict[str, int]] = {}

        def heartbeat_worker() -> None:
            try:
                interval = max(0.05, float(self.heartbeat_interval_getter()))
            except Exception:
                interval = 10.0
            while not stop_heartbeat.wait(interval):
                if not self._heartbeat_task(key, lease_id):
                    return

        heartbeat_thread = threading.Thread(
            target=heartbeat_worker,
            name=f"image-heartbeat-{task_id[:16]}",
            daemon=True,
        )
        heartbeat_thread.start()

        def progress_callback(step: str) -> None:
            nonlocal previous_stage_at
            now = time.time()
            if step == "__heartbeat__":
                self._heartbeat_task(key, lease_id)
                return
            now_monotonic = time.monotonic()
            stage_metrics[step] = {
                "elapsed_ms": int((now_monotonic - started_monotonic) * 1000),
                "stage_ms": int((now_monotonic - previous_stage_at) * 1000),
            }
            previous_stage_at = now_monotonic
            updates: dict[str, Any] = {
                "progress": step,
                "last_heartbeat_ts": now,
                "stage_metrics": dict(stage_metrics),
            }
            if self._transition_task(
                key,
                expected_statuses={TASK_STATUS_RUNNING},
                expected_lease_id=lease_id,
                **updates,
            ):
                _sync_unified_task(task_id, status=TASK_STATUS_RUNNING, mode=mode, owner_id=owner_id, progress=step)
                logger.info({
                    "event": "image_task_stage",
                    "task_id": task_id,
                    "stage": step,
                    **stage_metrics[step],
                })

        payload_with_progress = {
            **payload,
            "progress_callback": progress_callback,
            "_request_id": task_id,
            "_request_started_monotonic": started_monotonic,
            "_request_deadline_monotonic": deadline_monotonic,
        }
        try:
            handler = self.edit_handler if mode == "edit" else self.generation_handler
            result = handler(payload_with_progress)
            if not isinstance(result, dict):
                raise RuntimeError("image task returned streaming result unexpectedly")
            data = result.get("data")
            account_email = _clean(result.get("_account_email") or result.get("account_email"))
            account_hash = _clean(result.get("_account_hash") or result.get("account_hash"))
            if not isinstance(data, list) or not data:
                upstream = _clean(result.get("message"))
                if upstream:
                    message = upstream
                else:
                    message = "号池中没有可用账号或所有账号均被限流，请检查号池状态（账号额度、是否被封禁、是否到达生图上限）"
                error = RuntimeError(message)
                if account_email:
                    setattr(error, "account_email", account_email)
                raise error
            usage = result.get("usage")
            duration_ms = int((time.time() - started) * 1000)
            if not self._transition_task(
                key,
                expected_statuses={TASK_STATUS_RUNNING},
                expected_lease_id=lease_id,
                status=TASK_STATUS_SUCCESS,
                data=data,
                usage=usage,
                error="",
                duration_ms=duration_ms,
                account_hash=account_hash,
                stage_metrics=dict(stage_metrics),
            ):
                return
            _sync_unified_task(task_id, status=TASK_STATUS_SUCCESS, mode=mode, owner_id=owner_id, progress="succeeded", duration_ms=duration_ms)
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用完成",
                request_preview=request_text(payload.get("prompt")),
                urls=_collect_image_urls(data),
                account_email=account_email,
            )
        except Exception as exc:
            error_message = str(exc) or "image task failed"
            account_email = _clean(getattr(exc, "account_email", ""))
            conversation_id = _clean(getattr(exc, "conversation_id", ""))
            duration_ms = int((time.time() - started) * 1000)
            if not self._transition_task(
                key,
                expected_statuses={TASK_STATUS_RUNNING},
                expected_lease_id=lease_id,
                status=TASK_STATUS_ERROR,
                error=error_message,
                data=[],
                duration_ms=duration_ms,
                stage_metrics=dict(stage_metrics),
                **({"conversation_id": conversation_id} if conversation_id else {}),
            ):
                return
            _sync_unified_task(task_id, status=TASK_STATUS_ERROR, mode=mode, owner_id=owner_id, progress="failed", error=error_message, duration_ms=duration_ms)
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用失败",
                request_preview=request_text(payload.get("prompt")),
                status="failed",
                error=error_message,
                account_email=account_email,
            )
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=1.0)
            with self._lock:
                self._cancel_events.pop(key, None)

    def _log_call(
        self,
        identity: dict[str, object],
        mode: str,
        model: str,
        started: float,
        suffix: str,
        *,
        request_preview: str = "",
        status: str = "success",
        error: str = "",
        urls: list[str] | None = None,
        account_email: str = "",
    ) -> None:
        endpoint = "/v1/images/edits" if mode == "edit" else "/v1/images/generations"
        summary_prefix = "图生图" if mode == "edit" else "文生图"
        detail = {
            "key_id": identity.get("id"),
            "key_name": identity.get("name"),
            "role": identity.get("role"),
            "endpoint": endpoint,
            "model": model,
            "started_at": datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": _now_iso(),
            "duration_ms": int((time.time() - started) * 1000),
            "status": status,
        }
        if request_preview:
            detail["request_text"] = request_preview
        if error:
            detail["error"] = error
        if account_email:
            detail["account_email"] = account_email
        if urls:
            detail["urls"] = list(dict.fromkeys(urls))
        try:
            log_service.add(LOG_TYPE_CALL, f"{summary_prefix}{suffix}", detail)
        except Exception:
            pass

    def _transition_task(
        self,
        key: str,
        *,
        expected_statuses: set[str],
        expected_lease_id: str | None = None,
        **updates: Any,
    ) -> bool:
        """Compare-and-set task mutation used by workers and terminal transitions."""
        with self._lock:
            task = self._tasks.get(key)
            if task is None or task.get("status") not in expected_statuses:
                return False
            if expected_lease_id is not None and task.get("lease_id") != expected_lease_id:
                return False
            task.update(updates)
            task["updated_at"] = _now_iso()
            task["updated_ts"] = time.time()
            self._save_locked()
            return True

    def _heartbeat_task(self, key: str, lease_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(key)
            if (
                task is None
                or task.get("status") != TASK_STATUS_RUNNING
                or task.get("lease_id") != lease_id
            ):
                return False
            now = time.time()
            deadline_ts = float(task.get("deadline_ts") or now)
            if now >= deadline_ts:
                return False
            task["last_heartbeat_ts"] = now
            task["lease_expires_ts"] = min(
                deadline_ts + max(5.0, config.image_heartbeat_interval_secs),
                now + max(15.0, config.image_heartbeat_interval_secs * 2.0),
            )
            task["updated_at"] = _now_iso()
            task["updated_ts"] = now
            self._save_locked()
            return True

    def _update_task(self, key: str, **updates: Any) -> None:
        with self._lock:
            task = self._tasks.get(key)
            if task is None:
                return
            task.update(updates)
            task["updated_at"] = _now_iso()
            task["updated_ts"] = time.time()
            self._save_locked()

    def _load_locked(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        raw_items = raw.get("tasks") if isinstance(raw, dict) else raw
        if not isinstance(raw_items, list):
            return {}
        tasks: dict[str, dict[str, Any]] = {}
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            task_id = _clean(item.get("id"))
            owner = _clean(item.get("owner_id"))
            if not task_id or not owner:
                continue
            status = _clean(item.get("status"))
            if status not in {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING, TASK_STATUS_SUCCESS, TASK_STATUS_ERROR}:
                status = TASK_STATUS_ERROR
            task = {
                "id": task_id,
                "owner_id": owner,
                "status": status,
                "mode": "edit" if item.get("mode") == "edit" else "generate",
                "model": _clean(item.get("model"), "gpt-image-2"),
                "size": _clean(item.get("size")),
                "quality": _clean(item.get("quality"), "auto"),
                "created_at": _clean(item.get("created_at"), _now_iso()),
                "updated_at": _clean(item.get("updated_at"), _clean(item.get("created_at"), _now_iso())),
                "created_ts": item.get("created_ts"),
                "updated_ts": item.get("updated_ts"),
                "started_ts": item.get("started_ts"),
                "last_heartbeat_ts": item.get("last_heartbeat_ts"),
                "lease_id": _clean(item.get("lease_id")),
                "lease_expires_ts": item.get("lease_expires_ts"),
                "deadline_ts": item.get("deadline_ts"),
                "progress": item.get("progress"),
                "stage_metrics": item.get("stage_metrics") if isinstance(item.get("stage_metrics"), dict) else {},
                "account_hash": _clean(item.get("account_hash")),
                "conversation_id": _clean(item.get("conversation_id")),
                "duration_ms": item.get("duration_ms"),
            }
            data = item.get("data")
            if isinstance(data, list):
                task["data"] = data
            usage = item.get("usage")
            if isinstance(usage, dict):
                task["usage"] = usage
            error = _clean(item.get("error"))
            if error:
                task["error"] = error
            tasks[_task_key(owner, task_id)] = task
        return tasks

    def _save_locked(self) -> None:
        items = sorted(self._tasks.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps({"tasks": items}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(self.path)

    def _recover_unfinished_locked(self) -> bool:
        changed = False
        for task in self._tasks.values():
            if task.get("status") in UNFINISHED_STATUSES:
                task["status"] = TASK_STATUS_ERROR
                task["error"] = "服务已重启，未完成的图片任务已中断"
                task["updated_at"] = _now_iso()
                task["updated_ts"] = time.time()
                changed = True
        return changed

    def _mark_stale_unfinished_locked(self) -> bool:
        now = time.time()
        stale_after = _stale_heartbeat_secs()
        changed = False
        for task in self._tasks.values():
            if task.get("status") not in UNFINISHED_STATUSES:
                continue
            heartbeat = task.get("last_heartbeat_ts") or task.get("updated_ts") or task.get("created_ts") or 0
            try:
                heartbeat = float(heartbeat)
            except Exception:
                heartbeat = 0.0
            try:
                lease_expires = float(task.get("lease_expires_ts") or 0.0)
            except Exception:
                lease_expires = 0.0
            lease_expired = bool(lease_expires and now > lease_expires)
            heartbeat_stale = not heartbeat or now - heartbeat > stale_after
            if not lease_expired and not heartbeat_stale:
                continue
            task["status"] = TASK_STATUS_ERROR
            task["error"] = (
                "图片任务 worker 租约已过期，任务已按终态收口。"
                "后台迟到结果将被 CAS 拒绝，避免覆盖该终态。"
            )
            task["updated_at"] = _now_iso()
            task["updated_ts"] = now
            task["duration_ms"] = int(max(0.0, now - float(task.get("created_ts") or now)) * 1000)
            cancel_event = self._cancel_events.get(_task_key(_clean(task.get("owner_id")), _clean(task.get("id"))))
            if cancel_event is not None:
                cancel_event.set()
            changed = True
        return changed

    def _cleanup_locked(self) -> bool:
        try:
            retention_days = max(1, int(self.retention_days_getter()))
        except Exception:
            retention_days = 30
        cutoff = time.time() - retention_days * 86400
        removed_keys = [
            key
            for key, task in self._tasks.items()
            if task.get("status") in TERMINAL_STATUSES and _timestamp(task.get("updated_at")) < cutoff
        ]
        for key in removed_keys:
            self._tasks.pop(key, None)
        return bool(removed_keys)

    def resume_poll(
        self,
        identity: dict[str, object],
        task_id: str,
        extra_timeout_secs: float = 30.0,
    ) -> dict[str, Any]:
        """恢复对已超时任务的轮询，额外等待 extra_timeout_secs 秒。"""
        owner = _owner_id(identity)
        key = _task_key(owner, _clean(task_id))
        with self._lock:
            task = self._tasks.get(key)
            if task is None:
                raise ValueError("task not found")
            if task.get("status") != TASK_STATUS_ERROR:
                raise ValueError("task is not in error state")
            error_msg = _clean(task.get("error"))
            if "超时" not in error_msg:
                raise ValueError("task error is not a timeout error")
            conversation_id = _clean(task.get("conversation_id"))
            if not conversation_id:
                raise ValueError("task has no conversation_id")
            mode = task.get("mode", "generate")
            model = task.get("model", "gpt-image-2")
            # 将任务状态重置为 running
            self._update_task(key, status=TASK_STATUS_RUNNING, error="")

        # 启动新线程继续轮询
        thread = threading.Thread(
            target=self._run_resume_poll,
            args=(key, conversation_id, extra_timeout_secs, dict(identity), mode, model),
            name=f"image-resume-{_clean(task_id)[:16]}",
            daemon=True,
        )
        thread.start()
        return _public_task(task)

    def _run_resume_poll(
        self,
        key: str,
        conversation_id: str,
        extra_timeout_secs: float,
        identity: dict[str, object],
        mode: str,
        model: str,
    ) -> None:
        """后台线程：继续轮询已有 conversation_id 的图片结果。"""
        started = time.time()
        try:
            from services.openai_backend_api import OpenAIBackendAPI
            from services.protocol.conversation import format_image_result

            backend = OpenAIBackendAPI()
            try:
                file_ids, sediment_ids = backend._poll_image_results(
                    conversation_id,
                    extra_timeout_secs,
                )
                if not file_ids and not sediment_ids:
                    raise RuntimeError(
                        f"继续等待 {extra_timeout_secs} 秒后仍未找到图片结果。"
                    )

                image_urls = backend.resolve_conversation_image_urls(
                    conversation_id, file_ids, sediment_ids, poll=False,
                )
                if not image_urls:
                    raise RuntimeError("图片 URL 解析失败")

                image_items = [
                    {"b64_json": __import__("base64").b64encode(image_data).decode("ascii")}
                    for image_data in backend.download_image_bytes(image_urls)
                ]
            finally:
                backend.close()
            data = format_image_result(
                image_items,
                "",  # prompt 已不重要，结果已经拿到了
                "b64_json",
                "",
                int(time.time()),
            )["data"]
            self._update_task(key, status=TASK_STATUS_SUCCESS, data=data, error="", duration_ms=int((time.time() - started) * 1000))
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用完成（续轮询）",
                status="success",
                urls=_collect_image_urls(data),
            )
        except Exception as exc:
            error_message = str(exc) or "resume poll failed"
            duration_ms = int((time.time() - started) * 1000)
            self._update_task(key, status=TASK_STATUS_ERROR, error=error_message, data=[], duration_ms=duration_ms)
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用失败（续轮询）",
                status="failed",
                error=error_message,
            )


image_task_service = ImageTaskService(DATA_DIR / "image_tasks.json")
