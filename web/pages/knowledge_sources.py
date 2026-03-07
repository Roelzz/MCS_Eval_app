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
