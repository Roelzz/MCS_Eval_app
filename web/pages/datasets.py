"""Datasets page — CRUD with JSON/CSV upload/paste."""

import csv
import io
import json
from datetime import datetime

import reflex as rx
from sqlmodel import select

from web.components import empty_state, layout, page_header
from web.models import Dataset
from web.state import State


class DatasetState(State):
    datasets: list[dict] = []
    show_create_dialog: bool = False
    show_delete_dialog: bool = False

    # Create form
    new_name: str = ""
    new_description: str = ""
    new_eval_type: str = "single_turn"
    new_json_text: str = ""
    create_error: str = ""

    # Delete
    delete_dataset_id: int = 0
    delete_dataset_name: str = ""

    def set_new_name(self, value: str) -> None:
        self.new_name = value

    def set_new_description(self, value: str) -> None:
        self.new_description = value

    def set_new_eval_type(self, value: str) -> None:
        self.new_eval_type = value

    def set_new_json_text(self, value: str) -> None:
        self.new_json_text = value

    def load_datasets(self) -> None:
        with rx.session() as session:
            rows = session.exec(
                select(Dataset).order_by(Dataset.created_at.desc())
            ).all()
        self.datasets = [
            {
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "eval_type": d.eval_type,
                "num_cases": str(d.num_cases),
                "created": d.created_at.strftime("%d-%m-%Y %H:%M"),
            }
            for d in rows
        ]

    def open_create_dialog(self) -> None:
        self.show_create_dialog = True
        self.new_name = ""
        self.new_description = ""
        self.new_eval_type = "single_turn"
        self.new_json_text = ""
        self.create_error = ""

    def close_create_dialog(self) -> None:
        self.show_create_dialog = False

    def create_dataset(self) -> None:
        if not self.new_name.strip():
            self.create_error = "Name is required."
            return

        if not self.new_json_text.strip():
            self.create_error = "Test case JSON is required."
            return

        try:
            cases = json.loads(self.new_json_text)
        except json.JSONDecodeError as e:
            self.create_error = f"Invalid JSON: {e}"
            return

        if not isinstance(cases, list):
            self.create_error = "JSON must be a list of test cases."
            return

        if len(cases) == 0:
            self.create_error = "At least one test case is required."
            return

        validation_error = self._validate_cases(cases)
        if validation_error:
            self.create_error = validation_error
            return

        with rx.session() as session:
            dataset = Dataset(
                name=self.new_name.strip(),
                description=self.new_description.strip(),
                eval_type=self.new_eval_type,
                data_json=json.dumps(cases),
                num_cases=len(cases),
                created_at=datetime.utcnow(),
            )
            session.add(dataset)
            session.commit()

        self.show_create_dialog = False
        self.load_datasets()

    def _validate_cases(self, cases: list) -> str | None:
        for i, case in enumerate(cases):
            if not isinstance(case, dict):
                return f"Case {i}: must be an object."

            if self.new_eval_type == "autonomous":
                if "goal" not in case:
                    return f"Case {i}: autonomous type requires 'goal' field."
            else:
                if "turns" not in case:
                    return f"Case {i}: missing 'turns' field."
                if not isinstance(case["turns"], list) or len(case["turns"]) == 0:
                    return f"Case {i}: 'turns' must be a non-empty list."
                for j, turn in enumerate(case["turns"]):
                    if not isinstance(turn, dict) or "role" not in turn or "content" not in turn:
                        return f"Case {i}, turn {j}: must have 'role' and 'content'."
        return None

    def confirm_delete(self, dataset_id: int, name: str) -> None:
        self.delete_dataset_id = dataset_id
        self.delete_dataset_name = name
        self.show_delete_dialog = True

    def close_delete_dialog(self) -> None:
        self.show_delete_dialog = False

    def delete_dataset(self) -> None:
        with rx.session() as session:
            dataset = session.get(Dataset, self.delete_dataset_id)
            if dataset:
                session.delete(dataset)
                session.commit()
        self.show_delete_dialog = False
        self.load_datasets()

    async def handle_file_upload(self, files: list[rx.UploadFile]) -> None:
        if not files:
            return
        file = files[0]
        content = (await file.read()).decode("utf-8")
        filename = file.filename or ""

        if filename.lower().endswith(".csv"):
            cases = self._parse_csv(content)
            if cases:
                self.new_json_text = json.dumps(cases, indent=2)
            else:
                self.create_error = "CSV file produced no valid cases."
        else:
            self.new_json_text = content

    def _parse_csv(self, content: str) -> list[dict]:
        reader = csv.DictReader(io.StringIO(content))
        cases = []
        for row in reader:
            case: dict = {}
            if "user_message" in row:
                case["turns"] = [{"role": "user", "content": row["user_message"]}]
            elif "goal" in row:
                case["goal"] = row["goal"]
            else:
                continue
            if row.get("expected_output"):
                case["expected_output"] = row["expected_output"]
            if row.get("expected_topic"):
                case["expected_topic"] = row["expected_topic"]
            if row.get("context"):
                case["context"] = row["context"]
            if row.get("tags"):
                case["tags"] = row["tags"]
            if row.get("difficulty"):
                case["difficulty"] = row["difficulty"]
            cases.append(case)
        return cases


UPLOAD_ID = "dataset_upload"


def create_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("New Dataset"),
            rx.vstack(
                rx.input(
                    placeholder="Dataset name",
                    value=DatasetState.new_name,
                    on_change=DatasetState.set_new_name,
                    width="100%",
                ),
                rx.input(
                    placeholder="Description (optional)",
                    value=DatasetState.new_description,
                    on_change=DatasetState.set_new_description,
                    width="100%",
                ),
                rx.select(
                    ["single_turn", "multi_turn", "autonomous"],
                    value=DatasetState.new_eval_type,
                    on_change=DatasetState.set_new_eval_type,
                    width="100%",
                ),
                rx.text("Test Cases (JSON)", size="2", weight="medium"),
                rx.text_area(
                    placeholder='[{"turns": [{"role": "user", "content": "Hello"}]}]',
                    value=DatasetState.new_json_text,
                    on_change=DatasetState.set_new_json_text,
                    width="100%",
                    min_height="200px",
                    font_family="var(--font-mono)",
                ),
                rx.upload(
                    rx.hstack(
                        rx.icon("upload", size=16),
                        rx.text("Or upload a JSON/CSV file", size="2"),
                        spacing="2",
                        align="center",
                    ),
                    id=UPLOAD_ID,
                    border="1px dashed var(--gray-a6)",
                    border_radius="var(--radius-2)",
                    padding="12px",
                    width="100%",
                    cursor="pointer",
                    accept={
                        ".json": ["application/json"],
                        ".csv": ["text/csv"],
                    },
                    max_files=1,
                    on_drop=DatasetState.handle_file_upload(rx.upload_files(upload_id=UPLOAD_ID)),
                ),
                rx.callout(
                    "CSV columns: user_message (or goal), "
                    "expected_output, expected_topic, context, tags, difficulty",
                    icon="info",
                    color_scheme="blue",
                    size="1",
                    width="100%",
                ),
                rx.cond(
                    DatasetState.new_eval_type == "multi_turn",
                    rx.callout(
                        "Multi-turn datasets require JSON format "
                        "— CSV cannot represent nested turns.",
                        icon="triangle_alert",
                        color_scheme="orange",
                        size="1",
                        width="100%",
                    ),
                ),
                rx.cond(
                    DatasetState.create_error != "",
                    rx.callout(
                        DatasetState.create_error,
                        icon="triangle_alert",
                        color_scheme="red",
                        width="100%",
                    ),
                ),
                rx.hstack(
                    rx.dialog.close(
                        rx.button("Cancel", variant="soft", color_scheme="gray"),
                    ),
                    rx.button("Create Dataset", on_click=DatasetState.create_dataset),
                    spacing="3",
                    justify="end",
                    width="100%",
                ),
                spacing="3",
                width="100%",
            ),
            max_width="600px",
        ),
        open=DatasetState.show_create_dialog,
        on_open_change=lambda open: rx.cond(
            open,
            DatasetState.open_create_dialog(),
            DatasetState.close_create_dialog(),
        ),
    )


def delete_dialog() -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Delete Dataset"),
            rx.alert_dialog.description(
                rx.text(
                    "Are you sure you want to delete '",
                    rx.text(DatasetState.delete_dataset_name, weight="bold", as_="span"),
                    "'? This cannot be undone.",
                ),
            ),
            rx.hstack(
                rx.alert_dialog.cancel(rx.button("Cancel", variant="soft", color_scheme="gray")),
                rx.alert_dialog.action(
                    rx.button("Delete", color_scheme="red", on_click=DatasetState.delete_dataset),
                ),
                spacing="3",
                justify="end",
                width="100%",
            ),
        ),
        open=DatasetState.show_delete_dialog,
        on_open_change=lambda open: rx.cond(
            open,
            DatasetState.noop(),
            DatasetState.close_delete_dialog(),
        ),
    )


def datasets_table() -> rx.Component:
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell("Name"),
                rx.table.column_header_cell("Type"),
                rx.table.column_header_cell("Cases"),
                rx.table.column_header_cell("Created"),
                rx.table.column_header_cell("Actions"),
            ),
        ),
        rx.table.body(
            rx.foreach(
                DatasetState.datasets,
                lambda d: rx.table.row(
                    rx.table.cell(
                        rx.vstack(
                            rx.text(d["name"], weight="medium"),
                            rx.text(
                                d["description"],
                                size="1",
                                color="var(--gray-a8)",
                            ),
                            spacing="0",
                        ),
                    ),
                    rx.table.cell(
                        rx.badge(d["eval_type"], variant="soft"),
                    ),
                    rx.table.cell(
                        rx.text(
                            d["num_cases"],
                            font_family="var(--font-mono)",
                            size="2",
                        ),
                    ),
                    rx.table.cell(
                        rx.text(
                            d["created"],
                            size="1",
                            color="var(--gray-a8)",
                            font_family="var(--font-mono)",
                        ),
                    ),
                    rx.table.cell(
                        rx.hstack(
                            rx.link(
                                rx.button(
                                    rx.icon("eye", size=14),
                                    variant="ghost",
                                    size="1",
                                ),
                                href=rx.cond(
                                    True,
                                    "/datasets/" + d["id"].to(str),
                                    "",
                                ),
                            ),
                            rx.button(
                                rx.icon("trash-2", size=14),
                                variant="ghost",
                                size="1",
                                color_scheme="red",
                                on_click=DatasetState.confirm_delete(d["id"], d["name"]),
                            ),
                            spacing="1",
                        ),
                    ),
                ),
            ),
        ),
        width="100%",
    )


@rx.page(route="/datasets", title="Datasets", on_load=DatasetState.load_datasets)
def datasets_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.hstack(
                page_header("Datasets", "Manage test case datasets for evaluations"),
                rx.spacer(),
                rx.button(
                    rx.icon("plus", size=16),
                    "New Dataset",
                    on_click=DatasetState.open_create_dialog,
                    size="2",
                ),
                align="start",
                width="100%",
            ),
            rx.cond(
                DatasetState.datasets.length() > 0,
                datasets_table(),
                empty_state("No datasets yet. Create one to get started.", "database"),
            ),
            create_dialog(),
            delete_dialog(),
            spacing="4",
            width="100%",
        ),
    )
