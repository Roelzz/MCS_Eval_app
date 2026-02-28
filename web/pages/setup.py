"""Setup page — automated Azure AD app registration from Copilot Studio agent URL."""

import reflex as rx

from web.components import layout, page_header
from web.state import State


class SetupState(State):
    current_step: int = 1

    # Step 1
    agent_url: str = ""
    url_error: str = ""

    # Step 2
    environment_id: str = ""
    bot_id: str = ""
    agent_identifier: str = ""
    app_display_name: str = "Copilot Studio Eval Platform"

    # Step 3
    is_provisioning: bool = False
    provisioning_error: str = ""
    device_code: str = ""
    device_code_url: str = ""
    log_lines: list[str] = []

    # Step 4
    created_tenant_id: str = ""
    created_client_id: str = ""
    created_client_secret: str = ""
    env_written: bool = False

    def set_agent_url(self, value: str) -> None:
        self.agent_url = value

    def set_agent_identifier(self, value: str) -> None:
        self.agent_identifier = value

    def set_app_display_name(self, value: str) -> None:
        self.app_display_name = value

    def parse_url(self) -> None:
        self.url_error = ""
        try:
            from provisioner import parse_copilot_url

            parsed = parse_copilot_url(self.agent_url)
            self.environment_id = parsed.environment_id
            self.bot_id = parsed.bot_id
            self.agent_identifier = ""
            self.current_step = 2
        except ValueError as e:
            self.url_error = str(e)

    @rx.event(background=True)
    async def provision(self) -> None:
        async with self:
            self.current_step = 3
            self.is_provisioning = True
            self.provisioning_error = ""
            self.device_code = ""
            self.device_code_url = ""
            self.log_lines = []
            display_name = self.app_display_name
            env_id = self.environment_id
            bot_guid = self.bot_id
            agent_id = self.agent_identifier

        async def _log(msg: str) -> None:
            async with self:
                self.log_lines = [*self.log_lines, msg]

        try:
            import webbrowser

            from provisioner import (
                complete_device_flow,
                create_app_registration,
                initiate_device_flow,
                lookup_agent_schema_name,
                register_power_platform_admin_app,
                update_env_file,
            )

            await _log("Initiating device code flow for Microsoft Graph...")
            msal_app, flow = initiate_device_flow()

            async with self:
                self.device_code = flow["user_code"]
                self.device_code_url = flow["verification_uri"]

            await _log(f"Sign in at {flow['verification_uri']} with code: {flow['user_code']}")
            webbrowser.open(flow["verification_uri"])

            await _log("Waiting for sign-in...")
            token, tenant_id = complete_device_flow(msal_app, flow)
            await _log(f"Authenticated — tenant: {tenant_id}")

            async with self:
                self.device_code = ""

            await _log("Ensuring Power Platform API service principal exists...")
            await _log("Creating app registration...")
            result = create_app_registration(token, display_name)
            await _log(f"App created — client ID: {result.client_id}")

            await _log("Registering as Power Platform admin application...")

            def _on_bap_device_code(code: str, url: str) -> None:
                # Can't use async here — this runs in a sync context inside
                # register_power_platform_admin_app. We store the values and
                # the UI will pick them up via the state vars set below.
                pass

            # We need to update UI with BAP device code if needed.
            # Since the callback is sync, we pre-set a flag and use a wrapper.
            bap_code_info: list[tuple[str, str]] = []

            def _capture_bap_code(code: str, url: str) -> None:
                bap_code_info.append((code, url))

            # Try silent first, if it fails the callback captures the code
            # We need to handle the async state update between the callback
            # and the blocking device flow. Split into two phases:
            from provisioner import _BAP_SCOPES

            accounts = msal_app.get_accounts()
            silent = msal_app.acquire_token_silent(_BAP_SCOPES, account=accounts[0])
            if silent and "access_token" in silent:
                await _log("BAP token acquired silently (cached)")
                register_power_platform_admin_app(msal_app, result.client_id)
            else:
                await _log("Second sign-in required for Power Platform API...")
                bap_flow = msal_app.initiate_device_flow(scopes=_BAP_SCOPES)
                if "user_code" not in bap_flow:
                    raise RuntimeError(f"BAP device flow failed: {bap_flow}")

                async with self:
                    self.device_code = bap_flow["user_code"]
                    self.device_code_url = bap_flow["verification_uri"]

                await _log(
                    f"Sign in at {bap_flow['verification_uri']} "
                    f"with code: {bap_flow['user_code']}"
                )
                webbrowser.open(bap_flow["verification_uri"])

                await _log("Waiting for Power Platform sign-in...")
                bap_result = msal_app.acquire_token_by_device_flow(bap_flow)
                if "access_token" not in bap_result:
                    error = bap_result.get("error_description", "Unknown")
                    raise RuntimeError(f"BAP token failed: {error}")

                async with self:
                    self.device_code = ""

                await _log("BAP token acquired")
                # Now call register with the token already cached
                register_power_platform_admin_app(msal_app, result.client_id)

            await _log("Registered as Power Platform admin app")

            # Look up agent schema name if not manually provided
            if not agent_id:
                await _log("Looking up agent schema name from Dataverse...")
                agent_id = lookup_agent_schema_name(msal_app, env_id, bot_guid)
                await _log(f"Agent schema name: {agent_id}")
                async with self:
                    self.agent_identifier = agent_id

            updates = {
                "AZURE_AD_TENANT_ID": result.tenant_id,
                "AZURE_AD_CLIENT_ID": result.client_id,
                "AZURE_AD_CLIENT_SECRET": "",  # clear — D2E uses interactive auth
                "COPILOT_ENVIRONMENT_ID": env_id,
                "COPILOT_AGENT_IDENTIFIER": agent_id,
            }

            await _log("Writing .env file...")
            update_env_file(".env", updates)

            from dotenv import load_dotenv

            load_dotenv(override=True)
            await _log("Done! .env updated and reloaded into running process.")

            async with self:
                self.created_tenant_id = result.tenant_id
                self.created_client_id = result.client_id
                secret = result.client_secret
                if len(secret) > 8:
                    self.created_client_secret = secret[:4] + "..." + secret[-4:]
                else:
                    self.created_client_secret = "****"
                self.env_written = True
                self.is_provisioning = False
                self.current_step = 4

        except Exception as e:
            await _log(f"ERROR: {e}")
            async with self:
                self.provisioning_error = str(e)
                self.is_provisioning = False

    def go_back(self) -> None:
        if self.current_step > 1:
            self.current_step -= 1

    def go_to_step(self, step: int) -> None:
        self.current_step = step


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
                color_scheme="gray",
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
            rx.button(
                "Parse URL",
                on_click=SetupState.parse_url,
                size="3",
            ),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


def _step_2_confirm() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.text(
                "Confirm configuration",
                size="3",
                weight="bold",
                letter_spacing="-0.01em",
            ),
            rx.text(
                "Review the extracted values before creating the app registration.",
                size="2",
                color_scheme="gray",
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
                rx.text("Agent Identifier (schema name)", size="2", weight="medium"),
                rx.text(
                    "Leave empty to auto-detect from Dataverse, or enter manually "
                    "(e.g. cr123_myAgent).",
                    size="1",
                    color_scheme="gray",
                ),
                rx.input(
                    placeholder="auto-detect if empty",
                    value=SetupState.agent_identifier,
                    on_change=SetupState.set_agent_identifier,
                    width="100%",
                ),
                spacing="1",
            ),
            rx.vstack(
                rx.text("App Display Name", size="2", weight="medium"),
                rx.input(
                    value=SetupState.app_display_name,
                    on_change=SetupState.set_app_display_name,
                    width="100%",
                ),
                spacing="1",
            ),
            rx.callout(
                "This will open your browser for Azure AD login (possibly twice: "
                "once for Graph API, once for Power Platform). "
                "You need admin permissions in your tenant.",
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
                    "Create App Registration",
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
                "Creating app registration",
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
                    rx.text("Provisioning in progress...", size="2", color_scheme="gray"),
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
                "App registration created and .env updated successfully!",
                icon="check",
                color_scheme="green",
                width="100%",
            ),
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell("Setting"),
                        rx.table.column_header_cell("Value"),
                    ),
                ),
                rx.table.body(
                    rx.table.row(
                        rx.table.cell(rx.text("Tenant ID", weight="medium")),
                        rx.table.cell(rx.code(SetupState.created_tenant_id)),
                    ),
                    rx.table.row(
                        rx.table.cell(rx.text("Client ID", weight="medium")),
                        rx.table.cell(rx.code(SetupState.created_client_id)),
                    ),
                    rx.table.row(
                        rx.table.cell(rx.text("Client Secret", weight="medium")),
                        rx.table.cell(rx.code(SetupState.created_client_secret)),
                    ),
                ),
                width="100%",
            ),
            rx.callout(
                "The client secret is NOT written to .env — D2E uses interactive auth. "
                "Save the secret above if you need it for CI/headless runs later.",
                icon="info",
                color_scheme="orange",
                width="100%",
            ),
            rx.link(
                rx.button("Go to Settings", size="3", variant="outline"),
                href="/settings",
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
            page_header("Setup", "Automated Azure AD app registration from your agent URL"),
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
