"""Azure managed-identity token provider for AOAI.

For public Azure, tries the new Responses-API scope first
(`https://ai.azure.com/.default`) and falls back to the legacy
`https://cognitiveservices.azure.com/.default` on auth failure. Sovereign
cloud scopes never fall back across cloud boundaries.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from functools import lru_cache

from azure.core.exceptions import ClientAuthenticationError
from azure.identity import (
    AzureAuthorityHosts,
    CredentialUnavailableError,
    DefaultAzureCredential,
    get_bearer_token_provider,
)

log = logging.getLogger(__name__)

PRIMARY_SCOPE = "https://ai.azure.com/.default"
FALLBACK_SCOPE = "https://cognitiveservices.azure.com/.default"
US_GOV_SCOPE = "https://cognitiveservices.azure.us/.default"
CHINA_SCOPE = "https://cognitiveservices.azure.cn/.default"
SOVEREIGN_SCOPES = {US_GOV_SCOPE, CHINA_SCOPE}


class AuthError(Exception):
    """Raised when no Azure credential is usable (typically: needs `az login`)."""


def _authority_for_scope(scope: str) -> str | None:
    if scope == US_GOV_SCOPE:
        return AzureAuthorityHosts.AZURE_GOVERNMENT
    if scope == CHINA_SCOPE:
        return AzureAuthorityHosts.AZURE_CHINA
    return None


@lru_cache(maxsize=3)
def _credential(authority: str | None = None) -> DefaultAzureCredential:
    # exclude_interactive_browser_credential left at default (excluded)
    # so we don't pop a browser in headless contexts.
    if authority:
        return DefaultAzureCredential(authority=authority)
    return DefaultAzureCredential()


def acquire_token(scope: str) -> str:
    """Synchronously acquire a bearer token for a single scope.

    Use this in unit tests / one-shot checks. For OpenAI SDK integration,
    use `make_token_provider()` which returns an auto-refreshing callable.
    """
    try:
        authority = _authority_for_scope(scope)
        return _credential(authority).get_token(scope).token
    except (CredentialUnavailableError, ClientAuthenticationError) as e:
        raise AuthError(f"failed to acquire token for {scope}: {e}") from e


def make_token_provider(scope: str | None = None) -> Callable[[], str]:
    """Return an auto-refreshing token provider for the OpenAI SDK.

    The returned callable matches what `openai.OpenAI(api_key=...)` expects
    when used with Microsoft Entra ID auth.

    On first failure with the primary scope, sticks to the fallback for
    subsequent calls (avoids a per-call retry storm).
    """
    primary_scope = scope or PRIMARY_SCOPE
    cred = _credential(_authority_for_scope(primary_scope))
    primary = get_bearer_token_provider(cred, primary_scope)
    if primary_scope in SOVEREIGN_SCOPES:
        def sovereign_provider() -> str:
            try:
                return primary()
            except Exception as e:
                raise AuthError(
                    f"scope {primary_scope} failed: {e}"
                ) from e

        return sovereign_provider

    fallback = get_bearer_token_provider(cred, FALLBACK_SCOPE)

    state = {"use_fallback": primary_scope == FALLBACK_SCOPE}

    def provider() -> str:
        if state["use_fallback"]:
            try:
                return fallback()
            except Exception as e:
                raise AuthError(f"fallback scope failed: {e}") from e
        try:
            return primary()
        except Exception as e:
            log.warning(
                "primary scope %s failed (%s); switching to %s",
                primary_scope, e, FALLBACK_SCOPE,
            )
            state["use_fallback"] = True
            try:
                return fallback()
            except Exception as e2:
                raise AuthError(
                    f"both scopes failed; primary={e}; fallback={e2}"
                ) from e2

    return provider
