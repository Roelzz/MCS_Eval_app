"""SQLModel database models for the eval platform."""

from datetime import UTC, datetime

import reflex as rx
import sqlmodel


class Dataset(rx.Model, table=True):
    name: str
    description: str = ""
    eval_type: str = "single_turn"  # single_turn | multi_turn | autonomous
    data_json: str = "[]"  # JSON string: list of test cases
    num_cases: int = 0
    created_at: datetime = sqlmodel.Field(default_factory=lambda: datetime.now(UTC))


class EvalRun(rx.Model, table=True):
    name: str
    dataset_id: int
    status: str = "pending"  # pending | running | completed | failed
    metrics_json: str = "[]"  # JSON string: list of metric names used
    config_json: str = "{}"  # JSON string: threshold, delay, etc.
    avg_score: float = 0.0
    total_cases: int = 0
    completed_cases: int = 0
    error: str = ""
    created_at: datetime = sqlmodel.Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class EvalResult(rx.Model, table=True):
    eval_run_id: int
    test_case_index: int = 0
    input_json: str = "[]"  # JSON string: the turns sent
    actual_output: str = ""
    expected_output: str = ""
    scores_json: str = "{}"  # JSON string: per-metric scores + reasons
    activities_json: str = "[]"  # JSON string: raw D2E activities captured per case
    passed: bool = False
    duration_seconds: float = 0.0
