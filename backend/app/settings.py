"""App configuration.

Two layers:
- Persisted JSON at `<repo>/.aoai-chat/config.json` (written by Settings UI)
- Env-var overrides via `.env` or process environment (takes precedence)

`effective_config()` returns the merged view.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / ".aoai-chat"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_SAVE_DIR = REPO_ROOT / "conversations"


DEFAULT_DEPLOYMENTS = ["gpt-5.6-sol", "gpt-5.4-pro", "gpt-5.5"]


class PersistedConfig(BaseModel):
    """User-editable config persisted to .aoai-chat/config.json."""

    endpoint: str = ""
    default_deployment: str = ""
    # Azure OpenAI's data plane does not expose deployment enumeration; the user
    # manages this list. Defaults seed the two known Responses-API models.
    known_deployments: list[str] = []
    api_version: str = "2025-01-01-preview"
    save_dir: str = ""  # empty -> uses DEFAULT_SAVE_DIR
    theme: str = "dark"
    token_scope: str = ""  # empty -> auto (ai.azure.com then cognitiveservices)


class EnvOverrides(BaseSettings):
    AOAI_ENDPOINT: str = ""
    AOAI_DEPLOYMENT: str = ""
    ENDPOINT_URL: str = ""
    DEPLOYMENT_NAME: str = ""
    AOAI_API_VERSION: str = ""
    AOAI_SAVE_DIR: str = ""
    AOAI_TOKEN_SCOPE: str = ""
    AOAI_HOST: str = "127.0.0.1"
    AOAI_PORT: int = 8765

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


def load_persisted() -> PersistedConfig:
    if not CONFIG_FILE.exists():
        return PersistedConfig()
    try:
        data: dict[str, Any] = json.loads(CONFIG_FILE.read_text("utf-8"))
        return PersistedConfig.model_validate(data)
    except Exception as e:
        log.warning("invalid %s: %s — using defaults", CONFIG_FILE, e)
        return PersistedConfig()


def save_persisted(cfg: PersistedConfig) -> None:
    """Atomic write: tmp file + os.replace()."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(cfg.model_dump_json(indent=2), "utf-8")
    tmp.replace(CONFIG_FILE)


def effective_config() -> PersistedConfig:
    """Persisted JSON ∪ env vars (env wins where non-empty)."""
    persisted = load_persisted()
    env = EnvOverrides()
    # Seed known_deployments with the defaults if neither persisted nor env set it.
    known = persisted.known_deployments or list(DEFAULT_DEPLOYMENTS)
    return PersistedConfig(
        endpoint=env.AOAI_ENDPOINT or env.ENDPOINT_URL or persisted.endpoint,
        default_deployment=(
            env.AOAI_DEPLOYMENT
            or env.DEPLOYMENT_NAME
            or persisted.default_deployment
            or (known[0] if known else "")
        ),
        known_deployments=known,
        api_version=env.AOAI_API_VERSION or persisted.api_version,
        save_dir=env.AOAI_SAVE_DIR or persisted.save_dir or str(DEFAULT_SAVE_DIR),
        theme=persisted.theme,
        token_scope=env.AOAI_TOKEN_SCOPE or persisted.token_scope,
    )


def ensure_save_dir(cfg: PersistedConfig) -> Path:
    p = Path(cfg.save_dir or DEFAULT_SAVE_DIR).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def normalize_endpoint(url: str) -> str:
    """Strip trailing slash variations; ensure single trailing slash."""
    return url.strip().rstrip("/") + "/"
