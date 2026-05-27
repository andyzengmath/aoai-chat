"""Phase 5 end-to-end: chat -> transcript on disk -> list -> get -> delete."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

CONV_DIR = Path("C:/Users/andyzeng/Git/aoai_chat/conversations")
BASE = "http://127.0.0.1:8765"


async def stream_turn(client, content, deployment, conversation_id=None, previous_response_id=None):
    body = {
        "deployment": deployment,
        "content": content,
        "conversation_id": conversation_id,
        "previous_response_id": previous_response_id,
    }
    summary = {"meta": None, "start": None, "deltas": 0, "text": "", "done": None, "saved": None, "error": None}
    async with client.stream(
        "POST", f"{BASE}/api/chat",
        content=json.dumps(body),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        timeout=120.0,
    ) as r:
        if r.status_code != 200:
            summary["error"] = await r.aread()
            return summary
        evt = "message"
        async for line in r.aiter_lines():
            if not line:
                continue
            if line.startswith("event:"):
                evt = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                try:
                    data = json.loads(line.split(":", 1)[1].strip())
                except json.JSONDecodeError:
                    continue
                if evt == "meta":
                    summary["meta"] = data
                elif evt == "start":
                    summary["start"] = data
                elif evt == "delta":
                    summary["deltas"] += 1
                    summary["text"] += data.get("text", "")
                elif evt == "done":
                    summary["done"] = data
                elif evt == "saved":
                    summary["saved"] = data
                elif evt == "error":
                    summary["error"] = data
    return summary


async def main():
    if CONV_DIR.exists():
        for p in CONV_DIR.glob("*.md"):
            p.unlink()

    async with httpx.AsyncClient() as c:
        print("=== Turn 1 (no conversation_id) ===")
        t1 = await stream_turn(c, "Say SQUIRREL and nothing else.", "gpt-5.4-pro")
        print(f"  meta:  {t1['meta']}")
        print(f"  text:  {t1['text']!r}")
        print(f"  saved: {t1['saved']}")
        assert t1["meta"] and t1["meta"]["conversation_id"], t1
        assert t1["saved"] and t1["saved"]["turn_count"] == 1, t1
        cid = t1["meta"]["conversation_id"]
        fname = t1["saved"]["filename"]

        print(f"\n=== Files on disk ===")
        for p in sorted(CONV_DIR.glob("*.md")):
            print(f"  {p.name}  ({p.stat().st_size}B)")
        assert (CONV_DIR / fname).exists(), f"expected {fname}"

        print(f"\n=== Turn 2 (same conversation, chained) ===")
        prev = (t1["done"] or {}).get("response_id")
        t2 = await stream_turn(c, "Say it again.", "gpt-5.4-pro", conversation_id=cid, previous_response_id=prev)
        print(f"  text:  {t2['text']!r}")
        print(f"  saved: {t2['saved']}")
        assert t2["saved"] and t2["saved"]["turn_count"] == 2, t2

        print(f"\n=== Disk count (should still be 1 file) ===")
        files = sorted(CONV_DIR.glob("*.md"))
        for p in files: print(f"  {p.name}  ({p.stat().st_size}B)")
        assert len(files) == 1, f"expected 1 file, got {len(files)}"

        print(f"\n=== File contents ===")
        print((CONV_DIR / fname).read_text("utf-8"))

        print(f"\n=== GET /api/conversations ===")
        r = await c.get(f"{BASE}/api/conversations")
        listing = r.json()
        print(json.dumps(listing, indent=2))
        assert listing["count"] == 1
        assert listing["data"][0]["id"] == cid
        assert listing["data"][0]["turn_count"] == 2

        print(f"\n=== GET /api/conversations/{cid} ===")
        r = await c.get(f"{BASE}/api/conversations/{cid}")
        detail = r.json()
        print(f"  turns: {len(detail['turns'])} (expect 4: u/a/u/a)")
        for tn in detail["turns"]:
            print(f"    {tn['role']:9} ({tn.get('tokens')} tok): {tn['content'][:60]!r}")
        assert len(detail["turns"]) == 4, detail
        assert detail["turns"][0]["role"] == "user"
        assert detail["turns"][1]["role"] == "assistant"
        assert "squirrel" in detail["turns"][1]["content"].lower()

        print(f"\n=== DELETE /api/conversations/{cid} ===")
        r = await c.delete(f"{BASE}/api/conversations/{cid}")
        print(r.json())
        assert not (CONV_DIR / fname).exists(), "file should be gone"

        r = await c.get(f"{BASE}/api/conversations")
        assert r.json()["count"] == 0

        print("\nALL PHASE 5 TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
