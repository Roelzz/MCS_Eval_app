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
                "avg_score_num": run_obj.avg_score,
                "progress": (
                    f"{run_obj.completed_cases}/{run_obj.total_cases}"
                ),
                "progress_pct": (
                    round(run_obj.completed_cases / run_obj.total_cases * 100)
                    if run_obj.total_cases > 0 else 0
                ),
                "error": run_obj.error or "",
                "created": run_obj.created_at.strftime("%d-%m-%Y %H:%M"),
                "metrics": run_obj.metrics_json or "[]",
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

                # Build structured score items for visual rendering
                score_items = []
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
                    pct = round(val * 100)
                    score_items.append({
                        "name": mname.replace("_", " ").title(),
                        "raw": mname,
                        "val_pct": str(pct),
                        "val_text": f"{val:.0%}",
                        "color": color,
                        "reason": reason,
                        "bar_width": f"{pct}%",
                    })
                    if color == "red":
                        overall_color = "red"
                    elif color == "yellow" and overall_color != "red":
                        overall_color = "yellow"
                    elif overall_color == "gray":
                        overall_color = "green"

                scores_summary = " · ".join(
                    f"{it['raw']}: {it['val_text']}"
                    for it in score_items
                )

                # Pre-format conversation turns as structured list
                input_turns = (
                    json.loads(r.input_json) if r.input_json else []
                )
                conv_turns = []
                for t in input_turns:
                    role = t.get("role", "user")
                    conv_turns.append({
                        "role": "User" if role == "user" else "Assistant",
                        "content": t.get("content", ""),
                        "is_user": role == "user",
                    })

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
                tool_lines = []
                if tool_activities:
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
                            line = f"Plan · Topics: {', '.join(topics)}"
                            if kinds:
                                line += f"  |  Tools: {', '.join(kinds)}"
                            tool_lines.append({"icon": "map", "text": line, "color": "blue"})

                        elif aname == "DynamicPlanStepTriggered":
                            topic = value.get("taskDialogId", "").rsplit(".", 1)[-1]
                            state = value.get("state", "?")
                            step_type = value.get("type", "?")
                            line = f"Step · {topic} [{step_type}] state: {state}"
                            tool_lines.append({"icon": "play", "text": line, "color": "teal"})

                        elif aname == "DynamicPlanStepBindUpdate":
                            topic = value.get("taskDialogId", "").rsplit(".", 1)[-1]
                            args = value.get("arguments", {})
                            line = f"Bind · {topic}"
                            if args:
                                line += f"  args: {args}"
                            tool_lines.append({"icon": "link", "text": line, "color": "purple"})

                        else:
                            line = f"[{atype}] {aname}"
                            if value:
                                line += f"  →  {value}"
                            tool_lines.append({"icon": "zap", "text": line, "color": "gray"})

                if not tool_lines:
                    tool_lines.append({"icon": "minus", "text": "No tool activity captured", "color": "gray"})

                self.results.append({
                    "index": r.test_case_index,
                    "num": str(r.test_case_index + 1),
                    "passed": r.passed,
                    "duration": f"{r.duration_seconds:.1f}s",
                    "actual_output": r.actual_output or "",
                    "expected_output": r.expected_output or "",
                    "scores_summary": scores_summary,
                    "score_items": score_items,
                    "scores_color": overall_color,
                    "conv_turns": conv_turns,
                    "tool_lines": tool_lines,
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


def _score_bar(item: rx.Var) -> rx.Component:
    """Visual score bar row for a single metric."""
    color_map = {
        "green": "#34d399",
        "yellow": "#fbbf24",
        "red": "#f87171",
        "gray": "#94a3b8",
    }
    return rx.vstack(
        rx.hstack(
            rx.text(
                item["name"].to(str),
                size="1",
                color="var(--gray-a10)",
                weight="medium",
                min_width="160px",
            ),
            rx.box(
                rx.box(
                    width=item["bar_width"].to(str),
                    height="100%",
                    background=rx.cond(
                        item["color"] == "green", "#34d399",
                        rx.cond(
                            item["color"] == "yellow", "#fbbf24",
                            rx.cond(
                                item["color"] == "red", "#f87171",
                                "#94a3b8",
                            ),
                        ),
                    ),
                    border_radius="3px",
                    transition="width 0.4s ease",
                ),
                flex="1",
                height="8px",
                border_radius="4px",
                background="var(--gray-a3)",
                overflow="hidden",
            ),
            rx.text(
                item["val_text"].to(str),
                size="1",
                font_family="var(--font-mono)",
                weight="bold",
                color=rx.cond(
                    item["color"] == "green", "#34d399",
                    rx.cond(
                        item["color"] == "yellow", "#fbbf24",
                        rx.cond(
                            item["color"] == "red", "#f87171",
                            "var(--gray-a9)",
                        ),
                    ),
                ),
                min_width="40px",
                text_align="right",
            ),
            spacing="3",
            align="center",
            width="100%",
        ),
        rx.cond(
            item["reason"].to(str) != "",
            rx.text(
                item["reason"].to(str),
                size="1",
                color="var(--gray-a8)",
                padding_left="164px",
                font_style="italic",
            ),
        ),
        spacing="1",
        width="100%",
    )


def _chat_turn(turn: rx.Var) -> rx.Component:
    """Single chat bubble for conversation display."""
    return rx.cond(
        turn["is_user"],
        rx.hstack(
            rx.spacer(),
            rx.box(
                rx.text(
                    turn["content"].to(str),
                    size="2",
                    white_space="pre-wrap",
                ),
                padding="10px 14px",
                background="var(--accent-a3)",
                border="1px solid var(--accent-a5)",
                border_radius="12px 12px 4px 12px",
                max_width="75%",
            ),
            width="100%",
            spacing="2",
        ),
        rx.hstack(
            rx.box(
                rx.icon("bot", size=14, color="var(--gray-a8)"),
                padding="4px",
                border_radius="50%",
                background="var(--gray-a3)",
                flex_shrink="0",
                align_self="flex-start",
                margin_top="2px",
            ),
            rx.box(
                rx.text(
                    turn["content"].to(str),
                    size="2",
                    white_space="pre-wrap",
                ),
                padding="10px 14px",
                background="var(--gray-a2)",
                border="1px solid var(--gray-a4)",
                border_radius="4px 12px 12px 12px",
                max_width="75%",
            ),
            width="100%",
            spacing="2",
        ),
    )


def _tool_line(item: rx.Var) -> rx.Component:
    """Single tool activity line."""
    return rx.hstack(
        rx.box(
            rx.icon(
                item["icon"].to(str),
                size=12,
                color=rx.cond(
                    item["color"] == "blue", "var(--blue-9)",
                    rx.cond(
                        item["color"] == "teal", "var(--teal-9)",
                        rx.cond(
                            item["color"] == "purple", "var(--purple-9)",
                            "var(--gray-a7)",
                        ),
                    ),
                ),
            ),
            padding="4px",
            border_radius="4px",
            background=rx.cond(
                item["color"] == "blue", "var(--blue-a3)",
                rx.cond(
                    item["color"] == "teal", "var(--teal-a3)",
                    rx.cond(
                        item["color"] == "purple", "var(--purple-a3)",
                        "var(--gray-a2)",
                    ),
                ),
            ),
            flex_shrink="0",
        ),
        rx.text(
            item["text"].to(str),
            size="1",
            font_family="var(--font-mono)",
            white_space="pre-wrap",
            color="var(--gray-a10)",
        ),
        spacing="2",
        align="start",
        width="100%",
    )


def _expanded_content(r: rx.Var) -> rx.Component:
    return rx.vstack(
        rx.separator(margin_top="4px", margin_bottom="8px"),
        # Conversation
        rx.cond(
            r["conv_turns"].length() > 0,
            rx.vstack(
                rx.hstack(
                    rx.icon("message-circle", size=14, color="var(--accent-9)"),
                    rx.text("Conversation", size="2", weight="semibold"),
                    spacing="2",
                    align="center",
                ),
                rx.vstack(
                    rx.foreach(r["conv_turns"], _chat_turn),
                    spacing="2",
                    width="100%",
                    padding="12px",
                    border_radius="var(--radius-3)",
                    background="var(--gray-a1)",
                    border="1px solid var(--gray-a3)",
                ),
                spacing="2",
                width="100%",
            ),
        ),
        # Expected vs Actual
        rx.cond(
            r["expected_output"].to(str) != "",
            rx.hstack(
                rx.vstack(
                    rx.hstack(
                        rx.icon("target", size=13, color="var(--gray-a8)"),
                        rx.text("Expected", size="1", weight="medium", color="var(--gray-a9)"),
                        spacing="1",
                        align="center",
                    ),
                    rx.box(
                        rx.text(
                            r["expected_output"].to(str),
                            size="2",
                            white_space="pre-wrap",
                            color="var(--gray-a11)",
                        ),
                        padding="10px 12px",
                        border_radius="var(--radius-2)",
                        background="var(--gray-a2)",
                        border="1px solid var(--gray-a3)",
                        width="100%",
                    ),
                    spacing="2",
                    flex="1",
                ),
                rx.vstack(
                    rx.hstack(
                        rx.icon("message-square", size=13, color="var(--accent-a9)"),
                        rx.text("Actual", size="1", weight="medium", color="var(--gray-a9)"),
                        spacing="1",
                        align="center",
                    ),
                    rx.box(
                        rx.text(
                            r["actual_output"].to(str),
                            size="2",
                            white_space="pre-wrap",
                            color="var(--gray-a11)",
                        ),
                        padding="10px 12px",
                        border_radius="var(--radius-2)",
                        background="var(--accent-a2)",
                        border="1px solid var(--accent-a4)",
                        width="100%",
                    ),
                    spacing="2",
                    flex="1",
                ),
                spacing="3",
                width="100%",
                align="start",
            ),
        ),
        # Metric scores as visual bars
        rx.cond(
            r["score_items"].length() > 0,
            rx.vstack(
                rx.hstack(
                    rx.icon("bar-chart-2", size=14, color="var(--accent-9)"),
                    rx.text("Metric Scores", size="2", weight="semibold"),
                    spacing="2",
                    align="center",
                ),
                rx.vstack(
                    rx.foreach(r["score_items"], _score_bar),
                    spacing="3",
                    width="100%",
                    padding="14px 16px",
                    border_radius="var(--radius-3)",
                    background="var(--gray-a1)",
                    border="1px solid var(--gray-a3)",
                ),
                spacing="2",
                width="100%",
            ),
        ),
        # Tool activity
        rx.vstack(
            rx.hstack(
                rx.icon("zap", size=14, color="var(--accent-9)"),
                rx.text("Tool Activity", size="2", weight="semibold"),
                spacing="2",
                align="center",
            ),
            rx.vstack(
                rx.foreach(r["tool_lines"], _tool_line),
                spacing="2",
                width="100%",
                padding="12px 14px",
                border_radius="var(--radius-3)",
                background="var(--gray-a1)",
                border="1px solid var(--gray-a3)",
            ),
            spacing="2",
            width="100%",
        ),
        spacing="4",
        width="100%",
        padding_top="4px",
    )


def _result_row(r: rx.Var) -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                # Case number badge
                rx.badge(
                    "#" + r["num"].to(str),
                    variant="soft",
                    color_scheme="gray",
                    size="1",
                    font_family="var(--font-mono)",
                ),
                # Pass/Fail
                rx.cond(
                    r["passed"],
                    rx.hstack(
                        rx.icon("check-circle", size=13, color="#34d399"),
                        rx.text("Pass", size="1", color="#34d399", weight="medium"),
                        spacing="1",
                        align="center",
                    ),
                    rx.hstack(
                        rx.icon("x-circle", size=13, color="#f87171"),
                        rx.text("Fail", size="1", color="#f87171", weight="medium"),
                        spacing="1",
                        align="center",
                    ),
                ),
                # Scores summary (truncated)
                rx.text(
                    r["scores_summary"].to(str),
                    size="1",
                    color="var(--gray-a8)",
                    font_family="var(--font-mono)",
                    overflow="hidden",
                    text_overflow="ellipsis",
                    white_space="nowrap",
                    flex="1",
                    max_width="400px",
                ),
                rx.spacer(),
                # Duration
                rx.hstack(
                    rx.icon("clock", size=12, color="var(--gray-a6)"),
                    rx.text(
                        r["duration"].to(str),
                        size="1",
                        color="var(--gray-a7)",
                        font_family="var(--font-mono)",
                    ),
                    spacing="1",
                    align="center",
                ),
                # Expand toggle
                rx.button(
                    rx.cond(
                        RunDetailState.expanded_result == r["index"],
                        rx.icon("chevron-up", size=14),
                        rx.icon("chevron-down", size=14),
                    ),
                    variant="ghost",
                    size="1",
                    color_scheme="gray",
                    on_click=RunDetailState.toggle_result(r["index"]),
                ),
                align="center",
                width="100%",
                spacing="3",
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


def _score_summary_card() -> rx.Component:
    """Run-level score summary with big stat + progress bar."""
    return rx.card(
        rx.hstack(
            rx.vstack(
                rx.text(
                    RunDetailState.run["avg_score"],
                    size="8",
                    weight="bold",
                    letter_spacing="-0.04em",
                    font_family="var(--font-mono)",
                    color="var(--accent-9)",
                ),
                rx.text(
                    "Average Score",
                    size="1",
                    color="var(--gray-a8)",
                    weight="medium",
                    text_transform="uppercase",
                    letter_spacing="0.05em",
                ),
                spacing="1",
                align="start",
            ),
            rx.separator(orientation="vertical", size="3"),
            rx.vstack(
                rx.hstack(
                    rx.text("Progress", size="1", color="var(--gray-a8)", weight="medium"),
                    rx.spacer(),
                    rx.text(
                        RunDetailState.run["progress"],
                        size="1",
                        color="var(--gray-a9)",
                        font_family="var(--font-mono)",
                    ),
                    width="160px",
                ),
                rx.box(
                    rx.box(
                        width=RunDetailState.run["progress_pct"].to(str) + "%",
                        height="100%",
                        background="var(--accent-9)",
                        border_radius="4px",
                        transition="width 0.4s ease",
                    ),
                    width="160px",
                    height="8px",
                    border_radius="4px",
                    background="var(--gray-a3)",
                    overflow="hidden",
                ),
                spacing="2",
            ),
            rx.separator(orientation="vertical", size="3"),
            rx.vstack(
                rx.text("Dataset", size="1", color="var(--gray-a8)", weight="medium"),
                rx.text(RunDetailState.dataset_name, size="2", weight="medium"),
                spacing="1",
            ),
            rx.separator(orientation="vertical", size="3"),
            rx.vstack(
                rx.text("Created", size="1", color="var(--gray-a8)", weight="medium"),
                rx.text(
                    RunDetailState.run["created"],
                    size="2",
                    weight="medium",
                    font_family="var(--font-mono)",
                ),
                spacing="1",
            ),
            spacing="5",
            align="center",
            padding="4px 0",
        ),
        width="100%",
    )


def _header_section() -> rx.Component:
    return rx.vstack(
        rx.link(
            rx.hstack(
                rx.icon("arrow-left", size=14),
                rx.text("Eval Runs", size="2"),
                spacing="1",
                align="center",
            ),
            href="/runs",
            underline="none",
            color="var(--gray-a9)",
            _hover={"color": "var(--gray-a11)"},
        ),
        rx.hstack(
            rx.hstack(
                rx.heading(RunDetailState.run["name"], size="6", letter_spacing="-0.02em"),
                status_badge(RunDetailState.run["status"]),
                spacing="3",
                align="center",
            ),
            rx.spacer(),
            rx.button(
                rx.icon("rotate-cw", size=14),
                "Rerun",
                variant="soft",
                color_scheme="gray",
                size="1",
                on_click=RunDetailState.rerun,
            ),
            rx.button(
                rx.icon("download", size=14),
                "Export JSON",
                variant="soft",
                color_scheme="gray",
                size="1",
                on_click=RunDetailState.export_results,
            ),
            align="center",
            width="100%",
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
        _score_summary_card(),
        spacing="3",
        width="100%",
        padding_bottom="8px",
    )


def _results_section() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.hstack(
                rx.icon("list", size=16, color="var(--accent-9)"),
                rx.text(
                    "Results",
                    size="3",
                    weight="semibold",
                ),
                rx.badge(
                    RunDetailState.results.length().to(str),
                    variant="soft",
                    color_scheme="gray",
                    size="1",
                ),
                spacing="2",
                align="center",
            ),
            rx.spacer(),
            width="100%",
        ),
        rx.cond(
            RunDetailState.results.length() > 0,
            rx.vstack(
                rx.foreach(RunDetailState.results, _result_row),
                spacing="2",
                width="100%",
            ),
            rx.center(
                rx.vstack(
                    rx.icon("inbox", size=28, color="var(--gray-a5)"),
                    rx.text(
                        "No results yet.",
                        size="2",
                        color="var(--gray-a7)",
                    ),
                    align="center",
                    spacing="3",
                ),
                padding="60px 0",
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
