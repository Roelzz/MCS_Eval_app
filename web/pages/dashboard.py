"""Dashboard page — stats, charts, recent runs."""

import json

import reflex as rx
from sqlmodel import select

from web.components import (
    empty_state,
    layout,
    page_header,
    stat_card,
    status_badge,
)
from web.models import EvalResult, EvalRun
from web.state import State


class DashboardState(State):
    total_runs: int = 0
    avg_score_display: str = "—"
    pass_rate_display: str = "—"
    last_run_display: str = "—"
    recent_runs: list[dict] = []
    metric_chart_data: list[dict] = []
    trend_chart_data: list[dict] = []

    def load_dashboard(self) -> None:
        with rx.session() as session:
            runs = session.exec(
                select(EvalRun).order_by(EvalRun.created_at.desc())
            ).all()

        self.total_runs = len(runs)
        completed = [r for r in runs if r.status == "completed"]

        if completed:
            scores = [
                r.avg_score for r in completed if r.avg_score > 0
            ]
            self.avg_score_display = (
                f"{sum(scores) / len(scores):.1%}" if scores else "—"
            )

            with rx.session() as session:
                all_results = session.exec(select(EvalResult)).all()
            total_results = len(all_results)
            passed_results = len(
                [r for r in all_results if r.passed]
            )
            self.pass_rate_display = (
                f"{passed_results / total_results:.0%}"
                if total_results > 0
                else "—"
            )

            self.last_run_display = completed[0].created_at.strftime(
                "%d-%m-%Y %H:%M"
            )
        else:
            self.avg_score_display = "—"
            self.pass_rate_display = "—"
            self.last_run_display = "—"

        self.recent_runs = [
            {
                "id": r.id,
                "name": r.name,
                "status": r.status,
                "avg_score": (
                    f"{r.avg_score:.1%}" if r.avg_score > 0 else "—"
                ),
                "cases": f"{r.completed_cases}/{r.total_cases}",
                "created": r.created_at.strftime("%d-%m-%Y %H:%M"),
            }
            for r in runs[:10]
        ]

        self._build_metric_chart(completed)
        self._build_trend_chart(completed)

    def _build_metric_chart(self, completed: list) -> None:
        if not completed:
            self.metric_chart_data = []
            return

        metric_scores: dict[str, list[float]] = {}
        with rx.session() as session:
            for run in completed[:10]:
                results = session.exec(
                    select(EvalResult).where(
                        EvalResult.eval_run_id == run.id
                    )
                ).all()
                for result in results:
                    try:
                        scores = json.loads(result.scores_json)
                        for metric_name, score_data in scores.items():
                            if isinstance(score_data, dict):
                                score = score_data.get("score", 0)
                            else:
                                score = score_data
                            metric_scores.setdefault(
                                metric_name, []
                            ).append(score)
                    except (json.JSONDecodeError, AttributeError):
                        pass

        self.metric_chart_data = [
            {
                "metric": name.replace("_", " ").title(),
                "avg_score": round(sum(vals) / len(vals), 3),
            }
            for name, vals in metric_scores.items()
            if vals
        ]

    def _build_trend_chart(self, completed: list) -> None:
        self.trend_chart_data = [
            {
                "run": r.name[:20],
                "score": round(r.avg_score, 3),
            }
            for r in reversed(completed[:20])
            if r.avg_score > 0
        ]


def _recent_runs_table() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.text(
                    "Recent Runs",
                    size="3",
                    weight="bold",
                    letter_spacing="-0.01em",
                ),
                rx.spacer(),
                rx.link(
                    rx.text(
                        "View all",
                        size="1",
                        color="var(--accent-9)",
                        _hover={"text_decoration": "underline"},
                    ),
                    href="/runs",
                    underline="none",
                ),
                align="center",
                width="100%",
            ),
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell("Name"),
                        rx.table.column_header_cell("Status"),
                        rx.table.column_header_cell("Score"),
                        rx.table.column_header_cell("Cases"),
                        rx.table.column_header_cell("Created"),
                    ),
                ),
                rx.table.body(
                    rx.foreach(
                        DashboardState.recent_runs,
                        lambda run: rx.table.row(
                            rx.table.cell(
                                rx.link(
                                    run["name"],
                                    href="/runs/" + run["id"].to(str),
                                    weight="medium",
                                    underline="none",
                                    _hover={
                                        "text_decoration": "underline"
                                    },
                                ),
                            ),
                            rx.table.cell(
                                status_badge(run["status"])
                            ),
                            rx.table.cell(
                                rx.text(
                                    run["avg_score"],
                                    font_family="var(--font-mono)",
                                    size="2",
                                ),
                            ),
                            rx.table.cell(
                                rx.text(
                                    run["cases"],
                                    font_family="var(--font-mono)",
                                    size="2",
                                    color="var(--gray-a9)",
                                ),
                            ),
                            rx.table.cell(
                                rx.text(
                                    run["created"],
                                    size="1",
                                    color="var(--gray-a8)",
                                    font_family="var(--font-mono)",
                                ),
                            ),
                        ),
                    ),
                ),
                width="100%",
            ),
            spacing="4",
            width="100%",
        ),
        width="100%",
    )


@rx.page(
    route="/",
    title="Dashboard",
    on_load=DashboardState.load_dashboard,
)
def dashboard_page() -> rx.Component:
    return layout(
        rx.vstack(
            page_header("Dashboard", "Eval results overview"),
            rx.grid(
                stat_card(
                    "Total Runs",
                    DashboardState.total_runs.to(str),
                    "circle-play",
                    "teal",
                ),
                stat_card(
                    "Avg Score",
                    DashboardState.avg_score_display,
                    "bar-chart-3",
                    "teal",
                ),
                stat_card(
                    "Pass Rate",
                    DashboardState.pass_rate_display,
                    "circle-check",
                    "green",
                ),
                stat_card(
                    "Last Run",
                    DashboardState.last_run_display,
                    "clock",
                    "gray",
                ),
                columns=rx.breakpoints(initial="1", sm="2", lg="4"),
                spacing="4",
                width="100%",
            ),
            rx.cond(
                DashboardState.total_runs > 0,
                rx.vstack(
                    rx.grid(
                        rx.card(
                            rx.vstack(
                                rx.text(
                                    "Scores by Metric",
                                    size="3",
                                    weight="bold",
                                    letter_spacing="-0.01em",
                                ),
                                rx.recharts.bar_chart(
                                    rx.recharts.bar(
                                        data_key="avg_score",
                                        fill="var(--accent-9)",
                                        radius=[4, 4, 0, 0],
                                    ),
                                    rx.recharts.x_axis(
                                        data_key="metric",
                                        tick_size=8,
                                    ),
                                    rx.recharts.y_axis(
                                        domain=[0, 1],
                                    ),
                                    rx.recharts.cartesian_grid(
                                        stroke_dasharray="3 3",
                                    ),
                                    rx.recharts.graphing_tooltip(),
                                    data=DashboardState.metric_chart_data,
                                    width="100%",
                                    height=280,
                                ),
                                spacing="4",
                                width="100%",
                            ),
                            width="100%",
                        ),
                        rx.card(
                            rx.vstack(
                                rx.text(
                                    "Score Trend",
                                    size="3",
                                    weight="bold",
                                    letter_spacing="-0.01em",
                                ),
                                rx.recharts.line_chart(
                                    rx.recharts.line(
                                        data_key="score",
                                        stroke="var(--accent-9)",
                                        stroke_width=2,
                                        dot=False,
                                    ),
                                    rx.recharts.x_axis(
                                        data_key="run",
                                        tick_size=8,
                                    ),
                                    rx.recharts.y_axis(
                                        domain=[0, 1],
                                    ),
                                    rx.recharts.cartesian_grid(
                                        stroke_dasharray="3 3",
                                    ),
                                    rx.recharts.graphing_tooltip(),
                                    data=DashboardState.trend_chart_data,
                                    width="100%",
                                    height=280,
                                ),
                                spacing="4",
                                width="100%",
                            ),
                            width="100%",
                        ),
                        columns=rx.breakpoints(
                            initial="1", lg="2"
                        ),
                        spacing="4",
                        width="100%",
                    ),
                    _recent_runs_table(),
                    spacing="5",
                    width="100%",
                ),
                empty_state(
                    "No eval runs yet. Create a dataset and "
                    "run your first eval."
                ),
            ),
            spacing="5",
            width="100%",
        ),
    )
