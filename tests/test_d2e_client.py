"""Unit tests for D2E client with mocked CopilotClient."""

from unittest.mock import MagicMock, patch

import pytest


def _make_activity(type_val: str, text: str | None = None, conversation_id: str | None = None):
    """Create a mock Activity object."""
    activity = MagicMock()
    activity.type = type_val
    activity.text = text
    activity.suggested_actions = None
    if conversation_id:
        activity.conversation = MagicMock()
        activity.conversation.id = conversation_id
    else:
        activity.conversation = None
    return activity


async def _async_iter(items):
    """Helper to create an async iterable from a list."""
    for item in items:
        yield item


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("AZURE_AD_TENANT_ID", "test-tenant")
    monkeypatch.setenv("AZURE_AD_CLIENT_ID", "test-client")
    monkeypatch.setenv("AZURE_AD_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("COPILOT_ENVIRONMENT_ID", "test-env")
    monkeypatch.setenv("COPILOT_AGENT_IDENTIFIER", "test-agent")


@pytest.mark.asyncio
async def test_run_conversation_single_turn(mock_env):
    """Test single-turn conversation flow."""
    mock_client = MagicMock()
    mock_client.start_conversation = MagicMock(
        return_value=_async_iter([
            _make_activity("message", "Welcome!", "conv-123"),
        ])
    )
    mock_client.ask_question = MagicMock(
        return_value=_async_iter([
            _make_activity("message", "I can help with that!"),
            _make_activity("end_of_conversation"),
        ])
    )

    with (
        patch("d2e_client.CopilotClient", return_value=mock_client),
        patch("d2e_client.acquire_token", return_value="test-token"),
    ):
        from d2e_client import run_conversation

        result, activities = await run_conversation([{"role": "user", "content": "Hello"}])

    assert len(result) == 2
    assert result[0] == {"role": "user", "content": "Hello"}
    assert result[1]["role"] == "assistant"
    assert "I can help with that!" in result[1]["content"]
    assert isinstance(activities, list)


@pytest.mark.asyncio
async def test_run_conversation_multi_turn(mock_env):
    """Test multi-turn conversation."""
    mock_client = MagicMock()
    mock_client.start_conversation = MagicMock(
        return_value=_async_iter([
            _make_activity("message", "Hi!", "conv-456"),
        ])
    )

    def make_response(text, conv_id):
        return _async_iter([
            _make_activity("message", f"Response to: {text}"),
        ])

    mock_client.ask_question = MagicMock(side_effect=make_response)

    with (
        patch("d2e_client.CopilotClient", return_value=mock_client),
        patch("d2e_client.acquire_token", return_value="test-token"),
    ):
        from d2e_client import run_conversation

        result, activities = await run_conversation([
            {"role": "user", "content": "First question"},
            {"role": "user", "content": "Follow up"},
        ])

    assert len(result) == 4
    assert result[0]["content"] == "First question"
    assert result[1]["role"] == "assistant"
    assert result[2]["content"] == "Follow up"
    assert result[3]["role"] == "assistant"
    assert isinstance(activities, list)


@pytest.mark.asyncio
async def test_run_conversation_no_conversation_id(mock_env):
    """Test error when no conversation ID received."""
    mock_client = MagicMock()
    mock_client.start_conversation = MagicMock(
        return_value=_async_iter([
            _make_activity("typing"),  # No conversation ID
        ])
    )

    with (
        patch("d2e_client.CopilotClient", return_value=mock_client),
        patch("d2e_client.acquire_token", return_value="test-token"),
    ):
        from d2e_client import run_conversation

        with pytest.raises(RuntimeError, match="Failed to start conversation"):
            await run_conversation([{"role": "user", "content": "Hello"}])


@pytest.mark.asyncio
async def test_run_conversation_suggested_actions(mock_env):
    """Test that suggested actions are captured in response."""
    action_activity = _make_activity("message", "Choose an option:", None)
    action_activity.suggested_actions = MagicMock()
    action = MagicMock()
    action.title = "Option A"
    action_activity.suggested_actions.actions = [action]

    mock_client = MagicMock()
    mock_client.start_conversation = MagicMock(
        return_value=_async_iter([
            _make_activity("message", "Hi!", "conv-789"),
        ])
    )
    mock_client.ask_question = MagicMock(
        return_value=_async_iter([action_activity])
    )

    with (
        patch("d2e_client.CopilotClient", return_value=mock_client),
        patch("d2e_client.acquire_token", return_value="test-token"),
    ):
        from d2e_client import run_conversation

        result, activities = await run_conversation([{"role": "user", "content": "Help"}])

    assert "[Suggested: Option A]" in result[1]["content"]
    assert isinstance(activities, list)


@pytest.mark.asyncio
async def test_run_conversation_captures_invoke_activity(mock_env):
    """Test that invoke activities are captured in the activities list."""
    invoke_activity = MagicMock()
    invoke_activity.type = "invoke"
    invoke_activity.text = None
    invoke_activity.name = "SearchPlugin"
    invoke_activity.value = {"query": "weather"}
    invoke_activity.value_type = "application/json"
    invoke_activity.suggested_actions = None
    invoke_activity.entities = None
    invoke_activity.channel_data = None
    invoke_activity.conversation = None

    mock_client = MagicMock()
    mock_client.start_conversation = MagicMock(
        return_value=_async_iter([
            _make_activity("message", "Hi!", "conv-invoke"),
        ])
    )
    mock_client.ask_question = MagicMock(
        return_value=_async_iter([
            invoke_activity,
            _make_activity("message", "The weather is sunny."),
        ])
    )

    with (
        patch("d2e_client.CopilotClient", return_value=mock_client),
        patch("d2e_client.acquire_token", return_value="test-token"),
    ):
        from d2e_client import run_conversation

        result, activities = await run_conversation([
            {"role": "user", "content": "What's the weather?"},
        ])

    assert result[1]["content"] == "The weather is sunny."
    invoke_acts = [a for a in activities if a["type"] == "invoke"]
    assert len(invoke_acts) == 1
    assert invoke_acts[0]["name"] == "SearchPlugin"
    assert invoke_acts[0]["value"] == {"query": "weather"}
    assert invoke_acts[0]["value_type"] == "application/json"


@pytest.mark.asyncio
async def test_run_conversation_captures_trace_activity(mock_env):
    """Test that trace activities are captured in the activities list."""
    trace_activity = MagicMock()
    trace_activity.type = "trace"
    trace_activity.text = None
    trace_activity.name = "DialogTrace"
    trace_activity.value = {"step": "topic_redirect"}
    trace_activity.label = "TopicSwitch"
    trace_activity.suggested_actions = None
    trace_activity.entities = None
    trace_activity.channel_data = None
    trace_activity.conversation = None

    mock_client = MagicMock()
    mock_client.start_conversation = MagicMock(
        return_value=_async_iter([
            _make_activity("message", "Hi!", "conv-trace"),
        ])
    )
    mock_client.ask_question = MagicMock(
        return_value=_async_iter([
            trace_activity,
            _make_activity("message", "Redirected response."),
        ])
    )

    with (
        patch("d2e_client.CopilotClient", return_value=mock_client),
        patch("d2e_client.acquire_token", return_value="test-token"),
    ):
        from d2e_client import run_conversation

        result, activities = await run_conversation([
            {"role": "user", "content": "Switch topic"},
        ])

    assert result[1]["content"] == "Redirected response."
    trace_acts = [a for a in activities if a["type"] == "trace"]
    assert len(trace_acts) == 1
    assert trace_acts[0]["name"] == "DialogTrace"
    assert trace_acts[0]["value"] == {"step": "topic_redirect"}
    assert trace_acts[0]["label"] == "TopicSwitch"


def test_test_agent(mock_env):
    """Test synchronous test_agent wrapper."""
    mock_client = MagicMock()
    mock_client.start_conversation = MagicMock(
        return_value=_async_iter([
            _make_activity("message", "Welcome!", "conv-sync"),
        ])
    )
    mock_client.ask_question = MagicMock(
        return_value=_async_iter([
            _make_activity("message", "Test response"),
        ])
    )

    with (
        patch("d2e_client.CopilotClient", return_value=mock_client),
        patch("d2e_client.acquire_token", return_value="test-token"),
    ):
        from d2e_client import test_agent

        result = test_agent("Hello")

    assert result == "Test response"
