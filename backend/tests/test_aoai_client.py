"""Capability detection for the Responses API vs Chat Completions split."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import anyio
import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from openai import APIConnectionError, APIStatusError

import app.aoai_client as aoai_client
import app.auth as auth
import app.settings as app_settings
from app.aoai_client import (
    KNOWN_RESPONSES_MODELS,
    KNOWN_RESPONSES_PREFIXES,
    _background_retry_delay,
    _cancel_background_response,
    _is_transient_response_error,
    _make_client,
    _stream_responses,
    get_model_capabilities,
    stream_response,
    supports_responses_api,
)
from app.main import app
from app.routes import chat as chat_routes
from app.routes import config as config_routes
from app.routes import deployments as deployment_routes
from app.schemas import ChatRequest
from app.settings import (
    DEFAULT_DEPLOYMENTS,
    PersistedConfig,
    effective_config,
)
from app.transcript import Transcript


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


def test_unknown_responses_model_has_conservative_capabilities():
    capabilities = get_model_capabilities("o3-custom-deployment")

    assert capabilities.reasoning_efforts == ()
    assert capabilities.reasoning_modes == ()
    assert capabilities.context_window_tokens is None
    assert capabilities.max_input_tokens is None
    assert capabilities.max_output_tokens is None


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


def test_environment_default_is_included_in_known_deployments(monkeypatch):
    monkeypatch.setattr(
        app_settings,
        "load_persisted",
        lambda: PersistedConfig(known_deployments=["gpt-5.6-sol"]),
    )
    monkeypatch.setenv("AOAI_DEPLOYMENT", "custom-production-deployment")
    monkeypatch.delenv("DEPLOYMENT_NAME", raising=False)

    config = app_settings.effective_config()

    assert config.default_deployment == "custom-production-deployment"
    assert config.known_deployments[0] == "custom-production-deployment"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.openai.azure.com/",
        "https://attacker.example.com/",
        "https://example.openai.azure.com.attacker.example/",
        "https://user@example.openai.azure.com/",
    ],
)
def test_untrusted_azure_endpoint_is_rejected(endpoint):
    with pytest.raises(ValueError):
        app_settings.validate_azure_endpoint(endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.openai.azure.com/",
        "https://example.services.ai.azure.com",
        "https://example.cognitiveservices.azure.com:443/",
    ],
)
def test_official_azure_endpoint_is_accepted(endpoint):
    assert app_settings.validate_azure_endpoint(endpoint).endswith("/")


@pytest.mark.parametrize(
    "scope",
    [
        "https://management.azure.com/.default",
        "https://attacker.example/.default",
    ],
)
def test_untrusted_token_scope_is_rejected(scope):
    with pytest.raises(ValueError):
        app_settings.validate_token_scope(scope)


@pytest.mark.parametrize(
    ("endpoint", "expected_scope"),
    [
        (
            "https://example.openai.azure.us/",
            "https://cognitiveservices.azure.us/.default",
        ),
        (
            "https://example.openai.azure.cn/",
            "https://cognitiveservices.azure.cn/.default",
        ),
    ],
)
def test_sovereign_endpoint_resolves_matching_scope(
    endpoint,
    expected_scope,
):
    assert app_settings.validate_token_scope("", endpoint) == expected_scope
    assert (
        app_settings.validate_token_scope(expected_scope, endpoint)
        == expected_scope
    )


def test_sovereign_scope_is_rejected_for_public_endpoint():
    with pytest.raises(ValueError):
        app_settings.validate_token_scope(
            "https://cognitiveservices.azure.us/.default",
            "https://example.openai.azure.com/",
        )


def test_make_client_uses_sovereign_scope(monkeypatch):
    scopes = []
    monkeypatch.setattr(
        aoai_client,
        "effective_config",
        lambda: SimpleNamespace(
            endpoint="https://example.openai.azure.us/",
            token_scope="",
        ),
    )
    monkeypatch.setattr(
        "app.auth.make_token_provider",
        lambda scope: scopes.append(scope) or (lambda: "token"),
    )

    _make_client()

    assert scopes == ["https://cognitiveservices.azure.us/.default"]


def test_sovereign_provider_does_not_fallback_to_public_cloud(monkeypatch):
    scopes = []
    monkeypatch.setattr(auth, "_credential", lambda: object())

    def fake_provider(_credential, scope):
        scopes.append(scope)
        return lambda: "token"

    monkeypatch.setattr(auth, "get_bearer_token_provider", fake_provider)

    provider = auth.make_token_provider(
        "https://cognitiveservices.azure.us/.default"
    )

    assert provider() == "token"
    assert scopes == ["https://cognitiveservices.azure.us/.default"]


@pytest.mark.asyncio
async def test_config_update_rejects_untrusted_token_destination(monkeypatch):
    monkeypatch.setattr(
        config_routes,
        "load_persisted",
        lambda: PersistedConfig(),
    )
    saved = []
    monkeypatch.setattr(config_routes, "save_persisted", saved.append)

    with pytest.raises(HTTPException) as exc_info:
        await config_routes.put_config(
            config_routes.ConfigUpdate(
                endpoint="https://attacker.example/",
                token_scope="https://attacker.example/.default",
            )
        )

    assert exc_info.value.status_code == 400
    assert saved == []


def test_make_client_rejects_untrusted_runtime_endpoint(monkeypatch):
    monkeypatch.setattr(
        aoai_client,
        "effective_config",
        lambda: SimpleNamespace(
            endpoint="https://attacker.example/",
            token_scope="",
        ),
    )

    with pytest.raises(ValueError):
        _make_client()


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


def _response(
    status,
    *,
    response_id="resp_background",
    output_text="",
    usage=None,
    reason=None,
    error=None,
    output=None,
):
    details = SimpleNamespace(reason=reason) if reason else None
    return SimpleNamespace(
        id=response_id,
        status=status,
        output_text=output_text,
        usage=usage,
        incomplete_details=details,
        error=error,
        output=[] if output is None else output,
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


class _SequencedResponses(_FakeResponses):
    def __init__(self, attempts):
        super().__init__([], [])
        self.attempts = list(attempts)
        self.create_calls = []

    async def create(self, **kwargs):
        self.create_calls.append(kwargs)
        events = self.attempts.pop(0)

        async def stream():
            for event in events:
                yield event

        return stream()


def _server_failure_events(attempt):
    response_id = f"resp_failed_{attempt}"
    failed = _response(
        "failed",
        response_id=response_id,
        error=SimpleNamespace(
            code="server_error",
            message=f"transient failure {attempt}",
        ),
    )
    return [
        _event(
            "response.created",
            response=SimpleNamespace(id=response_id),
        ),
        _event("response.failed", response=failed),
    ]


@pytest.mark.asyncio
async def test_terminal_server_error_retries_then_completes(monkeypatch):
    completed = _response(
        "completed",
        response_id="resp_completed",
        output_text="OK",
        usage={"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
    )
    responses = _SequencedResponses(
        [
            _server_failure_events(1),
            [
                _event(
                    "response.created",
                    response=SimpleNamespace(id="resp_completed"),
                ),
                _event("response.output_text.delta", delta="OK"),
                _event("response.completed", response=completed),
            ],
        ]
    )
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )
    sleeps = []

    async def record_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    request = ChatRequest(
        deployment="gpt-5.6-sol",
        content="test",
        reasoning_effort="max",
        reasoning_mode="pro",
    )

    events = [event async for event in _stream_responses(request)]

    assert len(responses.create_calls) == 2
    assert sleeps == [2.0]
    assert [event["type"] for event in events] == [
        "start",
        "retrying",
        "start",
        "delta",
        "done",
    ]
    assert events[1] == {
        "type": "retrying",
        "attempt": 2,
        "max_attempts": 3,
        "delay_seconds": 2.0,
        "reason": "server_error",
    }
    assert events[-1]["response_id"] == "resp_completed"


@pytest.mark.asyncio
async def test_polled_terminal_server_error_retries_then_completes(
    monkeypatch,
):
    request_error = APIConnectionError(
        request=httpx.Request(
            "GET",
            "https://example.openai.azure.com/openai/v1/responses",
        )
    )
    failed = _response(
        "failed",
        response_id="resp_failed_poll",
        error=SimpleNamespace(
            code="server_error",
            message="transient polled failure",
        ),
    )
    completed = _response(
        "completed",
        response_id="resp_completed_poll",
        output_text="OK",
        usage={"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
    )

    class _PollingRetryResponses(_FakeResponses):
        def __init__(self):
            super().__init__([], [failed])
            self.create_calls = 0

        async def create(self, **kwargs):
            self.create_calls += 1

            async def first_stream():
                yield _event(
                    "response.created",
                    response=SimpleNamespace(id="resp_failed_poll"),
                )
                raise request_error

            async def second_stream():
                yield _event(
                    "response.created",
                    response=SimpleNamespace(id="resp_completed_poll"),
                )
                yield _event("response.output_text.delta", delta="OK")
                yield _event("response.completed", response=completed)

            return first_stream() if self.create_calls == 1 else second_stream()

    responses = _PollingRetryResponses()
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    request = ChatRequest(
        deployment="gpt-5.6-sol",
        content="test",
        reasoning_effort="max",
        reasoning_mode="pro",
    )

    events = [event async for event in _stream_responses(request)]

    assert responses.create_calls == 2
    assert responses.retrieve_calls == ["resp_failed_poll"]
    assert [event["type"] for event in events] == [
        "start",
        "retrying",
        "start",
        "delta",
        "done",
    ]
    assert events[-1]["response_id"] == "resp_completed_poll"


@pytest.mark.asyncio
async def test_terminal_server_error_stops_after_two_retries(monkeypatch):
    responses = _SequencedResponses(
        [
            _server_failure_events(1),
            _server_failure_events(2),
            _server_failure_events(3),
        ]
    )
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )
    sleeps = []

    async def record_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    request = ChatRequest(
        deployment="gpt-5.6-sol",
        content="test",
        reasoning_effort="max",
        reasoning_mode="pro",
    )

    events = [event async for event in _stream_responses(request)]

    assert len(responses.create_calls) == 3
    assert sleeps == [2.0, 5.0]
    assert [event["type"] for event in events].count("retrying") == 2
    assert events[-1] == {
        "type": "error",
        "error": "response_failed",
        "message": "transient failure 3",
    }


@pytest.mark.asyncio
async def test_non_server_failure_is_not_retried(monkeypatch):
    failed = _response(
        "failed",
        response_id="resp_filtered",
        error=SimpleNamespace(
            code="content_filter",
            message="blocked",
        ),
    )
    responses = _SequencedResponses(
        [
            [
                _event(
                    "response.created",
                    response=SimpleNamespace(id="resp_filtered"),
                ),
                _event("response.failed", response=failed),
            ],
        ]
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

    assert len(responses.create_calls) == 1
    assert all(event["type"] != "retrying" for event in events)
    assert events[-1] == {
        "type": "error",
        "error": "content_filter",
        "message": "blocked",
    }


@pytest.mark.asyncio
async def test_server_error_after_visible_output_is_not_retried(monkeypatch):
    failed = _response(
        "failed",
        response_id="resp_partial_failure",
        error=SimpleNamespace(
            code="server_error",
            message="failed after output",
        ),
    )
    responses = _SequencedResponses(
        [
            [
                _event(
                    "response.created",
                    response=SimpleNamespace(id="resp_partial_failure"),
                ),
                _event("response.output_text.delta", delta="Partial"),
                _event("response.failed", response=failed),
            ],
        ]
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

    assert len(responses.create_calls) == 1
    assert [event["type"] for event in events] == [
        "start",
        "delta",
        "error",
    ]
    assert events[-1]["error"] == "server_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prior_event",
    [
        _event("response.reasoning_summary_text.delta", delta="Thinking"),
        _event("response.output_text.done", text="Completed text"),
        _event(
            "response.reasoning_summary_text.done",
            text="Completed reasoning",
        ),
        _event("response.refusal.delta", delta="I cannot"),
        _event(
            "response.function_call_arguments.delta",
            delta='{"query":',
        ),
        _event(
            "response.output_item.added",
            item=SimpleNamespace(type="function_call"),
        ),
        None,
    ],
)
async def test_server_error_after_any_model_work_is_not_retried(
    monkeypatch,
    prior_event,
):
    output = (
        [SimpleNamespace(type="reasoning")]
        if prior_event is None
        else []
    )
    failed = _response(
        "failed",
        response_id="resp_work_failure",
        error=SimpleNamespace(
            code="server_error",
            message="failed after model work",
        ),
        output=output,
    )
    events = [
        _event(
            "response.created",
            response=SimpleNamespace(id="resp_work_failure"),
        ),
    ]
    if prior_event is not None:
        events.append(prior_event)
    events.append(_event("response.failed", response=failed))
    responses = _SequencedResponses([events])
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

    result = [event async for event in _stream_responses(request)]

    assert len(responses.create_calls) == 1
    assert all(event["type"] != "retrying" for event in result)
    assert result[-1]["error"] == "server_error"


@pytest.mark.asyncio
async def test_create_500_without_response_id_is_not_retried(monkeypatch):
    request = httpx.Request(
        "POST",
        "https://example.openai.azure.com/openai/v1/responses",
    )
    create_error = APIStatusError(
        "ambiguous create failure",
        response=httpx.Response(500, request=request),
        body={"error": {"message": "server error"}},
    )

    class _CreateFailureResponses(_FakeResponses):
        def __init__(self):
            super().__init__([], [])
            self.create_calls = 0

        async def create(self, **kwargs):
            self.create_calls += 1
            raise create_error

    responses = _CreateFailureResponses()
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )
    request_body = ChatRequest(
        deployment="gpt-5.6-sol",
        content="test",
        reasoning_effort="max",
        reasoning_mode="pro",
    )

    events = [event async for event in _stream_responses(request_body)]

    assert responses.create_calls == 1
    assert all(event["type"] != "retrying" for event in events)
    assert events[-1]["error"] == "responses_create_failed"


@pytest.mark.asyncio
async def test_incomplete_response_preserves_partial_output(monkeypatch):
    incomplete_response = _response(
        "incomplete",
        output_text="Partial answer",
        usage={
            "input_tokens": 100,
            "output_tokens": 32_768,
            "total_tokens": 32_868,
        },
        reason="max_output_tokens",
    )
    responses = _FakeResponses(
        stream_events=[
            _event(
                "response.created",
                response=SimpleNamespace(id="resp_background"),
            ),
            _event("response.output_text.delta", delta="Partial "),
            _event("response.incomplete", response=incomplete_response),
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

    assert "".join(
        event.get("text", "")
        for event in events
        if event["type"] == "delta"
    ) == "Partial answer"
    assert events[-1] == {
        "type": "incomplete",
        "response_id": "resp_background",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 32_768,
            "total_tokens": 32_868,
        },
        "path": "responses",
        "reason": "max_output_tokens",
    }


@pytest.mark.asyncio
async def test_content_filter_incomplete_is_not_resumable(monkeypatch):
    incomplete_response = _response(
        "incomplete",
        output_text="Filtered partial",
        usage={"output_tokens": 12, "total_tokens": 20},
        reason="content_filter",
    )
    responses = _FakeResponses(
        stream_events=[
            _event(
                "response.created",
                response=SimpleNamespace(id="resp_background"),
            ),
            _event("response.incomplete", response=incomplete_response),
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

    assert events[-1] == {
        "type": "error",
        "error": "response_incomplete",
        "message": "content_filter",
    }
    assert all(event["type"] != "incomplete" for event in events)
    assert all(event.get("text") != "Filtered partial" for event in events)


def test_chat_route_saves_incomplete_partial_turn(monkeypatch, tmp_path):
    transcript = Transcript.new(
        save_dir=tmp_path,
        first_user_msg="Solve this",
        endpoint="https://example.openai.azure.com/",
        deployment="gpt-5.6-sol",
        conversation_id="conversation_incomplete",
    )

    async def fake_stream_response(_request):
        yield {
            "type": "start",
            "response_id": "resp_incomplete",
            "path": "responses",
        }
        yield {"type": "delta", "text": "Preserved partial answer"}
        yield {
            "type": "incomplete",
            "response_id": "resp_incomplete",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 32_768,
                "total_tokens": 32_868,
            },
            "path": "responses",
            "reason": "max_output_tokens",
        }

    monkeypatch.setattr(
        chat_routes,
        "_open_or_create_transcript",
        lambda _request: transcript,
    )
    monkeypatch.setattr(chat_routes, "stream_response", fake_stream_response)

    response = TestClient(app).post(
        "/api/chat",
        json={
            "deployment": "gpt-5.6-sol",
            "content": "Solve this",
            "reasoning_effort": "max",
            "reasoning_mode": "pro",
            "max_output_tokens": 32_768,
        },
    )

    assert response.status_code == 200
    assert "event: incomplete" in response.text
    assert "event: saved" in response.text
    saved = Transcript.load(transcript.path)
    assert saved.meta.turn_count == 1
    assert saved.meta.response_id == "resp_incomplete"
    assert saved.turns[-1].content == "Preserved partial answer"
    assert saved.meta.usage_total["output_tokens"] == 32_768


@pytest.mark.asyncio
async def test_chat_route_persists_before_terminal_event(monkeypatch, tmp_path):
    transcript = Transcript.new(
        save_dir=tmp_path,
        first_user_msg="Solve this",
        endpoint="https://example.openai.azure.com/",
        deployment="gpt-5.6-sol",
        conversation_id="conversation_ordering",
    )
    persisted = False
    original_write = transcript.write_atomic

    def tracked_write():
        nonlocal persisted
        original_write()
        persisted = True

    async def fake_stream_response(_request):
        yield {"type": "delta", "text": "Partial answer"}
        yield {
            "type": "incomplete",
            "response_id": "resp_ordering",
            "usage": {"output_tokens": 10, "total_tokens": 20},
            "path": "responses",
            "reason": "max_output_tokens",
        }

    class CapturedEventSource:
        def __init__(self, body_iterator, **_kwargs):
            self.body_iterator = body_iterator

    monkeypatch.setattr(transcript, "write_atomic", tracked_write)
    monkeypatch.setattr(
        chat_routes,
        "_open_or_create_transcript",
        lambda _request: transcript,
    )
    monkeypatch.setattr(chat_routes, "stream_response", fake_stream_response)
    monkeypatch.setattr(chat_routes, "EventSourceResponse", CapturedEventSource)

    response = await chat_routes.chat(
        ChatRequest(
            deployment="gpt-5.6-sol",
            content="Solve this",
            reasoning_effort="max",
            reasoning_mode="pro",
        )
    )

    async for event in response.body_iterator:
        if event["event"] == "incomplete":
            assert persisted


@pytest.mark.asyncio
async def test_response_error_event_uses_sdk_direct_fields(monkeypatch):
    responses = _FakeResponses(
        stream_events=[
            _event(
                "response.created",
                response=SimpleNamespace(id="resp_error"),
            ),
            _event(
                "error",
                code="server_error",
                message="Azure temporarily unavailable",
                param=None,
            ),
        ],
        retrieved_responses=[],
    )
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )

    events = [
        event
        async for event in _stream_responses(
            ChatRequest(deployment="gpt-5.6-sol", content="test")
        )
    ]

    assert events[-1] == {
        "type": "error",
        "error": "server_error",
        "message": "Azure temporarily unavailable",
    }


@pytest.mark.asyncio
async def test_refusal_output_is_streamed_and_reconciled(monkeypatch):
    refusal_text = "I cannot help with that."
    completed = _response(
        "completed",
        output=[
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(type="refusal", refusal=refusal_text),
                ],
            )
        ],
    )
    responses = _FakeResponses(
        stream_events=[
            _event(
                "response.created",
                response=SimpleNamespace(id="resp_refusal"),
            ),
            _event("response.refusal.delta", delta="I cannot "),
            _event("response.completed", response=completed),
        ],
        retrieved_responses=[],
    )
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )

    events = [
        event
        async for event in _stream_responses(
            ChatRequest(deployment="gpt-5.6-sol", content="test")
        )
    ]
    text = "".join(
        event.get("text", "") for event in events if event["type"] == "delta"
    )

    assert text == refusal_text
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_background_cancel_cleanup_has_short_deadline(monkeypatch):
    class SlowResponses:
        def __init__(self):
            self.finished = False

        async def cancel(self, _response_id):
            await anyio.sleep(0.2)
            self.finished = True

    responses = SlowResponses()
    monkeypatch.setattr(
        aoai_client,
        "BACKGROUND_CANCEL_TIMEOUT_SECONDS",
        0.01,
        raising=False,
    )
    started = anyio.current_time()

    await _cancel_background_response(
        SimpleNamespace(responses=responses),
        "resp_slow_cancel",
    )

    assert anyio.current_time() - started < 0.1
    assert responses.finished is False


@pytest.mark.asyncio
async def test_explicit_cancel_has_short_deadline(monkeypatch):
    class SlowResponses:
        def __init__(self):
            self.finished = False

        async def cancel(self, _response_id):
            await anyio.sleep(0.2)
            self.finished = True

    responses = SlowResponses()
    monkeypatch.setattr(
        aoai_client,
        "_make_client",
        lambda: SimpleNamespace(responses=responses),
    )
    monkeypatch.setattr(
        aoai_client,
        "BACKGROUND_CANCEL_TIMEOUT_SECONDS",
        0.01,
    )
    started = anyio.current_time()

    with pytest.raises(TimeoutError):
        await aoai_client.cancel_response("resp_slow_cancel")

    assert anyio.current_time() - started < 0.1
    assert responses.finished is False


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
async def test_cancel_route_requires_stream_capability_token(
    monkeypatch,
    tmp_path,
):
    calls = []
    transcript = Transcript.new(
        save_dir=tmp_path,
        first_user_msg="Cancel this",
        endpoint="https://example.openai.azure.com/",
        deployment="gpt-5.6-sol",
        conversation_id="conversation_cancel",
    )

    async def fake_cancel(response_id):
        calls.append(response_id)
        return {"response_id": response_id, "status": "cancelled"}

    async def fake_stream_response(_request):
        yield {
            "type": "start",
            "response_id": "resp_test123",
            "path": "responses",
        }
        await anyio.sleep_forever()

    class CapturedEventSource:
        def __init__(self, body_iterator, **_kwargs):
            self.body_iterator = body_iterator

    monkeypatch.setattr(
        chat_routes,
        "cancel_aoai_response",
        fake_cancel,
        raising=False,
    )
    monkeypatch.setattr(
        chat_routes,
        "_open_or_create_transcript",
        lambda _request: transcript,
    )
    monkeypatch.setattr(chat_routes, "stream_response", fake_stream_response)
    monkeypatch.setattr(chat_routes, "EventSourceResponse", CapturedEventSource)

    response = await chat_routes.chat(
        ChatRequest(deployment="gpt-5.6-sol", content="Cancel this")
    )
    assert (await anext(response.body_iterator))["event"] == "meta"
    start = await anext(response.body_iterator)
    start_data = json.loads(start["data"])
    cancel_token = start_data["cancel_token"]

    with pytest.raises(HTTPException) as exc_info:
        await chat_routes.cancel_response(
            "resp_test123",
            chat_routes.CancelResponseRequest(cancel_token="x" * 43),
        )
    assert exc_info.value.status_code == 404
    assert calls == []

    result = await chat_routes.cancel_response(
        "resp_test123",
        chat_routes.CancelResponseRequest(cancel_token=cancel_token),
    )
    await response.body_iterator.aclose()

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
        "type": "incomplete",
        "response_id": "resp_background",
        "usage": None,
        "path": "responses",
        "reason": "max_output_tokens",
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
