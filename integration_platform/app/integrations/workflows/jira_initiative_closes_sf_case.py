"""
Workflow: Close Salesforce Case when Jira Initiative is fully closed.

How this fits in the platform
──────────────────────────────
                         ┌─────────────────────────────────────┐
  Every 5 min            │  jira.on_initiative_fully_closed    │  (polling trigger)
  PollingService  ──────▶│                                     │
                         │  Guards checked inside the trigger: │
                         │  • Initiative status category = Done│
                         │  • ALL direct children also Done    │
                         └──────────────┬──────────────────────┘
                                        │ event fires (one per initiative)
                                        ▼
                         ┌─────────────────────────────────────┐
                         │  WorkflowEngine._on_event()         │
                         │  matches trigger_service = "jira"   │
                         │  matches trigger_name  = "on_ini…"  │
                         └──────────────┬──────────────────────┘
                                        │
                                        ▼
                         ┌─────────────────────────────────────┐
                         │  Step 1 — salesforce.close_case_by_field
                         │                                     │
                         │  input_mapping:                     │
                         │    lookup_field  = "Jira_Initiative_Key__c"
                         │    lookup_value  = {{trigger.initiative_key}}
                         │                                     │
                         │  What it does internally:           │
                         │  1. SOQL: SELECT Id FROM Case WHERE │
                         │     Jira_Initiative_Key__c = 'key'  │
                         │     AND IsClosed = false LIMIT 1    │
                         │  2. PATCH Case.Status = 'Closed'    │
                         └─────────────────────────────────────┘

Salesforce prerequisite
────────────────────────
Create a custom Text field on the Case object:
  Label : Jira Initiative Key
  API   : Jira_Initiative_Key__c
  Length: 20 (Jira keys are typically 3–10 chars + number)
  ☑ External ID  (enables indexed lookup)

Populate this field when the Case is created (manually, via another
workflow, or via a Salesforce Flow that stamps it from the related
Jira project integration).

Usage — register at application startup
─────────────────────────────────────────
    from app.core.workflow_engine import get_workflow_engine
    from app.integrations.workflows.jira_initiative_closes_sf_case import register

    register(get_workflow_engine())

Usage — POST the recipe to the REST API
─────────────────────────────────────────
    import httpx, json
    from app.integrations.workflows.jira_initiative_closes_sf_case import workflow_as_dict

    httpx.post("http://localhost:7071/api/v1/workflows", json=workflow_as_dict())
"""
from __future__ import annotations

from typing import Dict

from app.core.workflow_engine import StepType, Workflow, WorkflowStep, WorkflowEngine

# Fixed IDs so this workflow is idempotent on re-registration
_WORKFLOW_ID = "jira-initiative-closes-sf-case"
_STEP_ID = "step-close-sf-case"


def workflow_as_dict() -> Dict:
    """
    Return the workflow as a plain dict matching the POST /api/v1/workflows body schema.
    Useful for seeding via the REST API or writing tests.
    """
    return {
        "workflow_id": _WORKFLOW_ID,
        "name": "Close Salesforce Case when Jira Initiative fully closes",
        "description": (
            "When a Jira Initiative transitions to Done AND every one of its direct "
            "child issues (Epics) is also Done, automatically close the linked "
            "Salesforce Case matched via the Jira_Initiative_Key__c custom field."
        ),
        "status": "active",
        "trigger_service": "jira",
        "trigger_name": "on_initiative_fully_closed",
        "steps": [
            {
                "step_id": _STEP_ID,
                "step_type": "action",
                "service_name": "salesforce",
                "operation_name": "close_case_by_field",
                "input_mapping": {
                    # Static literal — the exact Salesforce API field name.
                    # If your org uses a different field name, update this value.
                    "lookup_field": "Jira_Initiative_Key__c",
                    # Dynamic — resolved from the trigger event payload at run time.
                    # trigger.initiative_key = the Jira issue key, e.g. "PROJ-42"
                    "lookup_value": "{{trigger.initiative_key}}",
                    # Uncomment to use a different Case status value:
                    # "close_status": "Resolved",
                },
                "on_error": "stop",
            }
        ],
    }


def register(engine: WorkflowEngine) -> Workflow:
    """
    Build and register the workflow with the in-memory WorkflowEngine.

    Call this once during application startup (lifespan) so the engine
    begins reacting to jira.on_initiative_fully_closed events immediately.
    If the workflow_id already exists in the engine it is replaced.
    """
    d = workflow_as_dict()
    step_data = d["steps"][0]
    workflow = Workflow(
        workflow_id=d["workflow_id"],
        name=d["name"],
        description=d["description"],
        trigger_service=d["trigger_service"],
        trigger_name=d["trigger_name"],
        steps=[
            WorkflowStep(
                step_id=step_data["step_id"],
                step_type=StepType.ACTION,
                service_name=step_data["service_name"],
                operation_name=step_data["operation_name"],
                input_mapping=step_data["input_mapping"],
                on_error=step_data["on_error"],
            )
        ],
    )
    engine.register_workflow(workflow)
    return workflow
