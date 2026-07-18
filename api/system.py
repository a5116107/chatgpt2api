from __future__ import annotations

import os
import shutil

from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict

from api.support import require_admin, require_identity, resolve_image_base_url
from services.backup_service import BackupError, backup_service
from services.config import config
from services.image_service import (
    compress_images,
    delete_images,
    delete_to_target,
    download_images_zip,
    get_image_download_response,
    get_image_response,
    get_thumbnail_response,
    list_images,
    storage_stats,
)
from services.image_storage_service import ImageStorageError, image_storage_service
from services.image_tags_service import delete_tag, get_all_tags, set_tags
from services.log_service import log_service
from services.proxy_service import proxy_settings, test_clearance, test_proxy
from services.risk_control_service import risk_control_service


class SettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")


class ProxyTestRequest(BaseModel):
    url: str = ""


class ClearanceTestRequest(BaseModel):
    target_url: str = "https://chatgpt.com"


class ImageDeleteRequest(BaseModel):
    paths: list[str] = []
    start_date: str = ""
    end_date: str = ""
    all_matching: bool = False

class ImageDownloadRequest(BaseModel):
    paths: list[str]

class ImageTagsRequest(BaseModel):
    path: str
    tags: list[str]

class LogDeleteRequest(BaseModel):
    ids: list[str] = []
class BackupDeleteRequest(BaseModel):
    key: str = ""


class OpsRollbackPlanRequest(BaseModel):
    backup_key: str = ""


def create_router(app_version: str) -> APIRouter:
    router = APIRouter()

    @router.post("/auth/login")
    async def login(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        return {
            "ok": True,
            "version": app_version,
            "role": identity.get("role"),
            "subject_id": identity.get("id"),
            "name": identity.get("name"),
        }

    @router.get("/version")
    async def get_version():
        return {"version": app_version}

    @router.get("/api/settings")
    async def get_settings(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"config": config.get()}

    @router.get("/api/third-party-apps")
    async def get_third_party_apps(authorization: str | None = Header(default=None)):
        require_identity(authorization)
        return {"third_party_apps": config.get_third_party_apps_settings()}

    @router.post("/api/settings")
    async def save_settings(body: SettingsUpdateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"config": config.update(body.model_dump(mode="python"))}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/images")
    async def get_images(request: Request, start_date: str = "", end_date: str = "", authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return list_images(resolve_image_base_url(request), start_date=start_date.strip(), end_date=end_date.strip())

    @router.get("/images/{image_path:path}", include_in_schema=False)
    async def get_image(image_path: str):
        return get_image_response(image_path)

    @router.get("/image-thumbnails/{image_path:path}", include_in_schema=False)
    async def get_image_thumbnail(image_path: str):
        return get_thumbnail_response(image_path)

    @router.post("/api/images/delete")
    async def delete_images_endpoint(body: ImageDeleteRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return delete_images(body.paths, start_date=body.start_date.strip(), end_date=body.end_date.strip(), all_matching=body.all_matching)

    @router.post("/api/images/download")
    async def download_images_endpoint(body: ImageDownloadRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        buf = download_images_zip(body.paths)
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="images.zip"'},
        )

    @router.get("/api/images/download/{image_path:path}")
    async def download_single_image_endpoint(image_path: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return get_image_download_response(image_path)

    @router.get("/api/logs")
    async def get_logs(type: str = "", start_date: str = "", end_date: str = "", authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"items": log_service.list(type=type.strip(), start_date=start_date.strip(), end_date=end_date.strip())}

    @router.post("/api/logs/delete")
    async def delete_logs(body: LogDeleteRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return log_service.delete(body.ids)

    @router.post("/api/proxy/test")
    async def test_proxy_endpoint(body: ProxyTestRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"result": await run_in_threadpool(test_proxy, (body.url or "").strip())}

    @router.get("/api/proxy/runtime")
    async def get_proxy_runtime_endpoint(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {
            "runtime": config.get_public_proxy_runtime_settings(),
            "status": proxy_settings.get_runtime_status(),
        }

    @router.post("/api/proxy/runtime")
    async def save_proxy_runtime_endpoint(body: SettingsUpdateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            config.update({"proxy_runtime": body.model_dump(mode="python")})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {
            "runtime": config.get_public_proxy_runtime_settings(),
            "status": proxy_settings.get_runtime_status(),
        }

    @router.post("/api/proxy/clearance/test")
    async def test_proxy_clearance_endpoint(body: ClearanceTestRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"result": await run_in_threadpool(test_clearance, body.target_url)}

    @router.get("/api/storage/info")
    async def get_storage_info(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        storage = config.get_storage_backend()
        return {
            "backend": storage.get_backend_info(),
            "health": storage.health_check(),
        }

    @router.post("/api/backup/test")
    async def test_backup_connection(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"result": await run_in_threadpool(backup_service.test_connection)}
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/image-storage/test")
    async def test_image_storage_endpoint(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"result": await run_in_threadpool(image_storage_service.test_webdav)}

    @router.post("/api/image-storage/sync")
    async def sync_image_storage_endpoint(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"result": await run_in_threadpool(image_storage_service.sync_all)}
        except ImageStorageError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/backups")
    async def get_backups(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {
                "items": await run_in_threadpool(backup_service.list_backups),
                "state": backup_service.get_status(),
                "settings": backup_service.get_settings(),
            }
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/backups/run")
    async def run_backup_endpoint(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"result": await run_in_threadpool(backup_service.run_backup)}
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/backups/delete")
    async def delete_backup_endpoint(body: BackupDeleteRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            await run_in_threadpool(backup_service.delete_backup, body.key)
            return {"ok": True}
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/backups/detail")
    async def get_backup_detail(key: str = "", authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"item": await run_in_threadpool(backup_service.get_backup_detail, key)}
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/backups/download")
    async def download_backup_endpoint(key: str = "", authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            item = await run_in_threadpool(backup_service.download_backup, key)
        except BackupError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        filename = str(item.get("name") or "backup.bin")
        quoted = quote(filename)
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
            "Content-Length": str(int(item.get("size") or 0)),
        }
        return Response(
            content=bytes(item.get("payload") or b""),
            media_type=str(item.get("content_type") or "application/octet-stream"),
            headers=headers,
        )


    @router.get("/api/images/tags")
    async def list_image_tags(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"tags": get_all_tags()}

    @router.post("/api/images/tags")
    async def update_image_tags(body: ImageTagsRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        rel = body.path.strip().lstrip("/")
        if not rel:
            raise HTTPException(status_code=400, detail={"error": "path is required"})
        tags = set_tags(rel, body.tags)
        return {"ok": True, "tags": tags}

    @router.delete("/api/images/tags/{tag}")
    async def delete_image_tag(tag: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        count = delete_tag(tag)
        return {"ok": True, "removed_from": count}

    @router.get("/api/images/storage")
    async def get_image_storage(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return storage_stats()

    @router.post("/api/images/storage/compress")
    async def compress_all_images(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return await run_in_threadpool(compress_images)

    @router.post("/api/images/storage/cleanup-to-target")
    async def cleanup_to_target(
        target_free_mb: int = 500,
        dry_run: bool = False,
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        return await run_in_threadpool(delete_to_target, target_free_mb, dry_run)

    @router.get("/api/ops/migration")
    async def migration_status(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        storage = config.get_storage_backend()
        return {
            "schema_version": 1,
            "status": "ok",
            "pending": [],
            "storage": {
                "backend": storage.get_backend_info(),
                "health": storage.health_check(),
            },
            "data_files": {
                "accounts": config.accounts_file.exists(),
                "config": config.path.exists(),
            },
        }

    @router.post("/api/ops/smoke-check")
    async def smoke_check(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        storage = config.get_storage_backend()
        task_sync = await run_in_threadpool(risk_control_service.sync_task_center)
        proxy_runtime = proxy_settings.get_runtime_status()
        # PATCH_MARKER ops_smoke_enrich_r31
        disk_free_mb = 0
        try:
            import shutil as _shutil
            disk_free_mb = int(_shutil.disk_usage("/").free / 1024 / 1024)
        except Exception:
            disk_free_mb = -1
        route_stats = {}
        try:
            from services.register_service import register_service as _reg
            route_stats = _reg.route_stats()
        except Exception as exc:
            route_stats = {"error": str(exc)}
        return {
            "ok": True,
            "version": app_version,
            "features": config.get_feature_flags(),
            "chat_runtime": config.get_chat_runtime_settings(),
            "video": config.get_video_settings(),
            "storage": {"backend": storage.get_backend_info(), "health": storage.health_check()},
            "proxy_runtime": proxy_runtime,
            "tasks": task_sync,
            "disk_free_mb": disk_free_mb,
            "route_stats": route_stats,
            "markers": [
                "ops_cleanup_target_free_r31",
                "ops_smoke_enrich_r31",
                "register_route_retire_apply_r31",
                "chat_revoked_fast_rotate_r33",
                "video_upstream_ready_r33",
                "chat_stream_deadline_r34",
                "chat_timeout_rotate_r34",
            ],
        }

    @router.post("/api/ops/rollback-plan")
    async def rollback_plan(body: OpsRollbackPlanRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        backups = await run_in_threadpool(backup_service.list_backups)
        selected = str(body.backup_key or "").strip()
        candidate = next((item for item in backups if str(item.get("key") or "") == selected), None) if selected else (backups[0] if backups else None)
        return {
            "ok": bool(candidate),
            "selected": candidate,
            "available": backups[:20],
            "steps": [
                "确认 selected.key 与预期回滚点一致",
                "下载备份并离线校验 backup-metadata.json",
                "停止 app 容器，替换 data/config 目标文件",
                "启动 app 容器，运行 /api/ops/smoke-check",
            ],
        }

    @router.get("/api/ops/disk")
    async def ops_disk(authorization: str | None = Header(default=None)):
        # PATCH_MARKER ops_disk_cleanup_r29
        require_admin(authorization)

        def _disk() -> dict[str, object]:
            usage = shutil.disk_usage("/")
            data_dir = getattr(config, "DATA_DIR", None)
            try:
                from services.config import DATA_DIR as _DATA_DIR
                data_dir = _DATA_DIR
            except Exception:
                pass
            paths = {
                "data": str(data_dir) if data_dir else "/app/data",
                "videos": str((data_dir / "videos") if data_dir else "/app/data/videos"),
                "images": str((data_dir / "images") if data_dir else "/app/data/images"),
                "logs": str((data_dir / "logs.jsonl") if data_dir else "/app/data/logs.jsonl"),
            }
            sizes = {}
            for name, p in paths.items():
                try:
                    if os.path.isdir(p):
                        total = 0
                        for root, _dirs, files in os.walk(p):
                            for fn in files:
                                fp = os.path.join(root, fn)
                                try:
                                    total += os.path.getsize(fp)
                                except OSError:
                                    pass
                        sizes[name] = total
                    elif os.path.isfile(p):
                        sizes[name] = os.path.getsize(p)
                    else:
                        sizes[name] = 0
                except Exception:
                    sizes[name] = -1
            return {
                "root": {
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "free_mb": int(usage.free / 1024 / 1024),
                    "used_pct": round(usage.used * 100 / usage.total, 2) if usage.total else 0,
                },
                "paths": paths,
                "sizes_bytes": sizes,
                "video": config.get_video_settings(),
            }

        return {"ok": True, "disk": await run_in_threadpool(_disk)}

    @router.post("/api/ops/cleanup")
    async def ops_cleanup(authorization: str | None = Header(default=None), dry_run: bool = False, target_free_mb: int = 0):
        # PATCH_MARKER ops_disk_cleanup_r29
        # PATCH_MARKER ops_cleanup_images_logs_r30
        require_admin(authorization)

        def _cleanup() -> dict[str, object]:
            from services.config import DATA_DIR, config as app_config
            removed = []
            freed = 0
            candidates = []
            # safe: old account/register bak in data dir only
            for pattern in ("accounts.json.bak*", "register.json.bak*"):
                for path in DATA_DIR.glob(pattern):
                    if path.is_file():
                        candidates.append(path)
            for path in DATA_DIR.glob("*.tmp.*"):
                if path.is_file():
                    candidates.append(path)
            for path in candidates:
                size = path.stat().st_size if path.exists() else 0
                item = {"path": str(path), "bytes": size, "kind": "bak_or_tmp"}
                if not dry_run:
                    try:
                        path.unlink()
                        freed += size
                        item["deleted"] = True
                    except Exception as exc:
                        item["deleted"] = False
                        item["error"] = str(exc)
                else:
                    item["deleted"] = False
                removed.append(item)

            # image retention cleanup (respect image_retention_days)
            images_removed = 0
            if not dry_run:
                try:
                    images_removed = int(app_config.cleanup_old_images() or 0)
                except Exception as exc:
                    images_removed = -1
                    removed.append({"path": "images_retention", "error": str(exc), "deleted": False, "kind": "images"})
            else:
                # dry-run estimate: count files older than retention
                try:
                    import time as _time
                    cutoff = _time.time() - int(app_config.image_retention_days) * 86400
                    images_dir = app_config.images_dir
                    for path in images_dir.rglob("*"):
                        if path.is_file() and path.stat().st_mtime < cutoff:
                            images_removed += 1
                except Exception:
                    images_removed = 0

            # PATCH_MARKER ops_cleanup_target_free_r31
            # optional free-space reclamation: delete oldest images until target free MB
            target_free_info = {"enabled": False}
            try:
                import shutil as _shutil
                min_free = int(target_free_mb) if int(target_free_mb or 0) > 0 else int(getattr(app_config, "image_min_free_mb", None) or app_config.data.get("image_min_free_mb") or 500)
                # if explicit target_free_mb provided, force reclaim even when currently above default threshold
                force_target = int(target_free_mb or 0) > 0
                usage_now = _shutil.disk_usage("/")
                free_now = int(usage_now.free / 1024 / 1024)
                # honor query/env style via config only; default reclaims to image_min_free_mb when below threshold
                if force_target or free_now < min_free:
                    target_free_info = {
                        "enabled": True,
                        "before_free_mb": free_now,
                        "target_free_mb": min_free,
                    }
                    if not dry_run:
                        from services.image_service import delete_to_target
                        target_free_info["result"] = delete_to_target(min_free, dry_run=False)
                    else:
                        from services.image_service import delete_to_target
                        target_free_info["result"] = delete_to_target(min_free, dry_run=True)
                else:
                    target_free_info = {
                        "enabled": False,
                        "before_free_mb": free_now,
                        "target_free_mb": min_free,
                        "skipped": "free_space_above_threshold",
                    }
            except Exception as exc:
                target_free_info = {"enabled": True, "error": str(exc)}

            # logs.jsonl rotate if oversized (>8MB keep last 2MB)
            log_info = {"path": str(DATA_DIR / "logs.jsonl"), "rotated": False, "bytes_before": 0, "bytes_after": 0}
            log_path = DATA_DIR / "logs.jsonl"
            if log_path.exists() and log_path.is_file():
                size = log_path.stat().st_size
                log_info["bytes_before"] = size
                if size > 8 * 1024 * 1024:
                    if not dry_run:
                        try:
                            data = log_path.read_bytes()
                            keep = data[-(2 * 1024 * 1024):]
                            # align to next newline
                            nl = keep.find(b"\n")
                            if nl >= 0:
                                keep = keep[nl + 1 :]
                            log_path.write_bytes(keep)
                            after = log_path.stat().st_size
                            freed += max(0, size - after)
                            log_info["rotated"] = True
                            log_info["bytes_after"] = after
                            log_info["deleted"] = True
                        except Exception as exc:
                            log_info["error"] = str(exc)
                    else:
                        log_info["rotated"] = True
                        log_info["bytes_after"] = 2 * 1024 * 1024
                else:
                    log_info["bytes_after"] = size
            removed.append({**log_info, "kind": "logs"})

            # also repair conversations while cleaning ops surface
            repair = {}
            if not dry_run:
                try:
                    from services.conversation_store import conversation_store
                    from services.risk_control_service import risk_control_service
                    repair = conversation_store.repair_statuses(abandoned_idle_secs=3600, draft_idle_secs=600)
                    risk_control_service.sync_task_center()
                except Exception as exc:
                    repair = {"error": str(exc)}

            usage = shutil.disk_usage("/")
            return {
                "dry_run": bool(dry_run),
                "candidates": len([x for x in removed if x.get("kind") == "bak_or_tmp"]),
                "freed_bytes": freed,
                "images_removed": images_removed,
                "image_retention_days": getattr(app_config, "image_retention_days", None),
                "target_free": target_free_info,
                "repair": repair,
                "items": removed[:80],
                "root_free_mb": int(usage.free / 1024 / 1024),
            }

        return {"ok": True, "cleanup": await run_in_threadpool(_cleanup)}

    @router.get("/api/feature-flags")
    @router.get("/api/feature_flags")
    @router.get("/api/features")
    async def feature_flags_alias(authorization: str | None = Header(default=None)):
        # PATCH_MARKER feature_flags_alias_r28
        return {"features": config.get_feature_flags(), "video": config.get_video_settings()}

    @router.post("/api/ops/conversations/repair")
    async def repair_conversations(authorization: str | None = Header(default=None)):
        # PATCH_MARKER conversation_status_repair_r28
        require_admin(authorization)
        from services.conversation_store import conversation_store
        from services.risk_control_service import risk_control_service
        repaired = await run_in_threadpool(conversation_store.repair_statuses)
        synced = await run_in_threadpool(risk_control_service.sync_task_center)
        return {"ok": True, "repair": repaired, "tasks": synced}

    @router.get("/api/health", response_model=None)
    @router.get("/health", response_model=None)
    async def health_dashboard(request: Request, format: str = Query(default="")):
        # PATCH_MARKER api_health_alias_r27
        from services.account_service import account_service as acct_svc
        if not str(format or "").strip():
            path = str(getattr(getattr(request, "url", None), "path", "") or "")
            format = "json" if path.rstrip("/").endswith("/api/health") else "html"
        stats = acct_svc.get_stats()
        storage = config.get_storage_backend()
        storage_health = storage.health_check()
        healthy = stats["image_schedulable_accounts"] > 0

        stats_json = {
            "status": "ok" if healthy else "degraded",
            "healthy": healthy,
            "version": app_version,
            "storage": {"backend": storage.get_backend_info(), "health": storage_health},
            "proxy_runtime": proxy_settings.get_runtime_status(),
            "accounts": stats,
        }
        if format == "json":
            return stats_json
        return HTMLResponse(f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>号池健康监控 - chatgpt2api</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#0f1117;color:#e2e8f0;min-height:100vh}}
.header{{background:#1a1d27;border-bottom:1px solid #2a2d3a;padding:16px 24px;display:flex;justify-content:space-between;align-items:center}}
.header h1{{font-size:20px}}
.status-dot{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px}}
.status-ok{{background:#22c55e;box-shadow:0 0 8px #22c55e88}}
.status-degraded{{background:#f59e0b;box-shadow:0 0 8px #f59e0b88}}
.container{{max-width:960px;margin:0 auto;padding:24px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:24px}}
.card{{background:#1a1d27;border:1px solid #2a2d3a;border-radius:10px;padding:16px}}
.card .value{{font-size:28px;font-weight:700;margin:4px 0}}
.card .label{{font-size:13px;color:#94a3b8}}
.green{{color:#22c55e}}.yellow{{color:#f59e0b}}.red{{color:#ef4444}}.blue{{color:#6c63ff}}
table{{width:100%;border-collapse:collapse;background:#1a1d27;border:1px solid #2a2d3a;border-radius:10px;overflow:hidden}}
th{{background:#242836;font-weight:600;text-align:left;padding:10px 12px;font-size:12px;color:#94a3b8;text-transform:uppercase}}
td{{padding:8px 12px;border-top:1px solid #2a2d3a;font-size:14px}}tr:hover td{{background:rgba(108,99,255,.05)}}
.api-url{{font-family:monospace;font-size:12px;color:#6c63ff}}
.refresh{{font-size:12px;color:#64748b;text-align:center;margin-top:24px}}
</style>
<meta http-equiv="refresh" content="30">
</head>
<body>
<div class="header">
<h1><span class="status-dot {'status-ok' if healthy else 'status-degraded'}"></span>号池健康监控</h1>
<div style="font-size:13px;color:#94a3b8">v{app_version} · 30s 自动刷新</div>
</div>
<div class="container">
<div class="cards">
<div class="card"><div class="label">号池状态</div><div class="value {'green' if healthy else 'yellow'}">{'正常' if healthy else '异常'}</div></div>
<div class="card"><div class="label">当前账号</div><div class="value blue">{stats['total']}</div></div>
<div class="card"><div class="label">累计入库</div><div class="value">{stats['cumulative_total']}</div></div>
<div class="card"><div class="label">可调度账号</div><div class="value green">{stats['image_schedulable_accounts']}</div></div>
<div class="card"><div class="label">可用槽位</div><div class="value green">{stats['image_available_slots']}</div></div>
<div class="card"><div class="label">探测中 / 待探测</div><div class="value">{stats['image_probe_inflight']} / {stats['image_probe_due']}</div></div>
<div class="card"><div class="label">无限额</div><div class="value">{stats['unlimited_quota_count']}</div></div>
<div class="card"><div class="label">可调度额度</div><div class="value">{stats['image_schedulable_quota']}</div></div>
<div class="card"><div class="label">可信额度</div><div class="value">{stats['image_verified_quota']}</div></div>
<div class="card"><div class="label">限流</div><div class="value yellow">{stats['limited']}</div></div>
<div class="card"><div class="label">异常</div><div class="value red">{stats['abnormal']}</div></div>
<div class="card"><div class="label">禁用</div><div class="value">{stats['disabled']}</div></div>
<div class="card"><div class="label">成功/失败</div><div class="value">{stats['total_success']}<span style="font-size:18px;color:#94a3b8">/</span><span class="red">{stats['total_fail']}</span></div></div>
</div>
<h2 style="margin-bottom:12px;font-size:16px">图片号池状态</h2>
<table style="margin-bottom:24px">
<tr><th>就绪</th><th>观察</th><th>冷却</th><th>额度耗尽</th><th>隔离</th><th>停用</th></tr>
<tr><td>{stats['image_pool_states'].get('ready', 0)}</td><td>{stats['image_pool_states'].get('probation', 0)}</td><td>{stats['image_pool_states'].get('cooldown', 0)}</td><td>{stats['image_pool_states'].get('exhausted', 0)}</td><td>{stats['image_pool_states'].get('quarantined', 0)}</td><td>{stats['image_pool_states'].get('disabled', 0)}</td></tr>
</table>
<h2 style="margin-bottom:12px;font-size:16px">账号类型分布</h2>
<table>
<tr><th>类型</th><th>数量</th></tr>
{''.join(f'<tr><td>{t}</td><td>{c}</td></tr>' for t,c in sorted(stats['by_type'].items()))}
</table>
<div class="refresh">JSON: <span class="api-url">/health?format=json</span></div>
</div></body></html>""")

    return router
