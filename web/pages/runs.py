"""Eval Runs page — configure, execute, and view eval results."""

import asyncio
import json
import time
from datetime import datetime

import reflex as rx
from sqlmodel import select

from web.components import empty_state, layout, page_header, status_badge
from web.models import Dataset, EvalResult, EvalRun
from web.state import State

AVAILABLE_METRICS = [
    # Tier 1 — deterministic, no LLM cost
    "exact_match",
    "keyword_match_any",
    "keyword_match_all",
    "topic_routing",
    # Tier 2 — AI judge (DeepEval)
    "answer_relevancy",
    "conversation_completeness",
    "knowledge_retention",
    "role_adherence",
    "task_completion",
    "hallucination",
    "toxicity",
    "bias",
    "faithfulness",
]


class RunState(State):
    runs: list[dict] = []
    dataset_options: list[str] = []
    dataset_id_map: dict[str, int] = {}
    show_create_dialog: bool = False

    # Create form
    new_run_name: str = ""
    selected_dataset_id: int = 0
    selected_metrics: list[str] = ["answer_relevancy"]
    threshold: float = 0.5
    delay_seconds: float = 1.0
    max_concurrent: int = 3
    create_error: str = ""

    def load_runs(self) -> None:
        with rx.session() as session:
            rows = session.exec(
                select(EvalRun).order_by(EvalRun.created_at.desc())
            ).all()
        self.runs = [
            {
                "id": r.id,
                "name": r.name,
                "status": r.status,
                "avg_score": f"{r.avg_score:.1%}" if r.avg_score > 0 else "—",
                "progress": f"{r.completed_cases}/{r.total_cases}",
                "progress_pct": (
                    round(r.completed_cases / r.total_cases * 100)
                    if r.total_cases > 0 else 0
                ),
                "error": r.error,
                "created": r.created_at.strftime("%d-%m-%Y %H:%M"),
            }
            for r in rows
        ]

        # Load datasets for the select dropdown
        with rx.session() as ds_session:
            datasets = ds_session.exec(
                select(Dataset).order_by(Dataset.name)
            ).all()
            self.dataset_options = [
                f"{d.name} ({d.num_cases} cases, {d.eval_type})"
                for d in datasets
            ]
            self.dataset_id_map = {
                f"{d.name} ({d.num_cases} cases, {d.eval_type})": d.id
                for d in datasets
            }

    def open_create_dialog(self) -> None:
        self.show_create_dialog = True
        self.new_run_name = ""
        self.selected_dataset_id = 0
        self.selected_metrics = ["answer_relevancy"]
        self.threshold = 0.5
        self.delay_seconds = 1.0
        self.max_concurrent = 3
        self.create_error = ""

    def close_create_dialog(self) -> None:
        self.show_create_dialog = False

    def set_new_run_name(self, value: str) -> None:
        self.new_run_name = value

    def set_dataset_selection(self, value: str) -> None:
        self.selected_dataset_id = self.dataset_id_map.get(value, 0)

    def set_threshold_value(self, value: str) -> None:
        try:
            self.threshold = float(value) if value else 0.5
        except ValueError:
            self.threshold = 0.5

    def set_delay_value(self, value: str) -> None:
        try:
            self.delay_seconds = float(value) if value else 1.0
        except ValueError:
            self.delay_seconds = 1.0

    def set_max_concurrent_value(self, value: str) -> None:
        try:
            self.max_concurrent = max(1, int(value)) if value else 3
        except ValueError:
            self.max_concurrent = 3

    def toggle_metric(self, metric: str) -> None:
        if metric in self.selected_metrics:
            self.selected_metrics = [m for m in self.selected_metrics if m != metric]
        else:
            self.selected_metrics = self.selected_metrics + [metric]

    def rerun(self, run_id: int) -> None:
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

        self.load_runs()
        return RunState.execute_run(new_run_id)

    def start_run(self) -> None:
        if not self.new_run_name.strip():
            self.create_error = "Run name is required."
            return
        if self.selected_dataset_id == 0:
            self.create_error = "Select a dataset."
            return
        if not self.selected_metrics:
            self.create_error = "Select at least one metric."
            return

        with rx.session() as session:
            dataset = session.get(Dataset, self.selected_dataset_id)
            if not dataset:
                self.create_error = "Dataset not found."
                return

            run = EvalRun(
                name=self.new_run_name.strip(),
                dataset_id=self.selected_dataset_id,
                status="pending",
                metrics_json=json.dumps(self.selected_metrics),
                config_json=json.dumps({
                    "threshold": self.threshold,
                    "delay_seconds": self.delay_seconds,
                    "max_concurrent": self.max_concurrent,
                }),
                total_cases=dataset.num_cases,
                created_at=datetime.utcnow(),
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id

        self.show_create_dialog = False
        self.load_runs()
        return RunState.execute_run(run_id)

    @rx.event(background=True)
    async def execute_run(self, run_id: int) -> None:
        from d2e_client import run_conversation
        from eval_engine import evaluate_case

        async with self:
            with rx.session() as session:
                run = session.get(EvalRun, run_id)
                if not run:
                    return
                run.status = "running"
                session.add(run)
                session.commit()

                dataset = session.get(Dataset, run.dataset_id)
                if not dataset:
                    run.status = "failed"
                    run.error = "Dataset not found"
                    session.add(run)
                    session.commit()
                    return

                cases = json.loads(dataset.data_json)
                metrics = json.loads(run.metrics_json)
                config = json.loads(run.config_json)
                threshold = config.get("threshold", 0.5)
                delay = config.get("delay_seconds", 1.0)
                max_concurrent = config.get("max_concurrent", 1)

        all_scores: list[float] = []
        sem = asyncio.Semaphore(max_concurrent)

        async def process_case(idx: int, case: dict) -> None:
            async with sem:
                try:
                    turns = case.get("turns", [])
                    expected = case.get("expected_output", "")
                    context = case.get("context", "")
                    expected_topic = case.get("expected_topic", "")
                    keywords_any = case.get("keywords_any", [])
                    keywords_all = case.get("keywords_all", [])

                    start = time.time()
                    conversation, activities = await run_conversation(turns)
                    actual_output = conversation[-1]["content"] if conversation else ""
                    duration = time.time() - start

                    scores = await evaluate_case(
                        turns=turns,
                        conversation=conversation,
                        expected_output=expected,
                        context=context,
                        metric_names=metrics,
                        threshold=threshold,
                        activities=activities,
                        expected_topic=expected_topic,
                        keywords_any=keywords_any,
                        keywords_all=keywords_all,
                    )

                    avg_case_score = (
                        sum(s.get("score", 0) for s in scores.values()) / len(scores)
                        if scores else 0
                    )
                    passed = avg_case_score >= threshold

                    async with self:
                        all_scores.append(avg_case_score)
                        with rx.session() as session:
                            result = EvalResult(
                                eval_run_id=run_id,
                                test_case_index=idx,
                                input_json=json.dumps(turns),
                                actual_output=actual_output,
                                expected_output=expected,
                                scores_json=json.dumps(scores),
                                activities_json=json.dumps(activities, default=str),
                                passed=passed,
                                duration_seconds=round(duration, 2),
                            )
                            session.add(result)
                            run = session.get(EvalRun, run_id)
                            run.completed_cases += 1
                            session.add(run)
                            session.commit()

                except Exception as e:
                    async with self:
                        with rx.session() as session:
                            result = EvalResult(
                                eval_run_id=run_id,
                                test_case_index=idx,
                                input_json=json.dumps(case.get("turns", [])),
                                actual_output=f"Error: {e}",
                                expected_output=case.get("expected_output", ""),
                                scores_json="{}",
                                passed=False,
                                duration_seconds=0,
                            )
                            session.add(result)
                            run = session.get(EvalRun, run_id)
                            run.completed_cases += 1
                            session.add(run)
                            session.commit()

        tasks = []
        for i, case in enumerate(cases):
            tasks.append(asyncio.create_task(process_case(i, case)))
            if delay > 0 and i < len(cases) - 1:
                await asyncio.sleep(delay)

        await asyncio.gather(*tasks, return_exceptions=True)

        async with self:
            with rx.session() as session:
                run = session.get(EvalRun, run_id)
                run.status = "completed"
                run.avg_score = sum(all_scores) / len(all_scores) if all_scores else 0
                run.completed_at = datetime.utcnow()
                session.add(run)
                session.commit()
            self.load_runs()


def create_run_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("New Eval Run"),
            rx.vstack(
                rx.input(
                    placeholder="Run name",
                    value=RunState.new_run_name,
                    on_change=RunState.set_new_run_name,
                    width="100%",
                ),
                rx.select(
                    RunState.dataset_options,
                    placeholder="Select dataset...",
                    on_change=RunState.set_dataset_selection,
                    width="100%",
                ),
                rx.text("Metrics", size="2", weight="medium"),
                rx.vstack(
                    *[
                        rx.hstack(
                            rx.checkbox(
                                metric.replace("_", " ").title(),
                                checked=RunState.selected_metrics.contains(metric),
                                on_change=lambda _val, m=metric: RunState.toggle_metric(m),
                            ),
                            spacing="2",
                        )
                        for metric in AVAILABLE_METRICS
                    ],
                    spacing="2",
                    width="100%",
                ),
                rx.hstack(
                    rx.vstack(
                        rx.text("Threshold", size="2", weight="medium"),
                        rx.input(
                            value=RunState.threshold.to(str),
                            on_change=RunState.set_threshold_value,
                            type="number",
                            width="100%",
                        ),
                        spacing="1",
                        width="33%",
                    ),
                    rx.vstack(
                        rx.text("Delay (seconds)", size="2", weight="medium"),
                        rx.input(
                            value=RunState.delay_seconds.to(str),
                            on_change=RunState.set_delay_value,
                            type="number",
                            width="100%",
                        ),
                        spacing="1",
                        width="33%",
                    ),
                    rx.vstack(
                        rx.text("Max concurrent", size="2", weight="medium"),
                        rx.input(
                            value=RunState.max_concurrent.to(str),
                            on_change=RunState.set_max_concurrent_value,
                            type="number",
                            width="100%",
                        ),
                        spacing="1",
                        width="33%",
                    ),
                    spacing="3",
                    width="100%",
                ),
                rx.cond(
                    RunState.create_error != "",
                    rx.callout(
                        RunState.create_error,
                        icon="triangle_alert",
                        color_scheme="red",
                        width="100%",
                    ),
                ),
                rx.hstack(
                    rx.dialog.close(
                        rx.button("Cancel", variant="soft", color_scheme="gray"),
                    ),
                    rx.button("Start Run", on_click=RunState.start_run),
                    spacing="3",
                    justify="end",
                    width="100%",
                ),
                spacing="3",
                width="100%",
            ),
            max_width="550px",
        ),
        open=RunState.show_create_dialog,
        on_open_change=lambda open: rx.cond(
            open,
            RunState.open_create_dialog(),
            RunState.close_create_dialog(),
        ),
    )



def runs_table() -> rx.Component:
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell("Name"),
                rx.table.column_header_cell("Status"),
                rx.table.column_header_cell("Progress"),
                rx.table.column_header_cell("Avg Score"),
                rx.table.column_header_cell("Created"),
                rx.table.column_header_cell("Actions"),
            ),
        ),
        rx.table.body(
            rx.foreach(
                RunState.runs,
                lambda r: rx.table.row(
                    rx.table.cell(rx.text(r["name"], weight="medium")),
                    rx.table.cell(status_badge(r["status"])),
                    rx.table.cell(
                        rx.vstack(
                            rx.progress(value=r["progress_pct"], width="100px"),
                            rx.text(r["progress"], size="1", color_scheme="gray"),
                            spacing="1",
                        ),
                    ),
                    rx.table.cell(r["avg_score"]),
                    rx.table.cell(rx.text(r["created"], size="2", color_scheme="gray")),
                    rx.table.cell(
                        rx.hstack(
                            rx.link(
                                rx.button(
                                    rx.icon("eye", size=14),
                                    variant="ghost",
                                    size="1",
                                ),
                                href="/runs/" + r["id"].to(str),
                            ),
                            rx.button(
                                rx.icon("rotate-cw", size=14),
                                variant="ghost",
                                size="1",
                                on_click=RunState.rerun(r["id"]),
                            ),
                            spacing="1",
                        ),
                    ),
                ),
            ),
        ),
        width="100%",
    )


@rx.page(route="/runs", title="Eval Runs", on_load=RunState.load_runs)
def runs_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.hstack(
                page_header("Eval Runs", "Configure and run evaluations"),
                rx.spacer(),
                rx.button(
                    rx.icon("plus", size=16),
                    "New Run",
                    on_click=RunState.open_create_dialog,
                    size="2",
                ),
                align="start",
                width="100%",
            ),
            rx.cond(
                RunState.runs.length() > 0,
                runs_table(),
                empty_state("No eval runs yet. Create a dataset first, then start a run.", "play"),
            ),
            create_run_dialog(),
            spacing="4",
            width="100%",
        ),
    )
