from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from api.image_inputs import parse_image_edit_request, read_image_sources
from api.support import require_identity, resolve_image_base_url
from services.content_filter import check_request, request_shape, request_text
from services.editable_file_task_service import editable_file_task_service
from services.feature_flags import require_feature
from services.log_service import LoggedCall
from services.conversation_store import conversation_store
from services.protocol import (
    anthropic_v1_messages,
    openai_v1_chat_complete,
    openai_v1_image_edit,
    openai_v1_image_generations,
    openai_v1_models,
    openai_v1_response,
    openai_search,
)
from utils.helper import sse_json_stream


class ImageGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str = "gpt-image-2"
    n: int = Field(default=1, ge=1, le=4)
    size: str | None = None
    quality: str = "auto"
    response_format: str = "b64_json"
    history_disabled: bool = True
    stream: bool | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    prompt: str | None = None
    n: int | None = None
    stream: bool | None = None
    modalities: list[str] | None = None
    messages: list[dict[str, object]] | None = None
    conversation_id: str | None = None


class ResponseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    input: object | None = None
    tools: list[dict[str, object]] | None = None
    tool_choice: object | None = None
    stream: bool | None = None
    conversation_id: str | None = None


class AnthropicMessageRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    messages: list[dict[str, object]] | None = None
    system: object | None = None
    stream: bool | None = None


class SearchRequest(BaseModel):
    prompt: str = Field(..., min_length=1)


class EditableFileTaskRequest(BaseModel):
    prompt: str = ""
    base64_images: list[str] = Field(default_factory=list)
    client_task_id: str | None = None


async def filter_or_log(call: LoggedCall, text: str) -> None:
    try:
        await run_in_threadpool(check_request, text)
    except HTTPException as exc:
        call.log("调用失败", status="failed", error=str(exc.detail))
        raise


def _chat_stream_with_persistence(identity: dict[str, object], conversation_id: str, items):
    collected: list[str] = []
    failed = False
    try:
        for item in items:
            delta = conversation_store.record_stream_chunk(identity, conversation_id, "chat", item)
            if delta:
                collected.append(delta)
            yield item
    except Exception as exc:
        failed = True
        conversation_store.finalize_stream(identity, conversation_id, "", error=str(exc))
        raise
    finally:
        if not failed:
            conversation_store.finalize_stream(identity, conversation_id, "".join(collected))


def _response_stream_with_persistence(identity: dict[str, object], conversation_id: str, items):
    collected: list[str] = []
    failed = False
    try:
        for item in items:
            delta = conversation_store.record_stream_chunk(identity, conversation_id, "responses", item)
            if delta:
                collected.append(delta)
            yield item
    except Exception as exc:
        failed = True
        conversation_store.finalize_stream(identity, conversation_id, "", error=str(exc))
        raise
    finally:
        if not failed:
            conversation_store.finalize_stream(identity, conversation_id, "".join(collected))


async def _run_chat_with_conversation(call: LoggedCall, identity: dict[str, object], payload: dict[str, object], request_preview: str):
    conversation_id, _ = await run_in_threadpool(
        conversation_store.ensure_from_chat_payload,
        identity,
        payload,
        request_preview,
    )
    payload["conversation_id"] = conversation_id
    try:
        result = await run_in_threadpool(openai_v1_chat_complete.handle, payload)
    except Exception as exc:
        await run_in_threadpool(conversation_store.record_chat_result, identity, conversation_id, {}, error=str(exc))
        msg = str(exc or "").strip() or "chat failed"
        low = msg.lower()
        if "no available text account" in low:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": {
                        "message": "no available text account",
                        "type": "insufficient_quota",
                        "param": None,
                        "code": "insufficient_quota",
                    }
                },
            ) from exc
        if "token" in low and ("revoked" in low or "invalid" in low or "unauthorized" in low):
            raise HTTPException(
                status_code=401,
                detail={
                    "error": {
                        "message": msg[:300],
                        "type": "invalid_request_error",
                        "param": None,
                        "code": "token_invalidated",
                    }
                },
            ) from exc
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "message": msg[:500],
                    "type": "server_error",
                    "param": None,
                    "code": "upstream_error",
                }
            },
        ) from exc
    if isinstance(result, dict):
        await run_in_threadpool(conversation_store.record_chat_result, identity, conversation_id, result)
        response = await call.run(lambda _result: _result, result)
        if isinstance(response, dict):
            response["conversation_id"] = conversation_id
        return response
    return StreamingResponse(
        sse_json_stream(call.stream(_chat_stream_with_persistence(identity, conversation_id, result))),
        media_type="text/event-stream",
        headers={"X-Conversation-Id": conversation_id},
    )


async def _run_response_with_conversation(call: LoggedCall, identity: dict[str, object], payload: dict[str, object], request_preview: str):
    conversation_id, _ = await run_in_threadpool(
        conversation_store.ensure_from_response_payload,
        identity,
        payload,
        request_preview,
    )
    payload["conversation_id"] = conversation_id
    try:
        result = await run_in_threadpool(openai_v1_response.handle, payload)
    except Exception as exc:
        await run_in_threadpool(conversation_store.record_response_result, identity, conversation_id, {}, error=str(exc))
        raise
    if isinstance(result, dict):
        await run_in_threadpool(conversation_store.record_response_result, identity, conversation_id, result)
        response = await call.run(lambda _result: _result, result)
        if isinstance(response, dict):
            response["conversation_id"] = conversation_id
        return response
    return StreamingResponse(
        sse_json_stream(call.stream(_response_stream_with_persistence(identity, conversation_id, result))),
        media_type="text/event-stream",
        headers={"X-Conversation-Id": conversation_id},
    )


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/v1/models")
    async def list_models(authorization: str | None = Header(default=None)):
        require_identity(authorization)
        try:
            return await run_in_threadpool(openai_v1_models.list_models)
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    @router.post("/v1/images/generations")
    async def generate_images(
            body: ImageGenerationRequest,
            request: Request,
            authorization: str | None = Header(default=None),
    ):
        require_feature("image")
        identity = require_identity(authorization)
        payload = body.model_dump(mode="python")
        payload["base_url"] = resolve_image_base_url(request)
        call = LoggedCall(identity, "/v1/images/generations", body.model, "文生图", request_text=body.prompt)
        await filter_or_log(call, body.prompt)
        return await call.run(openai_v1_image_generations.handle, payload)

    @router.post("/v1/images/edits")
    async def edit_images(
            request: Request,
            authorization: str | None = Header(default=None),
    ):
        require_feature("image")
        identity = require_identity(authorization)
        payload, image_sources, mask_sources = await parse_image_edit_request(request)
        prompt = str(payload["prompt"])
        model = str(payload["model"])
        call = LoggedCall(identity, "/v1/images/edits", model, "图生图", request_text=prompt)
        await filter_or_log(call, prompt)
        payload["images"] = await read_image_sources(image_sources)
        if mask_sources:
            payload["mask"] = await read_image_sources(mask_sources)
        payload["base_url"] = resolve_image_base_url(request)
        return await call.run(openai_v1_image_edit.handle, payload)

    @router.post("/v1/chat/completions")
    async def create_chat_completion(body: ChatCompletionRequest, authorization: str | None = Header(default=None)):
        require_feature("chat")
        identity = require_identity(authorization)
        payload = body.model_dump(mode="python")
        model = str(payload.get("model") or "auto")
        request_preview = request_text(payload.get("prompt"), payload.get("messages"))
        call = LoggedCall(
            identity,
            "/v1/chat/completions",
            model,
            "文本生成",
            request_text=request_preview,
            request_shape=request_shape(payload.get("messages")),
        )
        await filter_or_log(call, request_preview)
        return await _run_chat_with_conversation(call, identity, payload, request_preview)

    @router.post("/v1/responses")
    async def create_response(body: ResponseCreateRequest, authorization: str | None = Header(default=None)):
        require_feature("chat")
        identity = require_identity(authorization)
        payload = body.model_dump(mode="python")
        model = str(payload.get("model") or "auto")
        request_preview = request_text(payload.get("input"), payload.get("instructions"))
        call = LoggedCall(
            identity,
            "/v1/responses",
            model,
            "Responses",
            request_text=request_preview,
            request_shape=request_shape(payload.get("input")),
        )
        await filter_or_log(call, request_preview)
        return await _run_response_with_conversation(call, identity, payload, request_preview)

    @router.post("/v1/messages")
    async def create_message(
            body: AnthropicMessageRequest,
            authorization: str | None = Header(default=None),
            x_api_key: str | None = Header(default=None, alias="x-api-key"),
            anthropic_version: str | None = Header(default=None, alias="anthropic-version"),
    ):
        require_feature("chat")
        identity = require_identity(authorization or (f"Bearer {x_api_key}" if x_api_key else None))
        payload = body.model_dump(mode="python")
        model = str(payload.get("model") or "auto")
        request_preview = request_text(payload.get("system"), payload.get("messages"), payload.get("tools"))
        call = LoggedCall(identity, "/v1/messages", model, "Messages", request_text=request_preview)
        await filter_or_log(call, request_preview)
        return await call.run(anthropic_v1_messages.handle, payload, sse="anthropic")

    @router.post("/v1/search")
    async def search(body: SearchRequest, authorization: str | None = Header(default=None)):
        require_feature("chat")
        identity = require_identity(authorization)
        call = LoggedCall(identity, "/v1/search", openai_search.MODEL, "搜索", request_text=body.prompt)
        await filter_or_log(call, body.prompt)
        return await call.run(openai_search.handle, body.model_dump(mode="python"))

    @router.get("/v1/editable-file-tasks")
    async def list_editable_file_tasks(ids: str = "", authorization: str | None = Header(default=None)):
        require_feature("chat")
        identity = require_identity(authorization)
        task_ids = [item.strip() for item in ids.split(",") if item.strip()]
        return await run_in_threadpool(editable_file_task_service.list_tasks, identity, task_ids)

    @router.get("/files/{file_path:path}")
    async def download_editable_file(file_path: str):
        try:
            path = await run_in_threadpool(editable_file_task_service.public_file_path, file_path)
        except Exception as exc:
            raise HTTPException(status_code=404, detail={"error": "file not found"}) from exc
        return FileResponse(path, filename=path.name)

    @router.post("/v1/ppt/generations")
    async def create_ppt_task(body: EditableFileTaskRequest, request: Request, authorization: str | None = Header(default=None)):
        require_feature("chat")
        identity = require_identity(authorization)
        await filter_or_log(LoggedCall(identity, "/v1/ppt/generations", "gpt-5-5-thinking", "PPT生成任务", request_text=body.prompt), body.prompt)
        return await run_in_threadpool(
            editable_file_task_service.submit_ppt,
            identity,
            client_task_id=body.client_task_id or "",
            prompt=body.prompt,
            base64_images=body.base64_images,
            base_url=resolve_image_base_url(request),
        )

    @router.post("/v1/psd/generations")
    async def create_psd_task(body: EditableFileTaskRequest, request: Request, authorization: str | None = Header(default=None)):
        require_feature("chat")
        identity = require_identity(authorization)
        await filter_or_log(LoggedCall(identity, "/v1/psd/generations", "gpt-5-5-thinking", "PSD生成任务", request_text=body.prompt), body.prompt)
        return await run_in_threadpool(
            editable_file_task_service.submit_psd,
            identity,
            client_task_id=body.client_task_id or "",
            prompt=body.prompt,
            base64_images=body.base64_images,
            base_url=resolve_image_base_url(request),
        )

    return router

# PATCH_MARKER chat_no_available_structured_r8
