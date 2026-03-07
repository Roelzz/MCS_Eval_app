"""Tests for knowledge source text extraction."""
import pytest
from knowledge_extractor import extract_text


def test_extract_txt():
    text = extract_text(b"Hello world", "txt")
    assert text == "Hello world"


def test_extract_md():
    text = extract_text(b"# Title\n\nBody text.", "md")
    assert text == "# Title\n\nBody text."


def test_unsupported_type_raises():
    with pytest.raises(ValueError, match="Unsupported"):
        extract_text(b"data", "xlsx")
