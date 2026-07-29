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
import re

import anyio
from fastapi import APIRouter, HTTPException
from openai import APIConnectionError, APIStatusError, APITimeoutError
from sse_starlette.sse import EventSourceResponse

from app.aoai_client import (
    cancel_response as cancel_aoai_response,
)
from app.aoai_client import (
    stream_response,
)
from app.schemas import ChatRequest
from app.settings import effective_config, ensure_save_dir
from app.transcript import Transcript, find_by_id

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])
_RESPONSE_ID_RE = re.compile(r"^resp_[A-Za-z0-9_-]{1,200}$")


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
        final_incomplete: dict | None = None
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

        response_events = stream_response(req)
        try:
            async for event in response_events:
                t = event.get("type")
                if t == "delta":
                    assistant_buf.append(event.get("text", ""))
                elif t == "reasoning_delta":
                    reasoning_buf.append(event.get("text", ""))
                elif t == "done":
                    final_done = event
                    continue
                elif t == "incomplete":
                    final_incomplete = event
                    continue
                elif t == "error":
                    had_error = True
                yield {
                    "event": t or "message",
                    "data": json.dumps(event, default=str),
                }
        finally:
            with anyio.CancelScope(shield=True):
                await response_events.aclose()

        final_response = final_done or final_incomplete
        if final_response and not had_error:
            transcript.append_turn(
                user_text=req.content,
                assistant_text="".join(assistant_buf),
                deployment=req.deployment,
                response_id=final_response.get("response_id"),
                usage=final_response.get("usage"),
                response_status=(
                    "incomplete" if final_incomplete else "completed"
                ),
                incomplete_reason=(
                    final_incomplete.get("reason")
                    if final_incomplete
                    else None
                ),
            )
            try:
                transcript.write_atomic()
                yield {
                    "event": final_response["type"],
                    "data": json.dumps(final_response, default=str),
                }
                yield {
                    "event": "saved",
                    "data": json.dumps({
                        "type": "saved",
                        "conversation_id": transcript.meta.id,
                        "filename": transcript.path.name,
                        "turn_count": transcript.meta.turn_count,
                        "status": (
                            "incomplete" if final_incomplete else "completed"
                        ),
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


@router.post("/responses/{response_id}/cancel")
async def cancel_response(response_id: str) -> dict[str, str]:
    if not _RESPONSE_ID_RE.fullmatch(response_id):
        raise HTTPException(400, "invalid response ID")
    try:
        return await cancel_aoai_response(response_id)
    except APIStatusError as e:
        raise HTTPException(
            e.status_code,
            f"Azure response cancellation failed ({e.status_code})",
        ) from e
    except (APIConnectionError, APITimeoutError) as e:
        raise HTTPException(502, "Azure response cancellation unavailable") from e
