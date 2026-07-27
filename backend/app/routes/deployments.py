"""Deployment management.

`GET  /api/deployments`            → list known deployments + capabilities
`POST /api/deployments`            → add a deployment name (idempotent)
`DELETE /api/deployments/{name}`   → remove a deployment name
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.aoai_client import list_known_deployments, supports_responses_api
from app.settings import (
    DEFAULT_DEPLOYMENTS,
    PersistedConfig,
    effective_config,
    load_persisted,
    save_persisted,
)


def _load_with_defaults() -> PersistedConfig:
    """Materialize default deployments when the persisted list is empty.

    This keeps the GET-view and the mutation-view consistent: an empty
    `known_deployments` field on disk is interpreted as "use defaults" by
    `effective_config()`, so mutations should see the same set.
    """
    p = load_persisted()
    if not p.known_deployments:
        p.known_deployments = list(DEFAULT_DEPLOYMENTS)
    return p

router = APIRouter(prefix="/api/deployments", tags=["deployments"])


class AddDeployment(BaseModel):
    name: str = Field(min_length=1, max_length=128)


def _serialize() -> dict:
    cfg = effective_config()
    items = list_known_deployments()
    return {
        "count": len(items),
        "default": cfg.default_deployment,
        "data": [
            {
                "id": d.id,
                "model": d.model,
                "model_version": d.model_version,
                "supports_responses_api": d.supports_responses_api,
                "reasoning_efforts": list(d.reasoning_efforts),
                "reasoning_modes": list(d.reasoning_modes),
                "context_window_tokens": d.context_window_tokens,
                "max_input_tokens": d.max_input_tokens,
                "max_output_tokens": d.max_output_tokens,
            }
            for d in items
        ],
    }


@router.get("")
async def get_deployments() -> dict:
    return _serialize()


@router.post("")
async def add_deployment(body: AddDeployment) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    persisted = _load_with_defaults()
    if name not in persisted.known_deployments:
        persisted.known_deployments = [*persisted.known_deployments, name]
    if not persisted.default_deployment:
        persisted.default_deployment = persisted.known_deployments[0]
    save_persisted(persisted)
    return {
        "added": name,
        "supports_responses_api": supports_responses_api(name),
        **_serialize(),
    }


@router.delete("/{name}")
async def remove_deployment(name: str) -> dict:
    persisted = _load_with_defaults()
    if name not in persisted.known_deployments:
        raise HTTPException(404, f"{name!r} not in known_deployments")
    persisted.known_deployments = [d for d in persisted.known_deployments if d != name]
    if persisted.default_deployment == name:
        persisted.default_deployment = (
            persisted.known_deployments[0] if persisted.known_deployments else ""
        )
    save_persisted(persisted)
    return {"removed": name, **_serialize()}
