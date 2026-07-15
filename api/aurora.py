from __future__ import annotations

import json
import time
from typing import Any, Iterator

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from api.support import require_identity
from services.account_service import account_service
from services.openai_backend_api import OpenAIBackendAPI
from services.protocol.conversation import ConversationRequest, stream_text_deltas
from services.protocol import openai_v1_models


def _body_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    messages = body.get("messages")
    if isinstance(messages, list) and messages:
        return [item for item in messages if isinstance(item, dict)]
    prompt = str(body.get("prompt") or "").strip()
    if prompt:
        return [{"role": "user", "content": prompt}]
    raise HTTPException(status_code=400, detail={"error": "messages or prompt is required"})


def _chatgpt_sse(body: dict[str, Any]) -> Iterator[str]:
    model = str(body.get("model") or "auto").strip() or "auto"
    request = ConversationRequest(model=model, messages=_body_messages(body))
    conversation_id = str(body.get("conversation_id") or "")
    message_id = f"msg_{int(time.time() * 1000)}"
    full_text = ""
    backend = OpenAIBackendAPI(access_token=account_service.get_text_access_token())
    try:
        for delta in stream_text_deltas(backend, request):
            if not delta:
                continue
            full_text += delta
            payload = {
                "type": "message",
                "message": {
                    "id": message_id,
                    "author": {"role": "assistant"},
                    "content": {"content_type": "text", "parts": [full_text]},
                    "metadata": {},
                },
                "conversation_id": conversation_id,
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        done = {
            "type": "message_done",
            "message": {
                "id": message_id,
                "author": {"role": "assistant"},
                "content": {"content_type": "text", "parts": [full_text]},
                "metadata": {"finish_reason": "stop"},
            },
            "conversation_id": conversation_id,
        }
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as exc:
        error = {"type": "error", "error": {"message": str(exc), "code": "upstream_error"}}
        yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"


def _models_payload() -> dict[str, Any]:
    data = openai_v1_models.list_models()
    models = [
        {
            "slug": item.get("id"),
            "title": item.get("id"),
            "description": "",
            "max_tokens": 0,
        }
        for item in data.get("data", [])
        if isinstance(item, dict)
    ]
    return {"models": models, **data}


def _with_text_backend(fn):
    token = account_service.get_text_access_token()
    backend = OpenAIBackendAPI(access_token=token)
    try:
        return fn(backend)
    finally:
        backend.close()


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/backend-api/models")
    @router.get("/backend-anon/models")
    async def backend_models(authorization: str | None = Header(default=None)):
        require_identity(authorization)
        return await run_in_threadpool(_models_payload)

    @router.post("/backend-api/conversation")
    @router.post("/backend-anon/conversation")
    async def backend_conversation(request: Request, authorization: str | None = Header(default=None)):
        require_identity(authorization)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail={"error": "JSON object body is required"})
        return StreamingResponse(_chatgpt_sse(body), media_type="text/event-stream")

    @router.get("/backend-api/conversation/{conversation_id}")
    async def get_conversation(conversation_id: str, authorization: str | None = Header(default=None)):
        require_identity(authorization)
        return await run_in_threadpool(lambda: _with_text_backend(lambda backend: backend._get_conversation(conversation_id)))

    @router.get("/backend-api/conversations")
    async def list_conversations(limit: int = 20, authorization: str | None = Header(default=None)):
        require_identity(authorization)
        safe_limit = max(1, min(int(limit or 20), 100))
        items = await run_in_threadpool(lambda: _with_text_backend(lambda backend: backend._list_recent_conversations(limit=safe_limit)))
        return {"items": items, "limit": safe_limit}

    @router.get("/backend-api/accounts/check/v4-2023-04-27")
    async def account_check(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        return {"account_plan": "compat", "user": {"id": identity.subject_id, "role": identity.role, "name": identity.name}}

    return router
