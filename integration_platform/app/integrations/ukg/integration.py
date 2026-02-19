"""
UKG (Kronos Workforce Ready / UKG Pro) Integration.

Auth: session token obtained from /authentication/login endpoint.
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


class UKGIntegration(BaseIntegration):

    service_name = "ukg"
    service_description = (
        "UKG (Kronos) Workforce Management — employees, timekeeping, scheduling, and HR data."
    )
    auth_type = AuthType.CUSTOM

    def __init__(self, credentials: Dict[str, Any]):
        super().__init__(credentials)
        self._base_url: str = credentials.get("base_url", "").rstrip("/")
        self._session_token: Optional[str] = None

    # ── Authentication ────────────────────────────────────────────────────────

    async def authenticate(self) -> bool:
        creds = self.credentials
        if not (self._base_url and creds.get("app_key") and creds.get("username")):
            raise AuthenticationError("UKG requires base_url, app_key, username, and password")

        client = await self._get_client()
        resp = await client.post(
            f"{self._base_url}/api/v1/authentication/login",
            json={
                "username": creds["username"],
                "password": creds["password"],
                "company": creds.get("company", ""),
                "appKey": creds["app_key"],
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            self._session_token = data.get("token") or data.get("Token")
            logger.info("UKG authenticated successfully")
            return True
        raise AuthenticationError(f"UKG auth failed: {resp.status_code} {resp.text}")

    def _headers(self) -> Dict[str, str]:
        return {
            "US-SESSION-TOKEN": self._session_token or "",
            "appkey": self.credentials.get("app_key", ""),
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
        description="Get all employees or a specific employee by ID",
        input_schema={
            "employee_id": "string (optional) — returns single employee if provided",
            "page": "integer (optional)",
            "per_page": "integer (optional, max 200)",
        },
    )
    async def get_employees(self, payload: Dict) -> ActionResult:
        emp_id = payload.get("employee_id")
        if emp_id:
            resp = await self._request("GET", f"employees/{emp_id}")
        else:
            params = {
                "page": payload.get("page", 1),
                "per_page": payload.get("per_page", 100),
            }
            resp = await self._request("GET", "employees", params=params)
        return ActionResult.ok(resp.json())

    @action(
        description="Get timecards for an employee within a date range",
        input_schema={
            "employee_id": "string",
            "start_date": "YYYY-MM-DD",
            "end_date": "YYYY-MM-DD",
        },
    )
    async def get_timecards(self, payload: Dict) -> ActionResult:
        params = {
            "employee_id": payload["employee_id"],
            "start_date": payload["start_date"],
            "end_date": payload["end_date"],
        }
        resp = await self._request("GET", "timecards", params=params)
        return ActionResult.ok(resp.json())

    @action(
        description="Punch in / out for an employee",
        input_schema={
            "employee_id": "string",
            "punch_type": "string — IN | OUT | TRANSFER",
            "timestamp": "ISO-8601 datetime (optional — defaults to now)",
            "labor_account": "object (optional)",
        },
    )
    async def punch(self, payload: Dict) -> ActionResult:
        body = {
            "employee": {"id": payload["employee_id"]},
            "type": payload.get("punch_type", "IN"),
        }
        if payload.get("timestamp"):
            body["time"] = payload["timestamp"]
        if payload.get("labor_account"):
            body["laborAccount"] = payload["labor_account"]
        resp = await self._request("POST", "timecards/punch", json=body)
        return ActionResult.ok(resp.json())

    @action(
        description="Get schedule for employees in a date range",
        input_schema={
            "employee_ids": "array of strings",
            "start_date": "YYYY-MM-DD",
            "end_date": "YYYY-MM-DD",
        },
    )
    async def get_schedule(self, payload: Dict) -> ActionResult:
        body = {
            "employees": [{"id": eid} for eid in payload.get("employee_ids", [])],
            "dateSpan": {
                "startDate": payload["start_date"],
                "endDate": payload["end_date"],
            },
        }
        resp = await self._request("POST", "scheduling/schedule/multi_read", json=body)
        return ActionResult.ok(resp.json())

    @action(
        description="Get accruals (PTO balances) for an employee",
        input_schema={"employee_id": "string"},
    )
    async def get_accruals(self, payload: Dict) -> ActionResult:
        resp = await self._request("GET", f"accruals/balances/{payload['employee_id']}")
        return ActionResult.ok(resp.json())

    @action(
        description="Submit a time-off request",
        input_schema={
            "employee_id": "string",
            "start_date": "YYYY-MM-DD",
            "end_date": "YYYY-MM-DD",
            "type": "string — e.g. Vacation",
            "notes": "string (optional)",
        },
    )
    async def submit_time_off_request(self, payload: Dict) -> ActionResult:
        body = {
            "employee": {"id": payload["employee_id"]},
            "requestedTimeOffPeriods": [
                {
                    "startDate": payload["start_date"],
                    "endDate": payload["end_date"],
                    "payCodeName": payload.get("type", "Vacation"),
                }
            ],
            "requestFor": {"id": payload["employee_id"]},
            "notes": payload.get("notes", ""),
        }
        resp = await self._request("POST", "scheduling/timeoff/request/submit", json=body)
        return ActionResult.ok(resp.json())

    @action(description="Get a list of pay codes configured in UKG")
    async def get_pay_codes(self, payload: Dict) -> ActionResult:
        resp = await self._request("GET", "paycodes")
        return ActionResult.ok(resp.json())

    # ── Triggers ──────────────────────────────────────────────────────────────

    @trigger(
        description="New or updated employee record (polling)",
        trigger_type=TriggerType.POLLING,
        poll_interval_seconds=600,
    )
    async def on_employee_change(self, context: Dict) -> TriggerResult:
        result = await self.get_employees({"page": 1, "per_page": 200})
        employees = result.data if result.success else []
        if isinstance(employees, dict):
            employees = employees.get("employees", [])
        return TriggerResult(events=employees if isinstance(employees, list) else [], has_more=False)

    @trigger(
        description="Inbound UKG event webhook",
        trigger_type=TriggerType.WEBHOOK,
    )
    async def on_webhook(self, context: Dict) -> TriggerResult:
        return TriggerResult(events=[context], has_more=False)
