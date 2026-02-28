"""DeepEval integration for eval metrics.

Uses Azure OpenAI GPT-4o as judge model.
"""

import asyncio
import os
from typing import Any

from deepeval.metrics import (
    AnswerRelevancyMetric,
    BiasMetric,
    ConversationCompletenessMetric,
    FaithfulnessMetric,
    GEval,
    HallucinationMetric,
    KnowledgeRetentionMetric,
    RoleAdherenceMetric,
    ToxicityMetric,
)
from deepeval.models import AzureOpenAIModel
from deepeval.test_case import ConversationalTestCase, LLMTestCase, LLMTestCaseParams, Turn
from loguru import logger


def _get_judge_model() -> AzureOpenAIModel:
    """Configure Azure OpenAI as the LLM judge."""
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
    return AzureOpenAIModel(
        model=deployment,
        deployment_name=deployment,
        base_url=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
    )


# Metric factory registry
METRIC_REGISTRY: dict[str, Any] = {
    "answer_relevancy": lambda model, threshold: AnswerRelevancyMetric(
        model=model, threshold=threshold
    ),
    "conversation_completeness": lambda model, threshold: ConversationCompletenessMetric(
        model=model, threshold=threshold
    ),
    "knowledge_retention": lambda model, threshold: KnowledgeRetentionMetric(
        model=model, threshold=threshold
    ),
    "role_adherence": lambda model, threshold: RoleAdherenceMetric(
        model=model, threshold=threshold
    ),
    "task_completion": lambda model, threshold: GEval(
        name="Task Completion",
        model=model,
        threshold=threshold,
        criteria=(
            "Determine whether the AI assistant successfully completed the user's requested task. "
            "Consider if the response directly addresses the user's need "
            "and provides a complete answer."
        ),
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.EXPECTED_OUTPUT,
        ],
    ),
    "hallucination": lambda model, threshold: HallucinationMetric(
        model=model, threshold=threshold
    ),
    "toxicity": lambda model, threshold: ToxicityMetric(
        model=model, threshold=threshold
    ),
    "bias": lambda model, threshold: BiasMetric(
        model=model, threshold=threshold
    ),
    "faithfulness": lambda model, threshold: FaithfulnessMetric(
        model=model, threshold=threshold
    ),
}


def _build_conversational_test_case(
    turns: list[dict],
    conversation: list[dict],
    expected_output: str = "",
    context: str = "",
) -> ConversationalTestCase:
    """Build a DeepEval ConversationalTestCase from conversation data."""
    deepeval_turns = []
    for msg in conversation:
        deepeval_turns.append(
            Turn(
                role=msg["role"],
                content=msg["content"],
            )
        )

    return ConversationalTestCase(
        turns=deepeval_turns,
    )


def _build_llm_test_case(
    turns: list[dict],
    conversation: list[dict],
    expected_output: str = "",
    context: str = "",
) -> LLMTestCase:
    """Build a DeepEval LLMTestCase for single-turn metrics."""
    user_input = turns[0]["content"] if turns else ""
    actual_output = ""
    for msg in reversed(conversation):
        if msg["role"] == "assistant":
            actual_output = msg["content"]
            break

    kwargs: dict[str, Any] = {
        "input": user_input,
        "actual_output": actual_output,
    }
    if expected_output:
        kwargs["expected_output"] = expected_output
    if context:
        kwargs["context"] = [context]
        kwargs["retrieval_context"] = [context]

    return LLMTestCase(**kwargs)


CONVERSATIONAL_METRICS = {
    "conversation_completeness",
    "knowledge_retention",
    "role_adherence",
}

CONTEXT_REQUIRED_METRICS = {"hallucination", "faithfulness"}


def _evaluate_topic_routing(
    activities: list[dict],
    expected_topic: str,
) -> dict:
    """Check if the agent routed to the expected topic."""
    if not expected_topic:
        return {"score": 1.0, "reason": "No expected topic specified", "passed": True}

    routed_topics: list[str] = []
    for a in activities:
        if a.get("name") == "DynamicPlanStepTriggered":
            value = a.get("value") or {}
            task_id = value.get("taskDialogId", "")
            if task_id:
                routed_topics.append(task_id)

    if not routed_topics:
        return {
            "score": 0.0,
            "reason": "No topic routing detected in activities",
            "passed": False,
        }

    expected_lower = expected_topic.lower()
    for topic in routed_topics:
        if expected_lower in topic.lower():
            short = topic.rsplit(".", 1)[-1]
            return {
                "score": 1.0,
                "reason": f"Routed to: {short}",
                "passed": True,
            }

    short_topics = [t.rsplit(".", 1)[-1] for t in routed_topics]
    return {
        "score": 0.0,
        "reason": f"Expected '{expected_topic}', got: {', '.join(short_topics)}",
        "passed": False,
    }


async def evaluate_case(
    turns: list[dict],
    conversation: list[dict],
    expected_output: str,
    context: str,
    metric_names: list[str],
    threshold: float = 0.5,
    activities: list[dict] | None = None,
    expected_topic: str = "",
) -> dict[str, dict]:
    """Run selected metrics on a test case.

    Returns:
        Dict mapping metric_name -> {"score": float, "reason": str, "passed": bool}
    """
    results: dict[str, dict] = {}
    model = None

    for name in metric_names:
        if name == "topic_routing":
            results[name] = _evaluate_topic_routing(activities or [], expected_topic)
            continue

        if name not in METRIC_REGISTRY:
            logger.warning(f"Unknown metric: {name}")
            continue

        if name in CONTEXT_REQUIRED_METRICS and not context:
            results[name] = {
                "score": 0.0,
                "reason": f"Metric '{name}' requires context in test case",
                "passed": False,
            }
            continue

        if model is None:
            model = _get_judge_model()
        metric = METRIC_REGISTRY[name](model, threshold)

        try:
            if name in CONVERSATIONAL_METRICS:
                test_case = _build_conversational_test_case(
                    turns, conversation, expected_output, context
                )
            else:
                test_case = _build_llm_test_case(
                    turns, conversation, expected_output, context
                )

            # DeepEval metrics have a measure() method
            await asyncio.to_thread(metric.measure, test_case)

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

    return results
