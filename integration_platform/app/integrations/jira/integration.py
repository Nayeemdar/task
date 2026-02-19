"""
Jira Integration (Jira Cloud REST API v3).

Adding a new Jira action: add a method with @action decorator.
No other file needs to change.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

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


class JiraIntegration(BaseIntegration):

    service_name = "jira"
    service_description = "Atlassian Jira — issue tracking, project management, and sprint planning."
    auth_type = AuthType.BASIC

    def __init__(self, credentials: Dict[str, Any]):
        super().__init__(credentials)
        self._base_url: Optional[str] = credentials.get("base_url", "").rstrip("/")
        self._auth_header: Optional[str] = None

    # ── Authentication ────────────────────────────────────────────────────────

    async def authenticate(self) -> bool:
        email = self.credentials.get("email", "")
        api_token = self.credentials.get("api_token", "")
        if not (self._base_url and email and api_token):
            raise AuthenticationError("Jira requires base_url, email, and api_token")
        token = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self._auth_header = f"Basic {token}"
        # Verify connectivity
        client = await self._get_client()
        resp = await client.get(
            f"{self._base_url}/rest/api/3/myself",
            headers=self._headers(),
        )
        if resp.status_code == 200:
            logger.info("Jira authenticated as: %s", resp.json().get("displayName"))
            return True
        raise AuthenticationError(f"Jira auth check failed: {resp.status_code}")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": self._auth_header or "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        client = await self._get_client()
        url = f"{self._base_url}/rest/api/3/{path.lstrip('/')}"
        resp = await client.request(method, url, headers=self._headers(), **kwargs)
        resp.raise_for_status()
        return resp

    # ── Actions ───────────────────────────────────────────────────────────────

    @action(
        description="Create a new Jira issue",
        input_schema={
            "project_key": "string — e.g. ENG",
            "summary": "string",
            "description": "string (optional)",
            "issue_type": "string — Bug | Story | Task | Epic",
            "assignee_id": "string (optional) — Atlassian account id",
            "labels": "array of strings (optional)",
            "priority": "string (optional) — Highest | High | Medium | Low | Lowest",
            "custom_fields": "object (optional) — additional field values",
        },
    )
    async def create_issue(self, payload: Dict) -> ActionResult:
        body: Dict[str, Any] = {
            "fields": {
                "project": {"key": payload["project_key"]},
                "summary": payload["summary"],
                "issuetype": {"name": payload.get("issue_type", "Task")},
            }
        }
        if payload.get("description"):
            body["fields"]["description"] = {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": payload["description"]}]}],
            }
        if payload.get("assignee_id"):
            body["fields"]["assignee"] = {"accountId": payload["assignee_id"]}
        if payload.get("labels"):
            body["fields"]["labels"] = payload["labels"]
        if payload.get("priority"):
            body["fields"]["priority"] = {"name": payload["priority"]}
        if payload.get("custom_fields"):
            body["fields"].update(payload["custom_fields"])

        resp = await self._request("POST", "issue", json=body)
        return ActionResult.ok(resp.json())

    @action(description="Get a Jira issue by key", input_schema={"issue_key": "string — e.g. ENG-123"})
    async def get_issue(self, payload: Dict) -> ActionResult:
        resp = await self._request("GET", f"issue/{payload['issue_key']}")
        return ActionResult.ok(resp.json())

    @action(
        description="Update a Jira issue",
        input_schema={"issue_key": "string", "fields": "object — fields to update"},
    )
    async def update_issue(self, payload: Dict) -> ActionResult:
        await self._request("PUT", f"issue/{payload['issue_key']}", json={"fields": payload.get("fields", {})})
        return ActionResult.ok({"updated": True, "issue_key": payload["issue_key"]})

    @action(description="Transition (change status of) a Jira issue",
            input_schema={"issue_key": "string", "transition_id": "string"})
    async def transition_issue(self, payload: Dict) -> ActionResult:
        await self._request(
            "POST",
            f"issue/{payload['issue_key']}/transitions",
            json={"transition": {"id": str(payload["transition_id"])}},
        )
        return ActionResult.ok({"transitioned": True, "issue_key": payload["issue_key"]})

    @action(description="Add a comment to a Jira issue",
            input_schema={"issue_key": "string", "comment": "string"})
    async def add_comment(self, payload: Dict) -> ActionResult:
        body = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": payload["comment"]}]}],
            }
        }
        resp = await self._request("POST", f"issue/{payload['issue_key']}/comment", json=body)
        return ActionResult.ok(resp.json())

    @action(description="Search issues with JQL",
            input_schema={"jql": "string", "max_results": "integer (optional, default 50)"})
    async def search_issues(self, payload: Dict) -> ActionResult:
        params = {
            "jql": payload["jql"],
            "maxResults": payload.get("max_results", 50),
            "startAt": 0,
        }
        resp = await self._request("GET", "search", params=params)
        data = resp.json()
        return ActionResult.ok({"issues": data.get("issues", []), "total": data.get("total", 0)})

    @action(description="Get all sprints for a board",
            input_schema={"board_id": "integer", "state": "string (optional) — active | closed | future"})
    async def get_sprints(self, payload: Dict) -> ActionResult:
        client = await self._get_client()
        params: Dict[str, Any] = {}
        if payload.get("state"):
            params["state"] = payload["state"]
        resp = await client.get(
            f"{self._base_url}/rest/agile/1.0/board/{payload['board_id']}/sprint",
            headers=self._headers(),
            params=params,
        )
        resp.raise_for_status()
        return ActionResult.ok(resp.json())

    @action(description="Assign an issue to a user",
            input_schema={"issue_key": "string", "account_id": "string"})
    async def assign_issue(self, payload: Dict) -> ActionResult:
        await self._request(
            "PUT",
            f"issue/{payload['issue_key']}/assignee",
            json={"accountId": payload["account_id"]},
        )
        return ActionResult.ok({"assigned": True})

    @action(description="Delete a Jira issue", input_schema={"issue_key": "string"})
    async def delete_issue(self, payload: Dict) -> ActionResult:
        await self._request("DELETE", f"issue/{payload['issue_key']}")
        return ActionResult.ok({"deleted": True, "issue_key": payload["issue_key"]})

    @action(description="List all projects the authenticated user can see")
    async def list_projects(self, payload: Dict) -> ActionResult:
        resp = await self._request("GET", "project/search")
        data = resp.json()
        return ActionResult.ok({"projects": data.get("values", [])})

    # ── Triggers ──────────────────────────────────────────────────────────────

    @trigger(
        description="New issue created in a Jira project (polling)",
        trigger_type=TriggerType.POLLING,
        poll_interval_seconds=180,
    )
    async def on_new_issue(self, context: Dict) -> TriggerResult:
        project = context.get("project_key", "")
        since = context.get("cursor") or context.get("since", "")
        jql_parts = [f"project = {project}"] if project else []
        if since:
            jql_parts.append(f"created >= '{since}'")
        jql = " AND ".join(jql_parts) + " ORDER BY created DESC"
        result = await self.search_issues({"jql": jql, "max_results": 50})
        issues = result.data.get("issues", []) if result.success else []
        cursor = issues[0]["fields"]["created"] if issues else since
        return TriggerResult(events=issues, cursor=cursor, has_more=len(issues) == 50)

    @trigger(
        description="Inbound Jira webhook (issue created / updated / deleted)",
        trigger_type=TriggerType.WEBHOOK,
    )
    async def on_webhook(self, context: Dict) -> TriggerResult:
        return TriggerResult(events=[context], has_more=False)
