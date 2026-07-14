from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from api.support import require_admin
from services.account_service import account_service


class RuntimeProfileBackfillRequest(BaseModel):
    access_tokens: list[str] = Field(default_factory=list)


class RuntimeProfileAccountRequest(BaseModel):
    access_token: str = ""
    profile_id: str = ""


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/runtime-profiles")
    async def list_runtime_profiles(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {
            "items": account_service.list_runtime_profiles(),
            "audit": account_service.audit_runtime_profiles(),
        }

    @router.get("/api/runtime-profiles/audit")
    async def audit_runtime_profiles(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return account_service.audit_runtime_profiles()

    @router.post("/api/runtime-profiles/backfill")
    async def backfill_runtime_profiles(
        body: RuntimeProfileBackfillRequest,
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        return account_service.backfill_runtime_profiles(body.access_tokens)

    @router.post("/api/runtime-profiles/repair")
    async def repair_runtime_profile(
        body: RuntimeProfileAccountRequest,
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        result = account_service.repair_runtime_profile(body.access_token, body.profile_id or None)
        if result is None:
            raise HTTPException(status_code=404, detail={"error": "account or runtime profile not found"})
        return result

    @router.post("/api/runtime-profiles/rebind")
    async def rebind_runtime_profile(
        body: RuntimeProfileAccountRequest,
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        result = account_service.rebind_runtime_profile(body.access_token, body.profile_id or None)
        if result is None:
            raise HTTPException(status_code=404, detail={"error": "account not found"})
        return result

    @router.post("/api/runtime-profiles/cleanup")
    async def cleanup_runtime_profiles(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return account_service.cleanup_orphan_runtime_profiles()

    @router.get("/api/runtime-profiles/{profile_id}")
    async def get_runtime_profile(profile_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        item = account_service.get_runtime_profile(profile_id)
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "runtime profile not found"})
        return {"item": item}

    return router
