"""Tests for KnowledgeSource model fields."""
from web.models import KnowledgeSource, Dataset


def test_knowledge_source_fields():
    ks = KnowledgeSource(
        name="policy.pdf",
        file_type="pdf",
        content="Return policy text.",
        size_bytes=1234,
    )
    assert ks.name == "policy.pdf"
    assert ks.file_type == "pdf"
    assert ks.content == "Return policy text."
    assert ks.size_bytes == 1234


def test_dataset_has_knowledge_source_ids_field():
    ds = Dataset(name="test", data_json="[]")
    assert ds.knowledge_source_ids == "[]"
