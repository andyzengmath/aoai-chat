"""Capability detection for the Responses API vs Chat Completions split."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import anyio
import httpx
import pytest
from openai import APIConnectionError, APIStatusError

import app.aoai_client as aoai_client
from app.aoai_client import (
    KNOWN_RESPONSES_MODELS,
    KNOWN_RESPONSES_PREFIXES,
    _background_retry_delay,
    _is_transient_response_error,
    _make_client,
    _stream_responses,
    stream_response,
    supports_responses_api,
)
from app.routes import chat as chat_routes
from app.routes import deployments as deployment_routes
from app.schemas import ChatRequest
from app.settings import DEFAULT_DEPLOYMENTS, effective_config


@pytest.mark.parametrize(
    "name,expected",
    [
        # Explicit known-good models (user's target deployments)
        ("gpt-5.4-pro", True),
        ("gpt-5.5", True),
        # Case-insensitive
        ("GPT-5.4-PRO", True),
        # Prefix-based heuristic
        ("gpt-5", True),
        ("gpt-5-codex", True),
        ("gpt-5.1-turbo", True),
        ("o1", True),
        ("o1-mini", True),
        ("o3", True),
        ("o3-pro", True),
        ("o4-mini", True),
        # Chat-only models
        ("gpt-4o", False),
        ("gpt-4o-mini", False),
        ("gpt-4-turbo", False),
        ("gpt-35-turbo", False),
        ("text-embedding-ada-002", False),
        # Empty / null
        ("", False),
        (None, False),
    ],
)
def test_supports_responses_api(name, expected):
    assert supports_responses_api(name) is expected


def test_known_responses_models_includes_user_targets():
    """The two models the user explicitly asked to support."""
    assert "gpt-5.4-pro" in KNOWN_RESPONSES_MODELS
    assert "gpt-5.5" in KNOWN_RESPONSES_MODELS


def test_prefix_list_covers_gpt5_o_series():
    expected_prefixes = {"gpt-5", "o1", "o3", "o4"}
    assert set(KNOWN_RESPONSES_PREFIXES) >= expected_prefixes


def test_gpt56_is_the_default_deployment():
    assert DEFAULT_DEPLOYMENTS[0] == "gpt-5.6-sol"


def test_gpt56_deployment_serializes_verified_capabilities(monkeypatch):
    config = SimpleNamespace(
        known_deployments=["gpt-5.6-sol"],
        default_deployment="gpt-5.6-sol",
    )
    monkeypatch.setattr(aoai_client, "effective_config", lambda: config)
    monkeypatch.setattr(deployment_routes, "effective_config", lambda: config)

    deployment = deployment_routes._serialize()["data"][0]

    assert deployment["reasoning_efforts"] == [
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert deployment["reasoning_modes"] == ["standard", "pro"]
    assert deployment["model_version"] == "2026-07-09"
    assert deployment["context_window_tokens"] == 1_050_000
    assert deployment["max_input_tokens"] == 922_000
    assert deployment["max_output_tokens"] == 128_000


@pytest.mark.parametrize("mode", ["standard", "pro"])
def test_chat_request_accepts_max_effort_and_gpt56_modes(mode):
    try:
        request = ChatRequest(
            deployment="gpt-5.6-sol",
            content="test",
            reasoning_effort="max",
            reasoning_mode=mode,
        )
    except Exception as exc:  # pragma: no cover - assertion reports the schema gap
        pytest.fail(f"GPT-5.6 max/pro request was rejected: {exc}")

    assert request.model_dump()["reasoning_mode"] == mode


def test_foundry_sample_environment_names_are_supported(monkeypatch):
    monkeypatch.setenv("ENDPOINT_URL", "https://sample.openai.azure.com/")
    monkeypatch.setenv("DEPLOYMENT_NAME", "gpt-5.6-sol")
    monkeypatch.delenv("AOAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AOAI_DEPLOYMENT", raising=False)

    config = effective_config()

    assert config.endpoint == "https://sample.openai.azure.com/"
    assert config.default_deployment == "gpt-5.6-sol"


class _FakeResponses:
    def __init__(self, stream_events, retrieved_responses):
        self.stream_events = stream_events
        self.retrieved_responses = list(retrieved_responses)
        self.create_kwargs = None
        self.retrieve_calls = []
        self.cancel_calls = []

    async def create(self, **kwargs):
        self.create_kwargs = kwargs

        async def stream():
            for event in self.stream_events:
                yield event

        return stream()

    async def retrieve(self, response_id):
        self.retrieve_calls.append(response_id)
        result = self.retrieved_responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def cancel(self, response_id):
        self.cancel_calls.append(response_id)
        return _response("cancelled")


def _event(event_type, **kwargs):
    return SimpleNamespace(type=event_type, sequence_number=1, **kwargs)


def _response(status, *, output_text="", usage=None, reason=None):
    details = SimpleNamespace(reason=reason) if reason else None
    return SimpleNamespace(
        id="resp_background",
        status=status,
        output_text=output_text,
        usage=usage,
        incomplete_details=details,
        error=None,
    )


@pytest.mark.asyncio
async def test_make_client_uses_refreshing_async_token_provider(monkeypatch):
    tokens = iter(["token-one", "token-two"])
    monkeypatch.setattr(
        aoai_client,
        "effective_config",
        lambda: SimpleNamespace(
            endpoint="https://example.openai.azure.com/",
            token_scope="",
        ),
    )
    monkeypatch.setattr(
        "app.auth.make_token_provider",
        lambda _scope: lambda: next(tokens),
    )

    client = _make_client()

    token_provider = client._api_key_provider
    assert callable(token_provider)
    assert await token_provider() == "token-one"
    assert await token_provider() == "token-two"


@pytest.mark.asyncio
async def test_gpt56_pro_mode_and_max_effort_are_forwarded(monkeypatch):
    completed_response = _response(
        "completed",
        output_text="OK",
        usage={"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
    )
    responses = _FakeResponses(
        stream_events=[
            _event(
                "response.created",
                response=SimpleNamespace(id="resp_background"),
            ),
            _event("response.completed", response=completed_response),
        ],
        retrieved_responses=[],
    )
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )
    request = ChatRequest(
        deployment="gpt-5.6-sol",
        content="test",
        reasoning_effort="max",
        reasoning_mode="pro",
    )

    events = [event async for event in _stream_responses(request)]

    assert responses.create_kwargs["reasoning"] == {
        "effort": "max",
        "mode": "pro",
        "summary": "auto",
    }
    assert responses.create_kwargs["background"] is True
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_background_recovery_retries_transient_retrieve_errors(monkeypatch):
    responses = _FakeResponses(
        stream_events=[
            _event(
                "response.created",
                response=SimpleNamespace(id="resp_background"),
            ),
        ],
        retrieved_responses=[
            APIConnectionError(
                request=httpx.Request(
                    "GET",
                    "https://example.openai.azure.com/openai/v1/responses/resp_background",
                )
            ),
            _response("completed", output_text="Recovered"),
        ],
    )
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    request = ChatRequest(
        deployment="gpt-5.6-sol",
        content="test",
        reasoning_effort="high",
    )

    events = [event async for event in _stream_responses(request)]

    assert responses.retrieve_calls == ["resp_background", "resp_background"]
    assert events[-2:] == [
        {"type": "delta", "text": "Recovered"},
        {
            "type": "done",
            "response_id": "resp_background",
            "usage": None,
            "path": "responses",
        },
    ]


@pytest.mark.parametrize("status_code", [408, 409, 429, 500, 503])
def test_retryable_response_statuses_are_transient(status_code):
    request = httpx.Request(
        "GET",
        "https://example.openai.azure.com/openai/v1/responses/resp_background",
    )
    error = APIStatusError(
        "transient",
        response=httpx.Response(status_code, request=request),
        body=None,
    )

    assert _is_transient_response_error(error)


def test_background_retry_honors_retry_after_header():
    request = httpx.Request(
        "GET",
        "https://example.openai.azure.com/openai/v1/responses/resp_background",
    )
    error = APIStatusError(
        "rate limited",
        response=httpx.Response(
            429,
            request=request,
            headers={"retry-after": "7"},
        ),
        body=None,
    )

    assert _background_retry_delay(error, attempt=1) == 7


@pytest.mark.asyncio
async def test_exhausted_background_recovery_cancels_response(monkeypatch):
    request = httpx.Request(
        "GET",
        "https://example.openai.azure.com/openai/v1/responses/resp_background",
    )
    responses = _FakeResponses(
        stream_events=[
            _event(
                "response.created",
                response=SimpleNamespace(id="resp_background"),
            ),
        ],
        retrieved_responses=[
            APIConnectionError(request=request),
            APIConnectionError(request=request),
            APIConnectionError(request=request),
            APIConnectionError(request=request),
        ],
    )
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    request_body = ChatRequest(
        deployment="gpt-5.6-sol",
        content="test",
        reasoning_effort="high",
    )

    events = [event async for event in _stream_responses(request_body)]

    assert events[-1]["error"] == "background_retrieve_failed"
    assert responses.cancel_calls == ["resp_background"]


@pytest.mark.asyncio
async def test_cancelling_background_stream_cancels_azure_response(monkeypatch):
    release_stream = asyncio.Event()

    class _BlockingResponses(_FakeResponses):
        async def create(self, **kwargs):
            self.create_kwargs = kwargs

            async def stream():
                yield _event(
                    "response.created",
                    response=SimpleNamespace(id="resp_background"),
                )
                await release_stream.wait()

            return stream()

    responses = _BlockingResponses([], [])
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )
    request = ChatRequest(
        deployment="gpt-5.6-sol",
        content="test",
        reasoning_effort="high",
    )
    generator = _stream_responses(request)

    assert await anext(generator) == {
        "type": "start",
        "response_id": "resp_background",
        "path": "responses",
    }
    pending = asyncio.create_task(anext(generator))
    await asyncio.sleep(0)
    pending.cancel()

    with pytest.raises(asyncio.CancelledError):
        await pending

    assert responses.cancel_calls == ["resp_background"]


@pytest.mark.asyncio
async def test_cancelling_before_response_created_waits_for_id_and_cancels(
    monkeypatch,
):
    release_response_id = asyncio.Event()

    class _DelayedCreatedResponses(_FakeResponses):
        async def create(self, **kwargs):
            self.create_kwargs = kwargs

            async def stream():
                await release_response_id.wait()
                yield _event(
                    "response.created",
                    response=SimpleNamespace(id="resp_background"),
                )

            return stream()

    responses = _DelayedCreatedResponses([], [])
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )
    request = ChatRequest(
        deployment="gpt-5.6-sol",
        content="test",
        reasoning_effort="high",
    )
    generator = _stream_responses(request)
    pending = asyncio.create_task(anext(generator))
    await asyncio.sleep(0)
    pending.cancel()
    release_response_id.set()

    with pytest.raises(asyncio.CancelledError):
        await pending

    assert responses.cancel_calls == ["resp_background"]


@pytest.mark.asyncio
async def test_anyio_scope_cancellation_shields_remote_cleanup(monkeypatch):
    stream_started = anyio.Event()

    class _AnyioBlockingResponses(_FakeResponses):
        async def create(self, **kwargs):
            self.create_kwargs = kwargs

            async def stream():
                yield _event(
                    "response.created",
                    response=SimpleNamespace(id="resp_background"),
                )
                stream_started.set()
                await anyio.sleep_forever()

            return stream()

        async def cancel(self, response_id):
            await anyio.sleep(0)
            self.cancel_calls.append(response_id)
            return _response("cancelled")

    responses = _AnyioBlockingResponses([], [])
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )
    request = ChatRequest(
        deployment="gpt-5.6-sol",
        content="test",
        reasoning_effort="high",
    )

    async def consume():
        async for _ in _stream_responses(request):
            pass

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(consume)
        await stream_started.wait()
        task_group.cancel_scope.cancel()

    assert responses.cancel_calls == ["resp_background"]


@pytest.mark.asyncio
async def test_closing_stream_dispatcher_cancels_yielded_background_response(
    monkeypatch,
):
    release_stream = asyncio.Event()

    class _YieldedEventResponses(_FakeResponses):
        async def create(self, **kwargs):
            self.create_kwargs = kwargs

            async def stream():
                yield _event(
                    "response.created",
                    response=SimpleNamespace(id="resp_background"),
                )
                await release_stream.wait()

            return stream()

    responses = _YieldedEventResponses([], [])
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )
    monkeypatch.setattr(
        aoai_client,
        "effective_config",
        lambda: SimpleNamespace(endpoint="https://example.openai.azure.com/"),
    )
    request = ChatRequest(
        deployment="gpt-5.6-sol",
        content="test",
        reasoning_effort="high",
    )
    dispatcher = stream_response(request)

    assert (await anext(dispatcher))["type"] == "start"
    await dispatcher.aclose()

    assert responses.cancel_calls == ["resp_background"]


@pytest.mark.asyncio
async def test_first_background_stream_failure_emits_structured_error(monkeypatch):
    class _FailingFirstEventResponses(_FakeResponses):
        async def create(self, **kwargs):
            self.create_kwargs = kwargs

            async def stream():
                raise APIConnectionError(
                    request=httpx.Request(
                        "GET",
                        "https://example.openai.azure.com/openai/v1/responses",
                    )
                )
                yield  # pragma: no cover

            return stream()

    responses = _FailingFirstEventResponses([], [])
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )
    request = ChatRequest(
        deployment="gpt-5.6-sol",
        content="test",
        reasoning_effort="high",
    )

    events = [event async for event in _stream_responses(request)]

    assert events == [
        {
            "type": "error",
            "error": "stream_iter_failed",
            "message": "Connection error.",
        }
    ]


@pytest.mark.asyncio
async def test_cancel_route_cancels_response_by_id(monkeypatch):
    calls = []

    async def fake_cancel(response_id):
        calls.append(response_id)
        return {"response_id": response_id, "status": "cancelled"}

    monkeypatch.setattr(
        chat_routes,
        "cancel_aoai_response",
        fake_cancel,
        raising=False,
    )

    result = await chat_routes.cancel_response("resp_test123")

    assert calls == ["resp_test123"]
    assert result == {
        "response_id": "resp_test123",
        "status": "cancelled",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_kwargs", "expected_error"),
    [
        ({"reasoning_effort": "max"}, "unsupported_reasoning_effort"),
        ({"reasoning_mode": "pro"}, "unsupported_reasoning_mode"),
    ],
)
async def test_gpt54_rejects_unsupported_reasoning_options(
    monkeypatch,
    request_kwargs,
    expected_error,
):
    monkeypatch.setattr(
        aoai_client,
        "effective_config",
        lambda: SimpleNamespace(endpoint="https://example.openai.azure.com/"),
    )

    async def successful_stream(_request):
        yield {"type": "done"}

    monkeypatch.setattr(aoai_client, "_stream_responses", successful_stream)
    request = ChatRequest(
        deployment="gpt-5.4-pro",
        content="test",
        **request_kwargs,
    )

    events = [event async for event in stream_response(request)]

    assert events[-1]["type"] == "error"
    assert events[-1]["error"] == expected_error


@pytest.mark.asyncio
async def test_background_stream_recovers_after_early_end(monkeypatch):
    responses = _FakeResponses(
        stream_events=[
            _event(
                "response.created",
                response=SimpleNamespace(id="resp_background"),
            ),
        ],
        retrieved_responses=[
            _response("in_progress"),
            _response(
                "completed",
                output_text="Recovered answer",
                usage={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
            ),
        ],
    )
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    request = ChatRequest(
        deployment="gpt-5.4-pro",
        content="test",
        reasoning_effort="high",
    )

    events = [event async for event in _stream_responses(request)]

    assert responses.create_kwargs["background"] is True
    assert responses.retrieve_calls == ["resp_background", "resp_background"]
    assert [event["type"] for event in events] == [
        "start",
        "keepalive",
        "delta",
        "done",
    ]
    assert events[2]["text"] == "Recovered answer"
    assert events[3]["response_id"] == "resp_background"


@pytest.mark.asyncio
async def test_background_recovery_emits_only_missing_output(monkeypatch):
    responses = _FakeResponses(
        stream_events=[
            _event(
                "response.created",
                response=SimpleNamespace(id="resp_background"),
            ),
            _event("response.output_text.delta", delta="Partial "),
        ],
        retrieved_responses=[
            _response("completed", output_text="Partial answer"),
        ],
    )
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )
    request = ChatRequest(
        deployment="gpt-5.4-pro",
        content="test",
        reasoning_effort="high",
    )

    events = [event async for event in _stream_responses(request)]
    text = "".join(
        event["text"] for event in events if event["type"] == "delta"
    )

    assert text == "Partial answer"
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_background_recovery_surfaces_incomplete_status(monkeypatch):
    responses = _FakeResponses(
        stream_events=[
            _event(
                "response.created",
                response=SimpleNamespace(id="resp_background"),
            ),
        ],
        retrieved_responses=[
            _response("incomplete", reason="max_output_tokens"),
        ],
    )
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )
    request = ChatRequest(
        deployment="gpt-5.4-pro",
        content="test",
        reasoning_effort="high",
    )

    events = [event async for event in _stream_responses(request)]

    assert events[-1] == {
        "type": "error",
        "error": "response_incomplete",
        "message": "max_output_tokens",
    }


@pytest.mark.asyncio
async def test_completed_event_recovers_output_when_deltas_are_missing(monkeypatch):
    completed_response = _response(
        "completed",
        output_text="Completed event answer",
        usage={"input_tokens": 3, "output_tokens": 3, "total_tokens": 6},
    )
    responses = _FakeResponses(
        stream_events=[
            _event(
                "response.created",
                response=SimpleNamespace(id="resp_background"),
            ),
            _event("response.completed", response=completed_response),
        ],
        retrieved_responses=[],
    )
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )
    request = ChatRequest(
        deployment="gpt-5.4-pro",
        content="test",
        reasoning_effort="high",
    )

    events = [event async for event in _stream_responses(request)]
    text = "".join(
        event["text"] for event in events if event["type"] == "delta"
    )

    assert text == "Completed event answer"
    assert events[-1]["type"] == "done"
