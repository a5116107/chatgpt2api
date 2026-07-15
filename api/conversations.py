from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from api.support import require_identity
from services.conversation_store import conversation_store


class ConversationCreateRequest(BaseModel):
    title: str = ""
    model: str = "auto"
    source: str = "manual"
    metadata: dict[str, object] = Field(default_factory=dict)


class ConversationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str | None = None
    model: str | None = None
    status: str | None = None
    source: str | None = None
    metadata: dict[str, object] | None = None


class ConversationMessageRequest(BaseModel):
    role: str = "user"
    content: str = Field(..., min_length=1)
    metadata: dict[str, object] = Field(default_factory=dict)


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/conversations")
    async def list_conversations(
        limit: int = Query(default=100, ge=1, le=500),
        include_messages: bool = False,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return {"items": conversation_store.list(identity, limit=limit, include_messages=include_messages)}

    @router.post("/api/conversations")
    async def create_conversation(body: ConversationCreateRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        return {"item": conversation_store.create(identity, title=body.title, model=body.model, source=body.source, metadata=body.metadata)}

    @router.get("/api/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        item = conversation_store.get(identity, conversation_id)
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "conversation not found"})
        return {"item": item}

    @router.post("/api/conversations/{conversation_id}")
    async def update_conversation(conversation_id: str, body: ConversationUpdateRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        item = conversation_store.update(identity, conversation_id, body.model_dump(exclude_none=True))
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "conversation not found"})
        return {"item": item}

    @router.delete("/api/conversations/{conversation_id}")
    async def delete_conversation(conversation_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        if not conversation_store.delete(identity, conversation_id):
            raise HTTPException(status_code=404, detail={"error": "conversation not found"})
        return {"removed": 1}

    @router.post("/api/conversations/{conversation_id}/messages")
    async def append_message(conversation_id: str, body: ConversationMessageRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        item = conversation_store.append_messages(identity, conversation_id, [{
            "role": body.role,
            "content": body.content,
            "metadata": body.metadata,
        }])
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "conversation not found"})
        return {"item": item}

    @router.get("/api/conversations/{conversation_id}/export")
    async def export_conversation(conversation_id: str, format: str = "json", authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        normalized_format = str(format or "json").strip().lower()
        item = conversation_store.get(identity, conversation_id)
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "conversation not found"})
        if normalized_format in {"md", "markdown"}:
            markdown = conversation_store.export_markdown(identity, conversation_id) or ""
            return Response(
                markdown,
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{conversation_id}.md"'},
            )
        return {"item": item}

    return router
