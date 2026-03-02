# Targeted Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Apply four targeted fixes — retry/backoff, jitter, auto-polling, and run comparison — to improve reliability, visibility, and analysis with zero new dependencies.

**Architecture:** Fix 1 & 4 are contained to `eval_engine.py` (add `_measure_with_retry` helper and per-metric jitter). Fix 2 adds a background polling loop in `RunDetailState`. Fix 3 adds compare selection + modal to the Runs page.

**Tech Stack:** Python 3.12, Reflex 0.8.x, DeepEval 2.0, pytest-asyncio, `asyncio` (stdlib only, no new deps)

---

## Task 1: Add `_measure_with_retry` helper to eval_engine.py

**Files:**
- Modify: `eval_engine.py`
- Test: `tests/test_eval_engine.py`

### Step 1: Write the failing tests

Add these three tests to `tests/test_eval_engine.py`:

```python
# --- Retry helper tests ---

@pytest.mark.asyncio
async def test_measure_with_retry_succeeds_first_try():
    """No retries when measure succeeds immediately."""
    from eval_engine import _measure_with_retry

    mock_metric = MagicMock()
    mock_metric.measure.return_value = None  # sync function, no exception

    with patch("asyncio.sleep") as mock_sleep:
        await _measure_with_retry(mock_metric, MagicMock())

    mock_metric.measure.assert_called_once()
    mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_measure_with_retry_succeeds_second_try():
    """Retries once when first attempt fails."""
    from eval_engine import _measure_with_retry

    mock_metric = MagicMock()
    mock_metric.measure.side_effect = [Exception("timeout"), None]

    with patch("eval_engine.asyncio.sleep") as mock_sleep:
        await _measure_with_retry(mock_metric, MagicMock(), max_attempts=3)

    assert mock_metric.measure.call_count == 2
    mock_sleep.assert_called_once_with(2.0)


@pytest.mark.asyncio
async def test_measure_with_retry_fails_all_attempts():
    """Raises after all attempts exhausted."""
    from eval_engine import _measure_with_retry

    mock_metric = MagicMock()
    mock_metric.measure.side_effect = Exception("persistent failure")

    with patch("eval_engine.asyncio.sleep") as mock_sleep:
        with pytest.raises(Exception, match="persistent failure"):
            await _measure_with_retry(mock_metric, MagicMock(), max_attempts=3)

    assert mock_metric.measure.call_count == 3
    assert mock_sleep.call_count == 2  # sleeps after attempt 1 and 2
    calls = [c.args[0] for c in mock_sleep.call_args_list]
    assert calls == [2.0, 4.0]
```

### Step 2: Run tests to confirm they fail

```bash
uv run pytest tests/test_eval_engine.py::test_measure_with_retry_succeeds_first_try tests/test_eval_engine.py::test_measure_with_retry_succeeds_second_try tests/test_eval_engine.py::test_measure_with_retry_fails_all_attempts -v
```

Expected: `ImportError: cannot import name '_measure_with_retry'`

### Step 3: Implement `_measure_with_retry` in eval_engine.py

Add after the `_build_llm_test_case` function (before `CONVERSATIONAL_METRICS`):

```python
async def _measure_with_retry(metric, test_case, max_attempts: int = 3) -> None:
    """Run metric.measure with exponential backoff retry.

    Sleeps 2s, 4s before retries 2 and 3. Raises on final failure.
    """
    for attempt in range(max_attempts):
        try:
            await asyncio.to_thread(metric.measure, test_case)
            return
        except Exception as e:
            if attempt == max_attempts - 1:
                raise
            delay = 2.0 * (2 ** attempt)
            logger.warning(
                f"{type(metric).__name__} attempt {attempt + 1} failed: {e}. "
                f"Retry in {delay}s"
            )
            await asyncio.sleep(delay)
```

Then in `evaluate_case`, replace:
```python
await asyncio.to_thread(metric.measure, test_case)
```
with:
```python
await _measure_with_retry(metric, test_case)
```

### Step 4: Update the existing error test to patch sleep

The existing `test_evaluate_case_metric_error` test has `mock_metric.measure.side_effect = Exception("API timeout")`. With retry, this now attempts 3 times (with 2+4=6s total sleep). Patch `asyncio.sleep` to keep the test fast:

```python
@pytest.mark.asyncio
async def test_evaluate_case_metric_error(mock_env):
    """Test graceful handling when a metric raises an error (after retries)."""
    mock_metric = MagicMock()
    mock_metric.measure.side_effect = Exception("API timeout")

    with (
        patch("eval_engine._get_judge_model", return_value=_mock_judge()),
        patch("eval_engine.METRIC_REGISTRY", {
            "answer_relevancy": lambda model, threshold: mock_metric,
        }),
        patch("eval_engine.asyncio.sleep"),  # skip retry delays
    ):
        from eval_engine import evaluate_case

        result = await evaluate_case(
            turns=[{"role": "user", "content": "Test"}],
            conversation=[
                {"role": "user", "content": "Test"},
                {"role": "assistant", "content": "Response"},
            ],
            expected_output="",
            context="",
            metric_names=["answer_relevancy"],
            threshold=0.5,
        )

    assert result["answer_relevancy"]["score"] == 0.0
    assert "Error" in result["answer_relevancy"]["reason"]
    assert result["answer_relevancy"]["passed"] is False
    assert mock_metric.measure.call_count == 3  # 3 attempts before giving up
```

### Step 5: Run all eval_engine tests

```bash
uv run pytest tests/test_eval_engine.py -v
```

Expected: All pass.

### Step 6: Commit

```bash
git add eval_engine.py tests/test_eval_engine.py
git commit -m "feat: add retry with exponential backoff for DeepEval metric calls"
```

---

## Task 2: Add per-metric jitter in eval_engine.py

**Files:**
- Modify: `eval_engine.py`
- Test: `tests/test_eval_engine.py`

### Step 1: Write the failing test

Add to `tests/test_eval_engine.py`:

```python
@pytest.mark.asyncio
async def test_evaluate_case_jitter_applied(mock_env):
    """Jitter sleep is called once per AI metric evaluated."""
    mock_metric = MagicMock()
    mock_metric.score = 0.8
    mock_metric.reason = "Good"

    with (
        patch("eval_engine._get_judge_model", return_value=_mock_judge()),
        patch("eval_engine.METRIC_REGISTRY", {
            "answer_relevancy": lambda model, threshold: mock_metric,
        }),
        patch("eval_engine.asyncio.sleep") as mock_sleep,
        patch("eval_engine.random.uniform", return_value=0.5) as mock_uniform,
    ):
        from eval_engine import evaluate_case

        await evaluate_case(
            turns=[{"role": "user", "content": "Test"}],
            conversation=[
                {"role": "user", "content": "Test"},
                {"role": "assistant", "content": "Response"},
            ],
            expected_output="",
            context="",
            metric_names=["answer_relevancy"],
            threshold=0.5,
        )

    mock_uniform.assert_called_once_with(0, 1.5)
    mock_sleep.assert_called_once_with(0.5)
```

### Step 2: Run test to confirm it fails

```bash
uv run pytest tests/test_eval_engine.py::test_evaluate_case_jitter_applied -v
```

Expected: FAIL — `random.uniform` never called.

### Step 3: Add jitter import and call in eval_engine.py

Add `import random` to the imports at the top of `eval_engine.py`.

In `evaluate_case`, just before `await _measure_with_retry(metric, test_case)`, add:

```python
await asyncio.sleep(random.uniform(0, 1.5))
await _measure_with_retry(metric, test_case)
```

The full try block in evaluate_case should then look like:

```python
try:
    if name in CONVERSATIONAL_METRICS:
        test_case = _build_conversational_test_case(
            turns, conversation, expected_output, context
        )
    else:
        test_case = _build_llm_test_case(
            turns, conversation, expected_output, context
        )

    await asyncio.sleep(random.uniform(0, 1.5))
    await _measure_with_retry(metric, test_case)

    results[name] = {
        "score": metric.score,
        "reason": getattr(metric, "reason", ""),
        "passed": metric.score >= threshold,
    }
    logger.debug(f"Metric {name}: {metric.score:.3f}")

except Exception as e:
    logger.error(f"Metric {name} failed: {e}")
    results[name] = {
        "score": 0.0,
        "reason": f"Error: {e}",
        "passed": False,
    }
```

### Step 4: Update other tests that patch asyncio.sleep

Any test that patches `eval_engine.asyncio.sleep` (the retry test from Task 1) may now see an extra call from jitter. Check and update if needed.

In `test_measure_with_retry_succeeds_second_try` and `test_measure_with_retry_fails_all_attempts`, the patch is on `eval_engine.asyncio.sleep` directly (used inside `_measure_with_retry`). These tests call `_measure_with_retry` directly, not `evaluate_case`, so jitter doesn't apply. They stay as-is.

### Step 5: Run all eval_engine tests

```bash
uv run pytest tests/test_eval_engine.py -v
```

Expected: All pass.

### Step 6: Commit

```bash
git add eval_engine.py tests/test_eval_engine.py
git commit -m "feat: add random jitter before AI metric calls to reduce Azure OAI rate limit spikes"
```

---

## Task 3: Add auto-polling on RunDetail page

**Files:**
- Modify: `web/pages/run_detail.py`

Note: Reflex state is hard to unit test in isolation. No new test file for this task — manual verification covers it.

### Step 1: Add `is_live` state var to `RunDetailState`

In `web/pages/run_detail.py`, in the `RunDetailState` class, add:

```python
is_live: bool = False
```

### Step 2: Add `_refresh_run_data` helper method

This does the actual DB read without touching `is_live`. Extract the core of `load_run` into a private sync method that updates state vars:

Add this private method to `RunDetailState` (above `load_run`):

```python
def _load_run_data(self, run_id: int) -> None:
    """Load run + results from DB into state. Does not touch is_live."""
    with rx.session() as session:
        run_obj = session.get(EvalRun, run_id)
        if not run_obj:
            return

        dataset = session.get(Dataset, run_obj.dataset_id)
        self.dataset_name = dataset.name if dataset else "Unknown"

        self.run = {
            "id": run_obj.id,
            "name": run_obj.name,
            "status": run_obj.status,
            "avg_score": (
                f"{run_obj.avg_score:.1%}"
                if run_obj.avg_score > 0 else "—"
            ),
            "progress": (
                f"{run_obj.completed_cases}/{run_obj.total_cases}"
            ),
            "error": run_obj.error,
            "created": run_obj.created_at.strftime("%d-%m-%Y %H:%M"),
        }

        result_rows = session.exec(
            select(EvalResult)
            .where(EvalResult.eval_run_id == run_id)
            .order_by(EvalResult.test_case_index)
        ).all()

        self.results = []
        for r in result_rows:
            scores_raw = (
                json.loads(r.scores_json) if r.scores_json else {}
            )

            score_parts = []
            score_detail_lines = []
            overall_color = "gray"
            for mname, data in scores_raw.items():
                val = (
                    data.get("score", 0)
                    if isinstance(data, dict) else 0
                )
                reason = (
                    data.get("reason", "")
                    if isinstance(data, dict) else ""
                )
                color = _color_for_score(val)
                score_parts.append(f"{mname}: {val:.0%}")
                detail = f"[{color}] {mname}: {val:.0%}"
                if reason:
                    detail += f"\n  {reason}"
                score_detail_lines.append(detail)
                if color == "red":
                    overall_color = "red"
                elif color == "yellow" and overall_color != "red":
                    overall_color = "yellow"
                elif overall_color == "gray":
                    overall_color = "green"

            scores_summary = " | ".join(score_parts)
            scores_detail = "\n\n".join(score_detail_lines)

            input_turns = (
                json.loads(r.input_json) if r.input_json else []
            )
            conv_lines = []
            for t in input_turns:
                role = (
                    "User" if t.get("role") == "user"
                    else "Assistant"
                )
                conv_lines.append(f"{role}: {t.get('content', '')}")
            conversation_text = "\n\n".join(conv_lines)

            raw_activities = (
                json.loads(r.activities_json) if r.activities_json else []
            )
            tool_activities = [
                a for a in raw_activities
                if a.get("type") not in (
                    "message", "end_of_conversation", "typing", None
                )
            ]
            if tool_activities:
                tool_lines = []
                for a in tool_activities:
                    atype = a.get("type", "?")
                    aname = a.get("name", "")
                    value = a.get("value") or {}

                    if aname == "DynamicPlanReceived":
                        steps = value.get("steps", [])
                        topics = [s.rsplit(".", 1)[-1] for s in steps]
                        tool_defs = value.get("toolDefinitions", [])
                        kinds = [
                            f"{d.get('displayName', '?')} ({d.get('toolKind', '?')})"
                            for d in tool_defs
                        ]
                        line = f"Plan → Topics: {', '.join(topics)}"
                        if kinds:
                            line += f"\n  Tools: {', '.join(kinds)}"

                    elif aname == "DynamicPlanStepTriggered":
                        topic = value.get("taskDialogId", "").rsplit(".", 1)[-1]
                        state = value.get("state", "?")
                        step_type = value.get("type", "?")
                        line = f"Step → {topic} [{step_type}] (state: {state})"

                    elif aname == "DynamicPlanStepBindUpdate":
                        topic = value.get("taskDialogId", "").rsplit(".", 1)[-1]
                        args = value.get("arguments", {})
                        line = f"Bind → {topic}"
                        if args:
                            line += f" (args: {args})"

                    else:
                        line = f"[{atype}] {aname}"
                        if value:
                            line += f"\n  {value}"

                    tool_lines.append(line)
                tool_calls_text = "\n".join(tool_lines)
            else:
                tool_calls_text = "No tool activity captured"

            self.results.append({
                "index": r.test_case_index,
                "num": str(r.test_case_index + 1),
                "passed": r.passed,
                "duration": f"{r.duration_seconds:.1f}s",
                "actual_output": r.actual_output,
                "expected_output": r.expected_output,
                "scores_summary": scores_summary,
                "scores_detail": scores_detail,
                "scores_color": overall_color,
                "conversation_text": conversation_text,
                "tool_calls_text": tool_calls_text,
            })

        self.expanded_result = -1
```

### Step 3: Rewrite `load_run` to use `_load_run_data` and start polling

Replace the existing `load_run` method body:

```python
def load_run(self) -> None:
    run_id_str = self.router.page.params.get("run_id", "0")
    try:
        run_id = int(run_id_str)
    except (ValueError, TypeError):
        return

    self._load_run_data(run_id)

    if self.run.get("status") == "running" and not self.is_live:
        self.is_live = True
        return RunDetailState.live_poll(run_id)
```

### Step 4: Add the `live_poll` background event

Add this method to `RunDetailState`:

```python
@rx.event(background=True)
async def live_poll(self, run_id: int) -> None:
    """Poll DB every 3s while run is still executing."""
    while True:
        await asyncio.sleep(3)
        async with self:
            self._load_run_data(run_id)
            if self.run.get("status") not in ("running", "pending"):
                self.is_live = False
                break
```

Add `import asyncio` to `run_detail.py` imports (it's not currently there).

### Step 5: Add a live indicator to the page header

In `_header_section()`, add a live pulse indicator next to the status badge when polling. Find this hstack in `_header_section`:

```python
rx.hstack(
    rx.heading(RunDetailState.run["name"], size="6"),
    status_badge(RunDetailState.run["status"]),
    rx.spacer(),
    ...
```

Add after `status_badge(...)`:

```python
rx.cond(
    RunDetailState.is_live,
    rx.badge("● Live", color_scheme="teal", variant="soft", size="1"),
),
```

### Step 6: Manual verification

Start the app, start an eval run with several cases, open Run Detail — verify:
- "● Live" badge appears while running
- Progress and results update every ~3s without page reload
- Badge disappears when run completes

```bash
uv run reflex run
```

### Step 7: Commit

```bash
git add web/pages/run_detail.py
git commit -m "feat: add live auto-polling on run detail page while run is executing"
```

---

## Task 4: Add run comparison view to Runs page

**Files:**
- Modify: `web/pages/runs.py`
- Test: `tests/test_run_comparison.py`

### Step 1: Write the failing tests

Create `tests/test_run_comparison.py`:

```python
"""Tests for run comparison state logic."""

import json
from unittest.mock import MagicMock, patch


def _make_run(run_id: int, name: str) -> MagicMock:
    r = MagicMock()
    r.id = run_id
    r.name = name
    r.avg_score = 0.75
    r.status = "completed"
    r.total_cases = 2
    r.completed_cases = 2
    r.error = ""
    return r


def _make_result(run_id: int, scores: dict) -> MagicMock:
    r = MagicMock()
    r.eval_run_id = run_id
    r.scores_json = json.dumps(scores)
    r.passed = all(v.get("passed", False) for v in scores.values())
    return r


def test_toggle_compare_select_adds_run():
    """Selecting a run adds it to compare_selected."""
    from web.pages.runs import RunState

    state = RunState()
    state.compare_selected = []
    state.toggle_compare(1)
    assert 1 in state.compare_selected


def test_toggle_compare_select_deselects_run():
    """Selecting an already-selected run removes it."""
    from web.pages.runs import RunState

    state = RunState()
    state.compare_selected = [1]
    state.toggle_compare(1)
    assert state.compare_selected == []


def test_toggle_compare_max_two():
    """Selecting a third run replaces the oldest selection."""
    from web.pages.runs import RunState

    state = RunState()
    state.compare_selected = [1, 2]
    state.toggle_compare(3)
    assert state.compare_selected == [2, 3]


def test_load_compare_data_structure():
    """compare_data has correct shape when two runs are loaded."""
    from web.pages.runs import RunState

    run_a = _make_run(1, "Run A")
    run_b = _make_run(2, "Run B")

    result_a1 = _make_result(1, {
        "answer_relevancy": {"score": 0.8, "passed": True},
    })
    result_a2 = _make_result(1, {
        "answer_relevancy": {"score": 0.6, "passed": True},
    })
    result_b1 = _make_result(2, {
        "answer_relevancy": {"score": 0.9, "passed": True},
    })
    result_b2 = _make_result(2, {
        "answer_relevancy": {"score": 0.7, "passed": True},
    })

    mock_session = MagicMock()
    mock_session.get.side_effect = lambda model, run_id: (
        run_a if run_id == 1 else run_b
    )
    mock_session.exec.return_value.all.side_effect = [
        [result_a1, result_a2],
        [result_b1, result_b2],
    ]

    state = RunState()
    state.compare_selected = [1, 2]

    with patch("web.pages.runs.rx.session") as mock_ctx:
        mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        state.load_compare_data()

    data = state.compare_data
    assert data["run_a"]["name"] == "Run A"
    assert data["run_b"]["name"] == "Run B"
    assert len(data["metrics"]) == 1
    metric = data["metrics"][0]
    assert metric["name"] == "answer_relevancy"
    assert abs(metric["a_score"] - 0.7) < 0.01   # avg of 0.8 + 0.6
    assert abs(metric["b_score"] - 0.8) < 0.01   # avg of 0.9 + 0.7
    assert abs(metric["delta"] - 0.1) < 0.01
```

### Step 2: Run tests to confirm they fail

```bash
uv run pytest tests/test_run_comparison.py -v
```

Expected: Import errors / missing state vars.

### Step 3: Add compare state vars and methods to RunState

In `web/pages/runs.py`, add to `RunState` class:

```python
# Compare mode
compare_selected: list[int] = []
show_compare: bool = False
compare_data: dict = {}

def toggle_compare(self, run_id: int) -> None:
    if run_id in self.compare_selected:
        self.compare_selected = [r for r in self.compare_selected if r != run_id]
    elif len(self.compare_selected) >= 2:
        # Drop oldest, add new
        self.compare_selected = [self.compare_selected[1], run_id]
    else:
        self.compare_selected = self.compare_selected + [run_id]

def load_compare_data(self) -> None:
    if len(self.compare_selected) != 2:
        return

    run_id_a, run_id_b = self.compare_selected[0], self.compare_selected[1]

    with rx.session() as session:
        run_a = session.get(EvalRun, run_id_a)
        run_b = session.get(EvalRun, run_id_b)
        if not run_a or not run_b:
            return

        results_a = session.exec(
            select(EvalResult).where(EvalResult.eval_run_id == run_id_a)
        ).all()
        results_b = session.exec(
            select(EvalResult).where(EvalResult.eval_run_id == run_id_b)
        ).all()

    def avg_scores(results):
        totals: dict[str, list[float]] = {}
        for r in results:
            scores = json.loads(r.scores_json) if r.scores_json else {}
            for mname, data in scores.items():
                if isinstance(data, dict):
                    totals.setdefault(mname, []).append(data.get("score", 0))
        return {m: sum(v) / len(v) for m, v in totals.items()}

    def pass_rate(results):
        if not results:
            return 0.0
        return sum(1 for r in results if r.passed) / len(results)

    scores_a = avg_scores(results_a)
    scores_b = avg_scores(results_b)
    all_metrics = sorted(set(scores_a) | set(scores_b))

    self.compare_data = {
        "run_a": {
            "id": run_a.id,
            "name": run_a.name,
            "pass_rate": f"{pass_rate(results_a):.0%}",
        },
        "run_b": {
            "id": run_b.id,
            "name": run_b.name,
            "pass_rate": f"{pass_rate(results_b):.0%}",
        },
        "metrics": [
            {
                "name": m,
                "a_score": scores_a.get(m, 0),
                "b_score": scores_b.get(m, 0),
                "delta": scores_b.get(m, 0) - scores_a.get(m, 0),
            }
            for m in all_metrics
        ],
    }

def open_compare(self) -> None:
    self.load_compare_data()
    self.show_compare = True

def close_compare(self) -> None:
    self.show_compare = False
```

### Step 4: Run the comparison tests

```bash
uv run pytest tests/test_run_comparison.py -v
```

Expected: All pass.

### Step 5: Add compare checkbox column to `runs_table()`

In `runs_table()`, add a checkbox as the first column header and cell:

Replace `runs_table()` function with:

```python
def runs_table() -> rx.Component:
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell(""),  # checkbox column
                rx.table.column_header_cell("Name"),
                rx.table.column_header_cell("Status"),
                rx.table.column_header_cell("Progress"),
                rx.table.column_header_cell("Avg Score"),
                rx.table.column_header_cell("Created"),
                rx.table.column_header_cell("Actions"),
            ),
        ),
        rx.table.body(
            rx.foreach(
                RunState.runs,
                lambda r: rx.table.row(
                    rx.table.cell(
                        rx.checkbox(
                            checked=RunState.compare_selected.contains(r["id"]),
                            on_change=lambda _: RunState.toggle_compare(r["id"]),
                        ),
                    ),
                    rx.table.cell(rx.text(r["name"], weight="medium")),
                    rx.table.cell(status_badge(r["status"])),
                    rx.table.cell(
                        rx.vstack(
                            rx.progress(value=r["progress_pct"], width="100px"),
                            rx.text(r["progress"], size="1", color_scheme="gray"),
                            spacing="1",
                        ),
                    ),
                    rx.table.cell(r["avg_score"]),
                    rx.table.cell(rx.text(r["created"], size="2", color_scheme="gray")),
                    rx.table.cell(
                        rx.hstack(
                            rx.link(
                                rx.button(
                                    rx.icon("eye", size=14),
                                    variant="ghost",
                                    size="1",
                                ),
                                href="/runs/" + r["id"].to(str),
                            ),
                            rx.button(
                                rx.icon("rotate-cw", size=14),
                                variant="ghost",
                                size="1",
                                on_click=RunState.rerun(r["id"]),
                            ),
                            spacing="1",
                        ),
                    ),
                ),
            ),
        ),
        width="100%",
    )
```

### Step 6: Add "Compare" button to page header and comparison modal

In `runs_page()`, replace the header hstack with:

```python
rx.hstack(
    page_header("Eval Runs", "Configure and run evaluations"),
    rx.spacer(),
    rx.cond(
        RunState.compare_selected.length() == 2,
        rx.button(
            rx.icon("git-compare", size=16),
            "Compare (2)",
            on_click=RunState.open_compare,
            variant="soft",
            size="2",
        ),
    ),
    rx.button(
        rx.icon("plus", size=16),
        "New Run",
        on_click=RunState.open_create_dialog,
        size="2",
    ),
    align="start",
    width="100%",
),
```

Then add a `compare_dialog()` function and call it in `runs_page()`:

```python
def compare_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(
                rx.hstack(
                    rx.text("Run Comparison"),
                    rx.spacer(),
                    rx.dialog.close(
                        rx.button(rx.icon("x", size=14), variant="ghost", size="1"),
                    ),
                ),
            ),
            rx.vstack(
                # Run names header
                rx.hstack(
                    rx.text("Metric", weight="medium", width="160px"),
                    rx.text(
                        RunState.compare_data["run_a"]["name"],
                        weight="medium",
                        flex="1",
                        color_scheme="blue",
                    ),
                    rx.text(
                        RunState.compare_data["run_b"]["name"],
                        weight="medium",
                        flex="1",
                        color_scheme="teal",
                    ),
                    rx.text("Delta", weight="medium", width="80px"),
                    width="100%",
                    spacing="2",
                ),
                rx.separator(width="100%"),
                # Metric rows
                rx.foreach(
                    RunState.compare_data["metrics"],
                    lambda m: rx.hstack(
                        rx.text(m["name"].to(str), size="2", width="160px"),
                        rx.text(
                            (m["a_score"] * 100).to(int).to(str) + "%",
                            size="2",
                            flex="1",
                        ),
                        rx.text(
                            (m["b_score"] * 100).to(int).to(str) + "%",
                            size="2",
                            flex="1",
                        ),
                        rx.cond(
                            m["delta"] > 0,
                            rx.badge(
                                "↑ " + (m["delta"] * 100).to(int).to(str) + "%",
                                color_scheme="green",
                                size="1",
                            ),
                            rx.cond(
                                m["delta"] < 0,
                                rx.badge(
                                    "↓ " + (m["delta"] * 100).to(int).to(str) + "%",
                                    color_scheme="red",
                                    size="1",
                                ),
                                rx.badge("—", color_scheme="gray", size="1"),
                            ),
                        ),
                        width="100%",
                        spacing="2",
                        align="center",
                    ),
                ),
                rx.separator(width="100%"),
                # Pass rate row
                rx.hstack(
                    rx.text("Pass Rate", size="2", weight="medium", width="160px"),
                    rx.text(
                        RunState.compare_data["run_a"]["pass_rate"],
                        size="2",
                        flex="1",
                    ),
                    rx.text(
                        RunState.compare_data["run_b"]["pass_rate"],
                        size="2",
                        flex="1",
                    ),
                    rx.text("", width="80px"),
                    width="100%",
                    spacing="2",
                ),
                spacing="2",
                width="100%",
            ),
            max_width="600px",
        ),
        open=RunState.show_compare,
        on_open_change=lambda open: rx.cond(
            open,
            RunState.open_compare(),
            RunState.close_compare(),
        ),
    )
```

Add `compare_dialog()` alongside `create_run_dialog()` in `runs_page()`.

### Step 7: Run all tests

```bash
uv run pytest tests/ -v
```

Expected: All pass.

### Step 8: Manual verification

Start the app, navigate to /runs, check two completed runs, click "Compare (2)" — verify the side-by-side metric table and delta column render correctly.

### Step 9: Commit

```bash
git add web/pages/runs.py tests/test_run_comparison.py
git commit -m "feat: add run comparison modal with per-metric delta view"
```

---

## Final Check

Run the full test suite one last time:

```bash
uv run pytest tests/ -v
```

Run the linter:

```bash
uv run ruff check . && uv run ruff format --check .
```

Fix any lint issues, then:

```bash
git add -p  # stage any lint fixes
git commit -m "fix: lint cleanup after improvements"
```
