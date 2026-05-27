"""Dump every event type emitted by the Responses API with reasoning.summary.

Helps figure out the exact event name we need to forward for the "thinking…"
streaming feature.
"""
from __future__ import annotations

import asyncio

from openai import AsyncOpenAI

from app.auth import make_token_provider
from app.settings import effective_config, normalize_endpoint


async def main() -> None:
    cfg = effective_config()
    endpoint = normalize_endpoint(cfg.endpoint)
    token = make_token_provider()()
    client = AsyncOpenAI(base_url=f"{endpoint}openai/v1/", api_key=token)

    seen_types: dict[str, int] = {}
    sample_delta: dict[str, str] = {}

    print("Sending: gpt-5.4-pro + summary='auto'...")
    stream = await client.responses.create(
        model="gpt-5.4-pro",
        input="Outline a proof that there are infinitely many primes of the form 4k+3 in Euclid's style. Be terse.",
        stream=True,
        store=True,
        reasoning={"effort": "medium", "summary": "auto"},
    )
    async for event in stream:
        t = getattr(event, "type", "") or "?"
        seen_types[t] = seen_types.get(t, 0) + 1
        if t not in sample_delta:
            # snapshot a sample
            delta = getattr(event, "delta", None)
            if isinstance(delta, str) and delta:
                sample_delta[t] = delta[:80]

    print("\n=== Event type counts ===")
    for t, n in sorted(seen_types.items()):
        sample = sample_delta.get(t, "")
        print(f"  {n:4} x {t}    {repr(sample) if sample else ''}")


if __name__ == "__main__":
    asyncio.run(main())
