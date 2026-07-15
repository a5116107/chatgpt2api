from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from api.support import require_identity, resolve_image_base_url
from services.content_filter import check_request
from services.feature_flags import require_feature
from services.video_task_service import video_task_service


class VideoGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    prompt: str = Field(..., min_length=1)
    model: str = "sora"
    size: str | None = None
    seconds: int | None = None
    client_task_id: str | None = None


class VideoCancelRequest(BaseModel):
    reason: str = ""


def _parse_ids(ids: str) -> list[str]:
    return [item.strip() for item in ids.split(",") if item.strip()]


def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/v1/videos/generations")
    async def create_video_generation(body: VideoGenerationRequest, request: Request, authorization: str | None = Header(default=None)):
        require_feature("video")
        identity = require_identity(authorization)
        await run_in_threadpool(check_request, body.prompt)
        try:
            return await run_in_threadpool(
                video_task_service.submit_generation,
                identity,
                client_task_id=body.client_task_id or "",
                prompt=body.prompt,
                model=body.model,
                size=body.size or "",
                seconds=body.seconds,
                base_url=resolve_image_base_url(request),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/v1/videos")
    async def list_video_tasks(ids: str = Query(default=""), authorization: str | None = Header(default=None)):
        require_feature("video")
        identity = require_identity(authorization)
        return await run_in_threadpool(video_task_service.list_tasks, identity, _parse_ids(ids))

    @router.get("/v1/videos/{task_id}")
    async def get_video_task(task_id: str, authorization: str | None = Header(default=None)):
        require_feature("video")
        identity = require_identity(authorization)
        item = await run_in_threadpool(video_task_service.get_task, identity, task_id)
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "video task not found"})
        return item

    @router.post("/v1/videos/{task_id}/cancel")
    async def cancel_video_task(task_id: str, _body: VideoCancelRequest | None = None, authorization: str | None = Header(default=None)):
        require_feature("video")
        identity = require_identity(authorization)
        item = await run_in_threadpool(video_task_service.cancel, identity, task_id)
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "video task not found"})
        return item

    @router.get("/v1/videos/{task_id}/content")
    async def get_video_content(task_id: str, authorization: str | None = Header(default=None)):
        require_feature("video")
        identity = require_identity(authorization)
        path = await run_in_threadpool(video_task_service.content_path, identity, task_id)
        if path is None:
            raise HTTPException(status_code=404, detail={"error": "video content not found"})
        return FileResponse(path, media_type="video/mp4", filename=f"{task_id}.mp4")

    return router
