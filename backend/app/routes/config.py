"""GET/PUT /api/config and POST /api/config/test-auth."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.auth import AuthError, make_token_provider
from app.settings import (
    PersistedConfig,
    effective_config,
    ensure_save_dir,
    load_persisted,
    normalize_endpoint,
    save_persisted,
)

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigUpdate(BaseModel):
    endpoint: str | None = None
    default_deployment: str | None = None
    known_deployments: list[str] | None = None
    api_version: str | None = None
    save_dir: str | None = None
    theme: str | None = None
    token_scope: str | None = None


@router.get("")
async def get_config() -> dict:
    cfg = effective_config()
    payload = cfg.model_dump()
    payload["save_dir"] = str(ensure_save_dir(cfg))
    payload["configured"] = bool(cfg.endpoint)
    return payload


@router.put("")
async def put_config(update: ConfigUpdate) -> dict:
    current = load_persisted()
    data = current.model_dump()
    for k, v in update.model_dump(exclude_none=True).items():
        data[k] = v

    ep = data.get("endpoint", "")
    if ep:
        if not (ep.startswith("https://") or ep.startswith("http://")):
            raise HTTPException(400, "endpoint must start with https:// or http://")
        data["endpoint"] = normalize_endpoint(ep)

    save_dir = data.get("save_dir", "")
    if save_dir:
        try:
            Path(save_dir).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(400, f"save_dir invalid: {e}") from e

    new_cfg = PersistedConfig.model_validate(data)
    save_persisted(new_cfg)
    return {**new_cfg.model_dump(), "configured": bool(new_cfg.endpoint)}


@router.post("/test-auth")
async def test_auth() -> dict:
    """Sanity-check the managed-identity flow without touching AOAI itself."""
    cfg = effective_config()
    scope = cfg.token_scope or None  # None → primary then fallback
    try:
        provider = make_token_provider(scope)
        token = provider()
    except AuthError as e:
        raise HTTPException(
            401,
            detail={
                "error": "az_login_required",
                "hint": "Run `az login` (or set up managed identity), then retry.",
                "underlying": str(e),
            },
        ) from e
    return {
        "ok": True,
        "scope": scope or "auto",
        "token_prefix": token[:12] + "…",
        "token_len": len(token),
    }
