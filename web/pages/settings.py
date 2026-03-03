"""Settings page — config display, connection test, test agent, app registration guide."""

import os
import re
from pathlib import Path

import reflex as rx

from web.components import layout, page_header
from web.state import State

ENV_VARS = [
    ("AZURE_AD_TENANT_ID", "Azure AD Tenant ID"),
    ("AZURE_AD_CLIENT_ID", "Azure AD Client ID"),
    ("AZURE_AD_CLIENT_SECRET", "Azure AD Client Secret"),
    ("COPILOT_ENVIRONMENT_ID", "Copilot Environment ID"),
    ("COPILOT_AGENT_IDENTIFIER", "Copilot Agent Identifier"),
    ("COPILOT_AGENT_SCHEMA", "Copilot Agent Schema Name"),
    ("DATAVERSE_ORG_URL", "Dataverse Org URL"),
    ("AZURE_OPENAI_ENDPOINT", "Azure OpenAI Endpoint"),
    ("AZURE_OPENAI_API_KEY", "Azure OpenAI API Key"),
    ("AZURE_OPENAI_DEPLOYMENT_NAME", "Azure OpenAI Deployment"),
    ("AZURE_OPENAI_API_VERSION", "Azure OpenAI API Version"),
]

SECRET_VARS = {"AZURE_AD_CLIENT_SECRET", "AZURE_OPENAI_API_KEY"}

# Build a flat list of var names for indexing
VAR_NAMES = [v[0] for v in ENV_VARS]


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _find_env_file() -> Path:
    """Locate .env file relative to the project root (CWD or parents)."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate
    return cwd / ".env"


class SettingsState(State):
    config_items: list[list[str]] = []
    connection_result: str = ""
    connection_success: bool = False
    is_testing_connection: bool = False
    test_agent_message: str = ""
    test_agent_response: str = ""
    is_testing_agent: bool = False
    guide_open: bool = False
    d2e_url: str = ""

    # Edit mode
    edit_mode: bool = False
    edit_values: list[str] = [""] * len(ENV_VARS)
    save_result: str = ""
    save_success: bool = False

    test_agent_403: bool = False
    client_id_display: str = ""

    # GUID resolve
    agent_id_is_guid: bool = False
    resolved_schema_name: str = ""
    schema_resolve_error: str = ""
    is_resolving_schema: bool = False
    manual_schema_name: str = ""

    def set_test_agent_message(self, value: str) -> None:
        self.test_agent_message = value

    def set_manual_schema_name(self, value: str) -> None:
        self.manual_schema_name = value

    def load_config(self) -> None:
        from dotenv import load_dotenv
        load_dotenv(override=True)
        items = []
        for var_name, label in ENV_VARS:
            value = os.getenv(var_name, "")
            if var_name in SECRET_VARS and value:
                display = value[:4] + "..." + value[-4:] if len(value) > 8 else "****"
            elif not value:
                display = "(not set)"
            else:
                display = value
            items.append([label, display, "set" if value else "missing"])
        self.config_items = items

        agent_id = os.getenv("COPILOT_AGENT_IDENTIFIER", "")
        schema_set = bool(os.getenv("COPILOT_AGENT_SCHEMA", "").strip())
        self.agent_id_is_guid = bool(_UUID_RE.match(agent_id)) and not schema_set
        self.resolved_schema_name = ""
        self.schema_resolve_error = ""
        self.manual_schema_name = ""
        self.client_id_display = os.getenv("AZURE_AD_CLIENT_ID", "(not set)")

        try:
            from d2e_client import _get_settings
            settings = _get_settings()
            from microsoft_agents.copilotstudio.client.power_platform_environment import PowerPlatformEnvironment
            self.d2e_url = PowerPlatformEnvironment.get_copilot_studio_connection_url(settings=settings)
        except Exception:
            self.d2e_url = ""

    def toggle_edit_mode(self) -> None:
        if not self.edit_mode:
            # Pre-populate edit fields with current raw values
            values = []
            for var_name, _ in ENV_VARS:
                values.append(os.getenv(var_name, ""))
            self.edit_values = values
            self.save_result = ""
        self.edit_mode = not self.edit_mode

    def set_edit_value(self, index: int, value: str) -> None:
        new_values = list(self.edit_values)
        new_values[index] = value
        self.edit_values = new_values

    def save_settings(self) -> None:
        """Write the edited values to the .env file and reload env."""
        env_path = _find_env_file()
        try:
            # Read existing .env content (to preserve comments and unrelated vars)
            existing_lines: list[str] = []
            if env_path.exists():
                existing_lines = env_path.read_text().splitlines()

            # Build a map of var -> line index for existing lines
            existing_map: dict[str, int] = {}
            for idx, line in enumerate(existing_lines):
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    existing_map[key] = idx

            # Apply edits
            for i, (var_name, _) in enumerate(ENV_VARS):
                new_val = self.edit_values[i]
                new_line = f"{var_name}={new_val}"
                if var_name in existing_map:
                    existing_lines[existing_map[var_name]] = new_line
                else:
                    existing_lines.append(new_line)

            env_path.write_text("\n".join(existing_lines) + "\n")

            # Reload into current process
            from dotenv import load_dotenv
            load_dotenv(str(env_path), override=True)

            self.save_success = True
            self.save_result = f"Saved to {env_path}"
            self.edit_mode = False
            self.load_config()

        except Exception as e:
            self.save_success = False
            self.save_result = f"Error saving: {e}"

    def resolve_schema_name(self) -> None:
        """Look up the agent schema name from Dataverse using the GUID in COPILOT_AGENT_IDENTIFIER."""
        from dotenv import load_dotenv
        load_dotenv(override=True)

        client_secret = os.getenv("AZURE_AD_CLIENT_SECRET", "")
        if not client_secret:
            self.schema_resolve_error = "AZURE_AD_CLIENT_SECRET is required for auto-resolve."
            return

        self.is_resolving_schema = True
        self.resolved_schema_name = ""
        self.schema_resolve_error = ""
        yield

        try:
            import httpx
            import msal

            tenant_id = os.environ["AZURE_AD_TENANT_ID"]
            client_id = os.environ["AZURE_AD_CLIENT_ID"]
            org_url = os.environ["DATAVERSE_ORG_URL"].rstrip("/")
            agent_id = os.environ["COPILOT_AGENT_IDENTIFIER"]

            app = msal.ConfidentialClientApplication(
                client_id,
                authority=f"https://login.microsoftonline.com/{tenant_id}",
                client_credential=client_secret,
            )
            result = app.acquire_token_for_client(scopes=[f"{org_url}/.default"])
            if "access_token" not in result:
                error = result.get("error_description", result.get("error", "Unknown"))
                self.schema_resolve_error = f"Token acquisition failed: {error}"
                return

            resp = httpx.get(
                f"{org_url}/api/data/v9.2/bots({agent_id})",
                params={"$select": "schemaname,name"},
                headers={"Authorization": f"Bearer {result['access_token']}"},
                timeout=30,
            )
            if resp.status_code >= 400:
                self.schema_resolve_error = f"Dataverse query failed: {resp.status_code} {resp.text[:200]}"
                return

            bot_data = resp.json()
            schema_name = bot_data.get("schemaname", "")
            if not schema_name:
                self.schema_resolve_error = "Bot found but has no schemaname field."
                return

            self.resolved_schema_name = schema_name
        except Exception as e:
            self.schema_resolve_error = f"Error: {e}"
        finally:
            self.is_resolving_schema = False

    def save_schema_name(self) -> None:
        """Write schema name (resolved or manual) as COPILOT_AGENT_SCHEMA to .env and reload."""
        schema_name = self.resolved_schema_name or self.manual_schema_name
        if not schema_name:
            return
        env_path = _find_env_file()
        try:
            existing_lines: list[str] = []
            if env_path.exists():
                existing_lines = env_path.read_text().splitlines()

            existing_map: dict[str, int] = {}
            for idx, line in enumerate(existing_lines):
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    existing_map[key] = idx

            new_line = f"COPILOT_AGENT_SCHEMA={schema_name}"
            if "COPILOT_AGENT_SCHEMA" in existing_map:
                existing_lines[existing_map["COPILOT_AGENT_SCHEMA"]] = new_line
            else:
                existing_lines.append(new_line)

            env_path.write_text("\n".join(existing_lines) + "\n")

            from dotenv import load_dotenv
            load_dotenv(str(env_path), override=True)

            self.load_config()
        except Exception as e:
            self.schema_resolve_error = f"Error saving: {e}"

    def test_connection(self) -> None:
        from dotenv import load_dotenv
        load_dotenv(override=True)

        self.is_testing_connection = True
        self.connection_result = ""
        yield
        try:
            from auth import test_connection
            result = test_connection()
            self.connection_success = result["success"]
            self.connection_result = result["message"]
        except Exception as e:
            self.connection_success = False
            self.connection_result = f"Error: {e}"
        finally:
            self.is_testing_connection = False

    def send_test_message(self) -> None:
        if not self.test_agent_message.strip():
            return

        from dotenv import load_dotenv
        load_dotenv(override=True)

        self.is_testing_agent = True
        self.test_agent_response = ""
        self.test_agent_403 = False
        yield
        try:
            from d2e_client import test_agent
            self.test_agent_response = test_agent(self.test_agent_message)
        except Exception as e:
            msg = str(e)
            self.test_agent_403 = "HTTP 403" in msg
            self.test_agent_response = f"Error: {msg}"
        finally:
            self.is_testing_agent = False

    def toggle_guide(self) -> None:
        self.guide_open = not self.guide_open


def _config_view_table() -> rx.Component:
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell("Setting"),
                rx.table.column_header_cell("Value"),
                rx.table.column_header_cell("Status"),
            ),
        ),
        rx.table.body(
            rx.foreach(
                SettingsState.config_items,
                lambda item: rx.table.row(
                    rx.table.cell(rx.text(item[0], weight="medium")),
                    rx.table.cell(rx.code(item[1])),
                    rx.table.cell(
                        rx.cond(
                            item[2] == "set",
                            rx.badge(
                                rx.icon("check", size=11),
                                "Set",
                                color_scheme="green",
                                variant="soft",
                            ),
                            rx.badge(
                                rx.icon("alert-circle", size=11),
                                "Missing",
                                color_scheme="red",
                                variant="soft",
                            ),
                        )
                    ),
                ),
            ),
        ),
        width="100%",
    )


def _edit_field(index: int, var_name: str, label: str) -> rx.Component:
    is_secret = var_name in SECRET_VARS
    return rx.hstack(
        rx.text(
            label,
            size="2",
            weight="medium",
            min_width="220px",
            color="var(--gray-a11)",
        ),
        rx.input(
            value=SettingsState.edit_values[index],
            on_change=lambda v: SettingsState.set_edit_value(index, v),
            placeholder=f"Enter {label}...",
            type="password" if is_secret else "text",
            width="100%",
            font_family="var(--font-mono)",
            size="2",
        ),
        spacing="4",
        align="center",
        width="100%",
    )


def _guid_warning_banner() -> rx.Component:
    return rx.vstack(
        rx.callout(
            rx.hstack(
                rx.text(
                    "COPILOT_AGENT_IDENTIFIER is a GUID. D2E requires the schema name (e.g. cr123_myagent).",
                    size="2",
                ),
                rx.spacer(),
                rx.button(
                    rx.cond(
                        SettingsState.is_resolving_schema,
                        rx.hstack(rx.spinner(size="1"), rx.text("Resolving..."), align="center", spacing="2"),
                        rx.text("Resolve from Dataverse"),
                    ),
                    on_click=SettingsState.resolve_schema_name,
                    disabled=SettingsState.is_resolving_schema,
                    size="2",
                    color_scheme="amber",
                    variant="soft",
                ),
                align="center",
                width="100%",
            ),
            icon="triangle_alert",
            color_scheme="amber",
            width="100%",
        ),
        rx.cond(
            SettingsState.schema_resolve_error != "",
            rx.vstack(
                rx.callout(
                    rx.vstack(
                        rx.text(SettingsState.schema_resolve_error, size="2"),
                        rx.text(
                            "The app service principal needs to be added as an Application User in Dataverse "
                            "(Power Platform Admin Center → Environments → Users → Application Users). "
                            "Or enter the schema name manually below.",
                            size="2",
                            color="var(--red-11)",
                        ),
                        spacing="1",
                        width="100%",
                    ),
                    icon="triangle_alert",
                    color_scheme="red",
                    width="100%",
                ),
                rx.hstack(
                    rx.input(
                        placeholder="e.g. cr123_myagent",
                        value=SettingsState.manual_schema_name,
                        on_change=SettingsState.set_manual_schema_name,
                        font_family="var(--font-mono)",
                        size="2",
                        width="100%",
                    ),
                    rx.button(
                        rx.icon("save", size=14),
                        "Save to .env",
                        on_click=SettingsState.save_schema_name,
                        disabled=SettingsState.manual_schema_name == "",
                        size="2",
                        color_scheme="green",
                    ),
                    align="center",
                    spacing="2",
                    width="100%",
                ),
                spacing="2",
                width="100%",
            ),
        ),
        rx.cond(
            SettingsState.resolved_schema_name != "",
            rx.callout(
                rx.hstack(
                    rx.vstack(
                        rx.text("Schema name resolved:", size="1", weight="medium", color="var(--gray-a8)"),
                        rx.code(SettingsState.resolved_schema_name, size="2"),
                        spacing="1",
                    ),
                    rx.spacer(),
                    rx.button(
                        rx.icon("save", size=14),
                        "Save to .env",
                        on_click=SettingsState.save_schema_name,
                        size="2",
                        color_scheme="green",
                    ),
                    align="center",
                    width="100%",
                ),
                icon="check",
                color_scheme="green",
                width="100%",
            ),
        ),
        spacing="2",
        width="100%",
    )


def config_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.hstack(
                    rx.icon("sliders-horizontal", size=16, color="var(--accent-9)"),
                    rx.text(
                        "Configuration",
                        size="3",
                        weight="bold",
                        letter_spacing="-0.01em",
                    ),
                    spacing="2",
                    align="center",
                ),
                rx.spacer(),
                rx.cond(
                    SettingsState.edit_mode,
                    rx.hstack(
                        rx.button(
                            "Cancel",
                            variant="soft",
                            color_scheme="gray",
                            size="2",
                            on_click=SettingsState.toggle_edit_mode,
                        ),
                        rx.button(
                            rx.icon("save", size=14),
                            "Save",
                            size="2",
                            on_click=SettingsState.save_settings,
                        ),
                        spacing="2",
                    ),
                    rx.button(
                        rx.icon("pencil", size=14),
                        "Edit",
                        variant="soft",
                        color_scheme="gray",
                        size="2",
                        on_click=SettingsState.toggle_edit_mode,
                    ),
                ),
                align="center",
                width="100%",
            ),
            rx.cond(
                SettingsState.save_result != "",
                rx.callout(
                    SettingsState.save_result,
                    icon=rx.cond(SettingsState.save_success, "check", "triangle_alert"),
                    color_scheme=rx.cond(SettingsState.save_success, "green", "red"),
                    width="100%",
                ),
            ),
            rx.cond(
                SettingsState.edit_mode,
                # Edit form
                rx.vstack(
                    rx.text(
                        "Changes are written to the .env file and reloaded immediately.",
                        size="2",
                        color="var(--gray-a8)",
                    ),
                    rx.separator(),
                    *[
                        _edit_field(i, var_name, label)
                        for i, (var_name, label) in enumerate(ENV_VARS)
                    ],
                    spacing="3",
                    width="100%",
                ),
                # Read-only table
                _config_view_table(),
            ),
            rx.cond(
                SettingsState.agent_id_is_guid,
                _guid_warning_banner(),
            ),
            spacing="4",
            width="100%",
        ),
        width="100%",
    )


def connection_test_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("wifi", size=16, color="var(--accent-9)"),
                rx.vstack(
                    rx.text(
                        "Connection Test",
                        size="3",
                        weight="bold",
                        letter_spacing="-0.01em",
                    ),
                    rx.text(
                        "Validate Azure AD credentials and Power Platform token.",
                        size="2",
                        color="var(--gray-a8)",
                    ),
                    spacing="0",
                ),
                spacing="3",
                align="start",
                width="100%",
            ),
            rx.button(
                rx.cond(
                    SettingsState.is_testing_connection,
                    rx.hstack(
                        rx.spinner(size="1"),
                        rx.text("Testing..."),
                        align="center",
                        spacing="2",
                    ),
                    rx.hstack(
                        rx.icon("play", size=14),
                        rx.text("Test Connection"),
                        spacing="2",
                        align="center",
                    ),
                ),
                on_click=SettingsState.test_connection,
                disabled=SettingsState.is_testing_connection,
                size="2",
                width="fit-content",
            ),
            rx.cond(
                SettingsState.connection_result != "",
                rx.callout(
                    SettingsState.connection_result,
                    icon=rx.cond(SettingsState.connection_success, "check", "triangle_alert"),
                    color_scheme=rx.cond(SettingsState.connection_success, "green", "red"),
                    width="100%",
                ),
            ),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


def test_agent_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("bot", size=16, color="var(--accent-9)"),
                rx.vstack(
                    rx.text(
                        "Test Agent",
                        size="3",
                        weight="bold",
                        letter_spacing="-0.01em",
                    ),
                    rx.text(
                        "Send a quick test message to the Copilot Studio agent.",
                        size="2",
                        color="var(--gray-a8)",
                    ),
                    spacing="0",
                ),
                spacing="3",
                align="start",
                width="100%",
            ),
            rx.cond(
                SettingsState.d2e_url != "",
                rx.vstack(
                    rx.text("D2E endpoint", size="1", weight="medium", color="var(--gray-a8)"),
                    rx.code(SettingsState.d2e_url, size="1"),
                    spacing="1",
                    width="100%",
                ),
            ),
            rx.hstack(
                rx.input(
                    placeholder="Type a test message...",
                    value=SettingsState.test_agent_message,
                    on_change=SettingsState.set_test_agent_message,
                    width="100%",
                ),
                rx.button(
                    rx.cond(
                        SettingsState.is_testing_agent,
                        rx.hstack(
                            rx.spinner(size="1"),
                            rx.text("Sending..."),
                            align="center",
                            spacing="2",
                        ),
                        rx.hstack(
                            rx.icon("send", size=14),
                            rx.text("Send"),
                            spacing="2",
                            align="center",
                        ),
                    ),
                    on_click=SettingsState.send_test_message,
                    disabled=SettingsState.is_testing_agent,
                    size="2",
                ),
                width="100%",
                align="end",
            ),
            rx.cond(
                SettingsState.test_agent_response != "",
                rx.vstack(
                    rx.hstack(
                        rx.icon("message-square", size=13, color="var(--gray-a8)"),
                        rx.text("Agent Response", size="1", weight="medium", color="var(--gray-a8)"),
                        spacing="1",
                        align="center",
                    ),
                    rx.code_block(
                        SettingsState.test_agent_response,
                        language="log",
                        width="100%",
                    ),
                    spacing="2",
                    width="100%",
                ),
            ),
            rx.cond(
                SettingsState.test_agent_403,
                rx.callout(
                    rx.vstack(
                        rx.text(
                            "403 — D2E rejected the request. Two likely causes:",
                            size="2",
                            weight="bold",
                        ),
                        rx.text(
                            "1. Stale token: the cached browser-login token expired. "
                            "Delete .local_token_cache.json and restart the app to force a fresh login.",
                            size="2",
                        ),
                        rx.text(
                            "2. Wrong agent identifier: if COPILOT_AGENT_SCHEMA is set in .env, "
                            "verify it matches the exact schema name of your agent (e.g. cr123_myagent).",
                            size="2",
                        ),
                        rx.code(
                            "Client ID: " + SettingsState.client_id_display,
                            size="1",
                        ),
                        spacing="2",
                        width="100%",
                    ),
                    icon="triangle_alert",
                    color_scheme="red",
                    width="100%",
                ),
            ),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


def app_registration_guide() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.hstack(
                    rx.icon("book-open", size=16, color="var(--accent-9)"),
                    rx.text(
                        "App Registration Guide",
                        size="3",
                        weight="bold",
                        letter_spacing="-0.01em",
                    ),
                    spacing="2",
                    align="center",
                ),
                rx.spacer(),
                rx.button(
                    rx.cond(
                        SettingsState.guide_open,
                        rx.icon("chevron-up", size=14),
                        rx.icon("chevron-down", size=14),
                    ),
                    rx.cond(SettingsState.guide_open, "Hide", "Show"),
                    variant="ghost",
                    color_scheme="gray",
                    size="2",
                    on_click=SettingsState.toggle_guide,
                ),
                width="100%",
                align="center",
            ),
            rx.cond(
                SettingsState.guide_open,
                rx.vstack(
                    rx.separator(),
                    _guide_step(
                        "1",
                        "Create App Registration",
                        "Azure Portal > AAD > App Registrations > New. "
                        "Name: 'Copilot Studio Eval'. Single tenant.",
                    ),
                    _guide_step(
                        "2",
                        "Note the IDs",
                        "Copy the Application (client) ID → AZURE_AD_CLIENT_ID. "
                        "Copy the Directory (tenant) ID → AZURE_AD_TENANT_ID.",
                    ),
                    _guide_step(
                        "3",
                        "Create Client Secret",
                        "Go to Certificates & secrets > New client secret. "
                        "Copy the Value → AZURE_AD_CLIENT_SECRET.",
                    ),
                    _guide_step(
                        "4",
                        "Add API Permissions",
                        "Go to API permissions > Add a permission > APIs my organization uses > "
                        "Search 'Power Platform API' > Delegated permissions > user_impersonation. "
                        "Grant admin consent.",
                    ),
                    _guide_step(
                        "5",
                        "Enable D2E on Agent",
                        "In Copilot Studio, open your agent > Settings > Security > "
                        "Enable Direct-to-Engine. Note the Environment ID and Agent Identifier.",
                    ),
                    spacing="3",
                    width="100%",
                ),
            ),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


def _guide_step(number: str, title: str, description: str) -> rx.Component:
    return rx.hstack(
        rx.badge(
            number, variant="solid", size="2", color_scheme="teal"
        ),
        rx.vstack(
            rx.text(title, weight="bold", size="2"),
            rx.text(description, size="2", color="var(--gray-a8)"),
            spacing="1",
        ),
        spacing="3",
        align="start",
        width="100%",
    )


@rx.page(route="/settings", title="Settings", on_load=SettingsState.load_config)
def settings_page() -> rx.Component:
    return layout(
        rx.vstack(
            page_header(
                "Settings",
                "Microsoft Copilot Studio D2E configuration and connection testing",
            ),
            config_section(),
            connection_test_section(),
            test_agent_section(),
            app_registration_guide(),
            spacing="5",
            width="100%",
            max_width="900px",
        ),
    )
