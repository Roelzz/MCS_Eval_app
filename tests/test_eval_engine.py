"""Unit tests for eval engine with mocked DeepEval metrics."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-02-01")


def _mock_judge():
    return MagicMock()


@pytest.mark.asyncio
async def test_evaluate_case_single_turn(mock_env):
    """Test single-turn evaluation with mocked metric."""
    mock_metric = MagicMock()
    mock_metric.score = 0.85
    mock_metric.reason = "Good answer"

    with (
        patch("eval_engine._get_judge_model", return_value=_mock_judge()),
        patch("eval_engine.METRIC_REGISTRY", {
            "answer_relevancy": lambda model, threshold: mock_metric,
        }),
    ):
        from eval_engine import evaluate_case

        result = await evaluate_case(
            turns=[{"role": "user", "content": "What is Python?"}],
            conversation=[
                {"role": "user", "content": "What is Python?"},
                {"role": "assistant", "content": "Python is a programming language."},
            ],
            expected_output="Python is a programming language.",
            context="",
            metric_names=["answer_relevancy"],
            threshold=0.5,
        )

    assert "answer_relevancy" in result
    assert result["answer_relevancy"]["score"] == 0.85
    assert result["answer_relevancy"]["passed"] is True


@pytest.mark.asyncio
async def test_evaluate_case_multiple_metrics(mock_env):
    """Test evaluation with multiple metrics."""
    mock_metric_a = MagicMock()
    mock_metric_a.score = 0.9
    mock_metric_a.reason = "Relevant"

    mock_metric_b = MagicMock()
    mock_metric_b.score = 0.3
    mock_metric_b.reason = "Incomplete"

    with (
        patch("eval_engine._get_judge_model", return_value=_mock_judge()),
        patch("eval_engine.METRIC_REGISTRY", {
            "answer_relevancy": lambda model, threshold: mock_metric_a,
            "task_completion": lambda model, threshold: mock_metric_b,
        }),
    ):
        from eval_engine import evaluate_case

        result = await evaluate_case(
            turns=[{"role": "user", "content": "Help me"}],
            conversation=[
                {"role": "user", "content": "Help me"},
                {"role": "assistant", "content": "Sure"},
            ],
            expected_output="Complete help",
            context="",
            metric_names=["answer_relevancy", "task_completion"],
            threshold=0.5,
        )

    assert len(result) == 2
    assert result["answer_relevancy"]["passed"] is True
    assert result["task_completion"]["passed"] is False


@pytest.mark.asyncio
async def test_evaluate_case_metric_error(mock_env):
    """Test graceful handling when a metric raises an error."""
    mock_metric = MagicMock()
    mock_metric.measure.side_effect = Exception("API timeout")

    with (
        patch("eval_engine._get_judge_model", return_value=_mock_judge()),
        patch("eval_engine.METRIC_REGISTRY", {
            "answer_relevancy": lambda model, threshold: mock_metric,
        }),
    ):
        from eval_engine import evaluate_case

        result = await evaluate_case(
            turns=[{"role": "user", "content": "Test"}],
            conversation=[
                {"role": "user", "content": "Test"},
                {"role": "assistant", "content": "Response"},
            ],
            expected_output="",
            context="",
            metric_names=["answer_relevancy"],
            threshold=0.5,
        )

    assert result["answer_relevancy"]["score"] == 0.0
    assert "Error" in result["answer_relevancy"]["reason"]
    assert result["answer_relevancy"]["passed"] is False


@pytest.mark.asyncio
async def test_evaluate_case_unknown_metric(mock_env):
    """Test that unknown metrics are skipped."""
    with patch("eval_engine._get_judge_model", return_value=_mock_judge()):
        from eval_engine import evaluate_case

        result = await evaluate_case(
            turns=[{"role": "user", "content": "Test"}],
            conversation=[
                {"role": "user", "content": "Test"},
                {"role": "assistant", "content": "Response"},
            ],
            expected_output="",
            context="",
            metric_names=["nonexistent_metric"],
            threshold=0.5,
        )

    assert "nonexistent_metric" not in result


# --- Topic routing tests ---


def _make_step_triggered(task_dialog_id: str, state: str = "inProgress") -> dict:
    return {
        "type": "event",
        "name": "DynamicPlanStepTriggered",
        "value": {
            "taskDialogId": task_dialog_id,
            "state": state,
            "type": "CustomTopic",
        },
    }


def test_topic_routing_match():
    from eval_engine import _evaluate_topic_routing

    activities = [
        _make_step_triggered("rrs_testAgent.topic.Greeting"),
    ]
    result = _evaluate_topic_routing(activities, "Greeting")
    assert result["score"] == 1.0
    assert result["passed"] is True
    assert "Greeting" in result["reason"]


def test_topic_routing_mismatch():
    from eval_engine import _evaluate_topic_routing

    activities = [
        _make_step_triggered("rrs_testAgent.topic.Greeting"),
    ]
    result = _evaluate_topic_routing(activities, "ITSupport")
    assert result["score"] == 0.0
    assert result["passed"] is False
    assert "Greeting" in result["reason"]
    assert "ITSupport" in result["reason"]


def test_topic_routing_no_activities():
    from eval_engine import _evaluate_topic_routing

    result = _evaluate_topic_routing([], "Greeting")
    assert result["score"] == 0.0
    assert result["passed"] is False
    assert "No topic routing" in result["reason"]


def test_topic_routing_no_expected():
    from eval_engine import _evaluate_topic_routing

    activities = [
        _make_step_triggered("rrs_testAgent.topic.Greeting"),
    ]
    result = _evaluate_topic_routing(activities, "")
    assert result["score"] == 1.0
    assert result["passed"] is True


@pytest.mark.asyncio
async def test_evaluate_case_topic_routing(mock_env):
    """Verify topic_routing dispatches to _evaluate_topic_routing without hitting DeepEval."""
    from eval_engine import evaluate_case

    activities = [
        _make_step_triggered("rrs_testAgent.topic.Greeting"),
    ]

    result = await evaluate_case(
        turns=[{"role": "user", "content": "Hi"}],
        conversation=[
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ],
        expected_output="",
        context="",
        metric_names=["topic_routing"],
        threshold=0.5,
        activities=activities,
        expected_topic="Greeting",
    )

    assert "topic_routing" in result
    assert result["topic_routing"]["score"] == 1.0
    assert result["topic_routing"]["passed"] is True
