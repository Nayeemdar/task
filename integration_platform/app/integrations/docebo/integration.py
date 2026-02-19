"""
Docebo LMS Integration.

Auth: OAuth2 client_credentials flow.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

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


class DoceboIntegration(BaseIntegration):

    service_name = "docebo"
    service_description = (
        "Docebo LMS — manage learners, courses, enrollments, and training completion data."
    )
    auth_type = AuthType.OAUTH2

    def __init__(self, credentials: Dict[str, Any]):
        super().__init__(credentials)
        self._base_url: str = credentials.get("base_url", "").rstrip("/")
        self._access_token: Optional[str] = None

    # ── Authentication ────────────────────────────────────────────────────────

    async def authenticate(self) -> bool:
        creds = self.credentials
        if not (self._base_url and creds.get("client_id")):
            raise AuthenticationError("Docebo requires base_url, client_id, and client_secret")

        client = await self._get_client()
        resp = await client.post(
            f"{self._base_url}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "scope": "api",
            },
        )
        if resp.status_code == 200:
            self._access_token = resp.json()["access_token"]
            logger.info("Docebo authenticated")
            return True
        raise AuthenticationError(f"Docebo auth failed: {resp.status_code} {resp.text}")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        client = await self._get_client()
        url = f"{self._base_url}/api/v1/{path.lstrip('/')}"
        resp = await client.request(method, url, headers=self._headers(), **kwargs)
        if resp.status_code == 401:
            await self.authenticate()
            resp = await client.request(method, url, headers=self._headers(), **kwargs)
        resp.raise_for_status()
        return resp

    # ── Actions ───────────────────────────────────────────────────────────────

    @action(
        description="Get all users or search by email / username",
        input_schema={
            "search": "string (optional) — filter by name or email",
            "page": "integer (optional)",
            "page_size": "integer (optional, max 200)",
        },
    )
    async def get_users(self, payload: Dict) -> ActionResult:
        params: Dict[str, Any] = {
            "page": payload.get("page", 1),
            "page_size": payload.get("page_size", 100),
        }
        if payload.get("search"):
            params["search_text"] = payload["search"]
        resp = await self._request("GET", "manage/v1/user", params=params)
        return ActionResult.ok(resp.json())

    @action(
        description="Create a new Docebo user",
        input_schema={
            "username": "string",
            "email": "string",
            "first_name": "string",
            "last_name": "string",
            "password": "string (optional — if omitted, invitation email is sent)",
            "role": "string (optional) — user | poweruser | superadmin",
            "branch_id": "integer (optional)",
        },
    )
    async def create_user(self, payload: Dict) -> ActionResult:
        body: Dict[str, Any] = {
            "username": payload["username"],
            "email": payload["email"],
            "firstname": payload["first_name"],
            "lastname": payload["last_name"],
        }
        if payload.get("password"):
            body["password"] = payload["password"]
        if payload.get("role"):
            body["role"] = payload["role"]
        if payload.get("branch_id"):
            body["branches"] = [{"branch_id": payload["branch_id"]}]
        resp = await self._request("POST", "manage/v1/user", json=body)
        return ActionResult.ok(resp.json())

    @action(
        description="Update an existing Docebo user",
        input_schema={"user_id": "integer", "fields": "object — fields to update"},
    )
    async def update_user(self, payload: Dict) -> ActionResult:
        resp = await self._request(
            "PUT", f"manage/v1/user/{payload['user_id']}", json=payload.get("fields", {})
        )
        return ActionResult.ok(resp.json())

    @action(description="Get all courses", input_schema={"page": "integer (optional)"})
    async def get_courses(self, payload: Dict) -> ActionResult:
        params = {"page": payload.get("page", 1), "page_size": payload.get("page_size", 100)}
        resp = await self._request("GET", "learn/v1/courses", params=params)
        return ActionResult.ok(resp.json())

    @action(
        description="Enroll a user in a course",
        input_schema={
            "user_id": "integer",
            "course_id": "integer",
            "level": "string (optional) — learner | tutor | instructor",
        },
    )
    async def enroll_user(self, payload: Dict) -> ActionResult:
        body = {
            "users": [{"user_id": payload["user_id"]}],
            "courses": [{"course_id": payload["course_id"]}],
            "level": payload.get("level", "learner"),
        }
        resp = await self._request("POST", "learn/v1/enrollment", json=body)
        return ActionResult.ok(resp.json())

    @action(
        description="Get enrollment/completion status for a user",
        input_schema={
            "user_id": "integer (optional)",
            "course_id": "integer (optional)",
            "status": "string (optional) — completed | in_progress | not_started",
        },
    )
    async def get_enrollments(self, payload: Dict) -> ActionResult:
        params: Dict[str, Any] = {}
        if payload.get("user_id"):
            params["user_id"] = payload["user_id"]
        if payload.get("course_id"):
            params["course_id"] = payload["course_id"]
        if payload.get("status"):
            params["completion_status"] = payload["status"]
        resp = await self._request("GET", "learn/v1/enrollment", params=params)
        return ActionResult.ok(resp.json())

    @action(
        description="Get training completion report for a date range",
        input_schema={"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"},
    )
    async def get_completion_report(self, payload: Dict) -> ActionResult:
        params = {
            "completion_date_from": payload["start_date"],
            "completion_date_to": payload["end_date"],
        }
        resp = await self._request("GET", "learn/v1/report/users/courses/completions", params=params)
        return ActionResult.ok(resp.json())

    @action(
        description="Assign a learning plan to a user",
        input_schema={"user_id": "integer", "learning_plan_id": "integer"},
    )
    async def assign_learning_plan(self, payload: Dict) -> ActionResult:
        body = {"users": [{"user_id": payload["user_id"]}]}
        resp = await self._request(
            "POST", f"learn/v1/lp/{payload['learning_plan_id']}/enroll", json=body
        )
        return ActionResult.ok(resp.json())

    # ── Triggers ──────────────────────────────────────────────────────────────

    @trigger(
        description="Course completion event (polling)",
        trigger_type=TriggerType.POLLING,
        poll_interval_seconds=900,
    )
    async def on_course_completion(self, context: Dict) -> TriggerResult:
        since = context.get("cursor") or context.get("since", "")
        result = await self.get_enrollments({"status": "completed"})
        records = result.data.get("data", {}).get("items", []) if result.success else []
        return TriggerResult(events=records, has_more=False)

    @trigger(
        description="Inbound Docebo webhook",
        trigger_type=TriggerType.WEBHOOK,
    )
    async def on_webhook(self, context: Dict) -> TriggerResult:
        return TriggerResult(events=[context], has_more=False)
