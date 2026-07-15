from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from api.support import require_admin
from services.risk_control_service import risk_control_service


class RiskEventRequest(BaseModel):
    code: str | None = None
    message: str = ""
    scope: str | None = None
    account_key: str | None = None
    proxy: str | None = None
    runtime_profile_id: str | None = None
    task_id: str | None = None
    status_code: int | None = None
    raw: dict | None = None


class CapabilityUpdateRequest(BaseModel):
    chat: bool | None = None
    responses: bool | None = None
    raw_conversation: bool | None = None
    search: bool | None = None
    image: bool | None = None
    image_edit: bool | None = None
    image_variation: bool | None = None
    file: bool | None = None
    audio_tts: bool | None = None
    audio_stt: bool | None = None
    audio_translation: bool | None = None
    video: bool | None = None
    plan: str | None = None
    source: str | None = "manual"


class ProxyUpsertRequest(BaseModel):
    proxy: str = Field(..., min_length=1)
    country: str | None = None
    provider: str | None = None
    asn: str | None = None


class ProxyEventRequest(BaseModel):
    proxy: str = Field(..., min_length=1)
    code: str = "success"
    status_code: int | None = None


class ProxyDeleteRequest(BaseModel):
    ids: list[str] | None = None
    statuses: list[str] | None = None


class ProxyPruneRequest(BaseModel):
    min_score: float = 30
    min_failures: int = 3
    include_cooldown: bool = False


class ProxyPoolTestRequest(BaseModel):
    limit: int = 50
    include_dead: bool = False


class TaskDeleteRequest(BaseModel):
    ids: list[str] | None = None
    terminal_only: bool = True


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/risk/summary")
    async def risk_summary(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return risk_control_service.summary()

    @router.get("/api/risk/events")
    async def risk_events(limit: int = Query(default=200, ge=1, le=1000), code: str = "", scope: str = "", authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"items": risk_control_service.list_events(limit=limit, code=code, scope=scope)}

    @router.post("/api/risk/events")
    async def create_risk_event(body: RiskEventRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"item": risk_control_service.record_event(code=body.code, message=body.message, scope=body.scope, account=body.account_key, proxy=body.proxy or "", profile_id=body.runtime_profile_id or "", task_id=body.task_id or "", status_code=body.status_code, raw=body.raw)}

    @router.get("/api/accounts/capabilities")
    async def capabilities(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"items": risk_control_service.list_capabilities()}

    @router.post("/api/accounts/capabilities/probe-batch")
    async def probe_capabilities(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return risk_control_service.sync_account_capabilities()

    @router.get("/api/accounts/{account_key}/capabilities")
    async def account_capability(account_key: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        item = risk_control_service.get_capability(account_key)
        if item is None:
            raise HTTPException(status_code=404, detail="capability not found")
        return {"item": item}

    @router.post("/api/accounts/{account_key}/capabilities")
    async def update_account_capability(account_key: str, body: CapabilityUpdateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"item": risk_control_service.update_capability(account_key, body.model_dump(exclude_none=True))}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="capability not found") from exc

    @router.get("/api/proxies")
    async def list_proxies(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"items": risk_control_service.list_proxies()}

    @router.post("/api/proxies")
    async def upsert_proxy(body: ProxyUpsertRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"item": risk_control_service.upsert_proxy(body.proxy, country=body.country or "", provider=body.provider or "manual", asn=body.asn or "")}

    @router.post("/api/proxies/events")
    async def proxy_event(body: ProxyEventRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"item": risk_control_service.report_proxy_event(body.proxy, body.code, body.status_code)}

    @router.post("/api/proxies/rebuild")
    async def rebuild_proxies(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return risk_control_service.rebuild_proxy_scores()

    @router.post("/api/proxies/delete")
    async def delete_proxies(body: ProxyDeleteRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return risk_control_service.delete_proxies(ids=body.ids, statuses=body.statuses)

    @router.post("/api/proxies/prune")
    async def prune_proxies(body: ProxyPruneRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return risk_control_service.prune_proxies(min_score=body.min_score, min_failures=body.min_failures, include_cooldown=body.include_cooldown)

    @router.post("/api/proxies/test-pool")
    async def test_proxy_pool(body: ProxyPoolTestRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return risk_control_service.test_proxy_pool(limit=body.limit, include_dead=body.include_dead)

    @router.get("/api/tasks")
    async def list_tasks(limit: int = Query(default=200, ge=1, le=1000), type: str = "", status: str = "", authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"items": risk_control_service.list_tasks(limit=limit, type_=type, status=status)}

    @router.post("/api/tasks/sync")
    async def sync_tasks(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return risk_control_service.sync_task_center()

    @router.post("/api/tasks/bulk-delete")
    async def bulk_delete_tasks(body: TaskDeleteRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return risk_control_service.delete_tasks(ids=body.ids, terminal_only=body.terminal_only)

    return router
