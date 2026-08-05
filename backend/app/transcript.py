"""Local Markdown transcript persistence.

One `.md` file per conversation under `<save_dir>/`. Filename:
  `YYYY-MM-DD_HHMMSS-{slug}.md`
where slug is the kebab-cased first ~6 words of the first user message.

Layout:
    ---
    id: <uuid>
    created_at: 2026-05-20T14:30:15Z
    updated_at: ...
    title: ...
    endpoint: ...
    deployment: ...
    response_id: <last>
    turn_count: <int>
    usage_total: {input_tokens, output_tokens, total_tokens, reasoning_tokens}
    ---

    ## User
    *2026-05-20 14:30:15Z*

    content...

    ## Assistant
    *2026-05-20 14:30:20Z · gpt-5.4-pro · 40 tokens*

    content...

    ---

    (next turn...)
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import frontmatter
from slugify import slugify

log = logging.getLogger(__name__)

TURN_SEPARATOR = "\n\n---\n\n"


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _filename_for(first_user_msg: str, created_at: str) -> str:
    slug = (
        slugify(
            first_user_msg,
            max_length=48,
            word_boundary=True,
            save_order=True,
        )
        or "conversation"
    )
    # Prefer a friendlier date prefix: 2026-05-20_143015
    dt = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
    prefix = dt.strftime("%Y-%m-%d_%H%M%S")
    return f"{prefix}-{slug}.md"


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    content: str
    timestamp: str
    # Per-turn metadata (used only on assistant turns)
    deployment: str | None = None
    response_id: str | None = None
    tokens: int | None = None
    thinking_ms: int | None = None
    reasoning_tokens: int | None = None
    reasoning_chars: int | None = None
    path: str | None = None
    response_status: str | None = None
    incomplete_reason: str | None = None


@dataclass
class TranscriptMeta:
    id: str
    created_at: str
    updated_at: str
    title: str
    endpoint: str
    deployment: str
    response_id: str | None = None
    response_status: str | None = None
    incomplete_reason: str | None = None
    response_deployment: str | None = None
    turn_count: int = 0
    usage_total: dict[str, int] = field(default_factory=dict)


@dataclass
class Transcript:
    path: Path
    meta: TranscriptMeta
    turns: list[Turn] = field(default_factory=list)

    @classmethod
    def new(
        cls,
        save_dir: Path,
        first_user_msg: str,
        endpoint: str,
        deployment: str,
        conversation_id: str | None = None,
    ) -> Transcript:
        now = _utcnow_iso()
        cid = conversation_id or uuid.uuid4().hex
        title = (first_user_msg or "").strip().splitlines()[0][:80] or "untitled"
        path = save_dir / _filename_for(first_user_msg, now)
        meta = TranscriptMeta(
            id=cid,
            created_at=now,
            updated_at=now,
            title=title,
            endpoint=endpoint,
            deployment=deployment,
        )
        return cls(path=path, meta=meta)

    def append_turn(
        self,
        user_text: str,
        assistant_text: str,
        deployment: str,
        response_id: str | None,
        usage: dict[str, Any] | None,
        response_status: str = "completed",
        incomplete_reason: str | None = None,
        thinking_ms: int | None = None,
        reasoning_chars: int | None = None,
        path: str | None = None,
    ) -> Turn:
        now = _utcnow_iso()
        self.turns.append(Turn(role="user", content=user_text, timestamp=now))
        total = (usage or {}).get("total_tokens") if isinstance(usage, dict) else None
        details = {}
        if isinstance(usage, dict):
            details = (
                usage.get("output_tokens_details")
                or usage.get("completion_tokens_details")
                or {}
            )
        reasoning_tokens = (
            details.get("reasoning_tokens")
            if isinstance(details, dict)
            else None
        )
        assistant_turn = Turn(
            role="assistant",
            content=assistant_text,
            timestamp=_utcnow_iso(),
            deployment=deployment,
            response_id=response_id,
            tokens=int(total) if isinstance(total, int) else None,
            thinking_ms=thinking_ms,
            reasoning_tokens=(
                int(reasoning_tokens)
                if isinstance(reasoning_tokens, int)
                else None
            ),
            reasoning_chars=reasoning_chars,
            path=path,
            response_status=response_status,
            incomplete_reason=incomplete_reason,
        )
        self.turns.append(assistant_turn)
        self.meta.updated_at = now
        self.meta.response_id = response_id or self.meta.response_id
        self.meta.response_status = response_status
        self.meta.incomplete_reason = incomplete_reason
        self.meta.response_deployment = deployment
        self.meta.turn_count += 1
        if isinstance(usage, dict):
            for k in ("input_tokens", "output_tokens", "total_tokens"):
                if k in usage and isinstance(usage[k], int):
                    self.meta.usage_total[k] = self.meta.usage_total.get(k, 0) + usage[k]
            r = (
                details.get("reasoning_tokens")
                if isinstance(details, dict)
                else None
            )
            if isinstance(r, int):
                self.meta.usage_total["reasoning_tokens"] = (
                    self.meta.usage_total.get("reasoning_tokens", 0) + r
                )
        return assistant_turn

    def _serialize_body(self) -> str:
        blocks: list[str] = []
        for i in range(0, len(self.turns), 2):
            u = self.turns[i]
            a = self.turns[i + 1] if i + 1 < len(self.turns) else None
            blocks.append(f"## User\n*{u.timestamp}*\n\n{u.content.rstrip()}\n")
            if a is not None:
                ann_bits = [a.timestamp]
                if a.deployment:
                    ann_bits.append(a.deployment)
                if a.tokens is not None:
                    ann_bits.append(f"{a.tokens} tokens")
                ann = " · ".join(ann_bits)
                blocks.append(f"## Assistant\n*{ann}*\n\n{a.content.rstrip()}\n")
        return TURN_SEPARATOR.join(blocks) + "\n"

    def write_atomic(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        turn_metadata = [
            {
                "deployment": turn.deployment,
                "response_id": turn.response_id,
                "tokens": turn.tokens,
                "thinking_ms": turn.thinking_ms,
                "reasoning_tokens": turn.reasoning_tokens,
                "reasoning_chars": turn.reasoning_chars,
                "path": turn.path,
                "response_status": turn.response_status,
                "incomplete_reason": turn.incomplete_reason,
            }
            for turn in self.turns
            if turn.role == "assistant"
        ]
        meta_dict = {
            "id": self.meta.id,
            "title": self.meta.title,
            "created_at": self.meta.created_at,
            "updated_at": self.meta.updated_at,
            "endpoint": self.meta.endpoint,
            "deployment": self.meta.deployment,
            "response_id": self.meta.response_id,
            "response_status": self.meta.response_status,
            "incomplete_reason": self.meta.incomplete_reason,
            "response_deployment": self.meta.response_deployment,
            "turn_count": self.meta.turn_count,
            "usage_total": dict(self.meta.usage_total),
            "turn_metadata": turn_metadata,
        }
        post = frontmatter.Post(self._serialize_body(), **meta_dict)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(frontmatter.dumps(post), "utf-8")
        tmp.replace(self.path)

    @classmethod
    def load(cls, path: Path) -> Transcript:
        post = frontmatter.load(str(path))
        m = post.metadata
        meta = TranscriptMeta(
            id=str(m.get("id", path.stem)),
            created_at=str(m.get("created_at", _utcnow_iso())),
            updated_at=str(m.get("updated_at", _utcnow_iso())),
            title=str(m.get("title", path.stem)),
            endpoint=str(m.get("endpoint", "")),
            deployment=str(m.get("deployment", "")),
            response_id=m.get("response_id"),
            response_status=m.get("response_status"),
            incomplete_reason=m.get("incomplete_reason"),
            response_deployment=m.get("response_deployment"),
            turn_count=int(m.get("turn_count", 0) or 0),
            usage_total=dict(m.get("usage_total") or {}),
        )
        turns = _parse_body(post.content)
        assistant_metadata = iter(m.get("turn_metadata") or [])
        for turn in turns:
            if turn.role != "assistant":
                continue
            raw = next(assistant_metadata, None)
            if not isinstance(raw, dict):
                continue
            turn.deployment = raw.get("deployment") or turn.deployment
            turn.response_id = raw.get("response_id")
            tokens = raw.get("tokens")
            turn.tokens = int(tokens) if isinstance(tokens, int) else turn.tokens
            thinking_ms = raw.get("thinking_ms")
            turn.thinking_ms = (
                int(thinking_ms) if isinstance(thinking_ms, int) else None
            )
            reasoning_tokens = raw.get("reasoning_tokens")
            turn.reasoning_tokens = (
                int(reasoning_tokens)
                if isinstance(reasoning_tokens, int)
                else None
            )
            reasoning_chars = raw.get("reasoning_chars")
            turn.reasoning_chars = (
                int(reasoning_chars)
                if isinstance(reasoning_chars, int)
                else None
            )
            raw_path = raw.get("path")
            turn.path = (
                raw_path
                if raw_path in {"responses", "chat"}
                else ("responses" if turn.response_id else None)
            )
            turn.response_status = raw.get("response_status")
            turn.incomplete_reason = raw.get("incomplete_reason")
        if not meta.response_deployment:
            last_assistant = next(
                (turn for turn in reversed(turns) if turn.role == "assistant"),
                None,
            )
            if last_assistant:
                meta.response_deployment = last_assistant.deployment
                meta.response_status = (
                    meta.response_status or last_assistant.response_status
                )
                meta.incomplete_reason = (
                    meta.incomplete_reason or last_assistant.incomplete_reason
                )
        return cls(path=path, meta=meta, turns=turns)


# ---------------------------------------------------------------------------
# Body parsing (best-effort; relies on our own `## User` / `## Assistant` shape)
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r"^##\s+(User|Assistant)\s*$", re.MULTILINE)
_ANNOTATION_RE = re.compile(r"^\*(?P<ts>[^*]+?)\*\s*$", re.MULTILINE)


def _parse_body(body: str) -> list[Turn]:
    matches = list(_HEADER_RE.finditer(body))
    turns: list[Turn] = []
    for i, m in enumerate(matches):
        role = m.group(1).lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        chunk = body[start:end].strip()
        # Strip the leading separator on subsequent turns
        chunk = chunk.rstrip("\n").rstrip("-").rstrip()
        ts = ""
        deployment = None
        tokens = None
        ann = _ANNOTATION_RE.match(chunk)
        if ann:
            annotation = [part.strip() for part in ann.group("ts").split("·")]
            ts = annotation[0]
            if role == "assistant":
                for part in annotation[1:]:
                    token_match = re.fullmatch(r"(\d+)\s+tokens?", part)
                    if token_match:
                        tokens = int(token_match.group(1))
                    elif deployment is None:
                        deployment = part
            chunk = chunk[ann.end():].lstrip("\n")
        turns.append(
            Turn(
                role=role,
                content=chunk,
                timestamp=ts,
                deployment=deployment,
                tokens=tokens,
            )
        )
    return turns


# ---------------------------------------------------------------------------
# Directory-level helpers
# ---------------------------------------------------------------------------


def find_by_id(save_dir: Path, conversation_id: str) -> Path | None:
    if not save_dir.exists():
        return None
    for p in save_dir.glob("*.md"):
        try:
            m = frontmatter.load(str(p)).metadata
            if str(m.get("id", "")) == conversation_id:
                return p
        except Exception:
            continue
    return None


def list_summaries(save_dir: Path) -> list[dict[str, Any]]:
    if not save_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in save_dir.glob("*.md"):
        try:
            m = frontmatter.load(str(p)).metadata
            out.append({
                "id": str(m.get("id", p.stem)),
                "title": str(m.get("title", p.stem)),
                "created_at": str(m.get("created_at", "")),
                "updated_at": str(m.get("updated_at", "")),
                "deployment": str(m.get("deployment", "")),
                "turn_count": int(m.get("turn_count", 0) or 0),
                "usage_total": dict(m.get("usage_total") or {}),
                "filename": p.name,
            })
        except Exception as e:
            log.warning("could not parse %s: %s", p, e)
    out.sort(key=lambda r: r["updated_at"] or r["created_at"], reverse=True)
    return out
