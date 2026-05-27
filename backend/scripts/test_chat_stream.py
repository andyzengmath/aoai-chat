"""Smoke test the /api/chat SSE endpoint.

Sends two turns to gpt-5.4-pro to verify Responses-API path + previous_response_id chaining.
"""
from __future__ import annotations

import asyncio
import json

import httpx


async def stream_turn(
    client: httpx.AsyncClient,
    *,
    content: str,
    deployment: str,
    previous_response_id: str | None = None,
) -> dict:
    """Send one /api/chat call, print events as they arrive, return summary dict."""
    body = {
        "deployment": deployment,
        "content": content,
        "previous_response_id": previous_response_id,
    }
    payload = json.dumps(body)
    print(f"\n>>> POST /api/chat (prev_id={previous_response_id!r}): {content!r}")

    summary: dict = {"start": None, "deltas": 0, "text": "", "done": None, "error": None}
    async with client.stream(
        "POST",
        "http://127.0.0.1:8765/api/chat",
        content=payload,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        timeout=120.0,
    ) as resp:
        if resp.status_code != 200:
            err_body = await resp.aread()
            print(f"!! status={resp.status_code} body={err_body[:300]!r}")
            summary["error"] = f"status_{resp.status_code}"
            return summary

        current_event = "message"
        async for line in resp.aiter_lines():
            if not line:
                continue
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload_str = line.split(":", 1)[1].strip()
                try:
                    data = json.loads(payload_str)
                except json.JSONDecodeError:
                    data = payload_str

                if current_event == "start":
                    summary["start"] = data
                    print(f"  start: {data}")
                elif current_event == "delta":
                    summary["deltas"] += 1
                    t = data.get("text", "") if isinstance(data, dict) else ""
                    summary["text"] += t
                    print(t, end="", flush=True)
                elif current_event == "done":
                    summary["done"] = data
                    print(f"\n  done: {data}")
                elif current_event == "error":
                    summary["error"] = data
                    print(f"\n  error: {data}")
                elif current_event == "fallback":
                    print(f"\n  fallback: {data}")
    return summary


async def main() -> None:
    async with httpx.AsyncClient() as client:
        turn1 = await stream_turn(
            client,
            content="Reply with exactly the word PING and nothing else.",
            deployment="gpt-5.4-pro",
        )
        assert turn1["start"], f"no start event: {turn1}"
        assert turn1["deltas"] > 0, f"no deltas: {turn1}"
        assert turn1["done"], f"no done: {turn1}"
        assert not turn1["error"], f"error: {turn1}"
        prev_id = (turn1["done"] or {}).get("response_id")
        assert prev_id, f"no response_id in done: {turn1}"
        print(f"\n[turn1 ok] response_id={prev_id} chars={len(turn1['text'])}")

        turn2 = await stream_turn(
            client,
            content="What word did you just say? Reply with just that word.",
            deployment="gpt-5.4-pro",
            previous_response_id=prev_id,
        )
        assert turn2["start"], f"no start: {turn2}"
        assert turn2["deltas"] > 0, f"no deltas: {turn2}"
        assert turn2["done"], f"no done: {turn2}"
        assert "ping" in turn2["text"].lower(), f"context not chained: {turn2['text']!r}"
        print(f"\n[turn2 ok] chained context proven (saw 'PING' in response)")

        print("\nALL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
