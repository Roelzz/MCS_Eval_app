"""Unit tests for DataverseClient.parse_transcript."""

import json

import pytest


def test_parse_transcript_intent_recognition():
    from dataverse_client import DataverseClient

    client = DataverseClient("https://org.crm.dynamics.com", "tenant", "client", "secret")
    content = json.dumps([
        {
            "valueType": "IntentRecognition",
            "value": {"topicName": "LeaveBalance", "confidence": 0.97},
        }
    ])
    result = client.parse_transcript(content)

    assert len(result["intent_recognition"]) == 1
    assert result["intent_recognition"][0]["topic"] == "LeaveBalance"
    assert result["intent_recognition"][0]["score"] == pytest.approx(0.97)


def test_parse_transcript_session_info():
    from dataverse_client import DataverseClient

    client = DataverseClient("https://org.crm.dynamics.com", "tenant", "client", "secret")
    content = json.dumps([
        {
            "valueType": "SessionInfo",
            "value": {"outcome": "Resolved"},
        }
    ])
    result = client.parse_transcript(content)

    assert result["session_info"]["outcome"] == "Resolved"


def test_parse_transcript_csat():
    from dataverse_client import DataverseClient

    client = DataverseClient("https://org.crm.dynamics.com", "tenant", "client", "secret")
    content = json.dumps([
        {
            "valueType": "CSATSurveyResponse",
            "value": {"rating": 4.5},
        }
    ])
    result = client.parse_transcript(content)

    assert result["csat"] == pytest.approx(4.5)


def test_parse_transcript_dialog_redirect():
    from dataverse_client import DataverseClient

    client = DataverseClient("https://org.crm.dynamics.com", "tenant", "client", "secret")
    content = json.dumps([
        {
            "valueType": "DialogRedirect",
            "value": {"targetDialogId": "EscalationTopic"},
        }
    ])
    result = client.parse_transcript(content)

    assert "EscalationTopic" in result["dialog_redirects"]


def test_parse_transcript_combined():
    from dataverse_client import DataverseClient

    client = DataverseClient("https://org.crm.dynamics.com", "tenant", "client", "secret")
    content = json.dumps([
        {
            "valueType": "IntentRecognition",
            "value": {"topicName": "ITSupport", "confidence": 0.88},
        },
        {
            "valueType": "SessionInfo",
            "value": {"outcome": "Escalated"},
        },
        {
            "valueType": "DialogRedirect",
            "value": {"targetDialogId": "LiveAgentHandoff"},
        },
        {
            "valueType": "CSATSurveyResponse",
            "value": {"rating": 3},
        },
    ])
    result = client.parse_transcript(content)

    assert result["intent_recognition"][0]["topic"] == "ITSupport"
    assert result["session_info"]["outcome"] == "Escalated"
    assert "LiveAgentHandoff" in result["dialog_redirects"]
    assert result["csat"] == pytest.approx(3.0)


def test_parse_transcript_empty_content():
    from dataverse_client import DataverseClient

    client = DataverseClient("https://org.crm.dynamics.com", "tenant", "client", "secret")
    result = client.parse_transcript("")

    assert result["intent_recognition"] == []
    assert result["session_info"]["outcome"] == ""
    assert result["dialog_redirects"] == []
    assert result["csat"] is None


def test_parse_transcript_invalid_json():
    from dataverse_client import DataverseClient

    client = DataverseClient("https://org.crm.dynamics.com", "tenant", "client", "secret")
    result = client.parse_transcript("{not valid json")

    assert result["intent_recognition"] == []
    assert result["csat"] is None


def test_parse_transcript_unknown_value_types():
    from dataverse_client import DataverseClient

    client = DataverseClient("https://org.crm.dynamics.com", "tenant", "client", "secret")
    content = json.dumps([
        {"valueType": "SomeUnknownType", "value": {"data": "ignored"}},
    ])
    result = client.parse_transcript(content)

    # Should return defaults without errors
    assert result["intent_recognition"] == []
    assert result["csat"] is None
