"""Conversation viewer — drill into individual conversations from a retro eval run."""

import json
from datetime import datetime

import reflex as rx
from sqlmodel import select

from web.components import empty_state, layout
from web.mermaid import MERMAID_RENDER_JS, mermaid_diagram, mermaid_script
from web.models import EvalResult, EvalRun
from web.state import State


# ---------------------------------------------------------------------------
# Diagram builder helpers
# ---------------------------------------------------------------------------


def _sanitize(text: str) -> str:
    """Remove Mermaid-conflicting characters and truncate."""
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    for ch in ('"', "'", "#", ";", "{", "}", "|", "<", ">"):
        text = text.replace(ch, " ")
    return text[:80]


def _make_pid(name: str) -> str:
    """Create a valid Mermaid participant ID from a topic name."""
    return "".join(c for c in name if c.isalnum() or c == "_") or "Unknown"


def build_sequence_mermaid(
    messages: list[dict],
    intent_recognition: list[dict],
    dialog_redirects: list[str],
    tools_used: list[str],
    outcome: str,
) -> str:
    lines = ["sequenceDiagram"]

    # Collect unique topic participants
    topics: list[str] = []
    seen_topics: set[str] = set()
    for intent in intent_recognition:
        t = intent.get("topic", "")
        if t and t not in seen_topics:
            topics.append(t)
            seen_topics.add(t)
    for rd in dialog_redirects:
        if rd and rd not in seen_topics:
            topics.append(rd)
            seen_topics.add(rd)

    lines.append("    participant User")
    lines.append("    participant Orchestrator")
    for t in topics:
        pid = _make_pid(t)
        lines.append(f"    participant {pid} as Topic - {_sanitize(t)}")

    # Interleave messages and routing events
    routing_inserted = False
    for msg in messages:
        content = _sanitize(msg.get("content", ""))
        if msg["role"] == "user":
            lines.append(f"    User->>Orchestrator: {content}")
            if not routing_inserted:
                for intent in intent_recognition:
                    t = _sanitize(intent.get("topic", ""))
                    score = intent.get("score", 0) or 0
                    pct = int(float(score) * 100) if float(score) <= 1.0 else int(float(score))
                    lines.append(f"    Note over Orchestrator: Plan - {t} ({pct}%)")
                for rd in dialog_redirects:
                    pid = _make_pid(rd)
                    lines.append(f"    Orchestrator->>{pid}: Execute {_sanitize(rd)}")
                    state = "failed" if outcome in ("Escalated", "Abandoned") else "done"
                    if state == "failed":
                        lines.append(f"    {pid}-->>Orchestrator: failed")
                    else:
                        lines.append(f"    {pid}->>Orchestrator: done")
                if tools_used:
                    tools_str = ", ".join(_sanitize(t) for t in tools_used[:4])
                    lines.append(f"    Note over Orchestrator: Tools: {tools_str}")
                routing_inserted = True
        else:
            lines.append(f"    Orchestrator->>User: {content}")

    lines.append(f"    Note over Orchestrator: Outcome: {outcome}")
    return "\n".join(lines)


def build_gantt_mermaid(
    messages: list[dict],
    intent_recognition: list[dict],
    dialog_redirects: list[str],
    outcome: str,
) -> str:
    MIN_DURATION_MS = 50

    timed: list[tuple[int, str, str]] = []
    has_ts = False
    t_origin: int | None = None

    for msg in messages:
        ts_raw = msg.get("timestamp")
        if ts_raw:
            try:
                dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                epoch_ms = int(dt.timestamp() * 1000)
                if t_origin is None:
                    t_origin = epoch_ms
                timed.append((epoch_ms - t_origin, msg["role"], msg.get("content", "")[:40]))
                has_ts = True
            except Exception:
                pass

    if not has_ts:
        for i, msg in enumerate(messages):
            timed.append((i * 1000, msg["role"], msg.get("content", "")[:40]))

    lines = [
        "gantt",
        "    dateFormat x",
        "    axisFormat %M:%S",
        "    title Execution Timeline",
    ]

    lines.append("    section User")
    event_idx = 0
    for ms, role, content in timed:
        if role == "user":
            label = _sanitize(content)
            lines.append(f"    {label} :active, e{event_idx}, {ms}, {ms + MIN_DURATION_MS}")
            event_idx += 1

    lines.append("    section Orchestrator")
    first_user_ms = next((ms for ms, role, _ in timed if role == "user"), 0)
    offset = first_user_ms + MIN_DURATION_MS
    for intent in intent_recognition:
        t = _sanitize(intent.get("topic", ""))
        score = intent.get("score", 0) or 0
        pct = int(float(score) * 100) if float(score) <= 1.0 else int(float(score))
        lines.append(f"    Plan - {t} ({pct}%) :e{event_idx}, {offset}, {offset + MIN_DURATION_MS}")
        event_idx += 1
        offset += MIN_DURATION_MS

    for rd in dialog_redirects:
        rd_label = _sanitize(rd)
        tag = "crit" if outcome in ("Escalated", "Abandoned") else "active"
        lines.append(f"    Execute {rd_label} :{tag}, e{event_idx}, {offset}, {offset + MIN_DURATION_MS}")
        event_idx += 1
        offset += MIN_DURATION_MS

    lines.append("    section Bot")
    for ms, role, content in timed:
        if role == "assistant":
            label = _sanitize(content)
            lines.append(f"    {label} :done, e{event_idx}, {ms}, {ms + MIN_DURATION_MS}")
            event_idx += 1

    last_ms = timed[-1][0] + MIN_DURATION_MS * 2 if timed else 0
    outcome_tag = "crit" if outcome in ("Escalated", "Abandoned") else "done"
    lines.append("    section Outcome")
    lines.append(f"    {outcome} :{outcome_tag}, e{event_idx}, {last_ms}, {last_ms + MIN_DURATION_MS}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class RetroConversationsState(State):
    retro_run_id: int = 0
    run_name: str = ""
    conversation_list: list[dict] = []
    selected_idx: int = -1
    selected_messages: list[dict] = []
    # Normalized fields from activities_json — always present, typed for rx.foreach
    sel_outcome: str = ""
    sel_csat: str = "—"
    sel_transcript_id: str = ""
    sel_intent_recognition: list[dict] = []   # [{"topic": str, "confidence": str}]
    sel_dialog_redirects: list[str] = []
    sel_tools_used: list[str] = []
    sel_seq_mermaid: str = ""
    sel_gantt_mermaid: str = ""
    sel_has_timeline: bool = False

    def load_page(self) -> None:
        run_id_str = self.router.page.params.get("run_id", "0")
        try:
            self.retro_run_id = int(run_id_str)
        except (ValueError, TypeError):
            self.retro_run_id = 0
            return

        self._reset_selection()

        with rx.session() as session:
            run = session.get(EvalRun, self.retro_run_id)
            if not run:
                return
            self.run_name = run.name
            results = session.exec(
                select(EvalResult)
                .where(EvalResult.eval_run_id == self.retro_run_id)
                .order_by(EvalResult.test_case_index)
            ).all()

        rows: list[dict] = []
        for r in results:
            try:
                conversation = json.loads(r.input_json)
            except Exception:
                conversation = []

            try:
                activities = json.loads(r.activities_json)
            except Exception:
                activities = []

            retro_info = next((a for a in activities if a.get("type") == "retro_info"), {})

            user_turns = [m for m in conversation if m.get("role") == "user"]
            first_utterance = ""
            if user_turns:
                raw = user_turns[0].get("content", "")
                first_utterance = raw[:60] + ("…" if len(raw) > 60 else "")

            outcome = retro_info.get("session_outcome", "Unknown")
            csat = retro_info.get("csat")
            tools_used = retro_info.get("tools_used") or []

            try:
                scores = json.loads(r.scores_json)
            except Exception:
                scores = {}
            topic_passed = bool(scores.get("topic_routing", {}).get("passed", False))

            rows.append({
                "result_id": r.id,
                "idx": r.test_case_index + 1,
                "first_utterance": first_utterance,
                "outcome": outcome,
                "csat": f"{csat:.1f}" if csat is not None else "—",
                "turns": len(conversation),
                "topic_passed": topic_passed,
                "tools_count": len(tools_used),
            })

        self.conversation_list = rows

    def _reset_selection(self) -> None:
        self.selected_idx = -1
        self.selected_messages = []
        self.sel_outcome = ""
        self.sel_csat = "—"
        self.sel_transcript_id = ""
        self.sel_intent_recognition = []
        self.sel_dialog_redirects = []
        self.sel_tools_used = []
        self.sel_seq_mermaid = ""
        self.sel_gantt_mermaid = ""
        self.sel_has_timeline = False

    def select_conversation(self, result_id: int) -> None:
        for i, row in enumerate(self.conversation_list):
            if row["result_id"] == result_id:
                self.selected_idx = i
                break

        with rx.session() as session:
            result = session.get(EvalResult, result_id)
            if not result:
                self._reset_selection()
                return

            try:
                self.selected_messages = json.loads(result.input_json)
            except Exception:
                self.selected_messages = []

            try:
                activities = json.loads(result.activities_json)
            except Exception:
                activities = []

        retro_info = next((a for a in activities if a.get("type") == "retro_info"), {})

        self.sel_outcome = retro_info.get("session_outcome", "Unknown")
        csat = retro_info.get("csat")
        self.sel_csat = f"{csat:.1f}" if csat is not None else "—"
        self.sel_transcript_id = retro_info.get("transcript_id", "")
        self.sel_tools_used = retro_info.get("tools_used") or []
        self.sel_dialog_redirects = retro_info.get("dialog_redirects") or []

        # Normalize intent_recognition: [{topic, confidence}]
        raw_intents = retro_info.get("intent_recognition") or []
        normalized: list[dict] = []
        for entry in raw_intents:
            if isinstance(entry, dict):
                score = entry.get("score", 0) or 0
                try:
                    pct = f"{float(score) * 100:.0f}" if float(score) <= 1.0 else f"{float(score):.0f}"
                except Exception:
                    pct = ""
                normalized.append({
                    "topic": str(entry.get("topic", "")),
                    "confidence": pct,
                })
        self.sel_intent_recognition = normalized

        # Build diagram texts
        self.sel_seq_mermaid = build_sequence_mermaid(
            self.selected_messages,
            raw_intents,
            retro_info.get("dialog_redirects") or [],
            retro_info.get("tools_used") or [],
            self.sel_outcome,
        )
        self.sel_gantt_mermaid = build_gantt_mermaid(
            self.selected_messages,
            raw_intents,
            retro_info.get("dialog_redirects") or [],
            self.sel_outcome,
        )
        self.sel_has_timeline = any(
            m.get("timestamp") for m in self.selected_messages
        )
        yield rx.call_script(MERMAID_RENDER_JS)


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


def _conversation_row(r: dict) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.text(
                r["idx"].to(str),
                size="1",
                color="var(--gray-a8)",
                font_family="var(--font-mono)",
                min_width="20px",
            ),
            rx.vstack(
                rx.text(r["first_utterance"], size="2", weight="medium"),
                rx.hstack(
                    _outcome_badge(r["outcome"]),
                    rx.text(
                        r["csat"],
                        size="1",
                        color="var(--gray-a9)",
                        font_family="var(--font-mono)",
                    ),
                    rx.text(
                        r["turns"].to(str) + " msg",
                        size="1",
                        color="var(--gray-a8)",
                    ),
                    spacing="2",
                    align="center",
                    flex_wrap="wrap",
                ),
                spacing="1",
                align="start",
                width="100%",
            ),
            spacing="2",
            align="start",
            width="100%",
        ),
        padding="10px 12px",
        border_radius="var(--radius-2)",
        cursor="pointer",
        _hover={"background": "var(--accent-a2)"},
        on_click=RetroConversationsState.select_conversation(r["result_id"]),
        width="100%",
    )


def _left_panel() -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.hstack(
                rx.icon("messages-square", size=14, color="var(--accent-9)"),
                rx.text("Conversations", size="2", weight="bold"),
                rx.badge(
                    RetroConversationsState.conversation_list.length().to(str),
                    variant="soft",
                    color_scheme="gray",
                    size="1",
                ),
                spacing="2",
                align="center",
                padding="12px 12px 8px",
            ),
            rx.separator(width="100%"),
            rx.scroll_area(
                rx.vstack(
                    rx.foreach(
                        RetroConversationsState.conversation_list,
                        _conversation_row,
                    ),
                    spacing="1",
                    padding="4px",
                    width="100%",
                ),
                height="calc(100vh - 220px)",
                scrollbars="vertical",
            ),
            spacing="0",
            width="100%",
        ),
        width="280px",
        min_width="280px",
        border_right="1px solid var(--gray-a5)",
        background="var(--gray-a1)",
        flex_shrink="0",
    )


def _message_bubble(msg: dict) -> rx.Component:
    return rx.cond(
        msg["role"] == "user",
        rx.hstack(
            rx.spacer(),
            rx.box(
                rx.text(msg["content"], size="2", white_space="pre-wrap"),
                background="var(--accent-a4)",
                border_radius="var(--radius-3)",
                padding="8px 12px",
                max_width="75%",
            ),
            rx.box(
                rx.icon("user", size=14, color="var(--accent-9)"),
                padding="4px",
                border_radius="var(--radius-full)",
                background="var(--accent-a3)",
                flex_shrink="0",
            ),
            spacing="2",
            align="start",
            width="100%",
        ),
        rx.hstack(
            rx.box(
                rx.icon("bot", size=14, color="var(--gray-a9)"),
                padding="4px",
                border_radius="var(--radius-full)",
                background="var(--gray-a3)",
                flex_shrink="0",
            ),
            rx.box(
                rx.text(msg["content"], size="2", white_space="pre-wrap"),
                background="var(--gray-a3)",
                border_radius="var(--radius-3)",
                padding="8px 12px",
                max_width="75%",
            ),
            rx.spacer(),
            spacing="2",
            align="start",
            width="100%",
        ),
    )


def _intent_row(intent: dict) -> rx.Component:
    return rx.hstack(
        rx.text(intent["topic"], size="2"),
        rx.badge(
            intent["confidence"].to(str) + "%",
            variant="soft",
            color_scheme="teal",
            size="1",
        ),
        spacing="2",
        align="center",
    )


def _trace_card() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("activity", size=16, color="var(--accent-9)"),
                rx.text("Execution Trace", size="3", weight="bold"),
                spacing="2",
                align="center",
            ),
            rx.separator(width="100%"),
            # Intent Routing
            rx.cond(
                RetroConversationsState.sel_intent_recognition.length() > 0,
                rx.vstack(
                    rx.hstack(
                        rx.icon("target", size=14, color="var(--accent-9)"),
                        rx.text("Intent Routing", size="2", weight="bold"),
                        spacing="2",
                        align="center",
                    ),
                    rx.foreach(
                        RetroConversationsState.sel_intent_recognition,
                        _intent_row,
                    ),
                    spacing="2",
                    align="start",
                    width="100%",
                ),
            ),
            # Dialog Redirects
            rx.cond(
                RetroConversationsState.sel_dialog_redirects.length() > 0,
                rx.vstack(
                    rx.hstack(
                        rx.icon("arrow-right-left", size=14, color="var(--accent-9)"),
                        rx.text("Dialog Redirects", size="2", weight="bold"),
                        spacing="2",
                        align="center",
                    ),
                    rx.foreach(
                        RetroConversationsState.sel_dialog_redirects,
                        lambda redirect: rx.hstack(
                            rx.text("→", size="2", color="var(--gray-a8)"),
                            rx.text(redirect, size="2"),
                            spacing="1",
                            align="center",
                        ),
                    ),
                    spacing="2",
                    align="start",
                    width="100%",
                ),
            ),
            # Tools Used
            rx.cond(
                RetroConversationsState.sel_tools_used.length() > 0,
                rx.vstack(
                    rx.hstack(
                        rx.icon("wrench", size=14, color="var(--accent-9)"),
                        rx.text("Tools Used", size="2", weight="bold"),
                        spacing="2",
                        align="center",
                    ),
                    rx.hstack(
                        rx.foreach(
                            RetroConversationsState.sel_tools_used,
                            lambda tool: rx.badge(
                                tool,
                                variant="outline",
                                size="1",
                                color_scheme="violet",
                            ),
                        ),
                        flex_wrap="wrap",
                        spacing="1",
                    ),
                    spacing="2",
                    align="start",
                    width="100%",
                ),
            ),
            # Session Outcome
            rx.vstack(
                rx.hstack(
                    rx.icon("check-circle", size=14, color="var(--accent-9)"),
                    rx.text("Session Outcome", size="2", weight="bold"),
                    spacing="2",
                    align="center",
                ),
                rx.hstack(
                    _outcome_badge(RetroConversationsState.sel_outcome),
                    rx.cond(
                        RetroConversationsState.sel_csat != "—",
                        rx.hstack(
                            rx.text("CSAT: " + RetroConversationsState.sel_csat, size="2"),
                            rx.icon("star", size=12, color="var(--yellow-9)"),
                            spacing="1",
                            align="center",
                        ),
                    ),
                    spacing="3",
                    align="center",
                ),
                spacing="2",
                align="start",
                width="100%",
            ),
            # Transcript ID
            rx.vstack(
                rx.hstack(
                    rx.icon("hash", size=14, color="var(--accent-9)"),
                    rx.text("Transcript ID", size="2", weight="bold"),
                    spacing="2",
                    align="center",
                ),
                rx.text(
                    RetroConversationsState.sel_transcript_id,
                    size="1",
                    color="var(--gray-a9)",
                    font_family="var(--font-mono)",
                ),
                spacing="1",
                align="start",
                width="100%",
            ),
            spacing="4",
            width="100%",
        ),
        width="100%",
    )


def _sequence_flow_card() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("git-branch", size=16, color="var(--accent-9)"),
                rx.text("Execution Flow", size="3", weight="bold"),
                spacing="2",
                align="center",
            ),
            rx.separator(width="100%"),
            mermaid_diagram(RetroConversationsState.sel_seq_mermaid),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


def _gantt_card() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("gantt-chart", size=16, color="var(--accent-9)"),
                rx.text("Execution Gantt", size="3", weight="bold"),
                rx.cond(
                    ~RetroConversationsState.sel_has_timeline,
                    rx.badge(
                        "approx. — run a new retro eval for real timestamps",
                        size="1",
                        color_scheme="amber",
                    ),
                ),
                spacing="2",
                align="center",
            ),
            rx.separator(width="100%"),
            mermaid_diagram(RetroConversationsState.sel_gantt_mermaid),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


def _right_panel() -> rx.Component:
    return rx.cond(
        RetroConversationsState.selected_idx >= 0,
        rx.scroll_area(
            rx.vstack(
                rx.card(
                    rx.vstack(
                        rx.hstack(
                            rx.icon("message-square", size=16, color="var(--accent-9)"),
                            rx.text("Conversation Thread", size="3", weight="bold"),
                            spacing="2",
                            align="center",
                        ),
                        rx.separator(width="100%"),
                        rx.vstack(
                            rx.foreach(
                                RetroConversationsState.selected_messages,
                                _message_bubble,
                            ),
                            spacing="3",
                            width="100%",
                        ),
                        spacing="3",
                        width="100%",
                    ),
                    width="100%",
                ),
                _trace_card(),
                _sequence_flow_card(),
                _gantt_card(),
                spacing="4",
                width="100%",
                padding="16px",
            ),
            height="calc(100vh - 160px)",
            scrollbars="vertical",
            width="100%",
        ),
        rx.box(
            rx.vstack(
                rx.icon("message-square", size=32, color="var(--gray-a6)"),
                rx.text(
                    "Select a conversation to view its trace",
                    size="3",
                    color="var(--gray-a8)",
                ),
                spacing="3",
                align="center",
            ),
            display="flex",
            align_items="center",
            justify_content="center",
            flex="1",
            height="calc(100vh - 160px)",
        ),
    )


@rx.page(
    route="/retro/conversations/[run_id]",
    title="Conversation Viewer",
    on_load=RetroConversationsState.load_page,
)
def retro_conversations_page() -> rx.Component:
    return layout(
        rx.vstack(
            mermaid_script(),
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
                    "Conversations — ",
                    RetroConversationsState.run_name,
                    size="6",
                    letter_spacing="-0.03em",
                    weight="bold",
                ),
                spacing="2",
                padding_bottom="12px",
                width="100%",
            ),
            rx.hstack(
                _left_panel(),
                _right_panel(),
                spacing="0",
                align="start",
                width="100%",
            ),
            spacing="0",
            width="100%",
        ),
    )
