# Targeted Improvements — Design

**Date**: 2026-03-02
**Approach**: A — four contained fixes, no new dependencies, no DB/schema changes

## Background

The evals platform is a mature MVP for evaluating Copilot Studio agents. Four pain points identified:
1. Silent metric failures pollute results (reliability)
2. No live feedback during runs (visibility)
3. No way to compare runs for regressions (analysis)
4. Concurrent DeepEval calls spike Azure OAI and trigger 429s (scale)

---

## Fix 1 — Retry with backoff (`eval_engine.py`)

Wrap each `asyncio.to_thread(metric.measure, test_case)` in a retry loop.
- 3 attempts, exponential backoff: 2s, 4s, 8s
- On final failure, raise and let the outer `except` in `evaluate_case` handle it (existing error result)
- No new imports — pure `asyncio.sleep`

```python
async def _measure_with_retry(metric, test_case, max_attempts=3):
    for attempt in range(max_attempts):
        try:
            await asyncio.to_thread(metric.measure, test_case)
            return
        except Exception as e:
            if attempt == max_attempts - 1:
                raise
            delay = 2.0 * (2 ** attempt)
            logger.warning(f"{type(metric).__name__} attempt {attempt+1} failed: {e}. Retry in {delay}s")
            await asyncio.sleep(delay)
```

Replace `await asyncio.to_thread(metric.measure, test_case)` with `await _measure_with_retry(metric, test_case)`.

---

## Fix 2 — Auto-polling on RunDetail (`web/pages/run_detail.py`)

Use `rx.moment` (already available in Reflex) or a conditional `rx.cond` + `rx.call_script` approach.
Simpler: add `poll_interval` state var. When `run["status"] == "running"`, render an invisible component that triggers `load_run` via `rx.el.div` with a JavaScript `setInterval`.

Cleaner option: Reflex's `@rx.event(background=True)` polling loop:
- Add `is_live: bool = False` to `RunDetailState`
- On `load_run`, if status is `running`, set `is_live = True` and start background polling loop
- Loop calls `load_run` every 3s until status != `running`
- On page unload or status change, set `is_live = False` to stop

---

## Fix 3 — Run comparison view (`web/pages/runs.py`)

Add to `RunState`:
- `compare_selected: list[int] = []` — IDs of runs selected for comparison (max 2)
- `show_compare: bool = False`
- `compare_data: dict = {}` — loaded comparison data

UI changes:
- Add checkbox column to `runs_table()`
- Show "Compare (2)" button in page header when 2 runs selected
- Comparison modal: table with metric | Run A avg | Run B avg | delta (color-coded ↑↓)
- Pass rate row at bottom

---

## Fix 4 — Jitter on concurrent DeepEval calls (`eval_engine.py`)

Add random jitter before each metric measure call to spread Azure OAI requests:

```python
import random
await asyncio.sleep(random.uniform(0, 1.5))
await _measure_with_retry(metric, test_case)
```

This pairs with the existing semaphore to smooth out token-per-minute spikes.

---

## File Impact

| File | Changes |
|------|---------|
| `eval_engine.py` | `_measure_with_retry()` helper + jitter (fixes 1 & 4) |
| `web/pages/run_detail.py` | Background polling loop while status=running (fix 2) |
| `web/pages/runs.py` | Compare selection + comparison modal (fix 3) |

## Constraints

- No new dependencies
- No DB schema changes
- No Docker/port changes
- No LLM model downgrades
