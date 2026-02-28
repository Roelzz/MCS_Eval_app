"""Dataset detail page — view, edit metadata, add/edit/delete test cases."""

import csv
import io
import json

import reflex as rx

from web.components import layout
from web.models import Dataset
from web.state import State


class DatasetDetailState(State):
    # Dataset metadata
    ds_id: int = 0
    dataset_name: str = ""
    dataset_description: str = ""
    dataset_eval_type: str = ""
    dataset_num_cases: int = 0

    # Flattened cases for rx.foreach
    cases: list[dict] = []

    # Expanded case preview
    expanded_case: int = -1

    # Edit metadata
    show_edit_metadata_dialog: bool = False
    edit_name: str = ""
    edit_description: str = ""

    # Add case
    show_add_case_dialog: bool = False
    new_turns: list[dict] = []
    new_goal: str = ""
    new_expected_output: str = ""
    new_expected_topic: str = ""
    new_context: str = ""
    new_tags_str: str = ""
    new_difficulty: str = ""
    add_case_error: str = ""

    # Edit case
    show_edit_case_dialog: bool = False
    editing_case_index: int = -1
    edit_turns: list[dict] = []
    edit_goal: str = ""
    edit_expected_output: str = ""
    edit_expected_topic: str = ""
    edit_context: str = ""
    edit_tags_str: str = ""
    edit_difficulty: str = ""
    edit_case_error: str = ""

    # Delete case
    show_delete_case_dialog: bool = False
    delete_case_index: int = -1

    def load_dataset(self) -> None:
        ds_id_str = self.router.page.params.get("dataset_id", "0")
        try:
            self.ds_id = int(ds_id_str)
        except (ValueError, TypeError):
            self.ds_id = 0
            return

        with rx.session() as session:
            dataset = session.get(Dataset, self.ds_id)
            if not dataset:
                return
            self.dataset_name = dataset.name
            self.dataset_description = dataset.description
            self.dataset_eval_type = dataset.eval_type
            self.dataset_num_cases = dataset.num_cases
            raw_cases = json.loads(dataset.data_json)

        self.cases = []
        self.expanded_case = -1
        for i, case in enumerate(raw_cases):
            turns = case.get("turns", [])
            goal = case.get("goal", "")
            expected = case.get("expected_output", "")
            tags = case.get("tags", "")
            difficulty = case.get("difficulty", "")

            if self.dataset_eval_type == "autonomous":
                turns_count = "—"
            elif turns:
                turns_count = str(len(turns))
            else:
                turns_count = "1"

            if goal:
                summary = goal[:80]
            elif turns:
                first_user = next(
                    (t["content"] for t in turns if t.get("role") == "user"), ""
                )
                summary = first_user[:80]
                if len(turns) > 1:
                    summary = f"{summary} ({len(turns)} turns)"
            else:
                summary = "(empty)"

            # Pre-format conversation for display
            conv_lines = []
            for t in turns:
                role_label = "User" if t.get("role") == "user" else "Assistant"
                conv_lines.append(f"{role_label}: {t.get('content', '')}")
            conversation_text = "\n\n".join(conv_lines) if conv_lines else ""

            self.cases.append(
                {
                    "index": i,
                    "num": str(i + 1),
                    "summary": summary,
                    "turns_count": turns_count,
                    "expected_output": expected[:60] if expected else "—",
                    "tags": tags,
                    "difficulty": difficulty,
                    "conversation_text": conversation_text,
                    "full_expected": expected,
                    "data": json.dumps(case),
                }
            )

    # --- Edit metadata ---

    def open_edit_metadata(self) -> None:
        self.edit_name = self.dataset_name
        self.edit_description = self.dataset_description
        self.show_edit_metadata_dialog = True

    def close_edit_metadata(self) -> None:
        self.show_edit_metadata_dialog = False

    def set_edit_name(self, value: str) -> None:
        self.edit_name = value

    def set_edit_description(self, value: str) -> None:
        self.edit_description = value

    def save_metadata(self) -> None:
        if not self.edit_name.strip():
            return
        with rx.session() as session:
            dataset = session.get(Dataset, self.ds_id)
            if dataset:
                dataset.name = self.edit_name.strip()
                dataset.description = self.edit_description.strip()
                session.add(dataset)
                session.commit()
        self.show_edit_metadata_dialog = False
        self.load_dataset()

    # --- Add case ---

    def open_add_case(self) -> None:
        self.new_turns = [{"role": "user", "content": ""}]
        self.new_goal = ""
        self.new_expected_output = ""
        self.new_expected_topic = ""
        self.new_context = ""
        self.new_tags_str = ""
        self.new_difficulty = ""
        self.add_case_error = ""
        self.show_add_case_dialog = True

    def close_add_case(self) -> None:
        self.show_add_case_dialog = False

    def set_new_goal(self, value: str) -> None:
        self.new_goal = value

    def set_new_expected_output(self, value: str) -> None:
        self.new_expected_output = value

    def set_new_expected_topic(self, value: str) -> None:
        self.new_expected_topic = value

    def set_new_context(self, value: str) -> None:
        self.new_context = value

    def set_new_tags_str(self, value: str) -> None:
        self.new_tags_str = value

    def set_new_difficulty(self, value: str) -> None:
        self.new_difficulty = value

    def add_new_turn(self) -> None:
        last_is_user = self.new_turns and self.new_turns[-1].get("role") == "user"
        role = "assistant" if last_is_user else "user"
        self.new_turns = self.new_turns + [{"role": role, "content": ""}]

    def remove_new_turn(self, idx: int) -> None:
        if len(self.new_turns) <= 1:
            return
        self.new_turns = [t for i, t in enumerate(self.new_turns) if i != idx]

    def set_new_turn_role(self, payload: list) -> None:
        idx, role = int(payload[0]), payload[1]
        self.new_turns[idx]["role"] = role
        self.new_turns = self.new_turns

    def set_new_turn_content(self, payload: list) -> None:
        idx, content = int(payload[0]), payload[1]
        self.new_turns[idx]["content"] = content
        self.new_turns = self.new_turns

    def add_case(self) -> None:
        case: dict = {}

        if self.dataset_eval_type == "autonomous":
            if not self.new_goal.strip():
                self.add_case_error = "Goal is required for autonomous type."
                return
            case["goal"] = self.new_goal.strip()
        else:
            valid_turns = [
                t for t in self.new_turns if t.get("content", "").strip()
            ]
            if not valid_turns:
                self.add_case_error = "At least one turn with content is required."
                return
            case["turns"] = valid_turns

        if self.new_expected_output.strip():
            case["expected_output"] = self.new_expected_output.strip()
        if self.new_expected_topic.strip():
            case["expected_topic"] = self.new_expected_topic.strip()
        if self.new_context.strip():
            case["context"] = self.new_context.strip()
        if self.new_tags_str.strip():
            case["tags"] = self.new_tags_str.strip()
        if self.new_difficulty:
            case["difficulty"] = self.new_difficulty

        self._append_case(case)
        self.show_add_case_dialog = False
        self.load_dataset()

    def _append_case(self, case: dict) -> None:
        with rx.session() as session:
            dataset = session.get(Dataset, self.ds_id)
            if not dataset:
                return
            existing = json.loads(dataset.data_json)
            existing.append(case)
            dataset.data_json = json.dumps(existing)
            dataset.num_cases = len(existing)
            session.add(dataset)
            session.commit()

    # --- Edit case ---

    def open_edit_case(self, index: int) -> None:
        case_data = None
        for c in self.cases:
            if c["index"] == index:
                case_data = json.loads(c["data"])
                break
        if not case_data:
            return

        self.editing_case_index = index
        self.edit_turns = case_data.get("turns", [{"role": "user", "content": ""}])
        self.edit_goal = case_data.get("goal", "")
        self.edit_expected_output = case_data.get("expected_output", "")
        self.edit_expected_topic = case_data.get("expected_topic", "")
        self.edit_context = case_data.get("context", "")
        self.edit_tags_str = case_data.get("tags", "")
        self.edit_difficulty = case_data.get("difficulty", "")
        self.edit_case_error = ""
        self.show_edit_case_dialog = True

    def close_edit_case(self) -> None:
        self.show_edit_case_dialog = False

    def set_edit_goal(self, value: str) -> None:
        self.edit_goal = value

    def set_edit_expected_output(self, value: str) -> None:
        self.edit_expected_output = value

    def set_edit_expected_topic(self, value: str) -> None:
        self.edit_expected_topic = value

    def set_edit_context(self, value: str) -> None:
        self.edit_context = value

    def set_edit_tags_str(self, value: str) -> None:
        self.edit_tags_str = value

    def set_edit_difficulty(self, value: str) -> None:
        self.edit_difficulty = value

    def add_edit_turn(self) -> None:
        last_is_user = self.edit_turns and self.edit_turns[-1].get("role") == "user"
        role = "assistant" if last_is_user else "user"
        self.edit_turns = self.edit_turns + [{"role": role, "content": ""}]

    def remove_edit_turn(self, idx: int) -> None:
        if len(self.edit_turns) <= 1:
            return
        self.edit_turns = [t for i, t in enumerate(self.edit_turns) if i != idx]

    def set_edit_turn_role(self, payload: list) -> None:
        idx, role = int(payload[0]), payload[1]
        self.edit_turns[idx]["role"] = role
        self.edit_turns = self.edit_turns

    def set_edit_turn_content(self, payload: list) -> None:
        idx, content = int(payload[0]), payload[1]
        self.edit_turns[idx]["content"] = content
        self.edit_turns = self.edit_turns

    def save_case(self) -> None:
        case: dict = {}

        if self.dataset_eval_type == "autonomous":
            if not self.edit_goal.strip():
                self.edit_case_error = "Goal is required for autonomous type."
                return
            case["goal"] = self.edit_goal.strip()
        else:
            valid_turns = [
                t for t in self.edit_turns if t.get("content", "").strip()
            ]
            if not valid_turns:
                self.edit_case_error = "At least one turn with content is required."
                return
            case["turns"] = valid_turns

        if self.edit_expected_output.strip():
            case["expected_output"] = self.edit_expected_output.strip()
        if self.edit_expected_topic.strip():
            case["expected_topic"] = self.edit_expected_topic.strip()
        if self.edit_context.strip():
            case["context"] = self.edit_context.strip()
        if self.edit_tags_str.strip():
            case["tags"] = self.edit_tags_str.strip()
        if self.edit_difficulty:
            case["difficulty"] = self.edit_difficulty

        self._update_case(self.editing_case_index, case)
        self.show_edit_case_dialog = False
        self.load_dataset()

    def _update_case(self, index: int, case: dict) -> None:
        with rx.session() as session:
            dataset = session.get(Dataset, self.ds_id)
            if not dataset:
                return
            existing = json.loads(dataset.data_json)
            if 0 <= index < len(existing):
                existing[index] = case
                dataset.data_json = json.dumps(existing)
                session.add(dataset)
                session.commit()

    # --- Delete case ---

    def confirm_delete_case(self, index: int) -> None:
        self.delete_case_index = index
        self.show_delete_case_dialog = True

    def close_delete_case(self) -> None:
        self.show_delete_case_dialog = False

    def delete_case(self) -> None:
        with rx.session() as session:
            dataset = session.get(Dataset, self.ds_id)
            if not dataset:
                return
            existing = json.loads(dataset.data_json)
            if 0 <= self.delete_case_index < len(existing):
                existing.pop(self.delete_case_index)
                dataset.data_json = json.dumps(existing)
                dataset.num_cases = len(existing)
                session.add(dataset)
                session.commit()
        self.show_delete_case_dialog = False
        self.load_dataset()

    # --- Case expand/collapse ---

    def toggle_case_expand(self, index: int) -> None:
        if self.expanded_case == index:
            self.expanded_case = -1
        else:
            self.expanded_case = index

    # --- CSV/JSON file upload on detail page ---

    async def handle_file_upload(self, files: list[rx.UploadFile]) -> None:
        if not files:
            return
        file = files[0]
        content = (await file.read()).decode("utf-8")
        filename = file.filename or ""

        if filename.lower().endswith(".csv"):
            cases = self._parse_csv(content)
        else:
            try:
                cases = json.loads(content)
            except json.JSONDecodeError:
                self.add_case_error = "Invalid JSON file."
                return

        if not isinstance(cases, list) or len(cases) == 0:
            self.add_case_error = "File must contain at least one test case."
            return

        with rx.session() as session:
            dataset = session.get(Dataset, self.ds_id)
            if not dataset:
                return
            existing = json.loads(dataset.data_json)
            existing.extend(cases)
            dataset.data_json = json.dumps(existing)
            dataset.num_cases = len(existing)
            session.add(dataset)
            session.commit()

        self.load_dataset()

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


# --- UI Components ---

UPLOAD_ID = "dataset_detail_upload"


def _turn_builder(
    turns_var: rx.Var,
    set_role_handler,
    set_content_handler,
    remove_handler,
    add_handler,
) -> rx.Component:
    return rx.vstack(
        rx.text("Turns", size="2", weight="medium"),
        rx.foreach(
            turns_var,
            lambda turn, idx: rx.card(
                rx.hstack(
                    rx.select(
                        ["user", "assistant"],
                        value=turn["role"],
                        on_change=lambda val: set_role_handler([idx, val]),
                        width="120px",
                    ),
                    rx.text_area(
                        value=turn["content"],
                        on_change=lambda val: set_content_handler([idx, val]),
                        placeholder="Message content...",
                        width="100%",
                        min_height="60px",
                    ),
                    rx.button(
                        rx.icon("x", size=14),
                        variant="ghost",
                        size="1",
                        color_scheme="red",
                        on_click=remove_handler(idx),
                    ),
                    align="start",
                    width="100%",
                    spacing="2",
                ),
                width="100%",
            ),
        ),
        rx.button(
            rx.icon("plus", size=14),
            "Add Turn",
            variant="soft",
            size="1",
            on_click=add_handler,
        ),
        spacing="2",
        width="100%",
    )


def _common_fields(
    expected_var,
    set_expected,
    expected_topic_var,
    set_expected_topic,
    context_var,
    set_context,
    tags_var,
    set_tags,
    difficulty_var,
    set_difficulty,
) -> rx.Component:
    return rx.vstack(
        rx.text("Expected Output", size="2", weight="medium"),
        rx.text_area(
            value=expected_var,
            on_change=set_expected,
            placeholder="What the agent should respond...",
            width="100%",
            min_height="80px",
        ),
        rx.text("Expected Topic (optional)", size="2", weight="medium"),
        rx.input(
            value=expected_topic_var,
            on_change=set_expected_topic,
            placeholder="e.g. Greeting, IT Support, ...",
            width="100%",
        ),
        rx.text("Context (optional)", size="2", weight="medium"),
        rx.text_area(
            value=context_var,
            on_change=set_context,
            placeholder="Reference info for evaluation...",
            width="100%",
            min_height="60px",
        ),
        rx.hstack(
            rx.vstack(
                rx.text("Tags (comma-separated)", size="2", weight="medium"),
                rx.input(
                    value=tags_var,
                    on_change=set_tags,
                    placeholder="basics, auth, ...",
                    width="100%",
                ),
                spacing="1",
                width="50%",
            ),
            rx.vstack(
                rx.text("Difficulty", size="2", weight="medium"),
                rx.select(
                    ["easy", "medium", "hard"],
                    value=difficulty_var,
                    on_change=set_difficulty,
                    placeholder="None",
                    width="100%",
                ),
                spacing="1",
                width="50%",
            ),
            spacing="3",
            width="100%",
        ),
        spacing="3",
        width="100%",
    )


def _add_case_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Add Test Case"),
            rx.box(
                rx.vstack(
                    # Autonomous: goal field
                    rx.cond(
                        DatasetDetailState.dataset_eval_type == "autonomous",
                        rx.vstack(
                            rx.text("Goal", size="2", weight="medium"),
                            rx.text_area(
                                value=DatasetDetailState.new_goal,
                                on_change=DatasetDetailState.set_new_goal,
                                placeholder="What the agent should accomplish...",
                                width="100%",
                                min_height="80px",
                            ),
                            spacing="2",
                            width="100%",
                        ),
                    ),
                    # Single turn: one user message
                    rx.cond(
                        DatasetDetailState.dataset_eval_type == "single_turn",
                        rx.vstack(
                            rx.text("User Message", size="2", weight="medium"),
                            rx.text_area(
                                value=DatasetDetailState.new_turns[0]["content"].to(str),
                                on_change=lambda val: DatasetDetailState.set_new_turn_content(
                                    [0, val]
                                ),
                                placeholder="What the user asks...",
                                width="100%",
                                min_height="80px",
                            ),
                            spacing="2",
                            width="100%",
                        ),
                    ),
                    # Multi-turn: turn builder
                    rx.cond(
                        DatasetDetailState.dataset_eval_type == "multi_turn",
                        _turn_builder(
                            DatasetDetailState.new_turns,
                            DatasetDetailState.set_new_turn_role,
                            DatasetDetailState.set_new_turn_content,
                            DatasetDetailState.remove_new_turn,
                            DatasetDetailState.add_new_turn,
                        ),
                    ),
                    _common_fields(
                        DatasetDetailState.new_expected_output,
                        DatasetDetailState.set_new_expected_output,
                        DatasetDetailState.new_expected_topic,
                        DatasetDetailState.set_new_expected_topic,
                        DatasetDetailState.new_context,
                        DatasetDetailState.set_new_context,
                        DatasetDetailState.new_tags_str,
                        DatasetDetailState.set_new_tags_str,
                        DatasetDetailState.new_difficulty,
                        DatasetDetailState.set_new_difficulty,
                    ),
                    rx.cond(
                        DatasetDetailState.add_case_error != "",
                        rx.callout(
                            DatasetDetailState.add_case_error,
                            icon="triangle_alert",
                            color_scheme="red",
                            width="100%",
                        ),
                    ),
                    rx.hstack(
                        rx.dialog.close(
                            rx.button("Cancel", variant="soft", color_scheme="gray"),
                        ),
                        rx.button("Add Case", on_click=DatasetDetailState.add_case),
                        spacing="3",
                        justify="end",
                        width="100%",
                    ),
                    spacing="3",
                    width="100%",
                ),
                max_height="70vh",
                overflow_y="auto",
                width="100%",
            ),
            max_width="650px",
        ),
        open=DatasetDetailState.show_add_case_dialog,
        on_open_change=lambda open: rx.cond(
            open,
            DatasetDetailState.open_add_case(),
            DatasetDetailState.close_add_case(),
        ),
    )


def _edit_case_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Edit Test Case"),
            rx.box(
                rx.vstack(
                    # Autonomous: goal field
                    rx.cond(
                        DatasetDetailState.dataset_eval_type == "autonomous",
                        rx.vstack(
                            rx.text("Goal", size="2", weight="medium"),
                            rx.text_area(
                                value=DatasetDetailState.edit_goal,
                                on_change=DatasetDetailState.set_edit_goal,
                                placeholder="What the agent should accomplish...",
                                width="100%",
                                min_height="80px",
                            ),
                            spacing="2",
                            width="100%",
                        ),
                    ),
                    # Single turn: one user message
                    rx.cond(
                        DatasetDetailState.dataset_eval_type == "single_turn",
                        rx.vstack(
                            rx.text("User Message", size="2", weight="medium"),
                            rx.text_area(
                                value=DatasetDetailState.edit_turns[0]["content"].to(str),
                                on_change=lambda val: DatasetDetailState.set_edit_turn_content(
                                    [0, val]
                                ),
                                placeholder="What the user asks...",
                                width="100%",
                                min_height="80px",
                            ),
                            spacing="2",
                            width="100%",
                        ),
                    ),
                    # Multi-turn: turn builder
                    rx.cond(
                        DatasetDetailState.dataset_eval_type == "multi_turn",
                        _turn_builder(
                            DatasetDetailState.edit_turns,
                            DatasetDetailState.set_edit_turn_role,
                            DatasetDetailState.set_edit_turn_content,
                            DatasetDetailState.remove_edit_turn,
                            DatasetDetailState.add_edit_turn,
                        ),
                    ),
                    _common_fields(
                        DatasetDetailState.edit_expected_output,
                        DatasetDetailState.set_edit_expected_output,
                        DatasetDetailState.edit_expected_topic,
                        DatasetDetailState.set_edit_expected_topic,
                        DatasetDetailState.edit_context,
                        DatasetDetailState.set_edit_context,
                        DatasetDetailState.edit_tags_str,
                        DatasetDetailState.set_edit_tags_str,
                        DatasetDetailState.edit_difficulty,
                        DatasetDetailState.set_edit_difficulty,
                    ),
                    rx.cond(
                        DatasetDetailState.edit_case_error != "",
                        rx.callout(
                            DatasetDetailState.edit_case_error,
                            icon="triangle_alert",
                            color_scheme="red",
                            width="100%",
                        ),
                    ),
                    rx.hstack(
                        rx.dialog.close(
                            rx.button("Cancel", variant="soft", color_scheme="gray"),
                        ),
                        rx.button("Save Changes", on_click=DatasetDetailState.save_case),
                        spacing="3",
                        justify="end",
                        width="100%",
                    ),
                    spacing="3",
                    width="100%",
                ),
                max_height="70vh",
                overflow_y="auto",
                width="100%",
            ),
            max_width="650px",
        ),
        open=DatasetDetailState.show_edit_case_dialog,
        on_open_change=lambda open: rx.cond(
            open,
            DatasetDetailState.noop(),
            DatasetDetailState.close_edit_case(),
        ),
    )


def _edit_metadata_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Edit Dataset"),
            rx.vstack(
                rx.text("Name", size="2", weight="medium"),
                rx.input(
                    value=DatasetDetailState.edit_name,
                    on_change=DatasetDetailState.set_edit_name,
                    width="100%",
                ),
                rx.text("Description", size="2", weight="medium"),
                rx.input(
                    value=DatasetDetailState.edit_description,
                    on_change=DatasetDetailState.set_edit_description,
                    width="100%",
                ),
                rx.hstack(
                    rx.dialog.close(
                        rx.button("Cancel", variant="soft", color_scheme="gray"),
                    ),
                    rx.button("Save", on_click=DatasetDetailState.save_metadata),
                    spacing="3",
                    justify="end",
                    width="100%",
                ),
                spacing="3",
                width="100%",
            ),
            max_width="450px",
        ),
        open=DatasetDetailState.show_edit_metadata_dialog,
        on_open_change=lambda open: rx.cond(
            open,
            DatasetDetailState.open_edit_metadata(),
            DatasetDetailState.close_edit_metadata(),
        ),
    )


def _delete_case_dialog() -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Delete Test Case"),
            rx.alert_dialog.description(
                rx.text(
                    "Are you sure you want to delete case #",
                    rx.text(
                        DatasetDetailState.delete_case_index.to(str),
                        weight="bold",
                        as_="span",
                    ),
                    "? This cannot be undone.",
                ),
            ),
            rx.hstack(
                rx.alert_dialog.cancel(
                    rx.button("Cancel", variant="soft", color_scheme="gray"),
                ),
                rx.alert_dialog.action(
                    rx.button(
                        "Delete",
                        color_scheme="red",
                        on_click=DatasetDetailState.delete_case,
                    ),
                ),
                spacing="3",
                justify="end",
                width="100%",
            ),
        ),
        open=DatasetDetailState.show_delete_case_dialog,
        on_open_change=lambda open: rx.cond(
            open,
            DatasetDetailState.noop(),
            DatasetDetailState.close_delete_case(),
        ),
    )


def _metadata_section() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.link(
                rx.hstack(
                    rx.icon("arrow-left", size=16),
                    rx.text("Datasets", size="2"),
                    spacing="1",
                    align="center",
                ),
                href="/datasets",
                underline="none",
            ),
            spacing="2",
        ),
        rx.hstack(
            rx.heading(DatasetDetailState.dataset_name, size="6"),
            rx.badge(DatasetDetailState.dataset_eval_type),
            rx.spacer(),
            rx.button(
                rx.icon("pencil", size=14),
                "Edit",
                variant="soft",
                size="1",
                on_click=DatasetDetailState.open_edit_metadata,
            ),
            align="center",
            width="100%",
        ),
        rx.cond(
            DatasetDetailState.dataset_description != "",
            rx.text(
                DatasetDetailState.dataset_description,
                size="2",
                color_scheme="gray",
            ),
        ),
        spacing="2",
        width="100%",
        padding_bottom="16px",
    )


def _cases_table() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.text(
                    "Test Cases (",
                    DatasetDetailState.dataset_num_cases.to(str),
                    ")",
                    size="3",
                    weight="medium",
                ),
                rx.spacer(),
                rx.upload(
                    rx.button(
                        rx.icon("upload", size=14),
                        "Import",
                        variant="soft",
                        size="1",
                    ),
                    id=UPLOAD_ID,
                    accept={
                        ".json": ["application/json"],
                        ".csv": ["text/csv"],
                    },
                    max_files=1,
                    on_drop=DatasetDetailState.handle_file_upload(
                        rx.upload_files(upload_id=UPLOAD_ID)
                    ),
                    no_click=False,
                    no_drag=True,
                ),
                rx.button(
                    rx.icon("plus", size=14),
                    "Add Case",
                    size="1",
                    on_click=DatasetDetailState.open_add_case,
                ),
                align="center",
                width="100%",
            ),
            rx.cond(
                DatasetDetailState.cases.length() > 0,
                rx.vstack(
                    rx.foreach(
                        DatasetDetailState.cases,
                        lambda c: rx.card(
                            rx.vstack(
                                rx.hstack(
                                    rx.text(c["num"], size="2", weight="medium", min_width="30px"),
                                    rx.cond(
                                        c["turns_count"] != "—",
                                        rx.badge(
                                            c["turns_count"].to(str) + " turns",
                                            variant="soft",
                                            size="1",
                                        ),
                                        rx.badge(
                                            "autonomous",
                                            variant="soft",
                                            color_scheme="purple",
                                            size="1",
                                        ),
                                    ),
                                    rx.text(
                                        c["summary"],
                                        size="2",
                                        overflow="hidden",
                                        text_overflow="ellipsis",
                                        white_space="nowrap",
                                        flex="1",
                                    ),
                                    rx.cond(
                                        c["tags"] != "",
                                        rx.badge(c["tags"], variant="soft", size="1"),
                                        rx.fragment(),
                                    ),
                                    rx.cond(
                                        c["difficulty"] != "",
                                        rx.badge(c["difficulty"], size="1"),
                                        rx.fragment(),
                                    ),
                                    rx.button(
                                        rx.cond(
                                            DatasetDetailState.expanded_case == c["index"],
                                            rx.icon("chevron-up", size=14),
                                            rx.icon("chevron-down", size=14),
                                        ),
                                        variant="ghost",
                                        size="1",
                                        on_click=DatasetDetailState.toggle_case_expand(c["index"]),
                                    ),
                                    rx.button(
                                        rx.icon("pencil", size=14),
                                        variant="ghost",
                                        size="1",
                                        on_click=DatasetDetailState.open_edit_case(c["index"]),
                                    ),
                                    rx.button(
                                        rx.icon("trash-2", size=14),
                                        variant="ghost",
                                        size="1",
                                        color_scheme="red",
                                        on_click=DatasetDetailState.confirm_delete_case(c["index"]),
                                    ),
                                    align="center",
                                    width="100%",
                                    spacing="2",
                                ),
                                rx.cond(
                                    DatasetDetailState.expanded_case == c["index"],
                                    rx.vstack(
                                        rx.separator(),
                                        rx.cond(
                                            c["conversation_text"].to(str) != "",
                                            rx.vstack(
                                                rx.text(
                                                    "Conversation",
                                                    size="2",
                                                    weight="medium",
                                                ),
                                                rx.box(
                                                    rx.text(
                                                        c["conversation_text"].to(str),
                                                        size="2",
                                                        white_space="pre-wrap",
                                                    ),
                                                    padding="12px",
                                                    border_radius="var(--radius-2)",
                                                    background="var(--gray-a2)",
                                                    width="100%",
                                                ),
                                                spacing="2",
                                                width="100%",
                                            ),
                                        ),
                                        rx.cond(
                                            c["full_expected"].to(str) != "",
                                            rx.vstack(
                                                rx.text(
                                                    "Expected Output",
                                                    size="2",
                                                    weight="medium",
                                                ),
                                                rx.box(
                                                    rx.text(
                                                        c["full_expected"].to(str),
                                                        size="2",
                                                    ),
                                                    padding="8px",
                                                    border_radius="var(--radius-2)",
                                                    background="var(--gray-a2)",
                                                    width="100%",
                                                ),
                                                spacing="1",
                                                width="100%",
                                            ),
                                        ),
                                        spacing="3",
                                        width="100%",
                                        padding_top="8px",
                                    ),
                                ),
                                spacing="2",
                                width="100%",
                            ),
                            width="100%",
                        ),
                    ),
                    spacing="2",
                    width="100%",
                ),
                rx.center(
                    rx.text(
                        "No test cases yet. Add one to get started.",
                        size="2",
                        color_scheme="gray",
                    ),
                    padding="40px 0",
                    width="100%",
                ),
            ),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


@rx.page(
    route="/datasets/[dataset_id]",
    title="Dataset Detail",
    on_load=DatasetDetailState.load_dataset,
)
def dataset_detail_page() -> rx.Component:
    return layout(
        rx.vstack(
            _metadata_section(),
            _cases_table(),
            _add_case_dialog(),
            _edit_case_dialog(),
            _edit_metadata_dialog(),
            _delete_case_dialog(),
            spacing="4",
            width="100%",
        ),
    )
