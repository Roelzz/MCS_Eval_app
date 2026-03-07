"""Scenario Wizard page — guides users from agent description to generated test dataset."""

import json
from datetime import datetime

import reflex as rx
from sqlmodel import select

from web.components import layout, page_header
from web.models import Dataset
from web.state import State


class ScenarioWizardState(State):
    step: int = 1

    # Step 1 — Agent profile
    agent_name: str = ""
    agent_purpose: str = ""
    agent_capabilities: list[str] = []
    agent_knowledge_source_ids: list[int] = []   # selected KnowledgeSource IDs
    available_sources: list[dict] = []           # {id, name, file_type} for all sources

    # Step 2 — Scenario selection
    scenarios_loading: bool = False
    available_scenarios: list[dict] = []
    selected_scenario_ids: list[str] = []
    num_cases_per_scenario: int = 5
    fetch_error: str = ""

    # Step 3 — Generate
    generating: bool = False
    generate_progress: int = 0
    generate_total: int = 0
    generated_cases: list[dict] = []
    generate_error: str = ""

    # Step 4 — Review & Create
    dataset_name: str = ""
    dataset_description: str = ""
    dataset_eval_type: str = "single_turn"
    save_error: str = ""

    def on_load(self) -> None:
        self.load_available_sources()

    def load_available_sources(self) -> None:
        from web.models import KnowledgeSource
        with rx.session() as session:
            rows = session.exec(
                select(KnowledgeSource).order_by(KnowledgeSource.name)
            ).all()
        self.available_sources = [
            {"id": ks.id, "name": ks.name, "file_type": ks.file_type}
            for ks in rows
        ]

    def toggle_knowledge_source(self, ks_id: int) -> None:
        ids = list(self.agent_knowledge_source_ids)
        if ks_id in ids:
            ids.remove(ks_id)
        else:
            ids.append(ks_id)
        self.agent_knowledge_source_ids = ids

    def _get_knowledge_source_content(self) -> str:
        if not self.agent_knowledge_source_ids:
            return ""
        from web.models import KnowledgeSource
        with rx.session() as session:
            rows = [session.get(KnowledgeSource, kid) for kid in self.agent_knowledge_source_ids]
        return "\n\n".join(ks.content for ks in rows if ks is not None)

    # Setters — Step 1
    def set_agent_name(self, value: str) -> None:
        self.agent_name = value

    def set_agent_purpose(self, value: str) -> None:
        self.agent_purpose = value

    def toggle_capability(self, checked: bool, cap: str) -> None:
        if checked and cap not in self.agent_capabilities:
            self.agent_capabilities = self.agent_capabilities + [cap]
        elif not checked and cap in self.agent_capabilities:
            self.agent_capabilities = [c for c in self.agent_capabilities if c != cap]

    # Setters — Step 2
    def toggle_scenario(self, scenario_id: str) -> None:
        if scenario_id in self.selected_scenario_ids:
            self.selected_scenario_ids = [
                s for s in self.selected_scenario_ids if s != scenario_id
            ]
        else:
            self.selected_scenario_ids = self.selected_scenario_ids + [scenario_id]

    def set_num_cases(self, value: list[int | float]) -> None:
        if value:
            self.num_cases_per_scenario = int(value[0])

    @rx.var
    def business_scenarios(self) -> list[dict]:
        return [s for s in self.available_scenarios if s.get("category") == "business"]

    @rx.var
    def capability_scenarios(self) -> list[dict]:
        return [s for s in self.available_scenarios if s.get("category") == "capability"]

    # Setters — Step 4
    def set_dataset_name(self, value: str) -> None:
        self.dataset_name = value

    def set_dataset_description(self, value: str) -> None:
        self.dataset_description = value

    def set_eval_type(self, value: str) -> None:
        self.dataset_eval_type = value

    def go_back(self) -> None:
        if self.step > 1:
            self.step = self.step - 1

    @rx.event(background=True)
    async def fetch_and_recommend(self) -> None:
        from scenario_fetcher import fetch_all_scenarios
        from scenario_generator import recommend_scenarios

        async with self:
            self.scenarios_loading = True
            self.fetch_error = ""
            self.step = 2

        try:
            scenarios = await fetch_all_scenarios()
        except Exception as e:
            async with self:
                self.fetch_error = f"Failed to fetch scenarios: {e}"
                self.scenarios_loading = False
            return

        agent_profile = {}
        async with self:
            agent_profile = {
                "name": self.agent_name,
                "purpose": self.agent_purpose,
                "capabilities": self.agent_capabilities,
                "knowledge_sources": self._get_knowledge_source_content(),
            }

        titles = [s["title"] for s in scenarios]
        try:
            recommendations = await recommend_scenarios(agent_profile, titles)
        except Exception as e:
            async with self:
                self.fetch_error = f"Failed to get recommendations: {e}"
                self.scenarios_loading = False
            return

        # Build a map of title -> {id, reason}
        rec_map: dict[str, dict] = {}
        for r in recommendations:
            title = r.get("title", "")
            rec_id = r.get("id", "")
            reason = r.get("reason", "")
            if title:
                rec_map[title] = {"rec_id": rec_id, "reason": reason}

        enriched = []
        pre_selected = []
        for s in scenarios:
            rec_info = rec_map.get(s["title"])
            recommended = rec_info is not None
            entry = {
                "id": s["id"],
                "title": s["title"],
                "category": s["category"],
                "content": s.get("content", ""),
                "recommended": recommended,
                "reason": rec_info["reason"] if rec_info else "",
                # Excerpt: first 200 chars of content after stripping front matter
                "excerpt": s.get("content", "")[:200].strip(),
            }
            enriched.append(entry)
            if recommended:
                pre_selected.append(s["id"])

        async with self:
            self.available_scenarios = enriched
            self.selected_scenario_ids = pre_selected
            self.scenarios_loading = False

    @rx.event(background=True)
    async def generate_cases(self) -> None:
        from scenario_generator import generate_test_cases

        async with self:
            self.generating = True
            self.generate_error = ""
            self.generate_progress = 0
            self.step = 3

        selected_ids: list[str] = []
        scenarios_map: dict[str, dict] = {}
        num_cases: int = 5
        agent_profile: dict = {}

        async with self:
            selected_ids = list(self.selected_scenario_ids)
            scenarios_map = {s["id"]: s for s in self.available_scenarios}
            num_cases = self.num_cases_per_scenario
            agent_profile = {
                "name": self.agent_name,
                "purpose": self.agent_purpose,
                "capabilities": self.agent_capabilities,
                "knowledge_sources": self._get_knowledge_source_content(),
            }

        async with self:
            self.generate_total = len(selected_ids)

        results: list[dict] = []
        for scenario_id in selected_ids:
            scenario = scenarios_map.get(scenario_id, {})
            try:
                cases = await generate_test_cases(
                    agent_profile=agent_profile,
                    scenario_content=scenario.get("content", ""),
                    scenario_title=scenario.get("title", scenario_id),
                    num_cases=num_cases,
                )
            except Exception as e:
                async with self:
                    self.generate_error = f"Azure OpenAI error: {e}"
                    self.generating = False
                return
            results.append(
                {
                    "scenario_id": scenario_id,
                    "scenario_title": scenario.get("title", scenario_id),
                    "case_count": f"{len(cases)} case{'s' if len(cases) != 1 else ''}",
                    "cases_json": json.dumps(cases, indent=2),
                }
            )
            async with self:
                self.generate_progress = self.generate_progress + 1

        async with self:
            self.generated_cases = results
            self.generating = False
            self.dataset_name = f"{self.agent_name} - Scenario Dataset" if self.agent_name else "Scenario Dataset"
            self.step = 4

    def save_dataset(self):
        if not self.dataset_name.strip():
            self.save_error = "Dataset name is required."
            return None

        all_cases: list[dict] = []
        for entry in self.generated_cases:
            all_cases.extend(json.loads(entry.get("cases_json", "[]")))

        if not all_cases:
            self.save_error = "No test cases to save."
            return None

        self.save_error = ""

        with rx.session() as session:
            dataset = Dataset(
                name=self.dataset_name.strip(),
                description=self.dataset_description.strip(),
                eval_type=self.dataset_eval_type,
                data_json=json.dumps(all_cases),
                num_cases=len(all_cases),
                created_at=datetime.utcnow(),
            )
            session.add(dataset)
            session.commit()

        return rx.redirect("/datasets")


# ──────────────────────────────────────────────
# Step indicator
# ──────────────────────────────────────────────

def _step_dot(num: int, label: str) -> rx.Component:
    is_active = ScenarioWizardState.step == num
    is_done = ScenarioWizardState.step > num
    return rx.vstack(
        rx.box(
            rx.cond(
                is_done,
                rx.icon("check", size=12, color="white"),
                rx.text(str(num), size="1", weight="bold", color=rx.cond(is_active, "white", "var(--gray-a8)")),
            ),
            width="28px",
            height="28px",
            border_radius="50%",
            background=rx.cond(
                is_done,
                "var(--teal-9)",
                rx.cond(is_active, "var(--accent-9)", "var(--gray-a4)"),
            ),
            display="flex",
            align_items="center",
            justify_content="center",
            flex_shrink="0",
        ),
        rx.text(
            label,
            size="1",
            color=rx.cond(is_active, "var(--gray-12)", "var(--gray-a8)"),
            weight=rx.cond(is_active, "medium", "regular"),
        ),
        align="center",
        spacing="1",
    )


def step_indicator() -> rx.Component:
    return rx.hstack(
        _step_dot(1, "Profile"),
        rx.box(height="1px", width="40px", background="var(--gray-a4)", margin_top="-12px"),
        _step_dot(2, "Scenarios"),
        rx.box(height="1px", width="40px", background="var(--gray-a4)", margin_top="-12px"),
        _step_dot(3, "Generate"),
        rx.box(height="1px", width="40px", background="var(--gray-a4)", margin_top="-12px"),
        _step_dot(4, "Review"),
        spacing="2",
        align="center",
        justify="center",
        padding_bottom="24px",
    )


# ──────────────────────────────────────────────
# Step 1 — Agent Profile
# ──────────────────────────────────────────────

_CAPABILITY_OPTIONS = [
    "Knowledge Q&A",
    "Tool Invocations",
    "Task Execution",
    "Multi-step Guidance",
    "Triage & Routing",
]


def step1_profile() -> rx.Component:
    return rx.vstack(
        rx.heading("Describe your agent", size="4", weight="bold"),
        rx.text("We'll use this to recommend the most relevant evaluation scenarios.", size="2", color="var(--gray-a9)"),
        rx.vstack(
            rx.text("Agent name", size="2", weight="medium"),
            rx.input(
                placeholder="e.g. HR Assistant, IT Helpdesk Bot",
                value=ScenarioWizardState.agent_name,
                on_change=ScenarioWizardState.set_agent_name,
                width="100%",
            ),
            spacing="1",
            width="100%",
        ),
        rx.vstack(
            rx.text("What does your agent do?", size="2", weight="medium"),
            rx.text_area(
                placeholder="Describe the primary purpose and use cases of your agent...",
                value=ScenarioWizardState.agent_purpose,
                on_change=ScenarioWizardState.set_agent_purpose,
                width="100%",
                min_height="100px",
            ),
            spacing="1",
            width="100%",
        ),
        rx.vstack(
            rx.text("Capabilities", size="2", weight="medium"),
            rx.vstack(
                *[
                    rx.checkbox(
                        cap,
                        checked=ScenarioWizardState.agent_capabilities.contains(cap),
                        on_change=lambda val, c=cap: ScenarioWizardState.toggle_capability(val, c),
                    )
                    for cap in _CAPABILITY_OPTIONS
                ],
                spacing="2",
                align="start",
            ),
            spacing="1",
            width="100%",
        ),
        rx.vstack(
            rx.hstack(
                rx.icon("file-text", size=16, color="var(--gray-a9)"),
                rx.text("Knowledge Sources", size="2", weight="medium"),
                spacing="2",
                align="center",
            ),
            rx.cond(
                ScenarioWizardState.available_sources.length() > 0,
                rx.vstack(
                    rx.foreach(
                        ScenarioWizardState.available_sources,
                        lambda ks: rx.hstack(
                            rx.checkbox(
                                checked=ScenarioWizardState.agent_knowledge_source_ids.contains(ks["id"]),
                                on_change=lambda _: ScenarioWizardState.toggle_knowledge_source(ks["id"]),
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
            spacing="2",
            width="100%",
        ),
        rx.hstack(
            rx.button(
                "Next: Find Scenarios",
                rx.icon("arrow-right", size=16),
                on_click=ScenarioWizardState.fetch_and_recommend,
                size="2",
            ),
            justify="end",
            width="100%",
        ),
        spacing="4",
        width="100%",
    )


# ──────────────────────────────────────────────
# Step 2 — Scenario Selection
# ──────────────────────────────────────────────

def scenario_card(s: dict) -> rx.Component:
    is_selected = ScenarioWizardState.selected_scenario_ids.contains(s["id"])
    return rx.card(
        rx.hstack(
            rx.checkbox(
                checked=is_selected,
                on_change=lambda _: ScenarioWizardState.toggle_scenario(s["id"]),
            ),
            rx.vstack(
                rx.hstack(
                    rx.text(s["title"], size="2", weight="medium"),
                    rx.cond(
                        s["recommended"],
                        rx.badge("Recommended", color_scheme="teal", variant="soft", size="1"),
                        rx.fragment(),
                    ),
                    spacing="2",
                    align="center",
                ),
                rx.cond(
                    s["reason"] != "",
                    rx.text(s["reason"], size="1", color="var(--teal-11)"),
                    rx.fragment(),
                ),
                rx.text(s["excerpt"], size="1", color="var(--gray-a8)"),
                spacing="1",
                flex="1",
            ),
            spacing="3",
            align="start",
            width="100%",
        ),
        width="100%",
        cursor="pointer",
        border=rx.cond(
            is_selected,
            "1px solid var(--accent-8)",
            "1px solid var(--gray-a4)",
        ),
        on_click=ScenarioWizardState.toggle_scenario(s["id"]),
    )


def business_scenarios_section() -> rx.Component:
    return rx.vstack(
        rx.text("Business Problem Scenarios", size="3", weight="bold", color="var(--gray-a11)"),
        rx.foreach(ScenarioWizardState.business_scenarios, scenario_card),
        spacing="2",
        width="100%",
    )


def capability_scenarios_section() -> rx.Component:
    return rx.vstack(
        rx.text("Capability Scenarios", size="3", weight="bold", color="var(--gray-a11)"),
        rx.foreach(ScenarioWizardState.capability_scenarios, scenario_card),
        spacing="2",
        width="100%",
    )


def step2_scenarios() -> rx.Component:
    return rx.cond(
        ScenarioWizardState.scenarios_loading,
        rx.center(
            rx.vstack(
                rx.spinner(size="3"),
                rx.text("Loading scenarios from GitHub...", size="2", color="var(--gray-a9)"),
                align="center",
                spacing="3",
                padding="60px 0",
            ),
            width="100%",
        ),
        rx.vstack(
            rx.heading("Select evaluation scenarios", size="4", weight="bold"),
            rx.text("AI-recommended scenarios are pre-selected. Adjust as needed.", size="2", color="var(--gray-a9)"),
            rx.cond(
                ScenarioWizardState.fetch_error != "",
                rx.callout(
                    ScenarioWizardState.fetch_error,
                    icon="triangle_alert",
                    color_scheme="red",
                    width="100%",
                ),
                rx.fragment(),
            ),
            business_scenarios_section(),
            capability_scenarios_section(),
            rx.vstack(
                rx.hstack(
                    rx.text("Test cases per scenario:", size="2", weight="medium"),
                    rx.text(
                        ScenarioWizardState.num_cases_per_scenario.to(str),
                        size="2",
                        weight="bold",
                        color="var(--accent-9)",
                        font_family="var(--font-mono)",
                    ),
                    spacing="2",
                    align="center",
                ),
                rx.slider(
                    min=1,
                    max=10,
                    step=1,
                    value=[ScenarioWizardState.num_cases_per_scenario],
                    on_value_commit=ScenarioWizardState.set_num_cases,
                    width="320px",
                ),
                spacing="2",
            ),
            rx.hstack(
                rx.button(
                    rx.icon("arrow-left", size=16),
                    "Back",
                    variant="soft",
                    color_scheme="gray",
                    on_click=ScenarioWizardState.go_back,
                    size="2",
                ),
                rx.button(
                    "Generate Test Cases",
                    rx.icon("arrow-right", size=16),
                    on_click=ScenarioWizardState.generate_cases,
                    size="2",
                    disabled=ScenarioWizardState.selected_scenario_ids.length() == 0,
                ),
                spacing="3",
                justify="end",
                width="100%",
            ),
            spacing="4",
            width="100%",
        ),
    )


# ──────────────────────────────────────────────
# Step 3 — Generating
# ──────────────────────────────────────────────

def step3_generating() -> rx.Component:
    return rx.center(
        rx.vstack(
            rx.spinner(size="3"),
            rx.text("Generating test cases with Azure OpenAI...", size="3", weight="medium"),
            rx.text(
                ScenarioWizardState.generate_progress.to(str)
                + " / "
                + ScenarioWizardState.generate_total.to(str)
                + " scenarios done",
                size="2",
                color="var(--gray-a9)",
                font_family="var(--font-mono)",
            ),
            rx.cond(
                ScenarioWizardState.generate_error != "",
                rx.callout(
                    ScenarioWizardState.generate_error,
                    icon="triangle_alert",
                    color_scheme="red",
                ),
                rx.fragment(),
            ),
            align="center",
            spacing="4",
            padding="80px 0",
        ),
        width="100%",
    )


# ──────────────────────────────────────────────
# Step 4 — Review & Create
# ──────────────────────────────────────────────

def scenario_accordion_item(entry: dict) -> rx.Component:
    return rx.accordion.item(
        header=rx.hstack(
            rx.text(entry["scenario_title"], size="2", weight="medium"),
            rx.badge(entry["case_count"], color_scheme="teal", variant="soft", size="1"),
            spacing="3",
            align="center",
        ),
        content=rx.text_area(
            value=entry["cases_json"],
            read_only=True,
            width="100%",
            min_height="220px",
            font_family="var(--font-mono)",
        ),
        value=entry["scenario_id"],
    )


def step4_review() -> rx.Component:
    return rx.vstack(
        rx.heading("Review & create dataset", size="4", weight="bold"),
        rx.accordion.root(
            rx.foreach(
                ScenarioWizardState.generated_cases,
                scenario_accordion_item,
            ),
            type="multiple",
            width="100%",
            variant="soft",
        ),
        rx.vstack(
            rx.text("Dataset name", size="2", weight="medium"),
            rx.input(
                value=ScenarioWizardState.dataset_name,
                on_change=ScenarioWizardState.set_dataset_name,
                width="100%",
            ),
            spacing="1",
            width="100%",
        ),
        rx.vstack(
            rx.text("Description (optional)", size="2", weight="medium"),
            rx.input(
                placeholder="Generated from Scenario Wizard",
                value=ScenarioWizardState.dataset_description,
                on_change=ScenarioWizardState.set_dataset_description,
                width="100%",
            ),
            spacing="1",
            width="100%",
        ),
        rx.vstack(
            rx.text("Eval type", size="2", weight="medium"),
            rx.select(
                ["single_turn", "multi_turn", "autonomous"],
                value=ScenarioWizardState.dataset_eval_type,
                on_change=ScenarioWizardState.set_eval_type,
                width="100%",
            ),
            spacing="1",
            width="100%",
        ),
        rx.cond(
            ScenarioWizardState.save_error != "",
            rx.callout(
                ScenarioWizardState.save_error,
                icon="triangle_alert",
                color_scheme="red",
                width="100%",
            ),
            rx.fragment(),
        ),
        rx.hstack(
            rx.button(
                rx.icon("arrow-left", size=16),
                "Back",
                variant="soft",
                color_scheme="gray",
                on_click=ScenarioWizardState.go_back,
                size="2",
            ),
            rx.button(
                rx.icon("database", size=16),
                "Create Dataset",
                on_click=ScenarioWizardState.save_dataset,
                size="2",
            ),
            spacing="3",
            justify="end",
            width="100%",
        ),
        spacing="4",
        width="100%",
    )


# ──────────────────────────────────────────────
# Page
# ──────────────────────────────────────────────

@rx.page(route="/scenarios", title="Scenario Wizard", on_load=ScenarioWizardState.on_load)
def scenarios_page() -> rx.Component:
    return layout(
        rx.vstack(
            page_header(
                "Scenario Wizard",
                "Generate context-specific test cases from the Microsoft AI Agent Eval Scenario Library",
            ),
            step_indicator(),
            rx.card(
                rx.cond(
                    ScenarioWizardState.step == 1,
                    step1_profile(),
                    rx.cond(
                        ScenarioWizardState.step == 2,
                        step2_scenarios(),
                        rx.cond(
                            ScenarioWizardState.step == 3,
                            step3_generating(),
                            step4_review(),
                        ),
                    ),
                ),
                width="100%",
                padding="24px",
            ),
            spacing="0",
            width="100%",
            max_width="860px",
        ),
    )
