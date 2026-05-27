"""Azure OpenAI client wrapper.

Phase 3 reality check: `/openai/deployments` does not exist on the AOAI data
plane (returns 404). The data plane offers `/openai/models` which lists the
full *model catalog* for the region (352 versioned model SKUs), not the user's
actual *deployments* — those live in the control plane (ARM). So we let the
user manage their deployment list in `.aoai-chat/config.json` and return that.

Models confirmed against the user's endpoint:
- `gpt-5.4-pro`: Responses API ONLY (Chat Completions returns 400 "unsupported")
- `gpt-5.5`:     Responses API (likely also Chat Completions)

Phase 4: dual-path streaming. Try Responses API for known-capable models, fall
back to Chat Completions for everything else. Server-side compaction keeps the
wire context bounded for 1M-token conversations.
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
from openai import AsyncOpenAI

from app.schemas import ChatRequest
from app.settings import effective_config, normalize_endpoint

log = logging.getLogger(__name__)

# Models explicitly confirmed to expose the Responses API.
KNOWN_RESPONSES_MODELS: set[str] = {"gpt-5.4-pro", "gpt-5.5"}

# Heuristic prefix list for models that very likely expose Responses.
KNOWN_RESPONSES_PREFIXES: tuple[str, ...] = ("gpt-5", "o1", "o3", "o4")

# Trigger server-side compaction when total output crosses this many tokens.
# 200k is OpenAI's recommended threshold; keeps payload small even at 1M context.
COMPACTION_THRESHOLD_TOKENS = 200_000

# ---- httpx timeouts for the OpenAI streaming client -----------------------
# httpx applies the `read` timeout *between* chunks — not as a total cap on
# the stream. AOAI's Responses API sends `keepalive` events periodically
# (~15s) so the gap between chunks during long reasoning is always small.
# A 30-minute read timeout (default) lets total stream time run hours while
# still catching genuine network stalls. Both axes are env-configurable.
_READ_TIMEOUT = float(os.getenv("AOAI_READ_TIMEOUT", "1800"))  # seconds
_CONNECT_TIMEOUT = float(os.getenv("AOAI_CONNECT_TIMEOUT", "15"))  # seconds


def supports_responses_api(model_or_id: str) -> bool:
    name = (model_or_id or "").lower()
    if not name:
        return False
    if name in {m.lower() for m in KNOWN_RESPONSES_MODELS}:
        return True
    return any(name.startswith(p) for p in KNOWN_RESPONSES_PREFIXES)


@dataclass(frozen=True)
class Deployment:
    id: str
    model: str
    supports_responses_api: bool


def list_known_deployments() -> list[Deployment]:
    cfg = effective_config()
    items: list[Deployment] = []
    seen: set[str] = set()
    for raw in cfg.known_deployments:
        name = (raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        items.append(
            Deployment(
                id=name,
                model=name,
                supports_responses_api=supports_responses_api(name),
            )
        )
    return items


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


class ResponsesUnsupportedError(Exception):
    """The deployment exists but does not expose the Responses API."""


def _make_client() -> AsyncOpenAI:
    """Build an `AsyncOpenAI` client pointed at AOAI's v1 unified path.

    Why this shape (vs `AsyncAzureOpenAI`):
    - The `/openai/v1/` path is api-version-free, sidestepping the
      "Responses API requires api-version 2025-03-01-preview+" gate.
    - We pre-fetch the bearer token (sync) and pass it as a string, because
      `AsyncOpenAI(api_key=<callable>)` awaits the callable's return value
      and our sync `azure-identity` provider returns a `str`, causing
      "object str can't be used in 'await' expression".
    - A fresh token is acquired per `_make_client()` invocation; since
      clients are created per chat request and AAD tokens live ~60 min,
      this comfortably outlasts any single stream.

    Timeouts:
    - The default openai SDK timeout (600s) is the `read` timeout between
      chunks. That's fine for chat but not for gpt-5.4-pro on `xhigh` effort,
      where the gap from `start` to first reasoning chunk can be many minutes
      on heavy prompts.
    - We pass an `httpx.Timeout` with `read` defaulting to 30 min,
      env-configurable via `AOAI_READ_TIMEOUT`. Because `read` is between
      chunks (not total), total stream length is effectively uncapped —
      a 3-hour reasoning session is fine as long as Azure's keepalive
      events arrive every <30 min, which they reliably do.
    - `max_retries=0`: SDK auto-retry would re-bill the stream and double
      the latency on transient errors. We surface errors to the UI instead.
    """
    from app.auth import make_token_provider

    cfg = effective_config()
    endpoint = normalize_endpoint(cfg.endpoint)
    token = make_token_provider(cfg.token_scope or None)()
    return AsyncOpenAI(
        base_url=f"{endpoint}openai/v1/",
        api_key=token,
        timeout=httpx.Timeout(
            connect=_CONNECT_TIMEOUT,
            read=_READ_TIMEOUT,
            write=60.0,
            pool=10.0,
        ),
        max_retries=0,
    )


def _usage_dump(usage: Any) -> dict | None:
    if usage is None:
        return None
    try:
        if hasattr(usage, "model_dump"):
            return usage.model_dump()
        if isinstance(usage, dict):
            return dict(usage)
        return {k: getattr(usage, k) for k in ("input_tokens", "output_tokens", "total_tokens")}
    except Exception:
        return None


async def stream_response(req: ChatRequest) -> AsyncIterator[dict]:
    """Stream a turn. Yields dict events: start | delta | done | error | fallback."""
    cfg = effective_config()
    if not cfg.endpoint:
        yield {
            "type": "error",
            "error": "not_configured",
            "message": "Endpoint not set. Open Settings and paste your AOAI endpoint URL.",
        }
        return

    use_responses = supports_responses_api(req.deployment)
    if use_responses:
        try:
            async for ev in _stream_responses(req):
                yield ev
            return
        except ResponsesUnsupportedError as e:
            log.info("Responses unsupported for %s: %s", req.deployment, e)
            yield {
                "type": "fallback",
                "from": "responses",
                "to": "chat",
                "reason": str(e)[:200],
            }

    async for ev in _stream_chat(req):
        yield ev


async def _stream_responses(req: ChatRequest) -> AsyncIterator[dict]:
    client = _make_client()
    kwargs: dict[str, Any] = {
        "model": req.deployment,
        "input": req.content,
        "stream": True,
        "store": True,  # required for previous_response_id chaining
        "context_management": [
            {"type": "compaction", "compact_threshold": COMPACTION_THRESHOLD_TOKENS}
        ],
    }
    if req.previous_response_id:
        kwargs["previous_response_id"] = req.previous_response_id
    if req.instructions:
        kwargs["instructions"] = req.instructions
    if req.reasoning_effort:
        # `summary: "auto"` asks the model to emit a streamable reasoning summary
        # via `response.reasoning_summary*.delta` events so the user can watch
        # the model think rather than staring at a static "thinking…" placeholder.
        kwargs["reasoning"] = {"effort": req.reasoning_effort, "summary": "auto"}
    if req.max_output_tokens:
        kwargs["max_output_tokens"] = req.max_output_tokens

    try:
        stream = await client.responses.create(**kwargs)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        low = msg.lower()
        # Only fall back when the *deployment itself* doesn't expose Responses.
        # Don't fall back for "Unsupported value: 'X'" (invalid parameter) or
        # "Unsupported model" — those are user/config errors we should surface.
        is_responses_disabled = (
            "operation is unsupported" in low
            or "operation not supported" in low
            or ("responses" in low and "not" in low and "support" in low)
        )
        is_value_error = "unsupported value" in low or "invalid_request_error" in low
        if is_responses_disabled and not is_value_error:
            raise ResponsesUnsupportedError(msg) from e
        # Surface non-recoverable errors directly to the client.
        yield {"type": "error", "error": "responses_create_failed", "message": msg[:500]}
        return

    response_id: str | None = None
    usage: dict | None = None
    try:
        async for event in stream:
            ev_type = getattr(event, "type", "") or ""
            if ev_type == "response.created":
                resp = getattr(event, "response", None)
                response_id = getattr(resp, "id", None) if resp else None
                yield {"type": "start", "response_id": response_id, "path": "responses"}
            elif ev_type == "response.output_text.delta":
                delta = getattr(event, "delta", "") or ""
                if delta:
                    yield {"type": "delta", "text": delta}
            elif "reasoning_summary" in ev_type and ev_type.endswith(".delta"):
                # Either `response.reasoning_summary.delta` or
                # `response.reasoning_summary_text.delta` depending on api version.
                delta = getattr(event, "delta", "") or ""
                if delta:
                    yield {"type": "reasoning_delta", "text": delta}
            elif "reasoning_summary" in ev_type and ev_type.endswith(".done"):
                yield {"type": "reasoning_done"}
            elif ev_type == "response.completed":
                resp = getattr(event, "response", None)
                if resp is not None:
                    response_id = response_id or getattr(resp, "id", None)
                    usage = _usage_dump(getattr(resp, "usage", None))
                    # Check for non-success terminal status. Azure's Responses
                    # API can complete with status="incomplete" (e.g. when
                    # max_output_tokens is hit before any text comes out) or
                    # "failed" (content filter, internal error, etc.). In both
                    # cases output may be empty and the user sees a ghost
                    # bubble unless we surface it.
                    status = getattr(resp, "status", None)
                    if status and status not in ("completed", None):
                        details = (
                            getattr(resp, "incomplete_details", None)
                            or getattr(resp, "error", None)
                        )
                        reason = ""
                        if details is not None:
                            reason = (
                                getattr(details, "reason", None)
                                or getattr(details, "message", None)
                                or str(details)
                            ) or ""
                        yield {
                            "type": "error",
                            "error": f"response_{status}",
                            "message": (reason or f"Response status: {status}")[:500],
                        }
                        return
                yield {
                    "type": "done",
                    "response_id": response_id,
                    "usage": usage,
                    "path": "responses",
                }
            elif ev_type in ("response.failed", "response.incomplete"):
                resp = getattr(event, "response", None)
                details = None
                if resp is not None:
                    details = (
                        getattr(resp, "incomplete_details", None)
                        or getattr(resp, "error", None)
                    )
                reason = ""
                if details is not None:
                    reason = (
                        getattr(details, "reason", None)
                        or getattr(details, "message", None)
                        or str(details)
                    ) or ""
                status = ev_type.rsplit(".", 1)[-1]
                yield {
                    "type": "error",
                    "error": f"response_{status}",
                    "message": (reason or f"Response {status}")[:500],
                }
                return
            elif ev_type == "error":
                err = getattr(event, "error", None)
                yield {
                    "type": "error",
                    "error": getattr(err, "code", "stream_error") if err else "stream_error",
                    "message": (getattr(err, "message", "") if err else "")[:500],
                }
                return
            elif ev_type == "keepalive" or ev_type == "":
                # Azure sends sparse keepalive events to prove the connection
                # is alive. Forward them so the frontend can reset its stale
                # watchdog and keep the freshness dot green during long
                # internal-reasoning gaps that emit no summary chunks. Empty
                # type events (some SDK versions) treated the same way.
                yield {"type": "keepalive"}
    except Exception as e:  # noqa: BLE001
        yield {"type": "error", "error": "stream_iter_failed", "message": str(e)[:500]}


async def _stream_chat(req: ChatRequest) -> AsyncIterator[dict]:
    client = _make_client()
    messages: list[dict] = []
    if req.instructions:
        messages.append({"role": "system", "content": req.instructions})
    for m in req.history:
        messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": req.content})

    kwargs: dict[str, Any] = {
        "model": req.deployment,
        "messages": messages,
        "stream": True,
    }
    if req.max_output_tokens:
        kwargs["max_completion_tokens"] = req.max_output_tokens

    yield {"type": "start", "response_id": None, "path": "chat"}
    try:
        stream = await client.chat.completions.create(**kwargs)
    except Exception as e:  # noqa: BLE001
        yield {"type": "error", "error": "chat_create_failed", "message": str(e)[:500]}
        return

    final_usage = None
    try:
        async for chunk in stream:
            if not chunk.choices:
                # Some chunks (e.g. usage-only) have no choices.
                if getattr(chunk, "usage", None) is not None:
                    final_usage = _usage_dump(chunk.usage)
                continue
            delta = chunk.choices[0].delta
            text = getattr(delta, "content", None)
            if text:
                yield {"type": "delta", "text": text}
    except Exception as e:  # noqa: BLE001
        yield {"type": "error", "error": "stream_iter_failed", "message": str(e)[:500]}
        return

    yield {"type": "done", "response_id": None, "usage": final_usage, "path": "chat"}
