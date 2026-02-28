"""MSAL authentication for Copilot Studio Direct-to-Engine API.

Supports two flows:
1. Interactive browser login (PublicClientApplication) — for local dev
2. Client credentials (ConfidentialClientApplication) — for automated eval runs
"""

import os

import msal
from loguru import logger

SCOPES = ["https://api.powerplatform.com/.default"]
TOKEN_CACHE_FILE = ".local_token_cache.json"


class LocalTokenCache(msal.SerializableTokenCache):
    """File-based MSAL token cache."""

    def __init__(self, cache_file: str = TOKEN_CACHE_FILE):
        super().__init__()
        self.cache_file = cache_file
        if os.path.exists(self.cache_file):
            self.deserialize(open(self.cache_file).read())

    def save(self) -> None:
        if self.has_state_changed:
            with open(self.cache_file, "w") as f:
                f.write(self.serialize())


def acquire_token(
    tenant_id: str,
    client_id: str,
    client_secret: str | None = None,
) -> str:
    """Acquire a Power Platform API token.

    Returns:
        Bearer token string.

    Raises:
        RuntimeError: If token acquisition fails.
    """
    authority = f"https://login.microsoftonline.com/{tenant_id}"

    if client_secret:
        logger.info("Auth flow: confidential (client credentials)")
        return _acquire_confidential(authority, client_id, client_secret)
    logger.info("Auth flow: interactive (delegated, no client secret)")
    return _acquire_interactive(authority, client_id)


def _acquire_interactive(authority: str, client_id: str) -> str:
    """Interactive browser login with token caching."""
    cache = LocalTokenCache()
    app = msal.PublicClientApplication(
        client_id,
        authority=authority,
        token_cache=cache,
    )

    accounts = app.get_accounts()
    if accounts:
        logger.debug(f"Found {len(accounts)} cached account(s), trying silent acquisition")
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            cache.save()
            token = result["access_token"]
            logger.info(f"Token acquired silently (len={len(token)}, prefix={token[:8]}...)")
            return token
        if result:
            err = result.get("error_description", result.get("error", "no details"))
            logger.warning(f"Silent acquisition failed: {err}")
        else:
            logger.warning("Silent acquisition returned None — cache may be stale")

    logger.info("Opening browser for interactive login...")
    result = app.acquire_token_interactive(
        scopes=SCOPES,
        prompt="select_account",
    )

    if "access_token" not in result:
        error = result.get("error_description", result.get("error", "Unknown error"))
        raise RuntimeError(f"Interactive token acquisition failed: {error}")

    cache.save()
    logger.info("Token acquired via interactive login")
    return result["access_token"]


def _acquire_confidential(authority: str, client_id: str, client_secret: str) -> str:
    """Client credentials flow for automated/CI runs."""
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret,
    )

    result = app.acquire_token_for_client(scopes=SCOPES)

    if "access_token" not in result:
        error = result.get("error_description", result.get("error", "Unknown error"))
        raise RuntimeError(f"Client credentials token acquisition failed: {error}")

    logger.info("Token acquired via client credentials")
    return result["access_token"]


def acquire_token_from_env() -> str:
    """Acquire token using environment variables."""
    tenant_id = os.environ["AZURE_AD_TENANT_ID"]
    client_id = os.environ["AZURE_AD_CLIENT_ID"]
    client_secret = os.environ.get("AZURE_AD_CLIENT_SECRET")
    return acquire_token(tenant_id, client_id, client_secret)


def test_connection() -> dict:
    """Test Azure AD connection. Returns {success: bool, message: str}."""
    try:
        token = acquire_token_from_env()
        return {"success": True, "message": f"Token acquired (length: {len(token)})"}
    except KeyError as e:
        return {"success": False, "message": f"Missing env var: {e}"}
    except RuntimeError as e:
        return {"success": False, "message": str(e)}
    except Exception as e:
        return {"success": False, "message": f"Unexpected error: {e}"}
