"""Automated Azure AD app registration from a Copilot Studio agent URL."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import msal
from loguru import logger

# Azure CLI public client — can access both Graph and BAP resources
_CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
_GRAPH_SCOPES = ["https://graph.microsoft.com/Application.ReadWrite.All"]
_BAP_SCOPES = ["https://api.bap.microsoft.com/.default"]
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_BAP_BASE = "https://api.bap.microsoft.com"

# Power Platform API app ID
_POWER_PLATFORM_APP_ID = "8578e004-a5c6-46e7-913e-12f58912df43"

# D2E requires this scope; user_impersonation alone is not enough
_D2E_SCOPE = "CopilotStudio.Copilots.Invoke"
_FALLBACK_SCOPE = "user_impersonation"

_COPILOT_URL_PATTERN = re.compile(
    r"https://copilotstudio(?:\.preview)?\.microsoft\.com"
    r"/environments/(?P<env>[0-9a-f\-]{36})"
    r"/bots/(?P<bot>[0-9a-f\-]{36})"
    r"(?:/\w*)?/?$"
)


@dataclass
class ParsedAgentUrl:
    environment_id: str
    bot_id: str


@dataclass
class ProvisioningResult:
    tenant_id: str
    client_id: str
    client_secret: str
    app_object_id: str
    app_display_name: str


def parse_copilot_url(url: str) -> ParsedAgentUrl:
    """Extract environment_id and bot_id from a Copilot Studio agent URL."""
    m = _COPILOT_URL_PATTERN.match(url.strip())
    if not m:
        raise ValueError(
            "Invalid Copilot Studio URL. Expected format: "
            "https://copilotstudio[.preview].microsoft.com/environments/{guid}/bots/{guid}/..."
        )
    return ParsedAgentUrl(environment_id=m.group("env"), bot_id=m.group("bot"))


def initiate_device_flow(
    scopes: list[str] | None = None,
) -> tuple[msal.PublicClientApplication, dict]:
    """Start device code flow. Returns (msal_app, flow_dict).

    flow_dict contains 'user_code' and 'verification_uri' for display.
    """
    app = msal.PublicClientApplication(
        client_id=_CLI_CLIENT_ID,
        authority="https://login.microsoftonline.com/common",
    )
    flow = app.initiate_device_flow(scopes=scopes or _GRAPH_SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Failed to initiate device flow: {flow.get('error_description')}")
    logger.info("Device flow started — code: {}", flow["user_code"])
    return app, flow


def complete_device_flow(
    app: msal.PublicClientApplication, flow: dict
) -> tuple[str, str]:
    """Wait for user to complete device code login. Returns (access_token, tenant_id)."""
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        error = result.get("error_description", result.get("error", "Unknown MSAL error"))
        raise RuntimeError(f"Failed to acquire token: {error}")

    token = result["access_token"]
    tenant_id = result.get("id_token_claims", {}).get("tid", "")
    if not tenant_id:
        raise RuntimeError("Could not determine tenant_id from login response")

    logger.info("Token acquired for tenant {}", tenant_id)
    return token, tenant_id


def _graph_get(headers: dict, path: str, params: dict | None = None) -> httpx.Response:
    return httpx.get(f"{_GRAPH_BASE}{path}", headers=headers, params=params, timeout=30)


def _graph_post(headers: dict, path: str, body: dict) -> httpx.Response:
    return httpx.post(f"{_GRAPH_BASE}{path}", headers=headers, json=body, timeout=30)


def _ensure_power_platform_sp(headers: dict) -> tuple[str, list[dict[str, str]]]:
    """Ensure Power Platform API service principal exists.

    Returns (sp_object_id, scopes) where scopes is a list of
    {"id": ..., "value": ...} dicts for the permission scopes to request.
    """
    resp = _graph_get(
        headers,
        "/servicePrincipals",
        {"$filter": f"appId eq '{_POWER_PLATFORM_APP_ID}'"},
    )
    resp.raise_for_status()
    sps = resp.json().get("value", [])

    if not sps:
        logger.info("Power Platform API SP not found, creating it...")
        resp = _graph_post(headers, "/servicePrincipals", {"appId": _POWER_PLATFORM_APP_ID})
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Failed to create Power Platform API service principal: "
                f"{resp.status_code} {resp.text}"
            )
        sp = resp.json()
    else:
        sp = sps[0]

    sp_id = sp["id"]
    available = sp.get("oauth2PermissionScopes", [])
    logger.debug("Power Platform API scopes: {}", [s.get("value") for s in available])

    found: list[dict[str, str]] = []
    scope_by_value = {s.get("value"): s for s in available}

    # D2E requires CopilotStudio.Copilots.Invoke
    if _D2E_SCOPE in scope_by_value:
        s = scope_by_value[_D2E_SCOPE]
        found.append({"id": s["id"], "value": s["value"]})
    else:
        logger.warning(
            "Scope '{}' not found on Power Platform API SP — "
            "D2E calls will likely fail with 403",
            _D2E_SCOPE,
        )

    # Also include user_impersonation for general access
    if _FALLBACK_SCOPE in scope_by_value:
        s = scope_by_value[_FALLBACK_SCOPE]
        found.append({"id": s["id"], "value": s["value"]})

    # Last resort: any User-type scope
    if not found:
        for s in available:
            if s.get("type") == "User":
                logger.info("Using fallback scope '{}' ({})", s.get("value"), s.get("id"))
                found.append({"id": s["id"], "value": s["value"]})
                break

    if not found:
        raise RuntimeError(
            "Power Platform API has no delegated permission scopes. "
            "This tenant may not have Power Platform configured."
        )

    logger.info("Selected scopes: {}", [s["value"] for s in found])
    return sp_id, found


def create_app_registration(graph_token: str, display_name: str) -> ProvisioningResult:
    """Create an Azure AD app registration with Power Platform API permissions + admin consent."""
    headers = {
        "Authorization": f"Bearer {graph_token}",
        "Content-Type": "application/json",
    }

    pp_sp_id, pp_scopes = _ensure_power_platform_sp(headers)
    logger.info("Power Platform API SP: {}, scopes: {}", pp_sp_id, [s["value"] for s in pp_scopes])

    resource_access = [{"id": s["id"], "type": "Scope"} for s in pp_scopes]
    app_body = {
        "displayName": display_name,
        "signInAudience": "AzureADMyOrg",
        "publicClient": {
            "redirectUris": ["http://localhost"],
        },
        "requiredResourceAccess": [
            {
                "resourceAppId": _POWER_PLATFORM_APP_ID,
                "resourceAccess": resource_access,
            }
        ],
    }
    resp = _graph_post(headers, "/applications", app_body)
    if resp.status_code >= 400:
        raise RuntimeError(f"Failed to create app registration: {resp.status_code} {resp.text}")
    app_data = resp.json()
    app_object_id = app_data["id"]
    client_id = app_data["appId"]
    logger.info("Created app registration: {} ({})", display_name, client_id)

    resp = _graph_post(headers, "/servicePrincipals", {"appId": client_id})
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Failed to create service principal for app: {resp.status_code} {resp.text}"
        )
    our_sp_id = resp.json()["id"]
    logger.info("Created service principal: {}", our_sp_id)

    secret_body = {"passwordCredential": {"displayName": "auto-provisioned"}}
    resp = _graph_post(headers, f"/applications/{app_object_id}/addPassword", secret_body)
    if resp.status_code >= 400:
        raise RuntimeError(f"Failed to create client secret: {resp.status_code} {resp.text}")
    client_secret = resp.json()["secretText"]
    logger.info("Created client secret for app {}", client_id)

    scope_names = " ".join(s["value"] for s in pp_scopes)
    consent_body = {
        "clientId": our_sp_id,
        "consentType": "AllPrincipals",
        "resourceId": pp_sp_id,
        "scope": scope_names,
    }
    resp = _graph_post(headers, "/oauth2PermissionGrants", consent_body)
    if resp.status_code >= 400:
        logger.warning("Admin consent grant failed: {} {}", resp.status_code, resp.text)
    else:
        logger.info("Admin consent granted for Power Platform API")

    import base64
    import json

    token_parts = graph_token.split(".")
    payload = token_parts[1] + "=" * (4 - len(token_parts[1]) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    tenant_id = claims.get("tid", "")

    return ProvisioningResult(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        app_object_id=app_object_id,
        app_display_name=display_name,
    )


def register_power_platform_admin_app(
    msal_app: msal.PublicClientApplication,
    client_id: str,
    on_device_code: Callable[[str, str], None] | None = None,
) -> None:
    """Register the app as a Power Platform admin application via BAP API.

    Args:
        msal_app: The MSAL app instance with cached account from Graph login.
        client_id: The appId of the newly created app registration.
        on_device_code: Callback(user_code, verification_uri) when a second
            login is needed for the BAP resource. If None, logs only.
    """
    accounts = msal_app.get_accounts()
    if not accounts:
        raise RuntimeError("No cached account found — cannot acquire BAP token")

    result = msal_app.acquire_token_silent(_BAP_SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        logger.info("Silent BAP token failed, starting device flow for BAP...")
        flow = msal_app.initiate_device_flow(scopes=_BAP_SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Failed to initiate BAP device flow: {flow}")

        if on_device_code:
            on_device_code(flow["user_code"], flow["verification_uri"])
        else:
            logger.info("BAP code: {} at {}", flow["user_code"], flow["verification_uri"])

        import webbrowser

        webbrowser.open(flow["verification_uri"])
        result = msal_app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            error = result.get("error_description", result.get("error", "Unknown"))
            raise RuntimeError(f"Failed to acquire BAP token: {error}")

    bap_token = result["access_token"]
    logger.info("BAP token acquired")
    headers = {"Authorization": f"Bearer {bap_token}", "Content-Type": "application/json"}

    resp = httpx.put(
        f"{_BAP_BASE}/providers/Microsoft.BusinessAppPlatform"
        f"/adminApplications/{client_id}?api-version=2020-10-01",
        headers=headers,
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Failed to register Power Platform admin app: {resp.status_code} {resp.text}"
        )
    logger.info("Registered app {} as Power Platform admin application", client_id)


def lookup_agent_schema_name(
    msal_app: msal.PublicClientApplication,
    environment_id: str,
    bot_id: str,
    bap_token: str | None = None,
) -> str:
    """Look up the agent's Dataverse schema name from its bot GUID.

    Flow: BAP API → get Dataverse org URL → Dataverse API → bot schema name.
    """
    accounts = msal_app.get_accounts()
    if not accounts:
        raise RuntimeError("No cached account for schema name lookup")

    # Get BAP token if not provided
    if not bap_token:
        result = msal_app.acquire_token_silent(_BAP_SCOPES, account=accounts[0])
        if not result or "access_token" not in result:
            raise RuntimeError("Cannot acquire BAP token silently for schema lookup")
        bap_token = result["access_token"]

    # Step 1: Get environment details to find the Dataverse org URL
    headers = {"Authorization": f"Bearer {bap_token}"}
    resp = httpx.get(
        f"{_BAP_BASE}/providers/Microsoft.BusinessAppPlatform"
        f"/environments/{environment_id}?api-version=2021-04-01",
        headers=headers,
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Failed to get environment info: {resp.status_code} {resp.text}")

    env_data = resp.json()
    # The org URL is in properties.linkedEnvironmentMetadata.instanceUrl
    org_url = (
        env_data.get("properties", {})
        .get("linkedEnvironmentMetadata", {})
        .get("instanceUrl", "")
    )
    if not org_url:
        raise RuntimeError("Could not find Dataverse org URL for this environment")
    org_url = org_url.rstrip("/")
    logger.info("Dataverse org URL: {}", org_url)

    # Step 2: Get a Dataverse token for this org
    dv_scopes = [f"{org_url}/.default"]
    result = msal_app.acquire_token_silent(dv_scopes, account=accounts[0])
    if not result or "access_token" not in result:
        # Try device flow
        flow = msal_app.initiate_device_flow(scopes=dv_scopes)
        if "user_code" not in flow:
            raise RuntimeError(f"Failed to initiate Dataverse device flow: {flow}")
        logger.info("Dataverse auth code: {} at {}", flow["user_code"], flow["verification_uri"])

        import webbrowser

        webbrowser.open(flow["verification_uri"])
        result = msal_app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise RuntimeError(f"Dataverse token failed: {result.get('error_description')}")

    dv_token = result["access_token"]

    # Step 3: Query the bot table for the schema name
    headers = {"Authorization": f"Bearer {dv_token}"}
    resp = httpx.get(
        f"{org_url}/api/data/v9.2/bots({bot_id})",
        params={"$select": "schemaname,name"},
        headers=headers,
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Failed to query bot from Dataverse: {resp.status_code} {resp.text}")

    bot_data = resp.json()
    schema_name = bot_data.get("schemaname", "")
    bot_name = bot_data.get("name", "")
    if not schema_name:
        raise RuntimeError(f"Bot {bot_id} found but has no schemaname")

    logger.info("Bot '{}' schema name: {}", bot_name, schema_name)
    return schema_name


def update_env_file(env_path: str | Path, updates: dict[str, str]) -> None:
    """Update .env file in-place: replace existing keys, append missing ones."""
    env_path = Path(env_path)
    remaining = dict(updates)
    lines: list[str] = []

    if env_path.exists():
        for line in env_path.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0]
                if key in remaining:
                    lines.append(f"{key}={remaining.pop(key)}")
                    continue
            lines.append(line)

    for key, value in remaining.items():
        lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n")
    logger.info("Updated .env with keys: {}", list(updates.keys()))
