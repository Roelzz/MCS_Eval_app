"""Unit tests for database models."""

import json

from web.models import Dataset, EvalResult, EvalRun


def test_dataset_defaults():
    d = Dataset(name="test")
    assert d.eval_type == "single_turn"
    assert d.data_json == "[]"
    assert d.num_cases == 0


def test_dataset_with_data():
    cases = [{"turns": [{"role": "user", "content": "hi"}], "expected_output": "hello"}]
    d = Dataset(
        name="test",
        eval_type="single_turn",
        data_json=json.dumps(cases),
        num_cases=1,
    )
    assert json.loads(d.data_json) == cases
    assert d.num_cases == 1


def test_eval_run_defaults():
    r = EvalRun(name="run-1", dataset_id=1)
    assert r.status == "pending"
    assert r.avg_score == 0.0
    assert r.total_cases == 0
    assert r.completed_cases == 0


def test_eval_result_defaults():
    r = EvalResult(eval_run_id=1, test_case_index=0)
    assert r.passed is False
    assert r.duration_seconds == 0.0
    assert r.scores_json == "{}"
