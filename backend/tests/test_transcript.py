"""Round-trip + edge-case tests for the Markdown transcript store."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.routes import conversations as conversation_routes
from app.transcript import Transcript, find_by_id, list_summaries


@pytest.fixture
def tmp_save_dir(tmp_path: Path) -> Path:
    d = tmp_path / "conversations"
    d.mkdir()
    return d


def _new(save_dir: Path, msg: str = "Hello world", deployment: str = "gpt-5.4-pro") -> Transcript:
    return Transcript.new(
        save_dir=save_dir,
        first_user_msg=msg,
        endpoint="https://test.openai.azure.com/",
        deployment=deployment,
    )


def test_filename_format_uses_date_slug(tmp_save_dir):
    t = _new(tmp_save_dir, "Explain how rockets work")
    # YYYY-MM-DD_HHMMSS-<slug>.md
    assert t.path.suffix == ".md"
    assert t.path.parent == tmp_save_dir
    stem = t.path.stem
    # date prefix is 4-2-2_6 digits
    assert stem[:4].isdigit() and stem[4] == "-" and stem[7] == "-"
    assert "explain-how-rockets-work" in stem


def test_filename_falls_back_when_message_unsluggable(tmp_save_dir):
    t = _new(tmp_save_dir, "    !!!    ")
    assert "conversation" in t.path.stem


def test_single_turn_roundtrip(tmp_save_dir):
    t = _new(tmp_save_dir)
    t.append_turn(
        user_text="Hello world",
        assistant_text="Hi there!",
        deployment="gpt-5.4-pro",
        response_id="resp_001",
        usage={
            "input_tokens": 5,
            "output_tokens": 10,
            "total_tokens": 15,
            "output_tokens_details": {"reasoning_tokens": 7},
        },
        thinking_ms=123_000,
        reasoning_chars=8_431,
        path="responses",
    )
    t.write_atomic()
    assert t.path.exists()

    loaded = Transcript.load(t.path)
    assert loaded.meta.id == t.meta.id
    assert loaded.meta.turn_count == 1
    assert loaded.meta.endpoint == "https://test.openai.azure.com/"
    assert loaded.meta.deployment == "gpt-5.4-pro"
    assert loaded.meta.response_id == "resp_001"
    assert loaded.meta.usage_total.get("total_tokens") == 15

    assert len(loaded.turns) == 2
    assert loaded.turns[0].role == "user"
    assert loaded.turns[0].content == "Hello world"
    assert loaded.turns[1].role == "assistant"
    assert loaded.turns[1].content == "Hi there!"
    assert loaded.turns[1].deployment == "gpt-5.4-pro"
    assert loaded.turns[1].response_id == "resp_001"
    assert loaded.turns[1].tokens == 15
    assert loaded.turns[1].thinking_ms == 123_000
    assert loaded.turns[1].reasoning_tokens == 7
    assert loaded.turns[1].reasoning_chars == 8_431
    assert loaded.turns[1].path == "responses"


def test_incomplete_turn_metadata_roundtrip(tmp_save_dir):
    t = _new(tmp_save_dir, deployment="gpt-5.6-sol")
    t.append_turn(
        user_text="Write a long answer",
        assistant_text="Partial answer",
        deployment="gpt-5.6-sol",
        response_id="resp_incomplete",
        usage={"output_tokens": 128_000, "total_tokens": 128_010},
        response_status="incomplete",
        incomplete_reason="max_output_tokens",
        thinking_ms=10_500,
        reasoning_chars=321,
        path="responses",
    )
    t.write_atomic()

    loaded = Transcript.load(t.path)

    assert loaded.meta.response_status == "incomplete"
    assert loaded.meta.incomplete_reason == "max_output_tokens"
    assert loaded.meta.response_deployment == "gpt-5.6-sol"
    assert loaded.turns[-1].response_status == "incomplete"
    assert loaded.turns[-1].incomplete_reason == "max_output_tokens"
    assert loaded.turns[-1].thinking_ms == 10_500
    assert loaded.turns[-1].reasoning_chars == 321
    assert loaded.turns[-1].path == "responses"


@pytest.mark.asyncio
async def test_conversation_detail_exposes_continuation_metadata(
    tmp_save_dir,
    monkeypatch,
):
    t = _new(tmp_save_dir, deployment="gpt-5.6-sol")
    t.append_turn(
        user_text="Write a long answer",
        assistant_text="Partial answer",
        deployment="gpt-5.6-sol",
        response_id="resp_incomplete",
        usage={"total_tokens": 128_010},
        response_status="incomplete",
        incomplete_reason="max_output_tokens",
        thinking_ms=10_500,
        reasoning_chars=321,
        path="responses",
    )
    t.write_atomic()
    monkeypatch.setattr(
        conversation_routes,
        "effective_config",
        lambda: SimpleNamespace(save_dir=str(tmp_save_dir)),
    )
    monkeypatch.setattr(
        conversation_routes,
        "ensure_save_dir",
        lambda _config: tmp_save_dir,
    )

    detail = await conversation_routes.get_conversation(t.meta.id)

    assert detail["response_status"] == "incomplete"
    assert detail["incomplete_reason"] == "max_output_tokens"
    assert detail["response_deployment"] == "gpt-5.6-sol"
    assert detail["turns"][-1]["response_status"] == "incomplete"
    assert detail["turns"][-1]["incomplete_reason"] == "max_output_tokens"
    assert detail["turns"][-1]["thinking_ms"] == 10_500
    assert detail["turns"][-1]["reasoning_chars"] == 321
    assert detail["turns"][-1]["path"] == "responses"


def test_two_turns_accumulate_usage(tmp_save_dir):
    t = _new(tmp_save_dir)
    t.append_turn(
        user_text="q1", assistant_text="a1", deployment="gpt-5.4-pro",
        response_id="resp_001",
        usage={"input_tokens": 5, "output_tokens": 10, "total_tokens": 15},
    )
    t.append_turn(
        user_text="q2", assistant_text="a2", deployment="gpt-5.4-pro",
        response_id="resp_002",
        usage={"input_tokens": 7, "output_tokens": 11, "total_tokens": 18},
    )
    t.write_atomic()

    loaded = Transcript.load(t.path)
    assert loaded.meta.turn_count == 2
    assert loaded.meta.response_id == "resp_002"
    assert loaded.meta.usage_total["input_tokens"] == 12
    assert loaded.meta.usage_total["output_tokens"] == 21
    assert loaded.meta.usage_total["total_tokens"] == 33

    assert len(loaded.turns) == 4
    assert [t.role for t in loaded.turns] == ["user", "assistant", "user", "assistant"]
    assert loaded.turns[2].content == "q2"
    assert loaded.turns[3].content == "a2"


def test_reasoning_tokens_accumulated(tmp_save_dir):
    t = _new(tmp_save_dir)
    t.append_turn(
        user_text="q", assistant_text="a", deployment="gpt-5.4-pro",
        response_id="resp_001",
        usage={
            "input_tokens": 10, "output_tokens": 20, "total_tokens": 30,
            "output_tokens_details": {"reasoning_tokens": 12},
        },
    )
    t.write_atomic()
    loaded = Transcript.load(t.path)
    assert loaded.meta.usage_total["reasoning_tokens"] == 12


def test_find_by_id(tmp_save_dir):
    t1 = _new(tmp_save_dir, "first")
    t1.append_turn("first", "a1", "gpt-5.4-pro", "resp_001", None)
    t1.write_atomic()

    t2 = _new(tmp_save_dir, "second")
    t2.append_turn("second", "a2", "gpt-5.4-pro", "resp_002", None)
    t2.write_atomic()

    assert find_by_id(tmp_save_dir, t1.meta.id) == t1.path
    assert find_by_id(tmp_save_dir, t2.meta.id) == t2.path
    assert find_by_id(tmp_save_dir, "nonexistent") is None


def test_list_summaries_sorted_by_updated_at(tmp_save_dir):
    t1 = _new(tmp_save_dir, "first message")
    t1.append_turn("first message", "a", "gpt-5.4-pro", "r1", None)
    t1.write_atomic()
    # Force a later updated_at by appending another turn
    t2 = _new(tmp_save_dir, "second message")
    t2.append_turn("second message", "a", "gpt-5.4-pro", "r2", None)
    t2.write_atomic()

    summaries = list_summaries(tmp_save_dir)
    assert len(summaries) == 2
    # Newest first
    assert summaries[0]["updated_at"] >= summaries[1]["updated_at"]


def test_atomic_write_uses_tmp_then_replace(tmp_save_dir, monkeypatch):
    """If the tmp write succeeds, the final file should appear without a `.tmp` survivor."""
    t = _new(tmp_save_dir)
    t.append_turn("hi", "hello", "gpt-5.4-pro", "r1", None)
    t.write_atomic()
    assert t.path.exists()
    tmp_files = list(tmp_save_dir.glob("*.tmp"))
    assert tmp_files == [], f"unexpected tmp files left over: {tmp_files}"


def test_markdown_body_has_expected_structure(tmp_save_dir):
    t = _new(tmp_save_dir)
    t.append_turn("hello", "world", "gpt-5.4-pro", "r1", {"total_tokens": 5})
    t.write_atomic()
    body = t.path.read_text("utf-8")

    assert "---" in body  # frontmatter delimiters
    assert "id:" in body
    assert "deployment: gpt-5.4-pro" in body
    assert "## User" in body
    assert "## Assistant" in body
    assert "hello" in body
    assert "world" in body
