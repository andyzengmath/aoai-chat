"""Conversation transcript routes.

GET    /api/conversations             list summaries
GET    /api/conversations/{id}        full transcript (turns + meta)
DELETE /api/conversations/{id}        remove from disk
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.settings import effective_config, ensure_save_dir
from app.transcript import Transcript, find_by_id, list_summaries

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("")
async def list_conversations() -> dict:
    cfg = effective_config()
    save_dir = ensure_save_dir(cfg)
    items = list_summaries(save_dir)
    return {"count": len(items), "data": items, "save_dir": str(save_dir)}


@router.get("/{cid}")
async def get_conversation(cid: str) -> dict:
    cfg = effective_config()
    save_dir = ensure_save_dir(cfg)
    path = find_by_id(save_dir, cid)
    if path is None:
        raise HTTPException(404, f"conversation {cid!r} not found")
    t = Transcript.load(path)
    return {
        "id": t.meta.id,
        "title": t.meta.title,
        "created_at": t.meta.created_at,
        "updated_at": t.meta.updated_at,
        "endpoint": t.meta.endpoint,
        "deployment": t.meta.deployment,
        "response_id": t.meta.response_id,
        "response_status": t.meta.response_status,
        "incomplete_reason": t.meta.incomplete_reason,
        "response_deployment": t.meta.response_deployment,
        "turn_count": t.meta.turn_count,
        "usage_total": t.meta.usage_total,
        "filename": path.name,
        "turns": [
            {
                "role": tn.role,
                "content": tn.content,
                "timestamp": tn.timestamp,
                "deployment": tn.deployment,
                "response_id": tn.response_id,
                "tokens": tn.tokens,
                "thinking_ms": tn.thinking_ms,
                "reasoning_tokens": tn.reasoning_tokens,
                "reasoning_chars": tn.reasoning_chars,
                "path": tn.path,
                "response_status": tn.response_status,
                "incomplete_reason": tn.incomplete_reason,
            }
            for tn in t.turns
        ],
    }


@router.delete("/{cid}")
async def delete_conversation(cid: str) -> dict:
    cfg = effective_config()
    save_dir = ensure_save_dir(cfg)
    path = find_by_id(save_dir, cid)
    if path is None:
        raise HTTPException(404, f"conversation {cid!r} not found")
    path.unlink()
    return {"deleted": cid, "filename": path.name}
