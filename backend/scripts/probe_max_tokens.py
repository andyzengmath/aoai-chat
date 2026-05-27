"""Probe the Responses API to find the empirical max_output_tokens cap.

Sends requests with progressively higher caps until Azure rejects with the
"max_output_tokens too large" error. The error body usually states the
actual ceiling.
"""
from __future__ import annotations

import asyncio

from openai import AsyncOpenAI

from app.auth import make_token_provider
from app.settings import effective_config, normalize_endpoint


CANDIDATES = [
    65_536,
    131_072,
    200_000,
    300_000,
    500_000,
    1_000_000,
    10_000_000,
]


async def probe_one(client: AsyncOpenAI, model: str, cap: int) -> None:
    """Send a tiny request with the given max_output_tokens cap.

    We don't care about the actual output — just whether the cap was accepted.
    `stream=False` keeps the response self-contained.
    """
    try:
        resp = await client.responses.create(
            model=model,
            input="ping",
            max_output_tokens=cap,
            stream=False,
            store=False,
        )
        status = getattr(resp, "status", "ok")
        out = getattr(resp, "output_text", "") or ""
        print(f"  {cap:>10,} → ACCEPTED  status={status} out={out[:30]!r}")
    except Exception as e:
        msg = str(e).replace("\n", " ")
        # Truncate long error bodies but keep the meaningful bit
        if len(msg) > 280:
            msg = msg[:280] + "…"
        print(f"  {cap:>10,} → REJECTED  {msg}")


async def main() -> None:
    cfg = effective_config()
    endpoint = normalize_endpoint(cfg.endpoint)
    token = make_token_provider()()
    client = AsyncOpenAI(base_url=f"{endpoint}openai/v1/", api_key=token)

    for model in ("gpt-5.4-pro", "gpt-5.5"):
        print(f"\n=== {model} ===")
        for cap in CANDIDATES:
            await probe_one(client, model, cap)


if __name__ == "__main__":
    asyncio.run(main())
