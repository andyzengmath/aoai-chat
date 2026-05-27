"""Capability detection for the Responses API vs Chat Completions split."""
from __future__ import annotations

import pytest

from app.aoai_client import (
    KNOWN_RESPONSES_MODELS,
    KNOWN_RESPONSES_PREFIXES,
    supports_responses_api,
)


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
