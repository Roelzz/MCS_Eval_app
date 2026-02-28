"""Tests for provisioner module — URL parsing, env file updates, Graph API calls."""

from unittest.mock import MagicMock, patch

import pytest

from provisioner import (
    ParsedAgentUrl,
    ProvisioningResult,
    create_app_registration,
    parse_copilot_url,
    register_power_platform_admin_app,
    update_env_file,
)

# --- parse_copilot_url ---


def test_parse_copilot_url_valid():
    url = (
        "https://copilotstudio.preview.microsoft.com"
        "/environments/2dd2ec79-3f5b-e241-b733-f7e34196b913"
        "/bots/9a237ebb-6014-f111-8341-000d3a340477/overview"
    )
    result = parse_copilot_url(url)
    assert result == ParsedAgentUrl(
        environment_id="2dd2ec79-3f5b-e241-b733-f7e34196b913",
        bot_id="9a237ebb-6014-f111-8341-000d3a340477",
    )


@pytest.mark.parametrize(
    "path_suffix",
    ["/overview", "/canvas", "/", ""],
)
def test_parse_copilot_url_with_path_variants(path_suffix):
    base = (
        "https://copilotstudio.preview.microsoft.com"
        "/environments/2dd2ec79-3f5b-e241-b733-f7e34196b913"
        "/bots/9a237ebb-6014-f111-8341-000d3a340477"
    )
    result = parse_copilot_url(base + path_suffix)
    assert result.environment_id == "2dd2ec79-3f5b-e241-b733-f7e34196b913"
    assert result.bot_id == "9a237ebb-6014-f111-8341-000d3a340477"


@pytest.mark.parametrize(
    "host",
    [
        "https://copilotstudio.preview.microsoft.com",
        "https://copilotstudio.microsoft.com",
    ],
)
def test_parse_copilot_url_different_hosts(host):
    url = (
        f"{host}"
        "/environments/2dd2ec79-3f5b-e241-b733-f7e34196b913"
        "/bots/9a237ebb-6014-f111-8341-000d3a340477/overview"
    )
    result = parse_copilot_url(url)
    assert result.environment_id == "2dd2ec79-3f5b-e241-b733-f7e34196b913"


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://example.com/not-copilot",
        "https://copilotstudio.preview.microsoft.com/environments/bad-guid/bots/also-bad",
        "",
        "not a url",
    ],
)
def test_parse_copilot_url_invalid(bad_url):
    with pytest.raises(ValueError):
        parse_copilot_url(bad_url)


# --- update_env_file ---


def test_update_env_file_updates_existing(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=old\nBAR=keep\n")
    update_env_file(env_file, {"FOO": "new"})
    content = env_file.read_text()
    assert "FOO=new" in content
    assert "BAR=keep" in content


def test_update_env_file_adds_missing(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=existing\n")
    update_env_file(env_file, {"NEW_KEY": "new_value"})
    content = env_file.read_text()
    assert "FOO=existing" in content
    assert "NEW_KEY=new_value" in content


def test_update_env_file_preserves_unrelated(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# This is a comment\nFOO=bar\n\n# Another comment\nBAZ=qux\n")
    update_env_file(env_file, {"FOO": "updated"})
    content = env_file.read_text()
    assert "# This is a comment" in content
    assert "# Another comment" in content
    assert "BAZ=qux" in content
    assert "FOO=updated" in content


def test_update_env_file_creates_if_missing(tmp_path):
    env_file = tmp_path / ".env"
    update_env_file(env_file, {"KEY": "value"})
    assert env_file.exists()
    assert "KEY=value" in env_file.read_text()


# --- create_app_registration ---


def _mock_graph_token() -> str:
    """Build a fake JWT with a tid claim for testing."""
    import base64
    import json

    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
    payload = (
        base64.urlsafe_b64encode(json.dumps({"tid": "test-tenant-id"}).encode())
        .rstrip(b"=")
        .decode()
    )
    return f"{header}.{payload}.sig"


def _mock_sp_lookup_response():
    """Standard mock for Power Platform API service principal lookup."""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "value": [
            {
                "id": "pp-sp-id-000",
                "oauth2PermissionScopes": [
                    {"value": "CopilotStudio.Copilots.Invoke", "id": "d2e-perm-guid-456"},
                    {"value": "user_impersonation", "id": "perm-guid-123"},
                ],
            }
        ]
    }
    return resp


@patch("provisioner.httpx.post")
@patch("provisioner.httpx.get")
def test_create_app_registration_success(mock_get, mock_post):
    # Mock service principal lookup
    mock_get.return_value = _mock_sp_lookup_response()

    # Mock POST calls: app creation, SP creation, secret, consent
    app_response = MagicMock()
    app_response.status_code = 201
    app_response.json.return_value = {"id": "obj-id-456", "appId": "client-id-789"}

    sp_response = MagicMock()
    sp_response.status_code = 201
    sp_response.json.return_value = {"id": "our-sp-id-111"}

    secret_response = MagicMock()
    secret_response.status_code = 200
    secret_response.json.return_value = {"secretText": "super-secret-value"}

    consent_response = MagicMock()
    consent_response.status_code = 201

    mock_post.side_effect = [app_response, sp_response, secret_response, consent_response]

    token = _mock_graph_token()
    result = create_app_registration(token, "Test App")

    assert isinstance(result, ProvisioningResult)
    assert result.tenant_id == "test-tenant-id"
    assert result.client_id == "client-id-789"
    assert result.client_secret == "super-secret-value"
    assert result.app_object_id == "obj-id-456"
    assert result.app_display_name == "Test App"

    # Verify consent was granted with both scopes
    consent_call = mock_post.call_args_list[3]
    consent_body = consent_call.kwargs.get("json") or consent_call[1].get("json")
    assert consent_body["clientId"] == "our-sp-id-111"
    assert consent_body["resourceId"] == "pp-sp-id-000"
    assert "CopilotStudio.Copilots.Invoke" in consent_body["scope"]
    assert "user_impersonation" in consent_body["scope"]

    # Verify app registration requested both scopes
    app_call = mock_post.call_args_list[0]
    app_body = app_call.kwargs.get("json") or app_call[1].get("json")
    scope_ids = [ra["id"] for ra in app_body["requiredResourceAccess"][0]["resourceAccess"]]
    assert "d2e-perm-guid-456" in scope_ids
    assert "perm-guid-123" in scope_ids


@patch("provisioner.httpx.post")
@patch("provisioner.httpx.get")
def test_create_app_registration_creates_pp_sp_if_missing(mock_get, mock_post):
    """Power Platform API SP is created when not found in tenant."""
    # First GET returns empty, meaning SP doesn't exist
    empty_resp = MagicMock()
    empty_resp.status_code = 200
    empty_resp.raise_for_status = MagicMock()
    empty_resp.json.return_value = {"value": []}
    mock_get.return_value = empty_resp

    # POST calls: create PP SP, create app, create our SP, secret, consent
    pp_sp_response = MagicMock()
    pp_sp_response.status_code = 201
    pp_sp_response.json.return_value = {
        "id": "pp-sp-id-new",
        "oauth2PermissionScopes": [
            {"value": "CopilotStudio.Copilots.Invoke", "id": "d2e-perm-guid-456"},
            {"value": "user_impersonation", "id": "perm-guid-123"},
        ],
    }

    app_response = MagicMock()
    app_response.status_code = 201
    app_response.json.return_value = {"id": "obj-id-456", "appId": "client-id-789"}

    our_sp_response = MagicMock()
    our_sp_response.status_code = 201
    our_sp_response.json.return_value = {"id": "our-sp-id-111"}

    secret_response = MagicMock()
    secret_response.status_code = 200
    secret_response.json.return_value = {"secretText": "secret-val"}

    consent_response = MagicMock()
    consent_response.status_code = 201

    mock_post.side_effect = [
        pp_sp_response,
        app_response,
        our_sp_response,
        secret_response,
        consent_response,
    ]

    token = _mock_graph_token()
    result = create_app_registration(token, "Test App")

    assert result.client_id == "client-id-789"
    # First POST should be creating the PP SP
    first_post_body = mock_post.call_args_list[0].kwargs.get("json")
    assert first_post_body["appId"] == "8578e004-a5c6-46e7-913e-12f58912df43"


@patch("provisioner.httpx.post")
@patch("provisioner.httpx.get")
def test_create_app_registration_graph_error(mock_get, mock_post):
    mock_get.return_value = _mock_sp_lookup_response()

    # App creation fails
    error_response = MagicMock()
    error_response.status_code = 403
    error_response.text = "Insufficient privileges"
    mock_post.return_value = error_response

    token = _mock_graph_token()
    with pytest.raises(RuntimeError, match="Failed to create app registration"):
        create_app_registration(token, "Test App")


@patch("provisioner.httpx.post")
@patch("provisioner.httpx.get")
def test_create_app_registration_no_scopes_raises(mock_get, mock_post):
    """Raises RuntimeError when Power Platform API has no delegated scopes."""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"value": [{"id": "pp-sp-id", "oauth2PermissionScopes": []}]}
    mock_get.return_value = resp

    token = _mock_graph_token()
    with pytest.raises(RuntimeError, match="no delegated permission scopes"):
        create_app_registration(token, "Test App")


# --- register_power_platform_admin_app ---


@patch("provisioner.httpx.put")
def test_register_power_platform_admin_app_success(mock_put):
    mock_msal_app = MagicMock()
    mock_msal_app.get_accounts.return_value = [{"username": "admin@test.com"}]
    mock_msal_app.acquire_token_silent.return_value = {"access_token": "bap-token"}

    put_resp = MagicMock()
    put_resp.status_code = 200
    mock_put.return_value = put_resp

    register_power_platform_admin_app(mock_msal_app, "test-client-id")

    mock_put.assert_called_once()
    args = mock_put.call_args
    call_url = args[0][0] if args[0] else args.kwargs["url"]
    assert "test-client-id" in call_url
    assert "adminApplications" in call_url


@patch("provisioner.httpx.put")
def test_register_power_platform_admin_app_failure(mock_put):
    mock_msal_app = MagicMock()
    mock_msal_app.get_accounts.return_value = [{"username": "admin@test.com"}]
    mock_msal_app.acquire_token_silent.return_value = {"access_token": "bap-token"}

    put_resp = MagicMock()
    put_resp.status_code = 403
    put_resp.text = "Forbidden"
    mock_put.return_value = put_resp

    with pytest.raises(RuntimeError, match="Failed to register Power Platform admin app"):
        register_power_platform_admin_app(mock_msal_app, "test-client-id")
