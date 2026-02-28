"""Run detail page — full results view with expandable rows."""

import json
from datetime import datetime

import reflex as rx
from sqlmodel import select

from web.components import layout, status_badge
from web.models import Dataset, EvalResult, EvalRun
from web.state import State


def _color_for_score(val: float) -> str:
    if val >= 0.7:
        return "green"
    if val >= 0.4:
        return "yellow"
    return "red"


class RunDetailState(State):
    run: dict = {}
    results: list[dict] = []
    expanded_result: int = -1
    dataset_name: str = ""

    def load_run(self) -> None:
        run_id_str = self.router.page.params.get("run_id", "0")
        try:
            run_id = int(run_id_str)
        except (ValueError, TypeError):
            return

        with rx.session() as session:
            run_obj = session.get(EvalRun, run_id)
            if not run_obj:
                return

            dataset = session.get(Dataset, run_obj.dataset_id)
            self.dataset_name = dataset.name if dataset else "Unknown"

            self.run = {
                "id": run_obj.id,
                "name": run_obj.name,
                "status": run_obj.status,
                "avg_score": (
                    f"{run_obj.avg_score:.1%}"
                    if run_obj.avg_score > 0 else "—"
                ),
                "progress": (
                    f"{run_obj.completed_cases}/{run_obj.total_cases}"
                ),
                "error": run_obj.error,
                "created": run_obj.created_at.strftime("%d-%m-%Y %H:%M"),
            }

            result_rows = session.exec(
                select(EvalResult)
                .where(EvalResult.eval_run_id == run_id)
                .order_by(EvalResult.test_case_index)
            ).all()

            self.results = []
            for r in result_rows:
                scores_raw = (
                    json.loads(r.scores_json) if r.scores_json else {}
                )

                # Pre-format scores summary: "metric: 85%" badges
                score_parts = []
                score_detail_lines = []
                overall_color = "gray"
                for mname, data in scores_raw.items():
                    val = (
                        data.get("score", 0)
                        if isinstance(data, dict) else 0
                    )
                    reason = (
                        data.get("reason", "")
                        if isinstance(data, dict) else ""
                    )
                    color = _color_for_score(val)
                    score_parts.append(f"{mname}: {val:.0%}")
                    detail = f"[{color}] {mname}: {val:.0%}"
                    if reason:
                        detail += f"\n  {reason}"
                    score_detail_lines.append(detail)
                    if color == "red":
                        overall_color = "red"
                    elif color == "yellow" and overall_color != "red":
                        overall_color = "yellow"
                    elif overall_color == "gray":
                        overall_color = "green"

                scores_summary = " | ".join(score_parts)
                scores_detail = "\n\n".join(score_detail_lines)

                # Pre-format conversation
                input_turns = (
                    json.loads(r.input_json) if r.input_json else []
                )
                conv_lines = []
                for t in input_turns:
                    role = (
                        "User" if t.get("role") == "user"
                        else "Assistant"
                    )
                    conv_lines.append(f"{role}: {t.get('content', '')}")
                conversation_text = "\n\n".join(conv_lines)

                # Parse tool/non-message activities
                raw_activities = (
                    json.loads(r.activities_json) if r.activities_json else []
                )
                tool_activities = [
                    a for a in raw_activities
                    if a.get("type") not in (
                        "message", "end_of_conversation", "typing", None
                    )
                ]
                if tool_activities:
                    tool_lines = []
                    for a in tool_activities:
                        atype = a.get("type", "?")
                        aname = a.get("name", "")
                        value = a.get("value") or {}

                        if aname == "DynamicPlanReceived":
                            steps = value.get("steps", [])
                            topics = [s.rsplit(".", 1)[-1] for s in steps]
                            tool_defs = value.get("toolDefinitions", [])
                            kinds = [
                                f"{d.get('displayName', '?')} ({d.get('toolKind', '?')})"
                                for d in tool_defs
                            ]
                            line = f"Plan → Topics: {', '.join(topics)}"
                            if kinds:
                                line += f"\n  Tools: {', '.join(kinds)}"

                        elif aname == "DynamicPlanStepTriggered":
                            topic = value.get("taskDialogId", "").rsplit(".", 1)[-1]
                            state = value.get("state", "?")
                            step_type = value.get("type", "?")
                            line = f"Step → {topic} [{step_type}] (state: {state})"

                        elif aname == "DynamicPlanStepBindUpdate":
                            topic = value.get("taskDialogId", "").rsplit(".", 1)[-1]
                            args = value.get("arguments", {})
                            line = f"Bind → {topic}"
                            if args:
                                line += f" (args: {args})"

                        else:
                            line = f"[{atype}] {aname}"
                            if value:
                                line += f"\n  {value}"

                        tool_lines.append(line)
                    tool_calls_text = "\n".join(tool_lines)
                else:
                    tool_calls_text = "No tool activity captured"

                self.results.append({
                    "index": r.test_case_index,
                    "num": str(r.test_case_index + 1),
                    "passed": r.passed,
                    "duration": f"{r.duration_seconds:.1f}s",
                    "actual_output": r.actual_output,
                    "expected_output": r.expected_output,
                    "scores_summary": scores_summary,
                    "scores_detail": scores_detail,
                    "scores_color": overall_color,
                    "conversation_text": conversation_text,
                    "tool_calls_text": tool_calls_text,
                })

        self.expanded_result = -1

    def toggle_result(self, index: int) -> None:
        if self.expanded_result == index:
            self.expanded_result = -1
        else:
            self.expanded_result = index

    def rerun(self) -> rx.Component:
        run_id_str = self.router.page.params.get("run_id", "0")
        try:
            run_id = int(run_id_str)
        except (ValueError, TypeError):
            return

        with rx.session() as session:
            original = session.get(EvalRun, run_id)
            if not original:
                return
            dataset = session.get(Dataset, original.dataset_id)
            if not dataset:
                return

            run = EvalRun(
                name=f"{original.name} (rerun)",
                dataset_id=original.dataset_id,
                status="pending",
                metrics_json=original.metrics_json,
                config_json=original.config_json,
                total_cases=dataset.num_cases,
                created_at=datetime.utcnow(),
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            new_run_id = run.id

        return rx.redirect(f"/runs/{new_run_id}")

    def export_results(self) -> rx.Component:
        export_data = {
            "run": self.run,
            "results": self.results,
        }
        return rx.download(
            data=json.dumps(export_data, indent=2, default=str),
            filename=(
                f"run_{self.run.get('id', 'unknown')}_results.json"
            ),
        )


def _expanded_content(r: rx.Var) -> rx.Component:
    return rx.vstack(
        rx.separator(),
        # Conversation
        rx.cond(
            r["conversation_text"].to(str) != "",
            rx.vstack(
                rx.text("Conversation", size="2", weight="medium"),
                rx.box(
                    rx.text(
                        r["conversation_text"].to(str),
                        size="2",
                        white_space="pre-wrap",
                    ),
                    padding="12px",
                    border_radius="var(--radius-2)",
                    background="var(--gray-a2)",
                    width="100%",
                ),
                spacing="2",
                width="100%",
            ),
        ),
        # Expected vs Actual side-by-side
        rx.cond(
            r["expected_output"].to(str) != "",
            rx.hstack(
                rx.vstack(
                    rx.text(
                        "Expected Output",
                        size="1",
                        weight="medium",
                        color_scheme="gray",
                    ),
                    rx.box(
                        rx.text(
                            r["expected_output"].to(str),
                            size="2",
                        ),
                        padding="8px",
                        border_radius="var(--radius-2)",
                        background="var(--gray-a2)",
                        width="100%",
                    ),
                    spacing="1",
                    flex="1",
                ),
                rx.vstack(
                    rx.text(
                        "Actual Output",
                        size="1",
                        weight="medium",
                        color_scheme="gray",
                    ),
                    rx.box(
                        rx.text(
                            r["actual_output"].to(str),
                            size="2",
                        ),
                        padding="8px",
                        border_radius="var(--radius-2)",
                        background="var(--gray-a2)",
                        width="100%",
                    ),
                    spacing="1",
                    flex="1",
                ),
                spacing="3",
                width="100%",
            ),
        ),
        # Per-metric scores detail
        rx.cond(
            r["scores_detail"].to(str) != "",
            rx.vstack(
                rx.text("Metric Scores", size="2", weight="medium"),
                rx.box(
                    rx.text(
                        r["scores_detail"].to(str),
                        size="2",
                        white_space="pre-wrap",
                    ),
                    padding="12px",
                    border_radius="var(--radius-2)",
                    background="var(--gray-a2)",
                    width="100%",
                ),
                spacing="2",
                width="100%",
            ),
        ),
        # Tool Calls
        rx.vstack(
            rx.text("Tool Calls", size="2", weight="medium"),
            rx.box(
                rx.text(
                    r["tool_calls_text"].to(str),
                    size="2",
                    white_space="pre-wrap",
                ),
                padding="12px",
                border_radius="var(--radius-2)",
                background="var(--gray-a2)",
                width="100%",
            ),
            spacing="2",
            width="100%",
        ),
        spacing="3",
        width="100%",
        padding_top="8px",
    )


def _result_row(r: rx.Var) -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.text(
                    r["num"].to(str),
                    size="2",
                    weight="medium",
                    min_width="30px",
                ),
                rx.cond(
                    r["passed"],
                    rx.badge("Pass", color_scheme="green", size="1"),
                    rx.badge("Fail", color_scheme="red", size="1"),
                ),
                rx.badge(
                    r["scores_summary"].to(str),
                    color_scheme=r["scores_color"].to(str),
                    variant="soft",
                    size="1",
                ),
                rx.spacer(),
                rx.text(
                    r["duration"].to(str),
                    size="1",
                    color_scheme="gray",
                ),
                rx.button(
                    rx.cond(
                        RunDetailState.expanded_result == r["index"],
                        rx.icon("chevron-up", size=14),
                        rx.icon("chevron-down", size=14),
                    ),
                    variant="ghost",
                    size="1",
                    on_click=RunDetailState.toggle_result(
                        r["index"]
                    ),
                ),
                align="center",
                width="100%",
                spacing="2",
            ),
            rx.cond(
                RunDetailState.expanded_result == r["index"],
                _expanded_content(r),
            ),
            spacing="2",
            width="100%",
        ),
        width="100%",
    )


def _header_section() -> rx.Component:
    return rx.vstack(
        rx.link(
            rx.hstack(
                rx.icon("arrow-left", size=16),
                rx.text("Eval Runs", size="2"),
                spacing="1",
                align="center",
            ),
            href="/runs",
            underline="none",
        ),
        rx.hstack(
            rx.heading(RunDetailState.run["name"], size="6"),
            status_badge(RunDetailState.run["status"]),
            rx.spacer(),
            rx.button(
                rx.icon("rotate-cw", size=14),
                "Rerun",
                variant="soft",
                size="1",
                on_click=RunDetailState.rerun,
            ),
            rx.button(
                rx.icon("download", size=14),
                "Export JSON",
                variant="soft",
                size="1",
                on_click=RunDetailState.export_results,
            ),
            align="center",
            width="100%",
        ),
        rx.hstack(
            rx.vstack(
                rx.text("Dataset", size="1", color_scheme="gray"),
                rx.text(
                    RunDetailState.dataset_name,
                    size="2",
                    weight="medium",
                ),
                spacing="0",
            ),
            rx.separator(orientation="vertical", size="2"),
            rx.vstack(
                rx.text("Avg Score", size="1", color_scheme="gray"),
                rx.text(
                    RunDetailState.run["avg_score"],
                    size="2",
                    weight="medium",
                ),
                spacing="0",
            ),
            rx.separator(orientation="vertical", size="2"),
            rx.vstack(
                rx.text("Progress", size="1", color_scheme="gray"),
                rx.text(
                    RunDetailState.run["progress"],
                    size="2",
                    weight="medium",
                ),
                spacing="0",
            ),
            rx.separator(orientation="vertical", size="2"),
            rx.vstack(
                rx.text("Created", size="1", color_scheme="gray"),
                rx.text(
                    RunDetailState.run["created"],
                    size="2",
                    weight="medium",
                ),
                spacing="0",
            ),
            spacing="4",
            align="center",
        ),
        rx.cond(
            RunDetailState.run["error"].to(str) != "",
            rx.callout(
                RunDetailState.run["error"],
                icon="triangle_alert",
                color_scheme="red",
                width="100%",
            ),
        ),
        spacing="3",
        width="100%",
        padding_bottom="8px",
    )


def _results_section() -> rx.Component:
    return rx.vstack(
        rx.text(
            "Results (",
            RunDetailState.results.length().to(str),
            ")",
            size="3",
            weight="medium",
        ),
        rx.cond(
            RunDetailState.results.length() > 0,
            rx.vstack(
                rx.foreach(RunDetailState.results, _result_row),
                spacing="2",
                width="100%",
            ),
            rx.center(
                rx.text(
                    "No results yet.",
                    size="2",
                    color_scheme="gray",
                ),
                padding="40px 0",
                width="100%",
            ),
        ),
        spacing="3",
        width="100%",
    )


@rx.page(
    route="/runs/[run_id]",
    title="Run Detail",
    on_load=RunDetailState.load_run,
)
def run_detail_page() -> rx.Component:
    return layout(
        rx.vstack(
            _header_section(),
            rx.separator(),
            _results_section(),
            spacing="4",
            width="100%",
        ),
    )
