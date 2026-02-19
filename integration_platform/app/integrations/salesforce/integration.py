"""
Salesforce Integration.

Covers:
  Triggers: webhook (Platform Events / outbound messages), polling
  Actions : CRUD on standard + custom objects, SOQL query, bulk upsert

Adding a NEW Salesforce action:  just add a method decorated with @action.
No router / infra changes needed.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import httpx

from app.core.base_integration import (
    ActionResult,
    AuthType,
    BaseIntegration,
    AuthenticationError,
    TriggerResult,
    TriggerType,
    action,
    trigger,
)

logger = logging.getLogger(__name__)

# Standard Salesforce objects that can be addressed generically
SF_STANDARD_OBJECTS = [
    "Lead", "Contact", "Account", "Opportunity", "Case",
    "Task", "Event", "Campaign", "Contract", "Quote",
]


class SalesforceIntegration(BaseIntegration):
    """
    Single entry point for all Salesforce operations.

    Authentication: OAuth2 username-password flow.
    Every method decorated with @action is automatically exposed as
    POST /api/v1/integrations/salesforce/actions/{method_name}
    """

    service_name = "salesforce"
    service_description = (
        "Salesforce CRM — create, read, update and delete records, "
        "run SOQL queries, and receive Platform Events."
    )
    auth_type = AuthType.OAUTH2

    def __init__(self, credentials: Dict[str, Any]):
        super().__init__(credentials)
        self._access_token: Optional[str] = None
        self._instance_url: Optional[str] = None
        self._api_version = credentials.get("api_version", "v59.0")

    # ── Authentication ────────────────────────────────────────────────────────

    async def authenticate(self) -> bool:
        creds = self.credentials
        token_url = urljoin(
            creds.get("instance_url", "https://login.salesforce.com"),
            "/services/oauth2/token",
        )
        params = {
            "grant_type": "password",
            "client_id": creds.get("client_id", ""),
            "client_secret": creds.get("client_secret", ""),
            "username": creds.get("username", ""),
            "password": (creds.get("password", "") + creds.get("security_token", "")),
        }
        client = await self._get_client()
        try:
            resp = await client.post(token_url, data=params)
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data["access_token"]
            self._instance_url = data["instance_url"]
            logger.info("Salesforce authenticated. Instance: %s", self._instance_url)
            return True
        except httpx.HTTPStatusError as exc:
            raise AuthenticationError(f"Salesforce auth failed: {exc.response.text}") from exc

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    def _api_url(self, path: str) -> str:
        return f"{self._instance_url}/services/data/{self._api_version}/{path.lstrip('/')}"

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        client = await self._get_client()
        url = self._api_url(path)
        resp = await client.request(method, url, headers=self._headers(), **kwargs)
        if resp.status_code == 401:
            # Re-authenticate once on token expiry
            await self.authenticate()
            resp = await client.request(method, url, headers=self._headers(), **kwargs)
        resp.raise_for_status()
        return resp

    # ─────────────────────────────── ACTIONS ─────────────────────────────────

    @action(
        description="Create any Salesforce object record (Lead, Contact, Account, …)",
        input_schema={
            "object_type": "string — Salesforce API object name e.g. Lead",
            "fields": "object — field/value pairs to set",
        },
    )
    async def create_record(self, payload: Dict) -> ActionResult:
        obj = payload.get("object_type", "Lead")
        fields = payload.get("fields", {})
        resp = await self._request("POST", f"sobjects/{obj}/", json=fields)
        data = resp.json()
        return ActionResult.ok(data, metadata={"object_type": obj})

    @action(
        description="Retrieve a Salesforce record by Id",
        input_schema={
            "object_type": "string",
            "record_id": "string — 15 or 18-char Salesforce Id",
            "fields": "array of strings (optional) — fields to return",
        },
    )
    async def get_record(self, payload: Dict) -> ActionResult:
        obj = payload["object_type"]
        record_id = payload["record_id"]
        fields_param = ",".join(payload["fields"]) if payload.get("fields") else ""
        path = f"sobjects/{obj}/{record_id}"
        if fields_param:
            path += f"?fields={fields_param}"
        resp = await self._request("GET", path)
        return ActionResult.ok(resp.json())

    @action(
        description="Update a Salesforce record",
        input_schema={
            "object_type": "string",
            "record_id": "string",
            "fields": "object — fields to update",
        },
    )
    async def update_record(self, payload: Dict) -> ActionResult:
        obj = payload["object_type"]
        record_id = payload["record_id"]
        fields = payload.get("fields", {})
        await self._request("PATCH", f"sobjects/{obj}/{record_id}", json=fields)
        return ActionResult.ok({"updated": True, "record_id": record_id})

    @action(
        description="Delete a Salesforce record",
        input_schema={"object_type": "string", "record_id": "string"},
    )
    async def delete_record(self, payload: Dict) -> ActionResult:
        obj = payload["object_type"]
        record_id = payload["record_id"]
        await self._request("DELETE", f"sobjects/{obj}/{record_id}")
        return ActionResult.ok({"deleted": True, "record_id": record_id})

    @action(
        description="Run a SOQL query and return all matching records",
        input_schema={"query": "string — SOQL query e.g. SELECT Id, Name FROM Lead LIMIT 10"},
    )
    async def query(self, payload: Dict) -> ActionResult:
        soql = payload["query"]
        resp = await self._request("GET", "query/", params={"q": soql})
        data = resp.json()
        records = data.get("records", [])

        # Auto-paginate through nextRecordsUrl
        while next_url := data.get("nextRecordsUrl"):
            client = await self._get_client()
            resp = await client.get(
                f"{self._instance_url}{next_url}", headers=self._headers()
            )
            resp.raise_for_status()
            data = resp.json()
            records.extend(data.get("records", []))

        return ActionResult.ok(
            {"records": records, "total_size": len(records)},
            metadata={"has_more": False},
        )

    @action(
        description="Upsert a record using an external Id field",
        input_schema={
            "object_type": "string",
            "external_id_field": "string — e.g. Employee_ID__c",
            "external_id_value": "string",
            "fields": "object",
        },
    )
    async def upsert_record(self, payload: Dict) -> ActionResult:
        obj = payload["object_type"]
        ext_field = payload["external_id_field"]
        ext_value = payload["external_id_value"]
        fields = payload.get("fields", {})
        resp = await self._request(
            "PATCH", f"sobjects/{obj}/{ext_field}/{ext_value}", json=fields
        )
        created = resp.status_code == 201
        data = resp.json() if resp.content else {}
        return ActionResult.ok({"created": created, **data})

    @action(
        description="Search Salesforce using SOSL (cross-object full-text search)",
        input_schema={"search": "string — SOSL e.g. FIND {Acme} IN ALL FIELDS RETURNING Account"},
    )
    async def search(self, payload: Dict) -> ActionResult:
        sosl = payload["search"]
        resp = await self._request("GET", "search/", params={"q": sosl})
        return ActionResult.ok(resp.json())

    @action(
        description="Describe a Salesforce object — returns all fields and metadata",
        input_schema={"object_type": "string"},
    )
    async def describe_object(self, payload: Dict) -> ActionResult:
        obj = payload["object_type"]
        resp = await self._request("GET", f"sobjects/{obj}/describe")
        return ActionResult.ok(resp.json())

    @action(
        description="Retrieve recently modified records for a given object",
        input_schema={
            "object_type": "string",
            "since": "ISO-8601 datetime string",
            "limit": "integer (optional, default 100)",
        },
    )
    async def get_recent_changes(self, payload: Dict) -> ActionResult:
        obj = payload["object_type"]
        since = payload.get("since", "")
        limit = payload.get("limit", 100)
        soql = (
            f"SELECT Id, LastModifiedDate FROM {obj} "
            f"WHERE LastModifiedDate >= {since} "
            f"ORDER BY LastModifiedDate DESC LIMIT {limit}"
        )
        return await self.query({"query": soql})

    @action(
        description="Subscribe to a Salesforce Platform Event channel (returns subscription URL)",
        input_schema={"channel": "string — e.g. /event/MyEvent__e"},
    )
    async def subscribe_platform_event(self, payload: Dict) -> ActionResult:
        channel = payload["channel"]
        # In practice this would set up a CometD subscription; we return the endpoint info
        return ActionResult.ok(
            {
                "channel": channel,
                "streaming_url": f"{self._instance_url}/cometd/{self._api_version}",
                "note": "Use the CometD client with the access_token to subscribe",
            }
        )

    # ─────────────────────────────── TRIGGERS ────────────────────────────────

    @trigger(
        description="New or updated Salesforce record (polling)",
        trigger_type=TriggerType.POLLING,
        poll_interval_seconds=300,
    )
    async def on_record_change(self, context: Dict) -> TriggerResult:
        """
        Polls for recently modified records on a configured object.
        Requires context: {"object_type": "...", "since": "ISO datetime"}
        """
        obj = context.get("object_type", "Lead")
        since = context.get("cursor") or context.get("since", "1970-01-01T00:00:00Z")
        result = await self.get_recent_changes({"object_type": obj, "since": since})
        records = result.data.get("records", []) if result.success else []
        cursor = records[-1]["LastModifiedDate"] if records else since
        return TriggerResult(events=records, cursor=cursor, has_more=len(records) == 100)

    @trigger(
        description="Inbound Salesforce outbound message or Platform Event webhook",
        trigger_type=TriggerType.WEBHOOK,
    )
    async def on_webhook(self, context: Dict) -> TriggerResult:
        """Processes an already-validated inbound webhook payload."""
        return TriggerResult(events=[context], has_more=False)
