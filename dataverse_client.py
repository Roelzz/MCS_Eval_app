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
import re
from datetime import datetime, timezone

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

import msal
from loguru import logger


DATAVERSE_SCOPE_TEMPLATE = "{org_url}/.default"


class DataverseClient:
    def __init__(
        self,
        org_url: str,
        tenant_id: str,
        client_id: str,
        client_secret: str = "",
        _prefetched_token: str = "",
    ) -> None:
        self.org_url = org_url.rstrip("/")
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = _prefetched_token or None

    async def _get_token(self) -> str:
        """Acquire a Dataverse API token.

        Returns the pre-fetched token if one was provided at construction,
        otherwise acquires one via client credentials flow.
        """
        if self._token:
            return self._token

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

    async def resolve_bot_guid(self, bot_identifier: str) -> str:
        """Return the bot's GUID, resolving from schema name if needed.

        If bot_identifier already looks like a UUID it is returned as-is.
        Otherwise a Dataverse lookup on the bots table is performed using
        schemaname as the filter. If that returns 403 (no permission to read
        the bots table), falls back to auto-detecting the GUID from transcripts.
        """
        if _UUID_RE.match(bot_identifier):
            return bot_identifier

        import httpx

        token = await self._get_token()
        url = (
            f"{self.org_url}/api/data/v9.2/bots"
            f"?$filter=schemaname eq '{bot_identifier}'"
            f"&$select=botid,name"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=30)

        if resp.status_code == 403:
            logger.warning(
                "403 on bots table for '{}' — falling back to transcript-based bot GUID detection",
                bot_identifier,
            )
            return await self._auto_detect_bot_guid()

        resp.raise_for_status()

        bots = resp.json().get("value", [])
        if not bots:
            raise RuntimeError(
                f"No bot found with schemaname '{bot_identifier}' in Dataverse"
            )

        bot_id = bots[0]["botid"]
        logger.info("Resolved '{}' → bot GUID {}", bot_identifier, bot_id)
        return bot_id

    async def _auto_detect_bot_guid(self) -> str:
        """Detect the bot GUID by sampling recent transcripts.

        Used as a fallback when the bots table returns 403. Fetches the 5 most
        recent transcripts and extracts unique _bot_conversationtranscriptid_value
        values. Raises RuntimeError if the result is ambiguous or empty.
        """
        import httpx

        token = await self._get_token()
        url = (
            f"{self.org_url}/api/data/v9.2/conversationtranscripts"
            f"?$top=5&$orderby=createdon desc"
            f"&$select=_bot_conversationtranscriptid_value"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=30)

        if resp.status_code == 403:
            raise RuntimeError(
                "Cannot resolve bot GUID: both the bots table and the unfiltered "
                "conversationtranscripts table returned 403. "
                "Set COPILOT_AGENT_IDENTIFIER to the bot UUID directly in your .env file. "
                "You can find the UUID in the Copilot Studio agent URL or Power Platform admin center."
            )

        resp.raise_for_status()

        records = resp.json().get("value", [])
        guids = {
            r["_bot_conversationtranscriptid_value"]
            for r in records
            if r.get("_bot_conversationtranscriptid_value")
        }

        if len(guids) == 1:
            guid = guids.pop()
            logger.warning(
                "Auto-detected bot GUID {} from transcripts. "
                "Set COPILOT_AGENT_IDENTIFIER={} to skip this lookup.",
                guid,
                guid,
            )
            return guid

        if not guids:
            raise RuntimeError(
                "Cannot auto-detect bot GUID: no recent transcripts found. "
                "Set COPILOT_AGENT_IDENTIFIER to the bot UUID directly."
            )

        raise RuntimeError(
            f"Multiple bots found in transcripts ({guids}). "
            "Set COPILOT_AGENT_IDENTIFIER to the bot UUID directly to select one."
        )

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

        bot_guid = await self.resolve_bot_guid(bot_guid)
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

    def extract_conversation(self, content_json) -> list[dict]:  # type: ignore[override]
        """Extract message turns from transcript content as a conversation list.

        Parses Bot Framework message activities from the content column of a
        conversationtranscript record.

        Args:
            content_json: The JSON string from the 'content' column, or an
                already-parsed list/dict (Dataverse OData sometimes pre-parses it).

        Returns:
            List of dicts with keys:
                role: "user" | "assistant"
                content: str — the message text
                timestamp: str | None
        """
        if not content_json:
            return []

        # Handle already-parsed content (OData may return it as list/dict, not string)
        if isinstance(content_json, (list, dict)):
            parsed = content_json
        else:
            try:
                parsed = json.loads(content_json)
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse transcript content: {e}")
                return []

        # Handle both flat array and wrapped object formats
        if isinstance(parsed, dict):
            activities = parsed.get("activities", parsed.get("value", []))
            if not isinstance(activities, list):
                logger.warning(f"Unexpected content dict structure, keys: {list(parsed.keys())[:5]}")
                return []
        elif isinstance(parsed, list):
            activities = parsed
        else:
            return []

        turns: list[dict] = []
        for activity in activities:
            if activity.get("type") != "message":
                continue

            text = activity.get("text", "") or activity.get("speak", "")
            if not text or not text.strip():
                continue

            from_field = activity.get("from") or {}
            raw_role = from_field.get("role", "")

            if raw_role == "user":
                role = "user"
            elif raw_role in ("bot", "skill"):
                role = "assistant"
            elif from_field.get("aadObjectId"):
                # Human users have AAD object IDs; bots don't
                role = "user"
            else:
                from_id = from_field.get("id", "").lower()
                if "user" in from_id:
                    role = "user"
                elif any(x in from_id for x in ("bot", "skill", "agent", "copilot")):
                    role = "assistant"
                elif activity.get("replyToId"):
                    # Bot replies reference the message they're replying to
                    role = "assistant"
                else:
                    role = "user"

            turns.append(
                {
                    "role": role,
                    "content": text.strip(),
                    "timestamp": activity.get("timestamp"),
                }
            )

        if not turns and activities:
            types = list({a.get("type", "unknown") for a in activities[:20]})
            logger.warning(
                f"No message turns extracted from {len(activities)} activities. "
                f"Activity types: {types}"
            )

        return turns

    _TOOL_KEYWORDS = ("action", "tool", "knowledge", "plugin", "connector", "invoke", "skill")

    def parse_transcript(self, content_json) -> dict:  # type: ignore[override]
        """Parse the content column of a conversationtranscript record.

        Args:
            content_json: The JSON string from the 'content' column.

        Returns:
            Dict with keys:
                intent_recognition: list of {"topic": str, "score": float}
                session_info: {"outcome": str}  # Resolved/Escalated/Abandoned
                dialog_redirects: list of str
                csat: float | None
                tools_used: list of str  # deduplicated tool/action/knowledge names
        """
        result: dict = {
            "intent_recognition": [],
            "session_info": {"outcome": ""},
            "dialog_redirects": [],
            "csat": None,
            "tools_used": [],
        }

        if not content_json:
            return result

        # Handle already-parsed content (same as extract_conversation)
        if isinstance(content_json, (list, dict)):
            parsed = content_json
        else:
            try:
                parsed = json.loads(content_json)
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse transcript content: {e}")
                return result

        if isinstance(parsed, dict):
            activities = parsed.get("activities", parsed.get("value", []))
            if not isinstance(activities, list):
                return result
        elif isinstance(parsed, list):
            activities = parsed
        else:
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

            elif value_type and any(kw in value_type.lower() for kw in self._TOOL_KEYWORDS):
                value = activity.get("value") or {}
                name = (
                    value.get("name")
                    or value.get("actionName")
                    or value.get("toolName")
                    or value_type
                )
                if name and name not in result["tools_used"]:
                    result["tools_used"].append(name)

            # Capture invoke-type activities by their activity name
            if activity.get("type") == "invoke":
                name = activity.get("name", "")
                if name and name not in result["tools_used"]:
                    result["tools_used"].append(name)

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
