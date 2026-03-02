"""Dataverse transcript client for Copilot Studio session enrichment.

Fetches conversation transcripts from the Dataverse conversationtranscripts table
and parses activity data for intent recognition, session outcomes, dialog redirects,
and CSAT scores.

Note: Dataverse transcripts have a ~30-minute write delay after a conversation ends.
This client is for historical/batch analysis, not live eval enrichment.

CLI usage:
    uv run python dataverse_client.py
"""

import asyncio
import json
import os
from datetime import datetime, timezone

import msal
from loguru import logger


DATAVERSE_SCOPE_TEMPLATE = "{org_url}/.default"


class DataverseClient:
    def __init__(
        self,
        org_url: str,
        tenant_id: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self.org_url = org_url.rstrip("/")
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None

    async def _get_token(self) -> str:
        """Acquire a Dataverse API token via client credentials."""
        scope = DATAVERSE_SCOPE_TEMPLATE.format(org_url=self.org_url)
        authority = f"https://login.microsoftonline.com/{self.tenant_id}"

        app = msal.ConfidentialClientApplication(
            self.client_id,
            authority=authority,
            client_credential=self.client_secret,
        )

        result = await asyncio.to_thread(app.acquire_token_for_client, scopes=[scope])

        if "access_token" not in result:
            error = result.get("error_description", result.get("error", "Unknown error"))
            raise RuntimeError(f"Dataverse token acquisition failed: {error}")

        logger.info("Dataverse token acquired")
        return result["access_token"]

    async def fetch_transcripts(
        self,
        bot_guid: str,
        since: datetime,
        top: int = 100,
    ) -> list[dict]:
        """Fetch conversation transcripts for a bot since a given datetime.

        Args:
            bot_guid: The bot's GUID (conversationtranscriptid value).
            since: Only return transcripts created after this datetime (UTC).
            top: Maximum number of records to return.

        Returns:
            List of raw transcript records from Dataverse.
        """
        import httpx

        token = await self._get_token()
        since_str = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        url = (
            f"{self.org_url}/api/data/v9.2/conversationtranscripts"
            f"?$filter=_bot_conversationtranscriptid_value eq '{bot_guid}'"
            f" and createdon gt {since_str}"
            f"&$top={top}"
            f"&$orderby=createdon desc"
        )

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=30)
            response.raise_for_status()

        data = response.json()
        records = data.get("value", [])
        logger.info(f"Fetched {len(records)} transcript(s) from Dataverse")
        return records

    def extract_conversation(self, content_json: str) -> list[dict]:
        """Extract message turns from transcript content as a conversation list.

        Parses Bot Framework message activities from the content column of a
        conversationtranscript record.

        Args:
            content_json: The JSON string from the 'content' column.

        Returns:
            List of dicts with keys:
                role: "user" | "assistant"
                content: str — the message text
                timestamp: str | None
        """
        if not content_json:
            return []

        try:
            activities = json.loads(content_json)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to parse transcript content for conversation: {e}")
            return []

        if not isinstance(activities, list):
            return []

        turns: list[dict] = []
        for activity in activities:
            if activity.get("type") != "message":
                continue

            text = activity.get("text", "")
            if not text or not text.strip():
                continue

            from_field = activity.get("from") or {}
            # Bot Framework: "user" for humans, "bot" for the agent
            raw_role = from_field.get("role", "")
            if raw_role == "user":
                role = "user"
            elif raw_role in ("bot", "skill"):
                role = "assistant"
            else:
                # Fall back: check from.id — bots often have 'bot' in their id
                from_id = from_field.get("id", "").lower()
                role = "user" if ("user" in from_id or not from_id) else "assistant"

            turns.append(
                {
                    "role": role,
                    "content": text.strip(),
                    "timestamp": activity.get("timestamp"),
                }
            )

        return turns

    def parse_transcript(self, content_json: str) -> dict:
        """Parse the content column of a conversationtranscript record.

        Args:
            content_json: The JSON string from the 'content' column.

        Returns:
            Dict with keys:
                intent_recognition: list of {"topic": str, "score": float}
                session_info: {"outcome": str}  # Resolved/Escalated/Abandoned
                dialog_redirects: list of str
                csat: float | None
        """
        result: dict = {
            "intent_recognition": [],
            "session_info": {"outcome": ""},
            "dialog_redirects": [],
            "csat": None,
        }

        if not content_json:
            return result

        try:
            activities = json.loads(content_json)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to parse transcript content: {e}")
            return result

        if not isinstance(activities, list):
            return result

        for activity in activities:
            value_type = activity.get("valueType", "")

            if value_type == "IntentRecognition":
                value = activity.get("value") or {}
                topic = value.get("topicName", value.get("intent", ""))
                score = float(value.get("confidence", value.get("score", 0.0)))
                if topic:
                    result["intent_recognition"].append({"topic": topic, "score": score})

            elif value_type == "SessionInfo":
                value = activity.get("value") or {}
                outcome = value.get("outcome", value.get("sessionOutcome", ""))
                result["session_info"]["outcome"] = outcome

            elif value_type == "DialogRedirect":
                value = activity.get("value") or {}
                target = value.get("targetDialogId", value.get("dialogId", ""))
                if target:
                    result["dialog_redirects"].append(target)

            elif value_type == "CSATSurveyResponse":
                value = activity.get("value") or {}
                raw = value.get("rating", value.get("csatScore"))
                if raw is not None:
                    try:
                        result["csat"] = float(raw)
                    except (TypeError, ValueError):
                        pass

        return result


def _client_from_env() -> DataverseClient:
    return DataverseClient(
        org_url=os.environ["DATAVERSE_ORG_URL"],
        tenant_id=os.environ["AZURE_AD_TENANT_ID"],
        client_id=os.environ["AZURE_AD_CLIENT_ID"],
        client_secret=os.environ["AZURE_AD_CLIENT_SECRET"],
    )


async def _cli_main() -> None:
    """Print recent transcripts to stdout. For manual testing."""
    from dotenv import load_dotenv

    load_dotenv()

    bot_guid = os.environ.get("COPILOT_AGENT_IDENTIFIER", "")
    if not bot_guid:
        logger.error("COPILOT_AGENT_IDENTIFIER not set")
        return

    client = _client_from_env()
    since = datetime(2025, 1, 1, tzinfo=timezone.utc)

    try:
        transcripts = await client.fetch_transcripts(bot_guid, since, top=5)
        for t in transcripts:
            content = t.get("content", "")
            parsed = client.parse_transcript(content)
            print(json.dumps({"raw_id": t.get("conversationtranscriptid"), **parsed}, indent=2))
    except Exception as e:
        logger.error(f"Failed to fetch transcripts: {e}")


if __name__ == "__main__":
    asyncio.run(_cli_main())
