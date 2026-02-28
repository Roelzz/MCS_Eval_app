"""Copilot Studio Direct-to-Engine client.

Pure async functions — no LangChain dependency.
Adapted from Evals-plural/copilot_bridge.py patterns.
"""

import asyncio
import os

import aiohttp
from loguru import logger
from microsoft_agents.activity import ActivityTypes
from microsoft_agents.copilotstudio.client import ConnectionSettings, CopilotClient
from microsoft_agents.copilotstudio.client.power_platform_environment import (
    PowerPlatformEnvironment,
)

from auth import acquire_token


def _get_settings() -> ConnectionSettings:
    """Build ConnectionSettings from env vars."""
    return ConnectionSettings(
        environment_id=os.environ["COPILOT_ENVIRONMENT_ID"],
        agent_identifier=os.environ["COPILOT_AGENT_IDENTIFIER"],
    )


def _get_token() -> str:
    """Acquire a fresh token (MSAL caches internally)."""
    return acquire_token(
        tenant_id=os.environ["AZURE_AD_TENANT_ID"],
        client_id=os.environ["AZURE_AD_CLIENT_ID"],
        client_secret=os.environ.get("AZURE_AD_CLIENT_SECRET"),
    )


async def _ask_question(
    client: CopilotClient, text: str, conversation_id: str
) -> tuple[str, list[dict]]:
    """Send a message and collect the response text + raw activities."""
    responses: list[str] = []
    activities: list[dict] = []

    async for reply in client.ask_question(text, conversation_id):
        activity_record: dict = {
            "type": reply.type,
            "text": reply.text if hasattr(reply, "text") else None,
            "name": reply.name if hasattr(reply, "name") else None,
        }

        if reply.type == ActivityTypes.invoke:
            activity_record["value"] = reply.value if hasattr(reply, "value") else None
            activity_record["value_type"] = (
                reply.value_type if hasattr(reply, "value_type") else None
            )

        if reply.type == ActivityTypes.trace:
            activity_record["value"] = reply.value if hasattr(reply, "value") else None
            activity_record["label"] = reply.label if hasattr(reply, "label") else None

        if reply.type == ActivityTypes.event:
            activity_record["value"] = reply.value if hasattr(reply, "value") else None

        if hasattr(reply, "entities") and reply.entities:
            activity_record["entities"] = [
                e.model_dump() if hasattr(e, "model_dump") else str(e)
                for e in reply.entities
            ]

        if hasattr(reply, "channel_data") and reply.channel_data:
            activity_record["channel_data"] = reply.channel_data

        activities.append(activity_record)

        if reply.type == ActivityTypes.message and reply.text:
            responses.append(reply.text)
        elif reply.type == ActivityTypes.end_of_conversation:
            break

        if hasattr(reply, "suggested_actions") and reply.suggested_actions:
            actions = reply.suggested_actions.actions
            if actions:
                titles = [a.title for a in actions if hasattr(a, "title")]
                if titles:
                    responses.append(f"[Suggested: {', '.join(titles)}]")

    text_response = "\n".join(responses) if responses else "[No response from agent]"
    return text_response, activities


async def run_conversation(
    turns: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Send user turns to Copilot Studio, collect responses.

    Args:
        turns: List of {"role": "user", "content": "..."} dicts.

    Returns:
        Tuple of (conversation, all_activities).
        conversation: list of {"role": "user"|"assistant", "content": "..."} dicts.
        all_activities: list of raw activity dicts captured from the D2E stream.
    """
    settings = _get_settings()
    token = _get_token()
    client = CopilotClient(settings, token)

    conversation_id: str | None = None
    conversation: list[dict] = []
    all_activities: list[dict] = []

    logger.debug("Starting D2E conversation...")
    async for activity in client.start_conversation(True):
        if hasattr(activity, "conversation") and activity.conversation:
            conversation_id = activity.conversation.id
            logger.debug(f"Conversation started: {conversation_id}")
        if activity.type == ActivityTypes.message and activity.text:
            logger.debug(f"Greeting: {activity.text}")

    if not conversation_id:
        raise RuntimeError("Failed to start conversation — no conversation ID received")

    for turn in turns:
        if turn.get("role") != "user":
            continue

        user_text = turn["content"]
        conversation.append({"role": "user", "content": user_text})

        logger.debug(f"Sending: {user_text[:80]}...")
        response, activities = await _ask_question(client, user_text, conversation_id)
        conversation.append({"role": "assistant", "content": response})
        all_activities.extend(activities)
        logger.debug(f"Received: {response[:80]}...")

    return conversation, all_activities


def test_agent(message: str) -> str:
    """Quick single-message test — synchronous wrapper for settings page."""
    async def _run() -> str:
        settings = _get_settings()
        token = _get_token()
        client = CopilotClient(settings, token)

        conversation_id = None
        try:
            async for activity in client.start_conversation(True):
                if hasattr(activity, "conversation") and activity.conversation:
                    conversation_id = activity.conversation.id
        except Exception as e:
            # SDK throws away the response body — make a raw request to get it
            url = PowerPlatformEnvironment.get_copilot_studio_connection_url(settings=settings)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json={"emitStartConversationEvent": True},
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {token}",
                        "Accept": "text/event-stream",
                    },
                ) as resp:
                    body = await resp.text()
                    raise RuntimeError(
                        f"D2E returned HTTP {resp.status}\n"
                        f"URL: {url}\n"
                        f"Response: {body[:500]}"
                    ) from e

        if not conversation_id:
            raise RuntimeError("Failed to start conversation")

        text, _ = await _ask_question(client, message, conversation_id)
        return text

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, _run()).result()
    return asyncio.run(_run())
