"""POST /api/chat — SSE stream of AOAI response events.

Event types streamed to the client:
- start    {response_id, path, conversation_id, filename}
- delta    {text}
- done     {response_id, usage, path}
- saved    {conversation_id, filename, turn_count}
- error    {error, message}
- fallback {from, to, reason}
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.aoai_client import stream_response
from app.schemas import ChatRequest
from app.settings import effective_config, ensure_save_dir
from app.transcript import Transcript, find_by_id

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


def _open_or_create_transcript(req: ChatRequest) -> Transcript:
    cfg = effective_config()
    save_dir = ensure_save_dir(cfg)
    if req.conversation_id:
        path = find_by_id(save_dir, req.conversation_id)
        if path is not None:
            return Transcript.load(path)
    return Transcript.new(
        save_dir=save_dir,
        first_user_msg=req.content,
        endpoint=cfg.endpoint,
        deployment=req.deployment,
        conversation_id=req.conversation_id,
    )


@router.post("/chat")
async def chat(req: ChatRequest):
    async def event_generator():
        transcript = _open_or_create_transcript(req)
        assistant_buf: list[str] = []
        reasoning_buf: list[str] = []
        final_done: dict | None = None
        had_error = False

        # Prepend a 'meta' event with conversation_id + filename so the client
        # can pin the URL / sidebar entry before any deltas arrive.
        yield {
            "event": "meta",
            "data": json.dumps({
                "type": "meta",
                "conversation_id": transcript.meta.id,
                "filename": transcript.path.name,
                "deployment": req.deployment,
            }),
        }

        async for event in stream_response(req):
            t = event.get("type")
            if t == "delta":
                assistant_buf.append(event.get("text", ""))
            elif t == "reasoning_delta":
                reasoning_buf.append(event.get("text", ""))
            elif t == "done":
                final_done = event
            elif t == "error":
                had_error = True
            yield {
                "event": t or "message",
                "data": json.dumps(event, default=str),
            }

        if final_done and not had_error:
            transcript.append_turn(
                user_text=req.content,
                assistant_text="".join(assistant_buf),
                deployment=req.deployment,
                response_id=(final_done or {}).get("response_id"),
                usage=(final_done or {}).get("usage"),
            )
            try:
                transcript.write_atomic()
                yield {
                    "event": "saved",
                    "data": json.dumps({
                        "type": "saved",
                        "conversation_id": transcript.meta.id,
                        "filename": transcript.path.name,
                        "turn_count": transcript.meta.turn_count,
                    }),
                }
            except Exception as e:  # noqa: BLE001
                log.exception("transcript write failed")
                yield {
                    "event": "error",
                    "data": json.dumps({
                        "type": "error",
                        "error": "transcript_write_failed",
                        "message": str(e)[:300],
                    }),
                }

    return EventSourceResponse(
        event_generator(),
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
