"""Setup page — fill environment variables from Copilot Studio agent URL."""

import os

import reflex as rx

from web.components import layout, page_header
from web.state import State


class SetupState(State):
    current_step: int = 1

    # Step 1
    agent_url: str = ""
    url_error: str = ""

    # Parsed from URL
    environment_id: str = ""
    bot_id: str = ""

    # Step 2 — existing app registration
    client_id: str = ""
    agent_identifier: str = ""  # optional; auto-detect from Dataverse if empty

    # Step 3 — provisioning
    is_provisioning: bool = False
    provisioning_error: str = ""
    device_code: str = ""
    device_code_url: str = ""
    log_lines: list[str] = []

    # Step 4 — result
    written_tenant_id: str = ""
    written_org_url: str = ""
    written_agent_id: str = ""

    def set_agent_url(self, value: str) -> None:
        self.agent_url = value

    def set_client_id(self, value: str) -> None:
        self.client_id = value

    def set_agent_identifier(self, value: str) -> None:
        self.agent_identifier = value

    def parse_url(self) -> None:
        self.url_error = ""
        try:
            from provisioner import parse_copilot_url

            parsed = parse_copilot_url(self.agent_url)
            self.environment_id = parsed.environment_id
            self.bot_id = parsed.bot_id
            self.client_id = os.getenv("AZURE_AD_CLIENT_ID", "")
            # Use the bot GUID directly — no schema name lookup needed
            self.agent_identifier = parsed.bot_id
            self.current_step = 2
        except ValueError as e:
            self.url_error = str(e)

    def go_back(self) -> None:
        if self.current_step > 1:
            self.current_step -= 1

    @rx.event(background=True)
    async def provision(self) -> None:
        async with self:
            self.current_step = 3
            self.is_provisioning = True
            self.provisioning_error = ""
            self.device_code = ""
            self.device_code_url = ""
            self.log_lines = []
            env_id = self.environment_id
            bot_id = self.bot_id
            app_client_id = self.client_id.strip()
            agent_id = self.agent_identifier.strip()

        async def _log(msg: str) -> None:
            async with self:
                self.log_lines = [*self.log_lines, msg]

        try:
            import webbrowser

            from provisioner import (
                add_secret_to_existing_app,
                complete_device_flow,
                initiate_device_flow,
                lookup_agent_schema_name,
                lookup_dataverse_org_url,
                update_env_file,
            )

            if not app_client_id:
                raise ValueError("Client ID is required")

            # Step 1: device flow for Graph (uses Azure CLI public client — no secret needed)
            await _log("Initiating device code flow for Microsoft Graph...")
            msal_app, flow = initiate_device_flow()

            async with self:
                self.device_code = flow["user_code"]
                self.device_code_url = flow["verification_uri"]

            await _log(f"Sign in at {flow['verification_uri']} with code: {flow['user_code']}")
            webbrowser.open(flow["verification_uri"])

            await _log("Waiting for sign-in...")
            graph_token, tenant_id = complete_device_flow(msal_app, flow)
            await _log(f"Authenticated — tenant: {tenant_id}")

            async with self:
                self.device_code = ""

            # Step 2: add a new client secret to the existing app registration
            await _log(f"Adding client secret to app {app_client_id}...")
            client_secret = add_secret_to_existing_app(graph_token, app_client_id)
            await _log("Client secret created.")

            # Step 3: BAP API → Dataverse org URL (uses cached BAP token, no second login)
            await _log("Looking up Dataverse org URL...")
            org_url = lookup_dataverse_org_url(msal_app, env_id)
            await _log(f"Dataverse org URL: {org_url}")

            # Step 4: look up agent schema name if not provided
            if not agent_id:
                await _log("Looking up agent schema name from Dataverse...")
                agent_id = lookup_agent_schema_name(msal_app, env_id, bot_id)
                await _log(f"Agent schema name: {agent_id}")

            # Step 5: write all vars to .env and reload
            updates = {
                "AZURE_AD_TENANT_ID": tenant_id,
                "AZURE_AD_CLIENT_ID": app_client_id,
                "AZURE_AD_CLIENT_SECRET": client_secret,
                "COPILOT_ENVIRONMENT_ID": env_id,
                "COPILOT_AGENT_IDENTIFIER": agent_id,
                "DATAVERSE_ORG_URL": org_url,
            }

            await _log("Writing .env file...")
            update_env_file(".env", updates)

            from dotenv import load_dotenv
            load_dotenv(override=True)
            await _log("Done.")

            async with self:
                self.written_tenant_id = tenant_id
                self.written_org_url = org_url
                self.written_agent_id = agent_id
                self.is_provisioning = False
                self.current_step = 4

        except Exception as e:
            await _log(f"ERROR: {e}")
            async with self:
                self.provisioning_error = str(e)
                self.is_provisioning = False


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------


def _step_indicator() -> rx.Component:
    steps = ["Paste URL", "Confirm", "Provision", "Done"]
    return rx.hstack(
        *[
            rx.hstack(
                rx.cond(
                    SetupState.current_step > i + 1,
                    rx.icon("check", size=14, color="var(--accent-9)"),
                    rx.text(str(i + 1), size="1", weight="bold"),
                ),
                rx.text(label, size="2", weight="medium"),
                spacing="2",
                align="center",
                padding="6px 12px",
                border_radius="var(--radius-2)",
                background=rx.cond(
                    SetupState.current_step == i + 1,
                    "var(--accent-a3)",
                    "transparent",
                ),
                opacity=rx.cond(
                    SetupState.current_step >= i + 1,
                    "1",
                    "0.4",
                ),
            )
            for i, label in enumerate(steps)
        ],
        spacing="2",
        padding_bottom="16px",
    )


def _step_1_paste_url() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.text(
                "Paste your Copilot Studio agent URL",
                size="3",
                weight="bold",
                letter_spacing="-0.01em",
            ),
            rx.text(
                "Open your agent in Copilot Studio and copy the URL from your browser.",
                size="2",
                color="var(--gray-a8)",
            ),
            rx.code(
                "https://copilotstudio.preview.microsoft.com"
                "/environments/{env}/bots/{bot}/overview",
                color_scheme="gray",
            ),
            rx.input(
                placeholder="https://copilotstudio.preview.microsoft.com/environments/...",
                value=SetupState.agent_url,
                on_change=SetupState.set_agent_url,
                width="100%",
                size="3",
            ),
            rx.cond(
                SetupState.url_error != "",
                rx.callout(
                    SetupState.url_error,
                    icon="triangle_alert",
                    color_scheme="red",
                    width="100%",
                ),
            ),
            rx.button("Next", on_click=SetupState.parse_url, size="3"),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


def _step_2_confirm() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.text(
                "Confirm your existing app registration",
                size="3",
                weight="bold",
                letter_spacing="-0.01em",
            ),
            rx.text(
                "A browser window will open for Microsoft login. "
                "A new client secret will be generated and added to your existing app registration.",
                size="2",
                color="var(--gray-a8)",
            ),
            rx.vstack(
                rx.text("Environment ID", size="2", weight="medium"),
                rx.code(SetupState.environment_id, size="2"),
                spacing="1",
            ),
            rx.vstack(
                rx.text("Bot ID", size="2", weight="medium"),
                rx.code(SetupState.bot_id, size="2"),
                spacing="1",
            ),
            rx.vstack(
                rx.text("Application (client) ID", size="2", weight="medium"),
                rx.text(
                    "Azure Portal → App registrations → your app → Overview → Application (client) ID.",
                    size="1",
                    color="var(--gray-a8)",
                ),
                rx.input(
                    placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
                    value=SetupState.client_id,
                    on_change=SetupState.set_client_id,
                    width="100%",
                    font_family="var(--font-mono)",
                ),
                spacing="1",
            ),
            rx.vstack(
                rx.text("Agent Identifier (bot UUID)", size="2", weight="medium"),
                rx.text(
                    "Pre-filled from the URL. Override only if you want to use a schema name instead.",
                    size="1",
                    color="var(--gray-a8)",
                ),
                rx.input(
                    placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
                    value=SetupState.agent_identifier,
                    on_change=SetupState.set_agent_identifier,
                    width="100%",
                    font_family="var(--font-mono)",
                ),
                spacing="1",
            ),
            rx.callout(
                "You need Application.ReadWrite.All in your tenant to add secrets to the app registration.",
                icon="info",
                color_scheme="blue",
                width="100%",
            ),
            rx.hstack(
                rx.button(
                    "Back",
                    variant="outline",
                    on_click=SetupState.go_back,
                    size="3",
                ),
                rx.button(
                    "Login & Configure",
                    on_click=SetupState.provision,
                    size="3",
                ),
                spacing="3",
            ),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


def _step_3_provisioning() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.text(
                "Configuring environment",
                size="3",
                weight="bold",
                letter_spacing="-0.01em",
            ),
            rx.cond(
                SetupState.device_code != "",
                rx.callout(
                    rx.vstack(
                        rx.text(
                            "A browser window has opened. Enter this code when prompted:",
                            size="2",
                        ),
                        rx.heading(SetupState.device_code, size="6"),
                        rx.hstack(
                            rx.text("Or go to: ", size="2"),
                            rx.link(
                                SetupState.device_code_url,
                                href=SetupState.device_code_url,
                                is_external=True,
                                size="2",
                            ),
                            spacing="1",
                            align="center",
                        ),
                        spacing="2",
                        align="center",
                    ),
                    icon="key",
                    color_scheme="blue",
                    width="100%",
                ),
            ),
            rx.cond(
                SetupState.is_provisioning,
                rx.hstack(
                    rx.spinner(size="3"),
                    rx.text("Working...", size="2", color="var(--gray-a8)"),
                    spacing="3",
                    align="center",
                ),
            ),
            rx.box(
                rx.foreach(
                    SetupState.log_lines,
                    lambda line: rx.text(line, size="1", as_="div"),
                ),
                width="100%",
                max_height="300px",
                overflow_y="auto",
                padding="12px",
                border_radius="var(--radius-2)",
                background="var(--gray-a2)",
                font_family="monospace",
            ),
            rx.cond(
                SetupState.provisioning_error != "",
                rx.vstack(
                    rx.callout(
                        SetupState.provisioning_error,
                        icon="triangle_alert",
                        color_scheme="red",
                        width="100%",
                    ),
                    rx.button(
                        "Retry",
                        on_click=SetupState.provision,
                        size="3",
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


def _step_4_done() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.callout(
                ".env updated and reloaded successfully.",
                icon="check",
                color_scheme="green",
                width="100%",
            ),
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell("Variable"),
                        rx.table.column_header_cell("Value"),
                    ),
                ),
                rx.table.body(
                    rx.table.row(
                        rx.table.cell(rx.text("AZURE_AD_TENANT_ID", weight="medium")),
                        rx.table.cell(rx.code(SetupState.written_tenant_id)),
                    ),
                    rx.table.row(
                        rx.table.cell(rx.text("DATAVERSE_ORG_URL", weight="medium")),
                        rx.table.cell(rx.code(SetupState.written_org_url)),
                    ),
                    rx.table.row(
                        rx.table.cell(rx.text("COPILOT_AGENT_IDENTIFIER", weight="medium")),
                        rx.table.cell(rx.code(SetupState.written_agent_id)),
                    ),
                    rx.table.row(
                        rx.table.cell(rx.text("AZURE_AD_CLIENT_SECRET", weight="medium")),
                        rx.table.cell(rx.text("written to .env", color="var(--gray-a8)", size="2")),
                    ),
                ),
                width="100%",
            ),
            rx.hstack(
                rx.link(
                    rx.button("Go to Transcript Extract", size="3"),
                    href="/retro",
                ),
                rx.link(
                    rx.button("Settings", size="3", variant="outline"),
                    href="/settings",
                ),
                spacing="3",
            ),
            spacing="4",
            width="100%",
        ),
        width="100%",
    )


@rx.page(route="/setup", title="Setup")
def setup_page() -> rx.Component:
    return layout(
        rx.vstack(
            page_header(
                "Setup",
                "Configure environment variables from your Copilot Studio agent URL",
            ),
            _step_indicator(),
            rx.cond(SetupState.current_step == 1, _step_1_paste_url()),
            rx.cond(SetupState.current_step == 2, _step_2_confirm()),
            rx.cond(SetupState.current_step == 3, _step_3_provisioning()),
            rx.cond(SetupState.current_step == 4, _step_4_done()),
            spacing="5",
            width="100%",
            max_width="900px",
        ),
    )
