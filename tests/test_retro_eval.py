"""Unit tests for retro_eval module."""

import json

import pytest


def _make_client():
    from dataverse_client import DataverseClient

    return DataverseClient("https://org.crm.dynamics.com", "tenant", "client", "secret")


# --- extract_conversation tests ---


def test_extract_conversation_basic():
    from dataverse_client import DataverseClient

    client = _make_client()
    content = json.dumps([
        {
            "type": "message",
            "from": {"id": "user-1", "role": "user"},
            "text": "Hello, I need help",
            "timestamp": "2024-01-15T10:00:00Z",
        },
        {
            "type": "message",
            "from": {"id": "bot-1", "role": "bot"},
            "text": "Sure, I can help you.",
            "timestamp": "2024-01-15T10:00:05Z",
        },
    ])

    turns = client.extract_conversation(content)

    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert turns[0]["content"] == "Hello, I need help"
    assert turns[1]["role"] == "assistant"
    assert turns[1]["content"] == "Sure, I can help you."


def test_extract_conversation_skips_non_message_activities():
    client = _make_client()
    content = json.dumps([
        {
            "type": "event",
            "valueType": "IntentRecognition",
            "value": {"topicName": "LeaveBalance", "confidence": 0.9},
        },
        {
            "type": "message",
            "from": {"id": "user-1", "role": "user"},
            "text": "What is my leave balance?",
        },
        {
            "type": "trace",
            "label": "Recognizer result",
        },
    ])

    turns = client.extract_conversation(content)

    assert len(turns) == 1
    assert turns[0]["role"] == "user"
    assert turns[0]["content"] == "What is my leave balance?"


def test_extract_conversation_skips_empty_text():
    client = _make_client()
    content = json.dumps([
        {
            "type": "message",
            "from": {"id": "user-1", "role": "user"},
            "text": "   ",  # whitespace only
        },
        {
            "type": "message",
            "from": {"id": "user-1", "role": "user"},
            "text": "Real message",
        },
    ])

    turns = client.extract_conversation(content)

    assert len(turns) == 1
    assert turns[0]["content"] == "Real message"


def test_extract_conversation_empty_content():
    client = _make_client()
    assert client.extract_conversation("") == []


def test_extract_conversation_invalid_json():
    client = _make_client()
    assert client.extract_conversation("{not json") == []


def test_extract_conversation_skill_role():
    """Role 'skill' should map to assistant."""
    client = _make_client()
    content = json.dumps([
        {
            "type": "message",
            "from": {"role": "skill"},
            "text": "Skill response",
        },
    ])

    turns = client.extract_conversation(content)
    assert turns[0]["role"] == "assistant"


def test_extract_conversation_fallback_role_from_id():
    """When role is missing, fall back to checking from.id."""
    client = _make_client()
    content = json.dumps([
        {
            "type": "message",
            "from": {"id": "bot-abc"},
            "text": "Bot response without role field",
        },
    ])

    turns = client.extract_conversation(content)
    assert turns[0]["role"] == "assistant"


# --- extract_test_case_from_transcript tests ---


def _make_transcript(content: list[dict], transcript_id: str = "tid-001") -> dict:
    return {
        "conversationtranscriptid": transcript_id,
        "content": json.dumps(content),
        "createdon": "2024-01-15T10:00:00Z",
    }


def test_extract_test_case_basic():
    from retro_eval import extract_test_case_from_transcript

    client = _make_client()
    content = [
        {
            "type": "message",
            "from": {"role": "user"},
            "text": "I need leave",
        },
        {
            "type": "message",
            "from": {"role": "bot"},
            "text": "You have 12 days remaining.",
        },
        {
            "valueType": "SessionInfo",
            "value": {"outcome": "Resolved"},
        },
        {
            "valueType": "IntentRecognition",
            "value": {"topicName": "LeaveBalance", "confidence": 0.95},
        },
    ]
    transcript = _make_transcript(content)
    tc = extract_test_case_from_transcript(transcript, client)

    assert tc is not None
    assert tc.transcript_id == "tid-001"
    assert len(tc.conversation) == 2
    assert tc.turns[0]["content"] == "I need leave"
    assert tc.session_outcome == "Resolved"
    assert tc.intent_recognition[0]["topic"] == "LeaveBalance"


def test_extract_test_case_no_messages_returns_none():
    from retro_eval import extract_test_case_from_transcript

    client = _make_client()
    content = [
        {"valueType": "SessionInfo", "value": {"outcome": "Abandoned"}},
    ]
    transcript = _make_transcript(content)
    result = extract_test_case_from_transcript(transcript, client)
    assert result is None


def test_extract_test_case_empty_content_returns_none():
    from retro_eval import extract_test_case_from_transcript

    client = _make_client()
    transcript = {"conversationtranscriptid": "tid-002", "content": "", "createdon": None}
    result = extract_test_case_from_transcript(transcript, client)
    assert result is None


# --- run_tier1_metrics tests ---


def _make_test_case(
    conversation: list[dict] | None = None,
    intents: list[dict] | None = None,
    outcome: str = "Resolved",
) -> "RetroTestCase":
    from retro_eval import RetroTestCase

    conv = conversation or [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    turns = [t for t in conv if t["role"] == "user"]
    return RetroTestCase(
        transcript_id="tid-test",
        conversation=conv,
        turns=turns,
        intent_recognition=intents or [],
        session_outcome=outcome,
        dialog_redirects=[],
        csat=None,
        created_at=None,
    )


def test_run_tier1_metrics_topic_routing_match():
    from retro_eval import run_tier1_metrics

    tc = _make_test_case(intents=[{"topic": "rrs_bot.topic.Greeting", "score": 0.9}])
    results = run_tier1_metrics(tc, expected_topic="Greeting")

    assert "topic_routing" in results
    assert results["topic_routing"]["passed"] is True


def test_run_tier1_metrics_topic_routing_mismatch():
    from retro_eval import run_tier1_metrics

    tc = _make_test_case(intents=[{"topic": "rrs_bot.topic.Greeting", "score": 0.9}])
    results = run_tier1_metrics(tc, expected_topic="ITSupport")

    assert "topic_routing" in results
    assert results["topic_routing"]["passed"] is False


def test_run_tier1_metrics_exact_match():
    from retro_eval import run_tier1_metrics

    tc = _make_test_case(
        conversation=[
            {"role": "user", "content": "Say hi"},
            {"role": "assistant", "content": "Hi there!"},
        ]
    )
    results = run_tier1_metrics(tc, expected_output="Hi there!")

    assert "exact_match" in results
    assert results["exact_match"]["passed"] is True


def test_run_tier1_metrics_keyword_any():
    from retro_eval import run_tier1_metrics

    tc = _make_test_case(
        conversation=[
            {"role": "user", "content": "Check leave"},
            {"role": "assistant", "content": "You have 5 days remaining."},
        ]
    )
    results = run_tier1_metrics(tc, keywords_any=["days remaining", "vacation"])

    assert "keyword_match_any" in results
    assert results["keyword_match_any"]["passed"] is True


def test_run_tier1_metrics_keyword_all_partial():
    from retro_eval import run_tier1_metrics

    tc = _make_test_case(
        conversation=[
            {"role": "user", "content": "Get policy"},
            {"role": "assistant", "content": "Policy ID: 123"},
        ]
    )
    results = run_tier1_metrics(tc, keywords_all=["Policy ID:", "expires"])

    assert "keyword_match_all" in results
    assert results["keyword_match_all"]["passed"] is False


def test_run_tier1_metrics_no_config_no_intents():
    """When no topic/keywords/expected_output and no intents, no metrics run."""
    from retro_eval import run_tier1_metrics

    tc = _make_test_case(intents=[])
    results = run_tier1_metrics(tc)

    assert results == {}


# --- suggest_dataset_cases tests ---


def test_suggest_dataset_cases_basic():
    from retro_eval import suggest_dataset_cases

    tc1 = _make_test_case(
        conversation=[
            {"role": "user", "content": "What is my leave balance?"},
            {"role": "assistant", "content": "You have 10 days."},
        ],
        intents=[{"topic": "LeaveBalance", "score": 0.9}],
        outcome="Resolved",
    )
    tc1.transcript_id = "tid-001"

    suggestions = suggest_dataset_cases([tc1])

    assert len(suggestions) == 1
    assert suggestions[0].utterance == "What is my leave balance?"
    assert suggestions[0].inferred_topic == "LeaveBalance"
    assert suggestions[0].is_multi_turn is False


def test_suggest_dataset_cases_deduplication():
    from retro_eval import suggest_dataset_cases

    tc1 = _make_test_case(
        conversation=[
            {"role": "user", "content": "Check my leave"},
            {"role": "assistant", "content": "10 days."},
        ]
    )
    tc1.transcript_id = "tid-001"

    tc2 = _make_test_case(
        conversation=[
            {"role": "user", "content": "Check my leave"},  # same utterance
            {"role": "assistant", "content": "12 days."},
        ]
    )
    tc2.transcript_id = "tid-002"

    suggestions = suggest_dataset_cases([tc1, tc2])
    assert len(suggestions) == 1


def test_suggest_dataset_cases_existing_utterances_filtered():
    from retro_eval import suggest_dataset_cases

    tc = _make_test_case(
        conversation=[
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
    )
    tc.transcript_id = "tid-001"

    suggestions = suggest_dataset_cases([tc], existing_utterances={"hello"})
    assert len(suggestions) == 0


def test_suggest_dataset_cases_multi_turn():
    from retro_eval import suggest_dataset_cases, RetroTestCase

    tc = RetroTestCase(
        transcript_id="tid-mt",
        conversation=[
            {"role": "user", "content": "I need IT support"},
            {"role": "assistant", "content": "Sure, what's the issue?"},
            {"role": "user", "content": "My laptop won't start"},
            {"role": "assistant", "content": "Let me raise a ticket."},
        ],
        turns=[
            {"role": "user", "content": "I need IT support"},
            {"role": "user", "content": "My laptop won't start"},
        ],
        intent_recognition=[{"topic": "ITSupport", "score": 0.88}],
        session_outcome="Escalated",
        dialog_redirects=[],
        csat=None,
        created_at=None,
    )

    suggestions = suggest_dataset_cases([tc])

    assert len(suggestions) == 1
    assert suggestions[0].is_multi_turn is True
    assert len(suggestions[0].follow_up_turns) == 1
    assert suggestions[0].follow_up_turns[0] == "My laptop won't start"


def test_suggest_dataset_cases_low_confidence_no_topic():
    from retro_eval import suggest_dataset_cases

    tc = _make_test_case(
        conversation=[
            {"role": "user", "content": "Random message"},
            {"role": "assistant", "content": "I see."},
        ],
        intents=[{"topic": "Something", "score": 0.2}],  # below default 0.5
    )
    tc.transcript_id = "tid-low"

    suggestions = suggest_dataset_cases([tc], min_confidence=0.5)

    assert len(suggestions) == 1
    # Topic not inferred due to low confidence
    assert suggestions[0].inferred_topic == ""


# --- Integration-style test: full pipeline ---


def test_full_retro_pipeline():
    from retro_eval import extract_test_case_from_transcript, run_tier1_metrics, suggest_dataset_cases

    client = _make_client()

    transcripts = [
        _make_transcript(
            [
                {
                    "type": "message",
                    "from": {"role": "user"},
                    "text": "How many vacation days do I have?",
                },
                {
                    "type": "message",
                    "from": {"role": "bot"},
                    "text": "You have 15 vacation days remaining.",
                },
                {
                    "valueType": "IntentRecognition",
                    "value": {"topicName": "VacationBalance", "confidence": 0.92},
                },
                {
                    "valueType": "SessionInfo",
                    "value": {"outcome": "Resolved"},
                },
            ],
            transcript_id="tid-full-001",
        ),
        _make_transcript(
            [
                {
                    "type": "message",
                    "from": {"role": "user"},
                    "text": "I want to report an IT issue",
                },
                {
                    "type": "message",
                    "from": {"role": "bot"},
                    "text": "Please describe your IT problem.",
                },
                {
                    "type": "message",
                    "from": {"role": "user"},
                    "text": "My VPN keeps disconnecting",
                },
                {
                    "type": "message",
                    "from": {"role": "bot"},
                    "text": "I've raised a ticket for you.",
                },
                {
                    "valueType": "IntentRecognition",
                    "value": {"topicName": "ITSupport", "confidence": 0.85},
                },
                {
                    "valueType": "SessionInfo",
                    "value": {"outcome": "Escalated"},
                },
            ],
            transcript_id="tid-full-002",
        ),
    ]

    test_cases = []
    for t in transcripts:
        tc = extract_test_case_from_transcript(t, client)
        if tc:
            test_cases.append(tc)

    assert len(test_cases) == 2

    # Run metrics
    results_0 = run_tier1_metrics(
        test_cases[0],
        keywords_any=["vacation days", "days remaining"],
    )
    assert results_0["keyword_match_any"]["passed"] is True

    # Dataset suggestions
    suggestions = suggest_dataset_cases(test_cases)
    assert len(suggestions) == 2

    multi_turn = [s for s in suggestions if s.is_multi_turn]
    assert len(multi_turn) == 1
    assert "IT" in multi_turn[0].utterance or "IT" in multi_turn[0].inferred_topic
