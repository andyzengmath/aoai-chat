"""One-shot diagnostic: what does the AOAI endpoint expose?"""
from __future__ import annotations

import httpx

from app.auth import make_token_provider
from app.settings import effective_config, normalize_endpoint


def main() -> None:
    cfg = effective_config()
    endpoint = normalize_endpoint(cfg.endpoint)
    token = make_token_provider()()
    H = {"Authorization": f"Bearer {token}"}

    with httpx.Client(timeout=10.0) as c:
        r = c.get(
            f"{endpoint}openai/models",
            headers=H,
            params={"api-version": "2024-10-21"},
        )
        items = r.json().get("data", [])

        print("=== matches for gpt-5 / pro ===")
        matches = [
            m for m in items
            if "gpt-5" in m.get("id", "").lower() or "pro" in m.get("id", "").lower()
        ]
        for m in matches:
            caps = m.get("capabilities", {})
            mid = m.get("id", "")
            cc = caps.get("chat_completion")
            life = m.get("lifecycle_status")
            status = m.get("status")
            print(f"  {mid:45} status={status:12} cc={cc} life={life}")
        if not matches:
            print("  (no gpt-5 or 'pro' entries)")

        chat = [
            m for m in items
            if m.get("capabilities", {}).get("chat_completion")
            and m.get("status") == "succeeded"
            and m.get("lifecycle_status") not in ("deprecated",)
        ]
        print(f"\n=== chat-capable, non-deprecated: {len(chat)} ===")
        for m in chat[:25]:
            mid = m.get("id", "")
            life = m.get("lifecycle_status")
            print(f"  {mid:45} life={life}")
        if len(chat) > 25:
            print(f"  ... and {len(chat) - 25} more")

        print("\n=== probe: POST chat/completions to deployment 'gpt-5.4-pro' ===")
        try:
            r2 = c.post(
                f"{endpoint}openai/deployments/gpt-5.4-pro/chat/completions",
                headers={**H, "Content-Type": "application/json"},
                params={"api-version": "2025-01-01-preview"},
                json={
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_completion_tokens": 5,
                },
            )
            print(f"  status: {r2.status_code}")
            print(f"  body:   {r2.text[:300]}")
        except Exception as e:
            print(f"  ERR {e}")

        print("\n=== probe: POST /openai/v1/responses for gpt-5.4-pro ===")
        try:
            r3 = c.post(
                f"{endpoint}openai/v1/responses",
                headers={**H, "Content-Type": "application/json"},
                json={"model": "gpt-5.4-pro", "input": "ping", "max_output_tokens": 16},
            )
            print(f"  status: {r3.status_code}")
            print(f"  body:   {r3.text[:300]}")
        except Exception as e:
            print(f"  ERR {e}")


if __name__ == "__main__":
    main()
