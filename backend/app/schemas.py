"""Pydantic request/response schemas shared across routes."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class ChatRequest(BaseModel):
    deployment: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1)
    conversation_id: str | None = None
    previous_response_id: str | None = None
    # Used only when the Responses API path is not taken (Chat Completions fallback).
    # The Responses API uses server-stored state via previous_response_id.
    history: list[ChatMessage] = Field(default_factory=list)
    instructions: str | None = None
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = None
    max_output_tokens: int | None = None


class TokenUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
