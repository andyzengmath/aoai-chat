"""Azure OpenAI client wrapper.

Phase 3 reality check: `/openai/deployments` does not exist on the AOAI data
plane (returns 404). The data plane offers `/openai/models` which lists the
full *model catalog* for the region (352 versioned model SKUs), not the user's
actual *deployments* — those live in the control plane (ARM). So we let the
user manage their deployment list in `.aoai-chat/config.json` and return that.

Models confirmed against the user's endpoint:
- `gpt-5.6-sol`: Responses API; max effort + standard/pro modes
- `gpt-5.4-pro`: Responses API ONLY (Chat Completions returns 400 "unsupported")
- `gpt-5.5`:     Responses API (likely also Chat Completions)

Phase 4: dual-path streaming. Try Responses API for known-capable models, fall
back to Chat Completions for everything else. Server-side compaction keeps the
wire context bounded for 1M-token conversations.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import anyio
import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from app.schemas import ChatRequest
from app.settings import (
    effective_config,
    validate_azure_endpoint,
    validate_token_scope,
)

log = logging.getLogger(__name__)

# Models explicitly confirmed to expose the Responses API.
KNOWN_RESPONSES_MODELS: set[str] = {"gpt-5.6-sol", "gpt-5.4-pro", "gpt-5.5"}

# Heuristic prefix list for models that very likely expose Responses.
KNOWN_RESPONSES_PREFIXES: tuple[str, ...] = ("gpt-5", "o1", "o3", "o4")

# Trigger server-side compaction when total output crosses this many tokens.
# 200k is OpenAI's recommended threshold; keeps payload small even at 1M context.
COMPACTION_THRESHOLD_TOKENS = 200_000
BACKGROUND_POLL_INTERVAL_SECONDS = 2.0
BACKGROUND_RETRIEVE_MAX_ATTEMPTS = 4
BACKGROUND_RESPONSE_ID_WAIT_SECONDS = 60.0
BACKGROUND_SERVER_ERROR_RETRY_DELAYS_SECONDS = (2.0, 5.0)
BACKGROUND_CANCEL_TIMEOUT_SECONDS = float(
    os.getenv("AOAI_CANCEL_TIMEOUT", "10")
)

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
class ModelCapabilities:
    model_version: str | None = None
    reasoning_efforts: tuple[str, ...] = ()
    reasoning_modes: tuple[str, ...] = ()
    context_window_tokens: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None


GPT56_CAPABILITIES = ModelCapabilities(
    model_version="2026-07-09",
    reasoning_efforts=("none", "low", "medium", "high", "xhigh", "max"),
    reasoning_modes=("standard", "pro"),
    context_window_tokens=1_050_000,
    max_input_tokens=922_000,
    max_output_tokens=128_000,
)

MODEL_CAPABILITIES: dict[str, ModelCapabilities] = {
    "gpt-5.6": GPT56_CAPABILITIES,
    "gpt-5.6-sol": GPT56_CAPABILITIES,
    "gpt-5.6-terra": GPT56_CAPABILITIES,
    "gpt-5.6-luna": GPT56_CAPABILITIES,
    "gpt-5.4-pro": ModelCapabilities(
        reasoning_efforts=("medium", "high", "xhigh"),
        context_window_tokens=1_050_000,
        max_input_tokens=922_000,
        max_output_tokens=128_000,
    ),
}

DEFAULT_RESPONSES_CAPABILITIES = ModelCapabilities()


def get_model_capabilities(model_or_id: str) -> ModelCapabilities:
    name = (model_or_id or "").strip().lower()
    if name in MODEL_CAPABILITIES:
        return MODEL_CAPABILITIES[name]
    if supports_responses_api(name):
        return DEFAULT_RESPONSES_CAPABILITIES
    return ModelCapabilities()


@dataclass(frozen=True)
class Deployment:
    id: str
    model: str
    model_version: str | None
    supports_responses_api: bool
    reasoning_efforts: tuple[str, ...]
    reasoning_modes: tuple[str, ...]
    context_window_tokens: int | None
    max_input_tokens: int | None
    max_output_tokens: int | None


def list_known_deployments() -> list[Deployment]:
    cfg = effective_config()
    items: list[Deployment] = []
    seen: set[str] = set()
    for raw in cfg.known_deployments:
        name = (raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        capabilities = get_model_capabilities(name)
        items.append(
            Deployment(
                id=name,
                model=name,
                model_version=capabilities.model_version,
                supports_responses_api=supports_responses_api(name),
                reasoning_efforts=capabilities.reasoning_efforts,
                reasoning_modes=capabilities.reasoning_modes,
                context_window_tokens=capabilities.context_window_tokens,
                max_input_tokens=capabilities.max_input_tokens,
                max_output_tokens=capabilities.max_output_tokens,
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
    - The OpenAI async client expects an awaitable token provider. Azure
      Identity exposes a synchronous provider, so we bridge it with
      `asyncio.to_thread`. The SDK invokes it for every HTTP request, allowing
      polling and cancellation to refresh credentials during multi-hour jobs.

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
    endpoint = validate_azure_endpoint(cfg.endpoint)
    token_scope = validate_token_scope(cfg.token_scope, endpoint)
    azure_token_provider = make_token_provider(token_scope or None)

    async def token_provider() -> str:
        return await asyncio.to_thread(azure_token_provider)

    return AsyncOpenAI(
        base_url=f"{endpoint}openai/v1/",
        api_key=token_provider,
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


def _response_error_message(response: Any, status: str) -> str:
    details = (
        getattr(response, "incomplete_details", None)
        or getattr(response, "error", None)
    )
    if details is None:
        return f"Response {status}"
    return (
        getattr(details, "reason", None)
        or getattr(details, "message", None)
        or str(details)
        or f"Response {status}"
    )


def _missing_output_text(final_text: str, streamed_text: str) -> str | None:
    if not final_text.startswith(streamed_text):
        return None
    return final_text[len(streamed_text):]


def _response_visible_text(response: Any) -> str:
    parts: list[str] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", None) or []:
            content_type = getattr(content, "type", None)
            if content_type == "output_text":
                text = getattr(content, "text", "") or ""
            elif content_type == "refusal":
                text = getattr(content, "refusal", "") or ""
            else:
                continue
            if text:
                parts.append(text)
    if parts:
        return "".join(parts)
    return getattr(response, "output_text", "") or ""


def _response_failure_details(response: Any) -> tuple[str, str]:
    error = getattr(response, "error", None)
    code = getattr(error, "code", None) or "response_failed"
    message = (
        getattr(error, "message", None)
        or str(error or "")
        or "Response failed"
    )
    return code, message[:500]


def _is_retryable_zero_work_server_error(response: Any) -> bool:
    code, _ = _response_failure_details(response)
    return (
        getattr(response, "status", None) == "failed"
        and code == "server_error"
        and not (getattr(response, "output_text", "") or "")
        and not (getattr(response, "output", None) or [])
        and getattr(response, "usage", None) is None
    )


_NON_WORK_STREAM_EVENTS = {
    "",
    "error",
    "keepalive",
    "response.created",
    "response.in_progress",
    "response.queued",
    "response.completed",
    "response.failed",
    "response.incomplete",
}


def _stream_event_indicates_model_work(event_type: str) -> bool:
    return event_type not in _NON_WORK_STREAM_EVENTS


def _is_transient_response_error(error: Exception) -> bool:
    if isinstance(error, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    return isinstance(error, APIStatusError) and (
        error.status_code in {408, 409, 429} or error.status_code >= 500
    )


def _background_retry_delay(error: Exception, attempt: int) -> float:
    if isinstance(error, APIStatusError):
        retry_after_ms = error.response.headers.get("retry-after-ms")
        retry_after = error.response.headers.get("retry-after")
        try:
            if retry_after_ms:
                return min(60.0, max(0.0, float(retry_after_ms) / 1000))
            if retry_after:
                return min(60.0, max(0.0, float(retry_after)))
        except ValueError:
            pass
    return BACKGROUND_POLL_INTERVAL_SECONDS * attempt


async def _cancel_background_response(
    client: AsyncOpenAI,
    response_id: str,
) -> None:
    try:
        with anyio.move_on_after(
            BACKGROUND_CANCEL_TIMEOUT_SECONDS,
            shield=True,
        ) as scope:
            await client.responses.cancel(response_id)
        if scope.cancel_called:
            log.warning(
                "timed out cancelling background response %s",
                response_id,
            )
    except Exception:  # noqa: BLE001
        log.exception("failed to cancel background response %s", response_id)


async def cancel_response(response_id: str) -> dict[str, str]:
    client = _make_client()
    with anyio.fail_after(BACKGROUND_CANCEL_TIMEOUT_SECONDS):
        response = await client.responses.cancel(response_id)
    return {
        "response_id": getattr(response, "id", response_id),
        "status": getattr(response, "status", "cancelled"),
    }


async def _response_id_from_stream(
    stream: AsyncIterator[Any],
    first_event_task: asyncio.Task[Any],
) -> str:
    event = await first_event_task
    while True:
        response = getattr(event, "response", None)
        response_id = getattr(response, "id", None) if response else None
        if response_id:
            return response_id
        event = await anext(stream)


async def _cancel_stream_when_id_available(
    client: AsyncOpenAI,
    stream: AsyncIterator[Any],
    first_event_task: asyncio.Task[Any],
) -> None:
    with anyio.CancelScope(shield=True):
        try:
            response_id = await asyncio.wait_for(
                _response_id_from_stream(stream, first_event_task),
                timeout=BACKGROUND_RESPONSE_ID_WAIT_SECONDS,
            )
        except (TimeoutError, StopAsyncIteration):
            log.warning("background stream ended before a response ID was available")
            return
        except Exception:  # noqa: BLE001
            log.exception("failed while waiting for a background response ID")
            return
        await _cancel_background_response(client, response_id)


async def _prepend_stream_event(
    first_event: Any,
    stream: AsyncIterator[Any],
) -> AsyncIterator[Any]:
    yield first_event
    async for event in stream:
        yield event


async def _recover_background_response(
    client: AsyncOpenAI,
    response_id: str,
    streamed_text: str,
    streamed_model_work_seen: bool = False,
) -> AsyncIterator[dict]:
    """Poll a background response after its SSE stream ends prematurely."""
    retrieve_failures = 0
    try:
        while True:
            try:
                response = await client.responses.retrieve(response_id)
                retrieve_failures = 0
            except Exception as e:  # noqa: BLE001
                retrieve_failures += 1
                if (
                    _is_transient_response_error(e)
                    and retrieve_failures < BACKGROUND_RETRIEVE_MAX_ATTEMPTS
                ):
                    log.warning(
                        "transient retrieval failure for %s (%d/%d): %s",
                        response_id,
                        retrieve_failures,
                        BACKGROUND_RETRIEVE_MAX_ATTEMPTS,
                        e,
                    )
                    yield {"type": "keepalive"}
                    await asyncio.sleep(_background_retry_delay(e, retrieve_failures))
                    continue
                await _cancel_background_response(client, response_id)
                yield {
                    "type": "error",
                    "error": "background_retrieve_failed",
                    "message": str(e)[:500],
                }
                return

            status = getattr(response, "status", None)
            if status in {"queued", "in_progress"}:
                yield {"type": "keepalive"}
                await asyncio.sleep(BACKGROUND_POLL_INTERVAL_SECONDS)
                continue

            if status == "completed":
                final_text = _response_visible_text(response)
                missing_text = _missing_output_text(final_text, streamed_text)
                if missing_text is None:
                    yield {
                        "type": "error",
                        "error": "background_output_mismatch",
                        "message": (
                            "Recovered output did not match the partial stream. "
                            "Retry the request."
                        ),
                    }
                    return
                if missing_text:
                    yield {"type": "delta", "text": missing_text}
                if not final_text:
                    yield {
                        "type": "error",
                        "error": "background_completed_empty",
                        "message": "Background response completed without output.",
                    }
                    return
                yield {
                    "type": "done",
                    "response_id": response_id,
                    "usage": _usage_dump(getattr(response, "usage", None)),
                    "path": "responses",
                }
                return

            if status == "incomplete":
                reason = _response_error_message(response, status)[:500]
                if reason != "max_output_tokens":
                    yield {
                        "type": "error",
                        "error": "response_incomplete",
                        "message": reason,
                    }
                    return
                final_text = _response_visible_text(response)
                missing_text = _missing_output_text(final_text, streamed_text)
                if missing_text is None:
                    yield {
                        "type": "error",
                        "error": "background_output_mismatch",
                        "message": (
                            "Recovered output did not match the partial stream. "
                            "Retry the request."
                        ),
                    }
                    return
                if missing_text:
                    yield {"type": "delta", "text": missing_text}
                yield {
                    "type": "incomplete",
                    "response_id": response_id,
                    "usage": _usage_dump(getattr(response, "usage", None)),
                    "path": "responses",
                    "reason": reason,
                }
                return

            if status == "failed":
                code, message = _response_failure_details(response)
                if (
                    _is_retryable_zero_work_server_error(response)
                    and not streamed_text
                    and not streamed_model_work_seen
                ):
                    yield {
                        "type": "_retryable_server_error",
                        "message": message,
                    }
                    return
                yield {
                    "type": "error",
                    "error": code,
                    "message": message,
                }
                return

            terminal_status = status or "unknown"
            yield {
                "type": "error",
                "error": f"response_{terminal_status}",
                "message": _response_error_message(response, terminal_status)[:500],
            }
            return
    except (asyncio.CancelledError, GeneratorExit):
        await _cancel_background_response(client, response_id)
        raise


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
        capabilities = get_model_capabilities(req.deployment)
        if (
            req.reasoning_effort
            and capabilities.reasoning_efforts
            and req.reasoning_effort not in capabilities.reasoning_efforts
        ):
            yield {
                "type": "error",
                "error": "unsupported_reasoning_effort",
                "message": (
                    f"{req.deployment} supports reasoning efforts: "
                    f"{', '.join(capabilities.reasoning_efforts)}"
                ),
            }
            return
        if req.reasoning_mode and req.reasoning_mode not in capabilities.reasoning_modes:
            yield {
                "type": "error",
                "error": "unsupported_reasoning_mode",
                "message": f"{req.deployment} does not support Pro reasoning mode.",
            }
            return
        response_events = _stream_responses(req)
        try:
            async for ev in response_events:
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
        finally:
            await response_events.aclose()

    chat_events = _stream_chat(req)
    try:
        async for ev in chat_events:
            yield ev
    finally:
        await chat_events.aclose()


async def _stream_responses(req: ChatRequest) -> AsyncIterator[dict]:
    client = _make_client()
    max_attempts = 1 + len(BACKGROUND_SERVER_ERROR_RETRY_DELAYS_SECONDS)
    for attempt in range(1, max_attempts + 1):
        retryable_failure: dict | None = None
        attempt_events = _stream_responses_once(req, client)
        try:
            async for event in attempt_events:
                if event.get("type") == "_retryable_server_error":
                    retryable_failure = event
                else:
                    yield event
        finally:
            await attempt_events.aclose()

        if retryable_failure is None:
            return
        if attempt >= max_attempts:
            yield {
                "type": "error",
                "error": "response_failed",
                "message": retryable_failure["message"],
            }
            return

        delay = BACKGROUND_SERVER_ERROR_RETRY_DELAYS_SECONDS[attempt - 1]
        yield {
            "type": "retrying",
            "attempt": attempt + 1,
            "max_attempts": max_attempts,
            "delay_seconds": delay,
            "reason": "server_error",
        }
        await asyncio.sleep(delay)


async def _stream_responses_once(
    req: ChatRequest,
    client: AsyncOpenAI,
) -> AsyncIterator[dict]:
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
    if req.reasoning_effort or req.reasoning_mode:
        # `summary: "auto"` asks the model to emit a streamable reasoning summary
        # via `response.reasoning_summary*.delta` events so the user can watch
        # the model think rather than staring at a static "thinking…" placeholder.
        reasoning: dict[str, str] = {"summary": "auto"}
        if req.reasoning_effort:
            reasoning["effort"] = req.reasoning_effort
        if req.reasoning_mode:
            reasoning["mode"] = req.reasoning_mode
        kwargs["reasoning"] = reasoning
        # `background=True` runs the response asynchronously on Azure's side,
        # untangling it from any single HTTP connection's lifetime. This avoids
        # "peer closed connection without sending complete message body" errors
        # on requests that exceed Azure's per-connection timeout (~30 min for
        # sync requests). The trade-off is significantly higher time-to-first-
        # token, so we only opt in for the effort levels that actually need it
        # — medium/low/minimal complete fast enough to stay synchronous.
        if req.reasoning_effort in ("high", "xhigh", "max") or req.reasoning_mode == "pro":
            kwargs["background"] = True
    if req.max_output_tokens:
        kwargs["max_output_tokens"] = req.max_output_tokens
    background_enabled = bool(kwargs.get("background"))
    response_id: str | None = None

    try:
        if background_enabled:
            create_task = asyncio.create_task(client.responses.create(**kwargs))
            try:
                stream = await asyncio.shield(create_task)
            except asyncio.CancelledError as cancellation:
                with anyio.CancelScope(shield=True):
                    try:
                        stream = await asyncio.wait_for(
                            create_task,
                            timeout=BACKGROUND_RESPONSE_ID_WAIT_SECONDS,
                        )
                        first_event_task = asyncio.create_task(anext(stream))
                        await _cancel_stream_when_id_available(
                            client,
                            stream,
                            first_event_task,
                        )
                    except Exception:  # noqa: BLE001
                        log.exception(
                            "failed to finish background response creation "
                            "after cancellation"
                        )
                raise cancellation
        else:
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

    event_stream = stream
    if background_enabled:
        first_event_task = asyncio.create_task(anext(stream))
        try:
            first_event = await asyncio.shield(first_event_task)
        except asyncio.CancelledError as cancellation:
            await _cancel_stream_when_id_available(
                client,
                stream,
                first_event_task,
            )
            raise cancellation
        except StopAsyncIteration:
            yield {
                "type": "error",
                "error": "stream_ended_without_terminal",
                "message": "Background response ended before it emitted a response ID.",
            }
            return
        except Exception as e:  # noqa: BLE001
            yield {
                "type": "error",
                "error": "stream_iter_failed",
                "message": str(e)[:500],
            }
            return
        first_response = getattr(first_event, "response", None)
        response_id = (
            getattr(first_response, "id", None)
            if first_response is not None
            else None
        )
        event_stream = _prepend_stream_event(first_event, stream)

    usage: dict | None = None
    streamed_text_parts: list[str] = []
    streamed_model_work_seen = False
    try:
        async for event in event_stream:
            ev_type = getattr(event, "type", "") or ""
            if _stream_event_indicates_model_work(ev_type):
                streamed_model_work_seen = True
            if ev_type == "response.created":
                resp = getattr(event, "response", None)
                response_id = getattr(resp, "id", None) if resp else None
                yield {"type": "start", "response_id": response_id, "path": "responses"}
            elif ev_type == "response.output_text.delta" or ev_type == "response.refusal.delta":
                delta = getattr(event, "delta", "") or ""
                if delta:
                    streamed_text_parts.append(delta)
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
                    if status == "incomplete":
                        reason = _response_error_message(resp, status)[:500]
                        if reason != "max_output_tokens":
                            yield {
                                "type": "error",
                                "error": "response_incomplete",
                                "message": reason,
                            }
                            return
                    final_text = _response_visible_text(resp)
                    streamed_text = "".join(streamed_text_parts)
                    missing_text = _missing_output_text(final_text, streamed_text)
                    if missing_text is None:
                        yield {
                            "type": "error",
                            "error": "response_output_mismatch",
                            "message": (
                                "Completed output did not match the streamed text. "
                                "Retry the request."
                            ),
                        }
                        return
                    if missing_text:
                        streamed_text_parts.append(missing_text)
                        yield {"type": "delta", "text": missing_text}
                    if status == "incomplete":
                        yield {
                            "type": "incomplete",
                            "response_id": response_id,
                            "usage": usage,
                            "path": "responses",
                            "reason": reason,
                        }
                        return
                    if status and status not in ("completed", None):
                        yield {
                            "type": "error",
                            "error": f"response_{status}",
                            "message": _response_error_message(resp, status)[:500],
                        }
                        return
                    if not final_text and not streamed_text:
                        yield {
                            "type": "error",
                            "error": "response_completed_empty",
                            "message": "Response completed without output.",
                        }
                        return
                yield {
                    "type": "done",
                    "response_id": response_id,
                    "usage": usage,
                    "path": "responses",
                }
                return
            elif ev_type == "response.incomplete":
                resp = getattr(event, "response", None)
                if resp is not None:
                    response_id = response_id or getattr(resp, "id", None)
                    usage = _usage_dump(getattr(resp, "usage", None))
                    reason = _response_error_message(resp, "incomplete")[:500]
                    if reason != "max_output_tokens":
                        yield {
                            "type": "error",
                            "error": "response_incomplete",
                            "message": reason,
                        }
                        return
                    final_text = _response_visible_text(resp)
                    streamed_text = "".join(streamed_text_parts)
                    missing_text = _missing_output_text(final_text, streamed_text)
                    if missing_text is None:
                        yield {
                            "type": "error",
                            "error": "response_output_mismatch",
                            "message": (
                                "Incomplete output did not match the streamed text. "
                                "Retry the request."
                            ),
                        }
                        return
                    if missing_text:
                        streamed_text_parts.append(missing_text)
                        yield {"type": "delta", "text": missing_text}
                yield {
                    "type": "incomplete",
                    "response_id": response_id,
                    "usage": usage,
                    "path": "responses",
                    "reason": (
                        reason if resp is not None else "Response incomplete"
                    ),
                }
                return
            elif ev_type == "response.failed":
                resp = getattr(event, "response", None)
                if resp is not None:
                    code, message = _response_failure_details(resp)
                    if (
                        _is_retryable_zero_work_server_error(resp)
                        and not streamed_text_parts
                        and not streamed_model_work_seen
                    ):
                        yield {
                            "type": "_retryable_server_error",
                            "message": message,
                        }
                        return
                else:
                    code, message = "response_failed", "Response failed"
                yield {
                    "type": "error",
                    "error": code,
                    "message": message,
                }
                return
            elif ev_type == "error":
                err = getattr(event, "error", None)
                code = (
                    getattr(event, "code", None)
                    or getattr(err, "code", None)
                    or "stream_error"
                )
                message = (
                    getattr(event, "message", None)
                    or getattr(err, "message", None)
                    or ""
                )
                yield {
                    "type": "error",
                    "error": code,
                    "message": message[:500],
                }
                return
            elif ev_type == "keepalive" or ev_type == "":
                # Azure sends sparse keepalive events to prove the connection
                # is alive. Forward them so the frontend can reset its stale
                # watchdog and keep the freshness dot green during long
                # internal-reasoning gaps that emit no summary chunks. Empty
                # type events (some SDK versions) treated the same way.
                yield {"type": "keepalive"}
    except (asyncio.CancelledError, GeneratorExit):
        if background_enabled and response_id:
            await _cancel_background_response(client, response_id)
        raise
    except Exception as e:  # noqa: BLE001
        if background_enabled and response_id:
            log.warning(
                "background stream %s failed before completion; polling response: %s",
                response_id,
                e,
            )
            async for event in _recover_background_response(
                client,
                response_id,
                "".join(streamed_text_parts),
                streamed_model_work_seen,
            ):
                yield event
            return
        yield {"type": "error", "error": "stream_iter_failed", "message": str(e)[:500]}
        return

    if background_enabled and response_id:
        log.warning(
            "background stream %s ended without a terminal event; polling response",
            response_id,
        )
        async for event in _recover_background_response(
            client,
            response_id,
            "".join(streamed_text_parts),
            streamed_model_work_seen,
        ):
            yield event
        return

    yield {
        "type": "error",
        "error": "stream_ended_without_terminal",
        "message": "Response stream ended before a terminal event arrived.",
    }


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
        "stream_options": {"include_usage": True},
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
