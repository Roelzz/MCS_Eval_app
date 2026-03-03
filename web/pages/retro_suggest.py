"""Dataset Builder page — turn retro eval results into a new dataset."""

import json

import reflex as rx
from sqlmodel import select

from web.components import empty_state, layout
from web.models import Dataset, EvalResult, EvalRun
from web.state import State


class RetroSuggestState(State):
    retro_run_id: int = 0
    run_name: str = ""

    # All deduped suggestions (lightweight, no conversation body)
    all_suggestions: list[dict] = []
    # Filtered view — recomputed whenever filters or selection changes
    filtered_suggestions: list[dict] = []

    # Filters
    filter_outcome: str = "all"  # all | Resolved | Escalated | Abandoned
    filter_turns: str = "all"    # all | single | multi
    filter_topic: str = "all"    # all | passed | failed

    # Stats (set on load)
    total_count: int = 0
    dedup_skipped: int = 0
    topic_passed_count: int = 0
    resolved_count: int = 0

    # Create form
    dataset_name: str = ""
    create_error: str = ""
    created_dataset_id: int = 0

    @rx.var
    def selected_count(self) -> int:
        return sum(1 for s in self.filtered_suggestions if s.get("selected"))

    @rx.var
    def create_disabled(self) -> bool:
        return self.selected_count == 0 or not self.dataset_name.strip()

    def load_page(self) -> None:
        run_id_str = self.router.page.params.get("run_id", "0")
        try:
            self.retro_run_id = int(run_id_str)
        except (ValueError, TypeError):
            self.retro_run_id = 0
            return

        with rx.session() as session:
            run = session.get(EvalRun, self.retro_run_id)
            if not run:
                return
            self.run_name = run.name
            results = session.exec(
                select(EvalResult).where(EvalResult.eval_run_id == self.retro_run_id)
            ).all()

        # Pre-pass: count occurrences per normalised first utterance
        utterance_counts: dict[str, int] = {}
        for r in results:
            try:
                conversation = json.loads(r.input_json)
            except Exception:
                conversation = []
            user_turns_pre = [t for t in conversation if t.get("role") == "user"]
            key = (user_turns_pre[0]["content"].strip().lower() if user_turns_pre else "")
            utterance_counts[key] = utterance_counts.get(key, 0) + 1

        suggestions: list[dict] = []
        seen_utterances: set[str] = set()
        skipped = 0
        topic_passed = 0
        resolved = 0

        for r in results:
            try:
                conversation = json.loads(r.input_json)
            except Exception:
                conversation = []

            user_turns = [t for t in conversation if t.get("role") == "user"]
            utterance = user_turns[0]["content"] if user_turns else ""
            utterance_key = utterance.strip().lower()

            if utterance_key in seen_utterances:
                skipped += 1
                continue
            seen_utterances.add(utterance_key)

            try:
                activities = json.loads(r.activities_json)
            except Exception:
                activities = []

            retro_info = next(
                (a for a in activities if a.get("type") == "retro_info"), {}
            )
            session_outcome = retro_info.get("session_outcome", "Unknown")
            csat = retro_info.get("csat")
            transcript_id = retro_info.get("transcript_id", "")

            try:
                scores = json.loads(r.scores_json)
            except Exception:
                scores = {}

            topic_score_val = scores.get("topic_routing", {}).get("score", 0.0)
            topic_pass = bool(scores.get("topic_routing", {}).get("passed", False))

            if topic_pass:
                topic_passed += 1
            if session_outcome == "Resolved":
                resolved += 1

            user_turns_text = "\n".join(
                f"{i + 1}. {t['content']}" for i, t in enumerate(user_turns)
            )
            tools_text = ", ".join(retro_info.get("tools_used", []))

            suggestions.append({
                "eval_result_index": r.test_case_index,
                "utterance": utterance,
                "user_turns_text": user_turns_text,
                "tools_text": tools_text,
                "duplicate_count": utterance_counts.get(utterance_key, 1),
                "is_duplicate": utterance_counts.get(utterance_key, 1) > 1,
                "num_follow_ups": len(user_turns) - 1,
                "is_multi_turn": len(user_turns) > 1,
                "session_outcome": session_outcome,
                "transcript_id": transcript_id,
                "csat": csat if csat is not None else -1.0,
                "topic_score": round(topic_score_val * 100),
                "topic_passed": topic_pass,
                "selected": True,
            })

        self.all_suggestions = suggestions
        self.total_count = len(suggestions)
        self.dedup_skipped = skipped
        self.topic_passed_count = topic_passed
        self.resolved_count = resolved
        self.dataset_name = self.run_name
        self.filter_outcome = "all"
        self.filter_turns = "all"
        self.filter_topic = "all"
        self.create_error = ""
        self.created_dataset_id = 0
        self._apply_filters()

    def _apply_filters(self) -> None:
        result = []
        for s in self.all_suggestions:
            if self.filter_outcome != "all" and s["session_outcome"] != self.filter_outcome:
                continue
            if self.filter_turns == "single" and s["is_multi_turn"]:
                continue
            if self.filter_turns == "multi" and not s["is_multi_turn"]:
                continue
            if self.filter_topic == "passed" and not s["topic_passed"]:
                continue
            if self.filter_topic == "failed" and s["topic_passed"]:
                continue
            result.append(s)
        self.filtered_suggestions = result

    def set_filter_outcome(self, value: str) -> None:
        self.filter_outcome = value
        self._apply_filters()

    def set_filter_turns(self, value: str) -> None:
        self.filter_turns = value
        self._apply_filters()

    def set_filter_topic(self, value: str) -> None:
        self.filter_topic = value
        self._apply_filters()

    def toggle_suggestion(self, eval_result_index: int) -> None:
        updated = []
        for s in self.all_suggestions:
            if s["eval_result_index"] == eval_result_index:
                updated.append({**s, "selected": not s["selected"]})
            else:
                updated.append(s)
        self.all_suggestions = updated
        self._apply_filters()

    def select_all(self) -> None:
        filtered_indices = {s["eval_result_index"] for s in self.filtered_suggestions}
        updated = []
        for s in self.all_suggestions:
            if s["eval_result_index"] in filtered_indices:
                updated.append({**s, "selected": True})
            else:
                updated.append(s)
        self.all_suggestions = updated
        self._apply_filters()

    def deselect_all(self) -> None:
        filtered_indices = {s["eval_result_index"] for s in self.filtered_suggestions}
        updated = []
        for s in self.all_suggestions:
            if s["eval_result_index"] in filtered_indices:
                updated.append({**s, "selected": False})
            else:
                updated.append(s)
        self.all_suggestions = updated
        self._apply_filters()

    def delete_suggestion(self, eval_result_index: int) -> None:
        self.all_suggestions = [
            s for s in self.all_suggestions if s["eval_result_index"] != eval_result_index
        ]
        self._apply_filters()

    def set_dataset_name(self, value: str) -> None:
        self.dataset_name = value

    def create_dataset(self):  # type: ignore[return]
        selected = [s for s in self.all_suggestions if s.get("selected")]
        if not selected:
            self.create_error = "No conversations selected."
            return None

        if not self.dataset_name.strip():
            self.create_error = "Dataset name is required."
            return None

        with rx.session() as session:
            results = session.exec(
                select(EvalResult).where(EvalResult.eval_run_id == self.retro_run_id)
            ).all()
            result_map = {r.test_case_index: r for r in results}

        cases = []
        for suggestion in selected:
            idx = suggestion["eval_result_index"]
            r = result_map.get(idx)
            if not r:
                continue
            try:
                conversation = json.loads(r.input_json)
            except Exception:
                conversation = []

            user_turns = [t for t in conversation if t.get("role") == "user"]
            turns = [{"role": "user", "content": t["content"]} for t in user_turns]
            if not turns:
                continue

            cases.append({
                "turns": turns,
                "expected_output": "",
                "expected_topic": "",
            })

        if not cases:
            self.create_error = "No valid cases to create."
            return None

        has_multi = any(s["is_multi_turn"] for s in selected)
        eval_type = "multi_turn" if has_multi else "single_turn"

        with rx.session() as session:
            dataset = Dataset(
                name=self.dataset_name.strip(),
                eval_type=eval_type,
                data_json=json.dumps(cases),
                num_cases=len(cases),
            )
            session.add(dataset)
            session.commit()
            session.refresh(dataset)
            new_id = dataset.id

        self.created_dataset_id = new_id
        self.create_error = ""
        return rx.redirect(f"/datasets/{new_id}")


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------


def _outcome_badge(outcome: rx.Var) -> rx.Component:
    return rx.badge(
        outcome,
        color_scheme=rx.cond(
            outcome == "Resolved",
            "green",
            rx.cond(
                outcome == "Escalated",
                "orange",
                rx.cond(
                    outcome == "Abandoned",
                    "red",
                    "gray",
                ),
            ),
        ),
        variant="soft",
        size="1",
    )


def _suggestion_row(s: dict) -> rx.Component:
    return rx.table.row(
        rx.table.cell(
            rx.checkbox(checked=s["selected"]),
            on_click=RetroSuggestState.toggle_suggestion(s["eval_result_index"]),
            cursor="pointer",
        ),
        rx.table.cell(
            rx.text(
                s["user_turns_text"],
                size="2",
                white_space="pre-line",
            ),
        ),
        rx.table.cell(
            rx.cond(
                s["is_multi_turn"],
                rx.badge("multi", variant="soft", size="1", color_scheme="blue"),
                rx.badge("single", variant="soft", size="1", color_scheme="gray"),
            ),
        ),
        rx.table.cell(_outcome_badge(s["session_outcome"])),
        rx.table.cell(
            rx.text(
                s["topic_score"].to(str) + "%",
                size="1",
                font_family="var(--font-mono)",
                color=rx.cond(
                    s["topic_passed"],
                    "var(--green-9)",
                    "var(--red-9)",
                ),
            ),
        ),
        rx.table.cell(
            rx.cond(
                s["csat"] == -1.0,
                rx.text("—", size="1", color="var(--gray-a7)"),
                rx.text(s["csat"].to(str), size="1", font_family="var(--font-mono)"),
            ),
        ),
        rx.table.cell(
            rx.cond(
                s["tools_text"] != "",
                rx.text(s["tools_text"], size="1", color="var(--violet-11)"),
                rx.text("—", size="1", color="var(--gray-a7)"),
            ),
        ),
        rx.table.cell(
            rx.cond(
                s["is_duplicate"],
                rx.badge(
                    "×" + s["duplicate_count"].to(str),
                    variant="soft",
                    size="1",
                    color_scheme="amber",
                ),
                rx.text("—", size="1", color="var(--gray-a7)"),
            ),
        ),
        rx.table.cell(
            rx.icon_button(
                rx.icon("trash-2", size=14),
                size="1",
                variant="ghost",
                color_scheme="red",
                on_click=RetroSuggestState.delete_suggestion(s["eval_result_index"]),
            ),
        ),
    )


def _stats_bar() -> rx.Component:
    return rx.hstack(
        rx.badge(
            RetroSuggestState.total_count.to(str) + " transcripts",
            variant="soft",
            color_scheme="teal",
        ),
        rx.badge(
            RetroSuggestState.dedup_skipped.to(str) + " duplicates removed",
            variant="soft",
            color_scheme="gray",
        ),
        rx.badge(
            RetroSuggestState.topic_passed_count.to(str) + " passed topic routing",
            variant="soft",
            color_scheme="blue",
        ),
        rx.badge(
            RetroSuggestState.resolved_count.to(str) + " Resolved",
            variant="soft",
            color_scheme="green",
        ),
        spacing="2",
        flex_wrap="wrap",
    )


def _filter_bar() -> rx.Component:
    return rx.hstack(
        rx.vstack(
            rx.text("Outcome", size="1", weight="medium", color="var(--gray-a9)"),
            rx.select(
                ["all", "Resolved", "Escalated", "Abandoned"],
                value=RetroSuggestState.filter_outcome,
                on_change=RetroSuggestState.set_filter_outcome,
                size="1",
            ),
            spacing="1",
        ),
        rx.vstack(
            rx.text("Turns", size="1", weight="medium", color="var(--gray-a9)"),
            rx.select(
                ["all", "single", "multi"],
                value=RetroSuggestState.filter_turns,
                on_change=RetroSuggestState.set_filter_turns,
                size="1",
            ),
            spacing="1",
        ),
        rx.vstack(
            rx.text("Topic Routing", size="1", weight="medium", color="var(--gray-a9)"),
            rx.select(
                ["all", "passed", "failed"],
                value=RetroSuggestState.filter_topic,
                on_change=RetroSuggestState.set_filter_topic,
                size="1",
            ),
            spacing="1",
        ),
        rx.spacer(),
        rx.button(
            "Select All",
            variant="soft",
            size="1",
            on_click=RetroSuggestState.select_all,
        ),
        rx.button(
            "Deselect All",
            variant="soft",
            size="1",
            color_scheme="gray",
            on_click=RetroSuggestState.deselect_all,
        ),
        align="end",
        spacing="3",
        width="100%",
        padding_y="8px",
    )


def _suggestion_table() -> rx.Component:
    return rx.cond(
        RetroSuggestState.all_suggestions.length() > 0,
        rx.cond(
            RetroSuggestState.filtered_suggestions.length() > 0,
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell(""),
                        rx.table.column_header_cell("Utterance"),
                        rx.table.column_header_cell("Turns"),
                        rx.table.column_header_cell("Outcome"),
                        rx.table.column_header_cell("Topic %"),
                        rx.table.column_header_cell("CSAT"),
                        rx.table.column_header_cell("Tools"),
                        rx.table.column_header_cell("Seen"),
                        rx.table.column_header_cell(""),
                    ),
                ),
                rx.table.body(
                    rx.foreach(
                        RetroSuggestState.filtered_suggestions,
                        _suggestion_row,
                    ),
                ),
                width="100%",
            ),
            empty_state("No conversations match the current filters.", "filter"),
        ),
        empty_state("No eval results found for this run.", "inbox"),
    )


def _create_card() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("database", size=16, color="var(--accent-9)"),
                rx.text("Create Dataset", size="3", weight="bold"),
                spacing="2",
                align="center",
            ),
            rx.vstack(
                rx.text("Dataset Name", size="2", weight="medium"),
                rx.input(
                    value=RetroSuggestState.dataset_name,
                    on_change=RetroSuggestState.set_dataset_name,
                    placeholder="Dataset name...",
                    width="100%",
                ),
                spacing="1",
                width="100%",
            ),
            rx.text(
                RetroSuggestState.selected_count.to(str) + " conversations selected",
                size="2",
                color="var(--gray-a9)",
            ),
            rx.cond(
                RetroSuggestState.create_error != "",
                rx.callout(
                    RetroSuggestState.create_error,
                    icon="triangle_alert",
                    color_scheme="red",
                    width="100%",
                ),
            ),
            rx.button(
                rx.icon("database", size=14),
                "Create Dataset",
                on_click=RetroSuggestState.create_dataset,
                disabled=RetroSuggestState.create_disabled,
                size="2",
            ),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


@rx.page(
    route="/retro/suggest/[run_id]",
    title="Dataset Builder",
    on_load=RetroSuggestState.load_page,
)
def retro_suggest_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.vstack(
                rx.link(
                    rx.hstack(
                        rx.icon("arrow-left", size=16),
                        rx.text("Transcript Extract", size="2"),
                        spacing="1",
                        align="center",
                    ),
                    href="/retro",
                    underline="none",
                ),
                rx.heading(
                    "Build Dataset — ",
                    RetroSuggestState.run_name,
                    size="6",
                    letter_spacing="-0.03em",
                    weight="bold",
                ),
                _stats_bar(),
                spacing="2",
                padding_bottom="16px",
                width="100%",
            ),
            _filter_bar(),
            _suggestion_table(),
            _create_card(),
            spacing="4",
            width="100%",
        ),
    )
