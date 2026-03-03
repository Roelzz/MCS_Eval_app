"""Tests for run comparison state logic."""

import json
from unittest.mock import MagicMock, patch


def _make_run(run_id: int, name: str) -> MagicMock:
    r = MagicMock()
    r.id = run_id
    r.name = name
    r.avg_score = 0.75
    r.status = "completed"
    r.total_cases = 2
    r.completed_cases = 2
    r.error = ""
    return r


def _make_result(run_id: int, scores: dict) -> MagicMock:
    r = MagicMock()
    r.eval_run_id = run_id
    r.scores_json = json.dumps(scores)
    r.passed = all(v.get("passed", False) for v in scores.values())
    return r


def test_toggle_compare_select_adds_run():
    """Selecting a run adds it to compare_selected."""
    from web.pages.runs import RunState

    state = RunState()
    state.compare_selected = []
    state.toggle_compare(1)
    assert 1 in state.compare_selected


def test_toggle_compare_select_deselects_run():
    """Selecting an already-selected run removes it."""
    from web.pages.runs import RunState

    state = RunState()
    state.compare_selected = [1]
    state.toggle_compare(1)
    assert state.compare_selected == []


def test_toggle_compare_max_two():
    """Selecting a third run replaces the oldest selection."""
    from web.pages.runs import RunState

    state = RunState()
    state.compare_selected = [1, 2]
    state.toggle_compare(3)
    assert state.compare_selected == [2, 3]


def test_load_compare_data_structure():
    """compare_run_a, compare_run_b, compare_metrics populated correctly."""
    from web.pages.runs import RunState

    run_a = _make_run(1, "Run A")
    run_b = _make_run(2, "Run B")

    result_a1 = _make_result(
        1,
        {
            "answer_relevancy": {"score": 0.8, "passed": True},
        },
    )
    result_a2 = _make_result(
        1,
        {
            "answer_relevancy": {"score": 0.6, "passed": True},
        },
    )
    result_b1 = _make_result(
        2,
        {
            "answer_relevancy": {"score": 0.9, "passed": True},
        },
    )
    result_b2 = _make_result(
        2,
        {
            "answer_relevancy": {"score": 0.7, "passed": True},
        },
    )

    mock_session = MagicMock()
    mock_session.get.side_effect = lambda model, run_id: run_a if run_id == 1 else run_b
    mock_session.exec.return_value.all.side_effect = [
        [result_a1, result_a2],
        [result_b1, result_b2],
    ]

    state = RunState()
    state.compare_selected = [1, 2]

    with patch("web.pages.runs.rx.session") as mock_ctx:
        mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        state.load_compare_data()

    assert state.compare_run_a["name"] == "Run A"
    assert state.compare_run_b["name"] == "Run B"
    assert len(state.compare_metrics) == 1

    metric = state.compare_metrics[0]
    assert metric["name"] == "answer_relevancy"
    assert metric["a_score"] == "70%"  # avg of 0.8 + 0.6 = 0.7 -> 70%
    assert metric["b_score"] == "80%"  # avg of 0.9 + 0.7 = 0.8 -> 80%
    assert metric["delta_up"] is True
    assert metric["delta_down"] is False
    assert metric["delta_str"] == "↑ 10%"  # abs(0.8 - 0.7) = 0.1 → 10%
