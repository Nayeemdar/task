"""
Sage Intacct Integration (XML/SOAP API).

Auth: XML session-based authentication.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional
import uuid

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

SAGE_ENDPOINT = "https://api.intacct.com/ia/xml/xmlgw.phtml"


class SageIntactIntegration(BaseIntegration):

    service_name = "sage_intact"
    service_description = (
        "Sage Intacct — financial management, GL, AP, AR, and project accounting."
    )
    auth_type = AuthType.CUSTOM

    def __init__(self, credentials: Dict[str, Any]):
        super().__init__(credentials)
        self._session_id: Optional[str] = None
        self._endpoint: str = SAGE_ENDPOINT

    # ── Authentication ────────────────────────────────────────────────────────

    async def authenticate(self) -> bool:
        creds = self.credentials
        required = ["company_id", "user_id", "user_password"]
        if not all(creds.get(k) for k in required):
            raise AuthenticationError(f"Sage Intacct requires: {required}")

        xml = self._build_request(
            f"""<authentication>
                <login>
                    <userid>{creds['user_id']}</userid>
                    <companyid>{creds['company_id']}</companyid>
                    <password>{creds['user_password']}</password>
                </login>
            </authentication>"""
        )
        resp_xml = await self._send_xml(xml)
        session_elem = resp_xml.find(".//sessionid")
        if session_elem is not None:
            self._session_id = session_elem.text
            logger.info("Sage Intacct authenticated")
            return True
        raise AuthenticationError("Sage Intacct: could not obtain session ID")

    def _build_request(self, auth_xml: str, function_xml: str = "") -> str:
        control_id = str(uuid.uuid4())[:8]
        client_id = self.credentials.get("client_id", "")
        client_secret = self.credentials.get("client_secret", "")
        sender_block = (
            f"<sender><senderid>{client_id}</senderid><password>{client_secret}</password></sender>"
            if client_id else "<sender><senderid>IntegrationPlatform</senderid><password/></sender>"
        )
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<request>
    <control>
        {sender_block}
        <controlid>{control_id}</controlid>
        <uniqueid>false</uniqueid>
        <dtdversion>3.0</dtdversion>
    </control>
    <operation>
        {auth_xml}
        <content>
            <function controlid="{control_id}">
                {function_xml}
            </function>
        </content>
    </operation>
</request>"""

    def _session_auth_xml(self) -> str:
        return f"<authentication><sessionid>{self._session_id}</sessionid></authentication>"

    async def _send_xml(self, xml: str) -> ET.Element:
        client = await self._get_client()
        resp = await client.post(
            self._endpoint,
            content=xml.encode("utf-8"),
            headers={"Content-Type": "text/xml"},
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        errmsgs = root.findall(".//errormessage/error")
        if errmsgs:
            desc = errmsgs[0].findtext("description2") or errmsgs[0].findtext("description", "Unknown error")
            raise RuntimeError(f"Sage Intacct API error: {desc}")
        return root

    async def _call(self, function_xml: str) -> ET.Element:
        xml = self._build_request(self._session_auth_xml(), function_xml)
        return await self._send_xml(xml)

    @staticmethod
    def _xml_to_dict(elem: ET.Element) -> Dict:
        """Recursively converts an XML element to a dict."""
        d: Dict[str, Any] = {}
        for child in elem:
            val = SageIntactIntegration._xml_to_dict(child) if len(child) else (child.text or "")
            if child.tag in d:
                existing = d[child.tag]
                if not isinstance(existing, list):
                    d[child.tag] = [existing]
                d[child.tag].append(val)
            else:
                d[child.tag] = val
        return d

    # ── Actions ───────────────────────────────────────────────────────────────

    @action(
        description="Query any Sage Intacct object",
        input_schema={
            "object": "string — e.g. GLENTRY, VENDOR, PROJECT",
            "fields": "array of strings — fields to return",
            "filters": "array of filter dicts (optional) — [{field, operator, value}]",
            "max_records": "integer (optional, default 100)",
        },
    )
    async def query_object(self, payload: Dict) -> ActionResult:
        obj = payload["object"]
        fields_xml = "".join(f"<field>{f}</field>" for f in payload.get("fields", ["*"]))
        max_rec = payload.get("max_records", 100)

        filters_xml = ""
        for f in payload.get("filters", []):
            op = f.get("operator", "equalto")
            filters_xml += f"<{op}><field>{f['field']}</field><value>{f['value']}</value></{op}>"
        filter_block = f"<filter>{filters_xml}</filter>" if filters_xml else ""

        function_xml = f"""
        <readByQuery>
            <object>{obj}</object>
            <fields>{fields_xml}</fields>
            {filter_block}
            <pagesize>{max_rec}</pagesize>
        </readByQuery>"""

        root = await self._call(function_xml)
        items_elem = root.find(".//data")
        items = [self._xml_to_dict(child) for child in (items_elem or [])]
        return ActionResult.ok({"records": items, "total": len(items)})

    @action(
        description="Create a Sage Intacct object record",
        input_schema={
            "object": "string — e.g. VENDOR, PROJECT, GLTRANSACTION",
            "fields": "object — field/value pairs",
        },
    )
    async def create_object(self, payload: Dict) -> ActionResult:
        obj = payload["object"]
        fields_xml = "".join(f"<{k}>{v}</{k}>" for k, v in payload.get("fields", {}).items())
        function_xml = f"<create><{obj}>{fields_xml}</{obj}></create>"
        root = await self._call(function_xml)
        result_elem = root.find(".//result")
        return ActionResult.ok(self._xml_to_dict(result_elem) if result_elem is not None else {})

    @action(
        description="Create a GL Journal Entry",
        input_schema={
            "journal": "string — journal symbol e.g. GJ",
            "date": "YYYY-MM-DD",
            "description": "string",
            "lines": "array — [{glaccountno, amount, trtype: debit|credit, departmentid?, projectid?}]",
        },
    )
    async def create_journal_entry(self, payload: Dict) -> ActionResult:
        lines_xml = ""
        for line in payload.get("lines", []):
            tr = "T" if line.get("trtype", "debit") == "debit" else "F"
            dept = f"<departmentid>{line['departmentid']}</departmentid>" if line.get("departmentid") else ""
            proj = f"<projectid>{line['projectid']}</projectid>" if line.get("projectid") else ""
            lines_xml += f"""
            <GLENTRY>
                <ACCOUNTNO>{line['glaccountno']}</ACCOUNTNO>
                <TR_TYPE>{tr}</TR_TYPE>
                <TRX_AMOUNT>{line['amount']}</TRX_AMOUNT>
                {dept}{proj}
            </GLENTRY>"""

        function_xml = f"""
        <create>
            <GLTRANSACTION>
                <JOURNAL>{payload['journal']}</JOURNAL>
                <BATCH_DATE>{payload['date']}</BATCH_DATE>
                <BATCH_TITLE>{payload['description']}</BATCH_TITLE>
                <ENTRIES>{lines_xml}</ENTRIES>
            </GLTRANSACTION>
        </create>"""
        root = await self._call(function_xml)
        result_elem = root.find(".//result")
        return ActionResult.ok(self._xml_to_dict(result_elem) if result_elem is not None else {})

    @action(
        description="Get AP/AR aging report",
        input_schema={"report_type": "string — AP | AR", "as_of_date": "YYYY-MM-DD (optional)"},
    )
    async def get_aging_report(self, payload: Dict) -> ActionResult:
        obj = "APAGINGREPORT" if payload.get("report_type", "AP") == "AP" else "ARAGINGREPORT"
        return await self.query_object({"object": obj, "fields": ["*"]})

    # ── Triggers ──────────────────────────────────────────────────────────────

    @trigger(
        description="New AP invoices (polling)",
        trigger_type=TriggerType.POLLING,
        poll_interval_seconds=1800,
    )
    async def on_new_ap_invoice(self, context: Dict) -> TriggerResult:
        result = await self.query_object({
            "object": "APBILL",
            "fields": ["RECORDNO", "VENDORID", "TOTALDUE", "WHENCREATED"],
            "max_records": 100,
        })
        records = result.data.get("records", []) if result.success else []
        return TriggerResult(events=records, has_more=len(records) == 100)
