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
from urllib.parse import urlsplit

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / ".aoai-chat"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_SAVE_DIR = REPO_ROOT / "conversations"


DEFAULT_DEPLOYMENTS = ["gpt-5.6-sol", "gpt-5.4-pro", "gpt-5.5"]
AZURE_AI_HOST_SUFFIXES = (
    ".openai.azure.com",
    ".services.ai.azure.com",
    ".cognitiveservices.azure.com",
    ".openai.azure.us",
    ".services.ai.azure.us",
    ".cognitiveservices.azure.us",
    ".openai.azure.cn",
    ".services.ai.azure.cn",
    ".cognitiveservices.azure.cn",
)


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
    known = list(persisted.known_deployments or DEFAULT_DEPLOYMENTS)
    default_deployment = (
        env.AOAI_DEPLOYMENT
        or env.DEPLOYMENT_NAME
        or persisted.default_deployment
        or (known[0] if known else "")
    )
    if default_deployment and default_deployment not in known:
        known.insert(0, default_deployment)
    return PersistedConfig(
        endpoint=env.AOAI_ENDPOINT or env.ENDPOINT_URL or persisted.endpoint,
        default_deployment=default_deployment,
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


def validate_azure_endpoint(url: str) -> str:
    """Return a normalized endpoint restricted to Azure AI data-plane hosts."""
    candidate = url.strip()
    parsed = urlsplit(candidate)
    try:
        port = parsed.port
    except ValueError as e:
        raise ValueError("endpoint has an invalid port") from e
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise ValueError("endpoint must use https")
    if not host or not any(
        host.endswith(suffix) and len(host) > len(suffix)
        for suffix in AZURE_AI_HOST_SUFFIXES
    ):
        raise ValueError("endpoint must use an official Azure AI hostname")
    if parsed.username or parsed.password:
        raise ValueError("endpoint must not contain user information")
    if port not in (None, 443):
        raise ValueError("endpoint port must be 443")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("endpoint must be an Azure AI origin URL")
    return normalize_endpoint(candidate)


def validate_token_scope(scope: str, endpoint: str = "") -> str:
    """Return a token scope paired with the endpoint's Azure cloud."""
    from app.auth import (
        CHINA_SCOPE,
        FALLBACK_SCOPE,
        PRIMARY_SCOPE,
        US_GOV_SCOPE,
    )

    candidate = scope.strip()
    if endpoint:
        validated_endpoint = validate_azure_endpoint(endpoint)
        host = (urlsplit(validated_endpoint).hostname or "").lower()
        if host.endswith(".azure.us"):
            allowed = {US_GOV_SCOPE}
            default_scope = US_GOV_SCOPE
        elif host.endswith(".azure.cn"):
            allowed = {CHINA_SCOPE}
            default_scope = CHINA_SCOPE
        else:
            allowed = {PRIMARY_SCOPE, FALLBACK_SCOPE}
            default_scope = ""
    else:
        allowed = {
            PRIMARY_SCOPE,
            FALLBACK_SCOPE,
            US_GOV_SCOPE,
            CHINA_SCOPE,
        }
        default_scope = ""
    if not candidate:
        return default_scope
    if candidate not in allowed:
        raise ValueError("token scope must target a supported Azure AI audience")
    return candidate
