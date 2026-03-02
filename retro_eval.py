"""Retrospective eval runner for Copilot Studio conversation transcripts.

Fetches historical transcripts from Dataverse and runs deterministic Tier 1
eval metrics without re-invoking the agent. Also suggests new test cases
from discovered utterance patterns.

CLI usage:
    uv run python retro_eval.py
    uv run python retro_eval.py --since 2025-01-01 --top 200 --suggest-only
"""

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger

from dataverse_client import DataverseClient
from eval_engine import (
    _evaluate_exact_match,
    _evaluate_keyword_match,
    _evaluate_topic_routing,
)


@dataclass
class RetroTestCase:
    """A test case extracted from a historical transcript."""

    transcript_id: str
    conversation: list[dict]  # [{"role": "user"|"assistant", "content": str}]
    turns: list[dict]  # user-only turns
    intent_recognition: list[dict]  # from parse_transcript
    session_outcome: str  # Resolved / Escalated / Abandoned
    dialog_redirects: list[str]
    csat: float | None
    created_at: str | None


@dataclass
class RetroEvalResult:
    """Result of running Tier 1 metrics on a single retro test case."""

    transcript_id: str
    metric_results: dict[str, dict]  # metric_name -> {score, reason, passed}
    session_outcome: str
    csat: float | None
    conversation_length: int


@dataclass
class DatasetSuggestion:
    """A suggested test case for dataset improvement."""

    utterance: str  # The user's first message
    follow_up_turns: list[str]  # Additional user messages (for multi-turn)
    inferred_topic: str  # From intent recognition
    session_outcome: str
    source_transcript_id: str
    is_multi_turn: bool


def extract_test_case_from_transcript(
    transcript: dict,
    client: DataverseClient,
) -> RetroTestCase | None:
    """Convert a raw Dataverse transcript record into a RetroTestCase.

    Args:
        transcript: Raw record from Dataverse conversationtranscripts table.
        client: DataverseClient instance for parsing.

    Returns:
        RetroTestCase or None if the transcript has no usable conversation.
    """
    content = transcript.get("content", "")
    transcript_id = transcript.get("conversationtranscriptid", "")

    conversation = client.extract_conversation(content)
    if not conversation:
        logger.debug(f"Transcript {transcript_id}: no message activities found")
        return None

    parsed = client.parse_transcript(content)
    turns = [t for t in conversation if t["role"] == "user"]

    if not turns:
        return None

    return RetroTestCase(
        transcript_id=transcript_id,
        conversation=[{"role": t["role"], "content": t["content"]} for t in conversation],
        turns=[{"role": "user", "content": t["content"]} for t in turns],
        intent_recognition=parsed["intent_recognition"],
        session_outcome=parsed["session_info"]["outcome"],
        dialog_redirects=parsed["dialog_redirects"],
        csat=parsed["csat"],
        created_at=transcript.get("createdon"),
    )


def run_tier1_metrics(
    test_case: RetroTestCase,
    expected_topic: str = "",
    keywords_any: list[str] | None = None,
    keywords_all: list[str] | None = None,
    expected_output: str = "",
) -> dict[str, dict]:
    """Run applicable Tier 1 deterministic metrics on a retro test case.

    Metrics that run automatically (no configuration needed):
    - topic_routing: if intent_recognition data is present

    Metrics that run when configuration is provided:
    - exact_match: if expected_output is set
    - keyword_match_any: if keywords_any is set
    - keyword_match_all: if keywords_all is set

    Args:
        test_case: The retro test case to evaluate.
        expected_topic: Topic name expected in intent recognition.
        keywords_any: Keywords where at least one must appear in the last response.
        keywords_all: Keywords that must all appear in the last response.
        expected_output: Exact expected text for the last assistant response.

    Returns:
        Dict mapping metric_name -> {score, reason, passed}
    """
    results: dict[str, dict] = {}

    # Get last assistant response for text-based metrics
    actual_output = ""
    for msg in reversed(test_case.conversation):
        if msg["role"] == "assistant":
            actual_output = msg["content"]
            break

    # topic_routing — uses intent recognition data extracted from transcript
    if expected_topic or test_case.intent_recognition:
        # Build synthetic activities from parsed intent recognition
        activities = []
        for intent in test_case.intent_recognition:
            topic = intent.get("topic", "")
            if topic:
                activities.append(
                    {
                        "name": "DynamicPlanStepTriggered",
                        "value": {"taskDialogId": topic},
                    }
                )
        if activities or expected_topic:
            results["topic_routing"] = _evaluate_topic_routing(activities, expected_topic)

    if expected_output:
        results["exact_match"] = _evaluate_exact_match(actual_output, expected_output)

    if keywords_any:
        results["keyword_match_any"] = _evaluate_keyword_match(actual_output, keywords_any, "any")

    if keywords_all:
        results["keyword_match_all"] = _evaluate_keyword_match(actual_output, keywords_all, "all")

    return results


def suggest_dataset_cases(
    test_cases: list[RetroTestCase],
    existing_utterances: set[str] | None = None,
    min_confidence: float = 0.5,
) -> list[DatasetSuggestion]:
    """Identify utterances from transcripts as dataset improvement suggestions.

    Filters out:
    - Transcripts with no clear intent (confidence below min_confidence)
    - Utterances already present in the existing dataset

    Multi-turn conversations (>1 user turn) are flagged as multi_turn suggestions.

    Args:
        test_cases: Extracted test cases from transcripts.
        existing_utterances: Normalised utterances already in datasets (lowercased).
        min_confidence: Minimum intent confidence to include a suggestion.

    Returns:
        List of DatasetSuggestion instances.
    """
    seen_utterances: set[str] = set(existing_utterances or [])
    suggestions: list[DatasetSuggestion] = []

    for tc in test_cases:
        if not tc.turns:
            continue

        first_utterance = tc.turns[0]["content"]
        normalised = first_utterance.strip().lower()

        if normalised in seen_utterances:
            continue

        # Pick best inferred topic
        inferred_topic = ""
        if tc.intent_recognition:
            best = max(tc.intent_recognition, key=lambda x: x.get("score", 0.0))
            if best.get("score", 0.0) >= min_confidence:
                inferred_topic = best.get("topic", "")

        follow_ups = [t["content"] for t in tc.turns[1:]]

        suggestions.append(
            DatasetSuggestion(
                utterance=first_utterance,
                follow_up_turns=follow_ups,
                inferred_topic=inferred_topic,
                session_outcome=tc.session_outcome,
                source_transcript_id=tc.transcript_id,
                is_multi_turn=len(tc.turns) > 1,
            )
        )
        seen_utterances.add(normalised)

    logger.info(
        f"Generated {len(suggestions)} dataset suggestion(s) from {len(test_cases)} transcript(s)"
    )
    return suggestions


async def run_retro_evals(
    bot_guid: str,
    client: DataverseClient,
    since: datetime,
    top: int = 100,
    expected_topic: str = "",
    keywords_any: list[str] | None = None,
    keywords_all: list[str] | None = None,
) -> list[RetroEvalResult]:
    """Fetch transcripts and run Tier 1 metrics on each.

    Args:
        bot_guid: The bot's GUID to fetch transcripts for.
        client: Authenticated DataverseClient.
        since: Fetch transcripts created after this datetime.
        top: Max number of transcripts to process.
        expected_topic: Optional topic name for topic_routing metric.
        keywords_any: Optional keywords for keyword_match_any metric.
        keywords_all: Optional keywords for keyword_match_all metric.

    Returns:
        List of RetroEvalResult for each processable transcript.
    """
    transcripts = await client.fetch_transcripts(bot_guid, since, top=top)
    logger.info(f"Processing {len(transcripts)} transcript(s)")

    eval_results: list[RetroEvalResult] = []
    skipped = 0

    for t in transcripts:
        test_case = extract_test_case_from_transcript(t, client)
        if test_case is None:
            skipped += 1
            continue

        metric_results = run_tier1_metrics(
            test_case,
            expected_topic=expected_topic,
            keywords_any=keywords_any,
            keywords_all=keywords_all,
        )

        eval_results.append(
            RetroEvalResult(
                transcript_id=test_case.transcript_id,
                metric_results=metric_results,
                session_outcome=test_case.session_outcome,
                csat=test_case.csat,
                conversation_length=len(test_case.conversation),
            )
        )

    logger.info(
        f"Retro eval complete: {len(eval_results)} evaluated, {skipped} skipped (no messages)"
    )
    return eval_results


def _print_summary(eval_results: list[RetroEvalResult]) -> None:
    """Print a human-readable summary of retro eval results."""
    if not eval_results:
        print("No results.")
        return

    total = len(eval_results)
    outcome_counts: dict[str, int] = {}
    for r in eval_results:
        outcome_counts[r.session_outcome or "Unknown"] = (
            outcome_counts.get(r.session_outcome or "Unknown", 0) + 1
        )

    print(f"\n=== Retro Eval Summary ({total} transcripts) ===")
    print("\nSession outcomes:")
    for outcome, count in sorted(outcome_counts.items()):
        print(f"  {outcome}: {count}")

    # Per-metric pass rates
    metric_pass: dict[str, list[bool]] = {}
    for r in eval_results:
        for metric, result in r.metric_results.items():
            metric_pass.setdefault(metric, []).append(result["passed"])

    if metric_pass:
        print("\nMetric pass rates:")
        for metric, passes in sorted(metric_pass.items()):
            rate = sum(passes) / len(passes) * 100
            print(f"  {metric}: {rate:.1f}% ({sum(passes)}/{len(passes)})")

    csat_scores = [r.csat for r in eval_results if r.csat is not None]
    if csat_scores:
        avg_csat = sum(csat_scores) / len(csat_scores)
        print(f"\nAvg CSAT: {avg_csat:.2f} (n={len(csat_scores)})")


async def _cli_main() -> None:
    """CLI entry point for running retro evals."""
    import argparse

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Run retrospective evals on Dataverse transcripts")
    parser.add_argument(
        "--since",
        default="2025-01-01",
        help="Fetch transcripts created after this date (YYYY-MM-DD). Default: 2025-01-01",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=100,
        help="Max number of transcripts to fetch. Default: 100",
    )
    parser.add_argument(
        "--suggest-only",
        action="store_true",
        help="Only print dataset suggestions, skip eval metrics",
    )
    parser.add_argument(
        "--expected-topic",
        default="",
        help="Expected topic for topic_routing metric",
    )
    parser.add_argument(
        "--output",
        choices=["summary", "json"],
        default="summary",
        help="Output format. Default: summary",
    )
    args = parser.parse_args()

    bot_guid = os.environ.get("COPILOT_AGENT_IDENTIFIER", "")
    if not bot_guid:
        logger.error("COPILOT_AGENT_IDENTIFIER not set")
        return

    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)

    dv_client = DataverseClient(
        org_url=os.environ["DATAVERSE_ORG_URL"],
        tenant_id=os.environ["AZURE_AD_TENANT_ID"],
        client_id=os.environ["AZURE_AD_CLIENT_ID"],
        client_secret=os.environ["AZURE_AD_CLIENT_SECRET"],
    )

    try:
        transcripts = await dv_client.fetch_transcripts(bot_guid, since, top=args.top)
    except Exception as e:
        logger.error(f"Failed to fetch transcripts: {e}")
        return

    test_cases = []
    for t in transcripts:
        tc = extract_test_case_from_transcript(t, dv_client)
        if tc:
            test_cases.append(tc)

    if args.suggest_only:
        suggestions = suggest_dataset_cases(test_cases)
        if args.output == "json":
            print(
                json.dumps(
                    [
                        {
                            "utterance": s.utterance,
                            "follow_up_turns": s.follow_up_turns,
                            "inferred_topic": s.inferred_topic,
                            "session_outcome": s.session_outcome,
                            "source_transcript_id": s.source_transcript_id,
                            "is_multi_turn": s.is_multi_turn,
                        }
                        for s in suggestions
                    ],
                    indent=2,
                )
            )
        else:
            print(f"\n=== Dataset Suggestions ({len(suggestions)}) ===")
            for s in suggestions:
                tag = "[multi-turn]" if s.is_multi_turn else "[single-turn]"
                print(f"\n{tag} {s.utterance[:80]}")
                if s.inferred_topic:
                    print(f"  Topic: {s.inferred_topic}")
                print(f"  Outcome: {s.session_outcome or 'Unknown'}")
                if s.follow_up_turns:
                    print(f"  Follow-ups: {len(s.follow_up_turns)} more turns")
        return

    eval_results = []
    for tc in test_cases:
        metric_results = run_tier1_metrics(tc, expected_topic=args.expected_topic)
        eval_results.append(
            RetroEvalResult(
                transcript_id=tc.transcript_id,
                metric_results=metric_results,
                session_outcome=tc.session_outcome,
                csat=tc.csat,
                conversation_length=len(tc.conversation),
            )
        )

    if args.output == "json":
        print(
            json.dumps(
                [
                    {
                        "transcript_id": r.transcript_id,
                        "metric_results": r.metric_results,
                        "session_outcome": r.session_outcome,
                        "csat": r.csat,
                        "conversation_length": r.conversation_length,
                    }
                    for r in eval_results
                ],
                indent=2,
            )
        )
    else:
        _print_summary(eval_results)


if __name__ == "__main__":
    asyncio.run(_cli_main())
