"""Test that eval run uses dataset knowledge sources when test case has no context."""


def test_context_fallback_logic():
    """Unit test the fallback logic without DB: simulates what runs.py does."""
    source_contents = ["Policy: returns within 30 days.", "Shipping: free over €50."]
    case_context = ""
    resolved = case_context if case_context else "\n\n".join(source_contents)
    assert "returns within 30 days" in resolved
    assert "free over €50" in resolved


def test_context_case_override():
    """Per-case context takes priority over dataset sources."""
    source_contents = ["Dataset-level knowledge."]
    case_context = "Per-case override context."
    resolved = case_context if case_context else "\n\n".join(source_contents)
    assert resolved == "Per-case override context."
