# Knowledge Sources Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow users to upload TXT/MD/PDF/DOCX files as reusable knowledge sources that serve as `context` for faithfulness/hallucination metrics, linked at dataset level with per-test-case override.

**Architecture:** New `KnowledgeSource` DB model stores extracted plain text. `Dataset` gets a `knowledge_source_ids` JSON field listing linked sources. At eval time, if a test case has no `context`, the dataset's linked source texts are concatenated and used instead. A new `/knowledge-sources` page handles upload/delete. The dataset detail page gets a section to link/unlink sources.

**Tech Stack:** Reflex, SQLModel, Alembic, pypdf (PDF extraction), python-docx (DOCX extraction)

> ⚠️ **Dependencies required:** `pypdf` and `python-docx` must be added before starting. Run:
> ```bash
> uv add pypdf python-docx
> ```
> This requires user permission per project rules.

---

### Task 1: Add dependencies

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add pypdf and python-docx**

```bash
uv add pypdf python-docx
```

**Step 2: Verify install**

```bash
uv run python -c "import pypdf; import docx; print('OK')"
```
Expected: `OK`

**Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: add pypdf and python-docx for knowledge source extraction"
```

---

### Task 2: Add KnowledgeSource model + Dataset field + migration

**Files:**
- Modify: `web/models.py`

**Step 1: Write a test for the new model**

In `tests/test_knowledge_source.py`:

```python
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
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_knowledge_source.py -v
```
Expected: FAIL — `KnowledgeSource` not defined

**Step 3: Add KnowledgeSource model and Dataset field to `web/models.py`**

After the existing `Dataset` class, add:

```python
class KnowledgeSource(rx.Model, table=True):
    name: str
    file_type: str  # txt | md | pdf | docx
    content: str
    size_bytes: int
    created_at: datetime = sqlmodel.Field(default_factory=lambda: datetime.now(UTC))
```

Also add to the `Dataset` class body (after `created_at`):

```python
    knowledge_source_ids: str = "[]"  # JSON list of KnowledgeSource IDs
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_knowledge_source.py -v
```
Expected: PASS

**Step 5: Generate and apply migration**

```bash
uv run reflex db makemigrations --message "add knowledge sources"
uv run reflex db migrate
```

Expected: new file in `alembic/versions/`, migration applied with `knowledgesource` table and `knowledge_source_ids` column on `dataset`.

**Step 6: Verify schema**

```bash
sqlite3 reflex.db ".schema knowledgesource"
sqlite3 reflex.db "PRAGMA table_info(dataset);" | grep knowledge
```
Expected: table exists, column present.

**Step 7: Commit**

```bash
git add web/models.py alembic/versions/ tests/test_knowledge_source.py
git commit -m "feat: add KnowledgeSource model and dataset.knowledge_source_ids field"
```

---

### Task 3: Create text extraction utility

**Files:**
- Create: `knowledge_extractor.py`

**Step 1: Write failing tests**

In `tests/test_knowledge_extractor.py`:

```python
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
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_knowledge_extractor.py -v
```
Expected: FAIL — `knowledge_extractor` not found

**Step 3: Create `knowledge_extractor.py`**

```python
"""Extract plain text from uploaded knowledge source files."""
import io

import pypdf
import docx


def extract_text(content: bytes, file_type: str) -> str:
    """Extract plain text from file bytes.

    Args:
        content: Raw file bytes.
        file_type: One of 'txt', 'md', 'pdf', 'docx'.

    Returns:
        Extracted plain text string.

    Raises:
        ValueError: If file_type is not supported.
    """
    if file_type in ("txt", "md"):
        return content.decode("utf-8", errors="replace")

    if file_type == "pdf":
        reader = pypdf.PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages)

    if file_type == "docx":
        doc = docx.Document(io.BytesIO(content))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)

    raise ValueError(f"Unsupported file type: {file_type}")
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_knowledge_extractor.py -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add knowledge_extractor.py tests/test_knowledge_extractor.py
git commit -m "feat: add knowledge source text extractor"
```

---

### Task 4: Create Knowledge Sources page

**Files:**
- Create: `web/pages/knowledge_sources.py`

**Step 1: Create the page**

```python
"""Knowledge Sources page — upload and manage reusable context documents."""
import json

import reflex as rx
from sqlmodel import select

from knowledge_extractor import extract_text
from web.components import empty_state, layout, page_header
from web.models import KnowledgeSource
from web.state import State


UPLOAD_ID = "ks_upload"

_ACCEPTED: dict = {
    ".txt": ["text/plain"],
    ".md": ["text/markdown", "text/plain"],
    ".pdf": ["application/pdf"],
    ".docx": [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ],
}


class KnowledgeSourceState(State):
    sources: list[dict] = []
    upload_error: str = ""

    # Delete
    show_delete_dialog: bool = False
    delete_ks_id: int = 0
    delete_ks_name: str = ""

    def load_sources(self) -> None:
        with rx.session() as session:
            rows = session.exec(
                select(KnowledgeSource).order_by(KnowledgeSource.created_at.desc())
            ).all()
        self.sources = [
            {
                "id": ks.id,
                "name": ks.name,
                "file_type": ks.file_type,
                "size_kb": f"{ks.size_bytes / 1024:.1f} KB",
                "created": ks.created_at.strftime("%d-%m-%Y %H:%M"),
                "preview": ks.content[:120] + "…" if len(ks.content) > 120 else ks.content,
            }
            for ks in rows
        ]

    async def handle_upload(self, files: list[rx.UploadFile]) -> None:
        self.upload_error = ""
        if not files:
            return
        for file in files:
            filename = file.filename or "unknown"
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext not in ("txt", "md", "pdf", "docx"):
                self.upload_error = f"Unsupported file type: .{ext}"
                return
            content = await file.read()
            try:
                text = extract_text(content, ext)
            except Exception as e:
                self.upload_error = f"Failed to extract text: {e}"
                return
            with rx.session() as session:
                ks = KnowledgeSource(
                    name=filename,
                    file_type=ext,
                    content=text,
                    size_bytes=len(content),
                )
                session.add(ks)
                session.commit()
        self.load_sources()

    def confirm_delete(self, ks_id: int, name: str) -> None:
        self.delete_ks_id = ks_id
        self.delete_ks_name = name
        self.show_delete_dialog = True

    def close_delete_dialog(self) -> None:
        self.show_delete_dialog = False

    def delete_source(self) -> None:
        with rx.session() as session:
            ks = session.get(KnowledgeSource, self.delete_ks_id)
            if ks:
                session.delete(ks)
                session.commit()
        self.show_delete_dialog = False
        self.load_sources()


def sources_table() -> rx.Component:
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell("Name"),
                rx.table.column_header_cell("Type"),
                rx.table.column_header_cell("Size"),
                rx.table.column_header_cell("Uploaded"),
                rx.table.column_header_cell("Preview"),
                rx.table.column_header_cell("Actions"),
            ),
        ),
        rx.table.body(
            rx.foreach(
                KnowledgeSourceState.sources,
                lambda ks: rx.table.row(
                    rx.table.cell(rx.text(ks["name"], weight="medium")),
                    rx.table.cell(rx.badge(ks["file_type"], variant="soft")),
                    rx.table.cell(
                        rx.text(ks["size_kb"], font_family="var(--font-mono)", size="2")
                    ),
                    rx.table.cell(
                        rx.text(ks["created"], size="1", color="var(--gray-a8)", font_family="var(--font-mono)")
                    ),
                    rx.table.cell(
                        rx.text(ks["preview"], size="1", color="var(--gray-a9)"),
                        max_width="300px",
                    ),
                    rx.table.cell(
                        rx.button(
                            rx.icon("trash-2", size=14),
                            variant="ghost",
                            size="1",
                            color_scheme="red",
                            on_click=KnowledgeSourceState.confirm_delete(ks["id"], ks["name"]),
                        ),
                    ),
                ),
            ),
        ),
        width="100%",
    )


def delete_dialog() -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Delete Knowledge Source"),
            rx.alert_dialog.description(
                rx.text(
                    "Delete '",
                    rx.text(KnowledgeSourceState.delete_ks_name, weight="bold", as_="span"),
                    "'? Any datasets linked to it will lose that context.",
                ),
            ),
            rx.hstack(
                rx.alert_dialog.cancel(
                    rx.button("Cancel", variant="soft", color_scheme="gray")
                ),
                rx.alert_dialog.action(
                    rx.button(
                        "Delete",
                        color_scheme="red",
                        on_click=KnowledgeSourceState.delete_source,
                    ),
                ),
                spacing="3",
                justify="end",
                width="100%",
            ),
        ),
        open=KnowledgeSourceState.show_delete_dialog,
        on_open_change=lambda open: rx.cond(
            open,
            State.noop(),
            KnowledgeSourceState.close_delete_dialog(),
        ),
    )


@rx.page(route="/knowledge-sources", title="Knowledge Sources", on_load=KnowledgeSourceState.load_sources)
def knowledge_sources_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.hstack(
                page_header(
                    "Knowledge Sources",
                    "Upload reference documents used as context for faithfulness and hallucination metrics",
                ),
                rx.spacer(),
                rx.upload(
                    rx.button(
                        rx.icon("upload", size=16),
                        "Upload File",
                        size="2",
                    ),
                    id=UPLOAD_ID,
                    accept=_ACCEPTED,
                    max_files=10,
                    on_drop=KnowledgeSourceState.handle_upload(
                        rx.upload_files(upload_id=UPLOAD_ID)
                    ),
                    border="none",
                    padding="0",
                ),
                align="start",
                width="100%",
            ),
            rx.cond(
                KnowledgeSourceState.upload_error != "",
                rx.callout(
                    KnowledgeSourceState.upload_error,
                    icon="triangle_alert",
                    color_scheme="red",
                    width="100%",
                ),
            ),
            rx.cond(
                KnowledgeSourceState.sources.length() > 0,
                sources_table(),
                empty_state(
                    "No knowledge sources yet. Upload a TXT, MD, PDF, or DOCX file.",
                    "file-text",
                ),
            ),
            delete_dialog(),
            spacing="4",
            width="100%",
        ),
    )
```

**Step 2: Run the app and verify the page loads**

```bash
uv run reflex run
# Navigate to http://localhost:3000/knowledge-sources
```
Expected: page renders with empty state, no errors in console.

**Step 3: Commit**

```bash
git add web/pages/knowledge_sources.py
git commit -m "feat: add knowledge sources page with file upload"
```

---

### Task 5: Register page in app + sidebar

**Files:**
- Modify: `web/web.py`
- Modify: `web/components.py`

**Step 1: Register page in `web/web.py`**

Add this import after the existing imports (keep alphabetical order with other page imports):

```python
from web.pages.knowledge_sources import knowledge_sources_page  # noqa: F401
```

**Step 2: Add sidebar link in `web/components.py`**

In the `sidebar()` function, after the `sidebar_link("Datasets", "/datasets", "database")` line, add:

```python
sidebar_link("Knowledge Sources", "/knowledge-sources", "file-text"),
```

**Step 3: Verify**

```bash
uv run reflex run
# Check sidebar shows "Knowledge Sources" link
# Navigate to /knowledge-sources — should render
```

**Step 4: Commit**

```bash
git add web/web.py web/components.py
git commit -m "feat: register knowledge sources page and sidebar link"
```

---

### Task 6: Add knowledge source linking to dataset detail page

**Files:**
- Modify: `web/pages/dataset_detail.py`

**Step 1: Add state fields and handlers to `DatasetDetailState`**

Add these fields to `DatasetDetailState`:

```python
    # Knowledge sources
    available_sources: list[dict] = []   # all sources in the system
    linked_source_ids: list[int] = []    # IDs currently linked to this dataset
```

Add these methods to `DatasetDetailState`:

```python
    def load_knowledge_sources(self) -> None:
        """Load all available sources and which ones are linked to this dataset."""
        with rx.session() as session:
            from sqlmodel import select
            from web.models import KnowledgeSource
            all_ks = session.exec(
                select(KnowledgeSource).order_by(KnowledgeSource.name)
            ).all()
            self.available_sources = [
                {"id": ks.id, "name": ks.name, "file_type": ks.file_type}
                for ks in all_ks
            ]
            dataset = session.get(Dataset, self.ds_id)
            if dataset:
                self.linked_source_ids = json.loads(dataset.knowledge_source_ids)

    def toggle_knowledge_source(self, ks_id: int) -> None:
        """Link or unlink a knowledge source from this dataset."""
        ids = list(self.linked_source_ids)
        if ks_id in ids:
            ids.remove(ks_id)
        else:
            ids.append(ks_id)
        with rx.session() as session:
            dataset = session.get(Dataset, self.ds_id)
            if dataset:
                dataset.knowledge_source_ids = json.dumps(ids)
                session.add(dataset)
                session.commit()
        self.linked_source_ids = ids
```

Also call `self.load_knowledge_sources()` at the end of the existing `load_dataset` method.

**Step 2: Add knowledge sources section to the page UI**

In `dataset_detail_page()`, add a "Knowledge Sources" card section after the existing metadata card. Find the section that renders the dataset metadata and add below it:

```python
rx.box(
    rx.vstack(
        rx.hstack(
            rx.icon("file-text", size=16, color="var(--gray-a9)"),
            rx.text("Knowledge Sources", size="3", weight="medium"),
            spacing="2",
            align="center",
        ),
        rx.text(
            "These documents provide context for faithfulness and hallucination metrics. "
            "Per-test-case context fields override these defaults.",
            size="2",
            color="var(--gray-a9)",
        ),
        rx.cond(
            DatasetDetailState.available_sources.length() > 0,
            rx.vstack(
                rx.foreach(
                    DatasetDetailState.available_sources,
                    lambda ks: rx.hstack(
                        rx.checkbox(
                            checked=DatasetDetailState.linked_source_ids.contains(ks["id"]),
                            on_change=lambda _: DatasetDetailState.toggle_knowledge_source(ks["id"]),
                        ),
                        rx.badge(ks["file_type"], variant="soft", size="1"),
                        rx.text(ks["name"], size="2"),
                        spacing="2",
                        align="center",
                    ),
                ),
                spacing="2",
                width="100%",
            ),
            rx.text(
                "No knowledge sources uploaded yet. ",
                rx.link("Upload one", href="/knowledge-sources"),
                ".",
                size="2",
                color="var(--gray-a9)",
            ),
        ),
        spacing="3",
        width="100%",
    ),
    padding="16px",
    border="1px solid var(--gray-a4)",
    border_radius="var(--radius-3)",
    width="100%",
),
```

**Step 3: Verify**

```bash
uv run reflex run
# Navigate to any dataset detail page
# "Knowledge Sources" section should appear
# Toggling a checkbox should persist on page reload
```

**Step 4: Commit**

```bash
git add web/pages/dataset_detail.py
git commit -m "feat: add knowledge source linking to dataset detail page"
```

---

### Task 7: Update eval engine context fallback

**Files:**
- Modify: `web/pages/runs.py`

**Step 1: Write a test for context fallback**

In `tests/test_context_fallback.py`:

```python
"""Test that eval run uses dataset knowledge sources when test case has no context."""
import json
import pytest


def test_context_fallback_logic():
    """Unit test the fallback logic without DB: simulates what runs.py does."""
    # Simulated knowledge source contents
    source_contents = ["Policy: returns within 30 days.", "Shipping: free over €50."]

    # Test case with no context
    case_context = ""

    # Fallback: join source contents
    resolved = case_context if case_context else "\n\n".join(source_contents)

    assert "returns within 30 days" in resolved
    assert "free over €50" in resolved


def test_context_case_override():
    """Per-case context takes priority over dataset sources."""
    source_contents = ["Dataset-level knowledge."]
    case_context = "Per-case override context."

    resolved = case_context if case_context else "\n\n".join(source_contents)

    assert resolved == "Per-case override context."
```

**Step 2: Run test to verify it passes (pure logic test)**

```bash
uv run pytest tests/test_context_fallback.py -v
```
Expected: PASS (pure logic, no DB needed)

**Step 3: Update context resolution in `web/pages/runs.py`**

Find the section around line 291–330 where the eval loop runs. The current code is:

```python
dataset = session.get(Dataset, run.dataset_id)
...
cases = json.loads(dataset.data_json)
...
context = case.get("context", "")
```

After `cases = json.loads(dataset.data_json)`, add:

```python
                # Pre-fetch dataset knowledge source texts for context fallback
                ks_ids: list[int] = json.loads(dataset.knowledge_source_ids)
                dataset_context = ""
                if ks_ids:
                    from web.models import KnowledgeSource
                    ks_rows = [session.get(KnowledgeSource, kid) for kid in ks_ids]
                    dataset_context = "\n\n".join(
                        ks.content for ks in ks_rows if ks is not None
                    )
```

Then change the line:
```python
context = case.get("context", "")
```
to:
```python
context = case.get("context", "") or dataset_context
```

**Step 4: Run full test suite**

```bash
uv run pytest -v
```
Expected: all tests PASS

**Step 5: Verify end-to-end**

```bash
uv run reflex run
# 1. Upload a TXT knowledge source at /knowledge-sources
# 2. Link it to a dataset on the dataset detail page
# 3. Create an eval run using that dataset with faithfulness metric
# 4. Verify run completes without "requires context" error
```

**Step 6: Commit**

```bash
git add web/pages/runs.py tests/test_context_fallback.py
git commit -m "feat: fall back to dataset knowledge sources when test case context is empty"
```
