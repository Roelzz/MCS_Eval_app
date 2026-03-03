"""Retro Evals page — run historical transcript evaluations."""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import reflex as rx
from sqlmodel import select

from web.components import empty_state, layout, page_header, stat_card, status_badge
from web.models import EvalResult, EvalRun
from web.state import State


class RetroState(State):
    since_str: str = ""
    top: int = 100
    expected_topic: str = ""
    status: str = "idle"  # idle | fetching | evaluating | completed | error
    error: str = ""
    last_run_id: int = 0
    total_fetched: int = 0
    total_evaluated: int = 0
    total_skipped: int = 0
    outcome_resolved: int = 0
    outcome_escalated: int = 0
    outcome_abandoned: int = 0
    topic_pass_rate: str = "—"
    avg_csat: str = "—"
    recent_runs: list[dict] = []
    missing_env_vars: list[str] = []
    device_code: str = ""
    device_code_url: str = ""

    @rx.var
    def progress_text(self) -> str:
        return f"Evaluating {self.total_evaluated} / {self.total_fetched}..."

    @rx.var
    def run_disabled(self) -> bool:
        return self.status not in ("idle", "error", "completed")

    def load_page(self) -> None:
        if not self.since_str:
            since_dt = datetime.now(timezone.utc) - timedelta(days=7)
            self.since_str = since_dt.strftime("%Y-%m-%d")

        required_env = [
            "COPILOT_AGENT_IDENTIFIER",
            "DATAVERSE_ORG_URL",
            "AZURE_AD_TENANT_ID",
            "AZURE_AD_CLIENT_ID",
        ]
        self.missing_env_vars = [k for k in required_env if not os.environ.get(k)]

        with rx.session() as session:
            rows = session.exec(
                select(EvalRun).order_by(EvalRun.created_at.desc())
            ).all()

        retro_rows = []
        for r in rows:
            try:
                cfg = json.loads(r.config_json) if r.config_json else {}
            except Exception:
                cfg = {}
            if cfg.get("run_type") != "retro":
                continue
            retro_rows.append({
                "id": r.id,
                "name": r.name,
                "status": r.status,
                "total_cases": r.total_cases,
                "avg_score": f"{r.avg_score:.0%}" if r.avg_score > 0 else "—",
                "created": r.created_at.strftime("%d-%m-%Y %H:%M"),
            })

        self.recent_runs = retro_rows

    def set_since_str(self, value: str) -> None:
        self.since_str = value

    def set_top(self, value: str) -> None:
        try:
            self.top = max(1, int(value)) if value else 100
        except ValueError:
            self.top = 100

    def set_expected_topic(self, value: str) -> None:
        self.expected_topic = value

    def delete_run(self, run_id: int) -> None:
        with rx.session() as session:
            results = session.exec(
                select(EvalResult).where(EvalResult.eval_run_id == run_id)
            ).all()
            for r in results:
                session.delete(r)
            run_obj = session.get(EvalRun, run_id)
            if run_obj:
                session.delete(run_obj)
            session.commit()
        self.recent_runs = [r for r in self.recent_runs if r["id"] != run_id]

    @rx.event(background=True)
    async def execute_retro_run(self) -> None:
        # Step 1: Validate env vars (client secret is optional — device flow used if absent)
        required_env = [
            "COPILOT_AGENT_IDENTIFIER",
            "DATAVERSE_ORG_URL",
            "AZURE_AD_TENANT_ID",
            "AZURE_AD_CLIENT_ID",
        ]
        missing = [k for k in required_env if not os.environ.get(k)]
        if missing:
            async with self:
                self.status = "error"
                self.error = f"Missing environment variables: {', '.join(missing)}"
            return

        # Step 2 & 3: Guard + snapshot config + reset counters
        async with self:
            if self.status not in ("idle", "error", "completed"):
                return
            since_str = self.since_str
            top = self.top
            expected_topic = self.expected_topic
            self.status = "fetching"
            self.error = ""
            self.total_fetched = 0
            self.total_evaluated = 0
            self.total_skipped = 0
            self.outcome_resolved = 0
            self.outcome_escalated = 0
            self.outcome_abandoned = 0
            self.topic_pass_rate = "—"
            self.avg_csat = "—"

        # Step 4: Create EvalRun
        config = {
            "run_type": "retro",
            "since": since_str,
            "top": top,
            "expected_topic": expected_topic,
        }
        run_name = f"Transcript Extract {since_str} (top {top})"
        run_id: int = 0

        async with self:
            with rx.session() as session:
                run_obj = EvalRun(
                    name=run_name,
                    dataset_id=0,
                    status="running",
                    config_json=json.dumps(config),
                    metrics_json=json.dumps(["topic_routing"]),
                    total_cases=0,
                )
                session.add(run_obj)
                session.commit()
                session.refresh(run_obj)
                run_id = run_obj.id
            self.last_run_id = run_id

        # Step 5: Fetch transcripts
        try:
            from dataverse_client import DataverseClient  # noqa: PLC0415
            from dotenv import load_dotenv  # noqa: PLC0415
            load_dotenv(override=True)

            since_dt = datetime.fromisoformat(since_str).replace(tzinfo=timezone.utc)
            bot_guid = os.environ["COPILOT_AGENT_IDENTIFIER"]
            org_url = os.environ["DATAVERSE_ORG_URL"]
            tenant_id_env = os.environ["AZURE_AD_TENANT_ID"]
            client_id_env = os.environ["AZURE_AD_CLIENT_ID"]
            # Always use device flow for Dataverse — client credentials don't have
            # Dataverse table access unless the app is registered as a Dataverse
            # application user, which is a separate setup step.
            import msal  # noqa: PLC0415
            _CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
            msal_app = msal.PublicClientApplication(
                _CLI_CLIENT_ID,
                authority=f"https://login.microsoftonline.com/{tenant_id_env}",
            )
            flow = msal_app.initiate_device_flow(scopes=[f"{org_url}/.default"])
            if "user_code" not in flow:
                raise RuntimeError(f"Device flow initiation failed: {flow}")

            async with self:
                self.device_code = flow["user_code"]
                self.device_code_url = flow["verification_uri"]

            import webbrowser  # noqa: PLC0415
            webbrowser.open(flow["verification_uri"])

            result = await asyncio.to_thread(msal_app.acquire_token_by_device_flow, flow)
            if "access_token" not in result:
                raise RuntimeError(
                    f"Device flow login failed: {result.get('error_description', result.get('error'))}"
                )

            async with self:
                self.device_code = ""
                self.device_code_url = ""

            dv_client = DataverseClient(
                org_url=org_url,
                tenant_id=tenant_id_env,
                client_id=client_id_env,
                _prefetched_token=result["access_token"],
            )

            transcripts = await dv_client.fetch_transcripts(bot_guid, since_dt, top=top)
        except Exception as exc:
            async with self:
                self.status = "error"
                self.error = str(exc)
                with rx.session() as session:
                    failed = session.get(EvalRun, run_id)
                    if failed:
                        failed.status = "failed"
                        failed.error = str(exc)
                        session.add(failed)
                        session.commit()
            return

        # Step 6: Update total_cases, move to evaluating
        async with self:
            self.total_fetched = len(transcripts)
            self.status = "evaluating"
            with rx.session() as session:
                run_obj = session.get(EvalRun, run_id)
                if run_obj:
                    run_obj.total_cases = len(transcripts)
                    session.add(run_obj)
                    session.commit()

        # Step 7: Process each transcript
        from retro_eval import (  # noqa: PLC0415
            extract_test_case_from_transcript,
            run_tier1_metrics,
        )

        all_topic_pass: list[bool] = []
        all_csat: list[float] = []
        outcome_counts: dict[str, int] = {"Resolved": 0, "Escalated": 0, "Abandoned": 0}

        for idx, transcript in enumerate(transcripts):
            test_case = extract_test_case_from_transcript(transcript, dv_client)

            if test_case is None:
                async with self:
                    self.total_skipped += 1
                    with rx.session() as session:
                        run_obj = session.get(EvalRun, run_id)
                        if run_obj:
                            run_obj.completed_cases += 1
                            session.add(run_obj)
                            session.commit()
                continue

            metric_results = run_tier1_metrics(test_case, expected_topic=expected_topic)

            outcome = test_case.session_outcome or "Unknown"
            if outcome in outcome_counts:
                outcome_counts[outcome] += 1
            if test_case.csat is not None:
                all_csat.append(test_case.csat)
            for m, r in metric_results.items():
                if m == "topic_routing":
                    all_topic_pass.append(r.get("passed", False))

            activities = [
                {
                    "type": "retro_info",
                    "session_outcome": outcome,
                    "csat": test_case.csat,
                    "transcript_id": test_case.transcript_id,
                    "conversation_length": len(test_case.conversation),
                    "tools_used": test_case.tools_used,
                    "intent_recognition": test_case.intent_recognition,
                    "dialog_redirects": test_case.dialog_redirects,
                }
            ]

            avg_case_score = (
                sum(r.get("score", 0) for r in metric_results.values()) / len(metric_results)
                if metric_results
                else 0.0
            )
            passed = avg_case_score >= 0.5
            actual_output = ""
            if test_case.conversation:
                for msg in reversed(test_case.conversation):
                    if msg.get("role") == "assistant":
                        actual_output = msg.get("content", "")
                        break

            async with self:
                self.total_evaluated += 1
                with rx.session() as session:
                    result = EvalResult(
                        eval_run_id=run_id,
                        test_case_index=idx,
                        input_json=json.dumps(test_case.conversation),
                        actual_output=actual_output,
                        expected_output="",
                        scores_json=json.dumps(metric_results),
                        activities_json=json.dumps(activities),
                        passed=passed,
                        duration_seconds=0.0,
                    )
                    session.add(result)
                    run_obj = session.get(EvalRun, run_id)
                    if run_obj:
                        run_obj.completed_cases += 1
                        session.add(run_obj)
                    session.commit()

        # Step 8: Finalize run
        topic_rate = sum(all_topic_pass) / len(all_topic_pass) if all_topic_pass else 0.0
        avg_csat_val = sum(all_csat) / len(all_csat) if all_csat else None

        async with self:
            with rx.session() as session:
                run_obj = session.get(EvalRun, run_id)
                if run_obj:
                    run_obj.status = "completed"
                    run_obj.avg_score = topic_rate
                    run_obj.completed_at = datetime.utcnow()
                    session.add(run_obj)
                    session.commit()

            # Step 9: Update outcome / CSAT / pass-rate state vars
            self.outcome_resolved = outcome_counts.get("Resolved", 0)
            self.outcome_escalated = outcome_counts.get("Escalated", 0)
            self.outcome_abandoned = outcome_counts.get("Abandoned", 0)
            self.topic_pass_rate = f"{topic_rate:.0%}" if all_topic_pass else "—"
            self.avg_csat = f"{avg_csat_val:.1f}" if avg_csat_val is not None else "—"
            self.status = "completed"

            # Step 10: Refresh recent runs table
            self.load_page()


# ---------------------------------------------------------------------------
# UI components
# ---------------------------------------------------------------------------


def _config_card() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("history", size=16, color="var(--accent-9)"),
                rx.text("Configuration", size="3", weight="bold"),
                spacing="2",
                align="center",
            ),
            rx.grid(
                rx.vstack(
                    rx.text("Since Date", size="2", weight="medium"),
                    rx.input(
                        value=RetroState.since_str,
                        on_change=RetroState.set_since_str,
                        type="date",
                        width="100%",
                    ),
                    spacing="1",
                    width="100%",
                ),
                rx.vstack(
                    rx.text("Top N Transcripts", size="2", weight="medium"),
                    rx.input(
                        value=RetroState.top.to(str),
                        on_change=RetroState.set_top,
                        type="number",
                        width="100%",
                    ),
                    spacing="1",
                    width="100%",
                ),
                rx.vstack(
                    rx.text("Expected Topic (optional)", size="2", weight="medium"),
                    rx.input(
                        value=RetroState.expected_topic,
                        on_change=RetroState.set_expected_topic,
                        placeholder="e.g. Billing",
                        width="100%",
                    ),
                    spacing="1",
                    width="100%",
                ),
                columns="3",
                gap="4",
                width="100%",
            ),
            rx.button(
                rx.icon("play", size=14),
                "Transcript Extract",
                on_click=RetroState.execute_retro_run,
                disabled=RetroState.run_disabled,
                size="2",
            ),
            spacing="4",
            width="100%",
        ),
        width="100%",
    )


def _status_bar() -> rx.Component:
    device_code_callout = rx.cond(
        RetroState.device_code != "",
        rx.callout(
            rx.vstack(
                rx.text(
                    "A browser window has opened. Enter this code when prompted:",
                    size="2",
                ),
                rx.heading(RetroState.device_code, size="6"),
                rx.hstack(
                    rx.text("Or go to: ", size="2"),
                    rx.link(
                        RetroState.device_code_url,
                        href=RetroState.device_code_url,
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
    )

    run_status_card = rx.card(
        rx.cond(
            RetroState.status == "completed",
            rx.hstack(
                rx.icon("check-circle", size=16, color="#34d399"),
                rx.text("Run completed.", size="2", weight="medium"),
                rx.link(
                    rx.hstack(
                        rx.text("View detail", size="2"),
                        rx.icon("arrow-up-right", size=12),
                        spacing="1",
                        align="center",
                    ),
                    href="/runs/" + RetroState.last_run_id.to(str),
                    underline="none",
                    color="var(--accent-9)",
                ),
                spacing="3",
                align="center",
            ),
            rx.cond(
                RetroState.status == "error",
                rx.callout(
                    RetroState.error,
                    icon="triangle_alert",
                    color_scheme="red",
                    width="100%",
                ),
                rx.hstack(
                    rx.spinner(size="2"),
                    rx.cond(
                        RetroState.status == "fetching",
                        rx.text("Fetching transcripts from Dataverse...", size="2"),
                        rx.text(RetroState.progress_text, size="2"),
                    ),
                    spacing="3",
                    align="center",
                ),
            ),
        ),
        width="100%",
    )

    return rx.cond(
        RetroState.status != "idle",
        rx.vstack(
            device_code_callout,
            run_status_card,
            width="100%",
        ),
    )


def _summary_stats() -> rx.Component:
    return rx.cond(
        RetroState.status == "completed",
        rx.grid(
            stat_card(
                "Resolved",
                RetroState.outcome_resolved.to(str),
                "check-circle",
                "green",
            ),
            stat_card(
                "Escalated",
                RetroState.outcome_escalated.to(str),
                "arrow-up-right",
                "orange",
            ),
            stat_card(
                "Abandoned",
                RetroState.outcome_abandoned.to(str),
                "x-circle",
                "red",
            ),
            stat_card(
                "Topic Pass Rate",
                RetroState.topic_pass_rate,
                "target",
                "teal",
            ),
            stat_card(
                "Avg CSAT",
                RetroState.avg_csat,
                "star",
                "yellow",
            ),
            columns="5",
            gap="3",
            width="100%",
        ),
    )


def _recent_runs_table() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.hstack(
                rx.icon("history", size=16, color="var(--accent-9)"),
                rx.text("Recent Runs", size="3", weight="bold"),
                rx.badge(
                    RetroState.recent_runs.length().to(str),
                    variant="soft",
                    color_scheme="gray",
                    size="1",
                ),
                spacing="2",
                align="center",
            ),
            width="100%",
        ),
        rx.cond(
            RetroState.recent_runs.length() > 0,
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell("Name"),
                        rx.table.column_header_cell("Status"),
                        rx.table.column_header_cell("Transcripts"),
                        rx.table.column_header_cell("Avg Score"),
                        rx.table.column_header_cell("Created"),
                        rx.table.column_header_cell(""),
                        rx.table.column_header_cell(""),
                        rx.table.column_header_cell(""),
                    ),
                ),
                rx.table.body(
                    rx.foreach(
                        RetroState.recent_runs,
                        lambda r: rx.table.row(
                            rx.table.cell(
                                rx.link(
                                    rx.hstack(
                                        rx.text(r["name"], weight="medium", size="2"),
                                        rx.icon(
                                            "arrow-up-right",
                                            size=12,
                                            color="var(--gray-a7)",
                                        ),
                                        spacing="1",
                                        align="center",
                                    ),
                                    href="/runs/" + r["id"].to(str),
                                    underline="none",
                                    class_name="run-name-link",
                                ),
                            ),
                            rx.table.cell(status_badge(r["status"])),
                            rx.table.cell(
                                rx.text(
                                    r["total_cases"].to(str),
                                    size="2",
                                    font_family="var(--font-mono)",
                                ),
                            ),
                            rx.table.cell(
                                rx.text(
                                    r["avg_score"],
                                    size="2",
                                    font_family="var(--font-mono)",
                                    weight="medium",
                                ),
                            ),
                            rx.table.cell(
                                rx.text(
                                    r["created"],
                                    size="1",
                                    color="var(--gray-a8)",
                                    font_family="var(--font-mono)",
                                ),
                            ),
                            rx.table.cell(
                                rx.cond(
                                    r["status"] == "completed",
                                    rx.link(
                                        rx.hstack(
                                            rx.icon("database", size=12),
                                            rx.text("Build Dataset", size="1"),
                                            spacing="1",
                                            align="center",
                                        ),
                                        href="/retro/suggest/" + r["id"].to(str),
                                        underline="none",
                                        color="var(--accent-9)",
                                    ),
                                ),
                            ),
                            rx.table.cell(
                                rx.cond(
                                    r["status"] == "completed",
                                    rx.link(
                                        rx.hstack(
                                            rx.icon("messages-square", size=12),
                                            rx.text("View Conversations", size="1"),
                                            spacing="1",
                                            align="center",
                                        ),
                                        href="/retro/conversations/" + r["id"].to(str),
                                        underline="none",
                                        color="var(--accent-9)",
                                    ),
                                ),
                            ),
                            rx.table.cell(
                                rx.icon_button(
                                    rx.icon("trash-2", size=14),
                                    size="1",
                                    variant="ghost",
                                    color_scheme="red",
                                    on_click=RetroState.delete_run(r["id"]),
                                ),
                            ),
                        ),
                    ),
                ),
                width="100%",
            ),
            empty_state("No retro eval runs yet. Configure above and click Run.", "history"),
        ),
        spacing="3",
        width="100%",
    )


def _missing_vars_callout() -> rx.Component:
    return rx.cond(
        RetroState.missing_env_vars.length() > 0,
        rx.callout(
            rx.hstack(
                rx.text(
                    "Missing Dataverse credentials. Configure them in ",
                    size="2",
                ),
                rx.link("Settings", href="/settings", size="2", underline="always"),
                rx.text(" before running.", size="2"),
                spacing="1",
                align="center",
                flex_wrap="wrap",
            ),
            icon="triangle_alert",
            color_scheme="amber",
            width="100%",
        ),
    )


@rx.page(route="/retro", title="Transcript Extract", on_load=RetroState.load_page)
def retro_page() -> rx.Component:
    return layout(
        rx.vstack(
            page_header(
                "Transcript Extract",
                "Evaluate historical Copilot Studio transcripts from Dataverse",
            ),
            _missing_vars_callout(),
            _config_card(),
            _status_bar(),
            _summary_stats(),
            rx.separator(),
            _recent_runs_table(),
            spacing="4",
            width="100%",
        ),
    )
