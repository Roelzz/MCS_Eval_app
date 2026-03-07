# Knowledge Sources — Design

Date: 2026-03-07

## Problem

The `faithfulness` and `hallucination` metrics require a `context` string per test case.
Currently this must be typed manually into each test case JSON. There is no way to upload
reference documents and reuse them across datasets or test cases.

## Solution

Flat file store with text extraction on upload. Uploaded documents are extracted to plain
text and stored in the database. Datasets link to one or more knowledge sources as defaults.
Individual test cases can still override context with a plain string.

## Data Model

### New table: `KnowledgeSource`

| Column | Type | Notes |
|---|---|---|
| `id` | int | PK |
| `name` | str | Filename or user-given label |
| `file_type` | str | `txt`, `md`, `pdf`, `docx` |
| `content` | str | Extracted plain text |
| `size_bytes` | int | Original file size |
| `created_at` | datetime | Upload timestamp |

### Join: `dataset_knowledge_sources`

Raw SQLite table: `(dataset_id INT, knowledge_source_id INT)`.

### `Dataset` changes

New field: `knowledge_source_ids: str = "[]"` — JSON list of linked source IDs.

### Per-test-case override

No change. Existing `context` string in test case JSON takes priority when non-empty.

## UI

### New page: `/knowledge-sources`

- Upload button → file picker (TXT, MD, PDF, DOCX)
- Table: name, type, size, uploaded date, delete action
- Extraction happens server-side on upload

### Dataset detail page

- New "Knowledge Sources" section
- Multi-select from uploaded sources → saved as dataset defaults
- Displays currently linked sources

## Eval Engine

Context resolution at eval time (per test case):

1. If test case `context` is non-empty → use it (no change to existing behavior)
2. If `context` is empty → fetch dataset's linked knowledge sources from DB, concatenate
   content with `\n\n`, use as context

No changes to DeepEval metric integration.

## Out of Scope

- Chunking / vector search
- Per-run knowledge source selection
- File versioning
