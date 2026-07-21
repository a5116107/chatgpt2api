from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.support import require_admin
from services.feature_flags import require_feature
from services.register_service import register_service


class RegisterConfigRequest(BaseModel):
    mail: dict | None = None
    proxy: str | None = None
    proxy_session_ttl_seconds: int | None = Field(default=None, ge=60, le=3600)
    proxy_region: str | None = None
    total: int | None = None
    threads: int | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=20)
    mode: str | None = None
    target_quota: int | None = None
    target_available: int | None = None
    check_interval: int | None = None


class RegisterStartRequest(BaseModel):
    confirm: bool = False


class OutlookPoolResetRequest(BaseModel):
    scope: str | None = None


class RouteRetireApplyRequest(BaseModel):
    confirm: bool = False
    dry_run: bool = True
    min_attempts: int = 20
    max_success_rate: float = 0.2
    demote_legacy: bool = True
    disable_mail_providers: bool = False
    disable_domains: bool = False



def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/register")
    async def get_register_config(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.get()}

    @router.post("/api/register")
    async def update_register_config(body: RegisterConfigRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        require_feature("register")
        return {"register": register_service.update(body.model_dump(exclude_none=True))}

    @router.post("/api/register/start")
    async def start_register(body: RegisterStartRequest | None = None, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        require_feature("register")
        if body is None or not body.confirm:
            raise HTTPException(status_code=400, detail="启动注册任务需要显式确认")
        return {"register": register_service.start()}

    @router.post("/api/register/stop")
    async def stop_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.stop()}

    @router.post("/api/register/reset")
    async def reset_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.reset()}

    @router.post("/api/register/outlook-pool/reset")
    async def reset_outlook_pool(body: OutlookPoolResetRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.reset_outlook_pool(body.scope or "all")}

    @router.get("/api/register/route-stats")
    async def get_register_route_stats(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"route_stats": register_service.route_stats()}

    

    @router.post("/api/register/route-stats/retire")
    async def apply_register_route_retirement(body: RouteRetireApplyRequest | None = None, authorization: str | None = Header(default=None)):
        # PATCH_MARKER register_route_retire_apply_r31
        require_admin(authorization)
        payload = body or RouteRetireApplyRequest()
        return {
            "result": register_service.apply_route_retirement(
                confirm=bool(payload.confirm),
                dry_run=bool(payload.dry_run),
                min_attempts=int(payload.min_attempts or 20),
                max_success_rate=float(payload.max_success_rate or 0.2),
                demote_legacy=bool(payload.demote_legacy),
                disable_mail_providers=bool(payload.disable_mail_providers),
                disable_domains=bool(payload.disable_domains),
            )
        }

    @router.get("/api/register/events")
    async def register_events(token: str = ""):
        require_admin(f"Bearer {token}")

        async def stream():
            last = ""
            while True:
                payload = json.dumps(register_service.get(), ensure_ascii=False)
                if payload != last:
                    last = payload
                    yield f"data: {payload}\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return router
