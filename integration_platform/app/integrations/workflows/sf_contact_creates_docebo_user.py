"""
Workflow: Create Docebo LMS user when a Salesforce Contact is created.

Overview
────────
When a new Contact is created in Salesforce (e.g. a sales rep creates a new
customer, or HR adds a new hire as a Contact), this workflow:

  1. Detects the new Contact via the salesforce.on_contact_created polling trigger.
  2. Creates a matching user in Docebo LMS using the Contact's name and email.
  3. Assigns one or more pre-configured learning plans to the new user.

Data flow
─────────
  Every 5 min
  PollingService ──► salesforce.on_contact_created
                            │
                            │  event payload per contact:
                            │  {
                            │    contact_id, email,
                            │    first_name, last_name,
                            │    title, phone, account_id,
                            │    created_date
                            │  }
                            ▼
  WorkflowEngine._on_event()
  (matches trigger_service="salesforce", trigger_name="on_contact_created")
                            │
                            ▼
  Step 1 — docebo.create_user_and_assign_plans
    input_mapping:
      email             = {{trigger.email}}
      first_name        = {{trigger.first_name}}
      last_name         = {{trigger.last_name}}
      username          = {{trigger.email}}       ← Docebo username = SF email
      learning_plan_ids = [101, 202]              ← static IDs, configure below
    output:
      {user_id, email, plans_assigned, plans_failed}

Customising learning plan IDs
──────────────────────────────
Find your plan IDs in Docebo:
  Admin → E-Learning → Learning Plans → click a plan → the ID is in the URL

Then update LEARNING_PLAN_IDS in this file and re-register, or pass them
directly to workflow_as_dict(learning_plan_ids=[...]).

Salesforce prerequisite
────────────────────────
The trigger polls the Contact object using CreatedDate.  No custom fields are
required.  If you want to store the Docebo user_id back on the Contact, create:
  Custom Field: Docebo_User_ID__c (Text, length 20, External ID)
and add a second step using salesforce.update_record.

Usage — register at application startup
─────────────────────────────────────────
    from app.core.workflow_engine import get_workflow_engine
    from app.integrations.workflows.sf_contact_creates_docebo_user import register

    register(get_workflow_engine())

Usage — POST the recipe to the REST API
─────────────────────────────────────────
    import httpx
    from app.integrations.workflows.sf_contact_creates_docebo_user import workflow_as_dict

    httpx.post("http://localhost:7071/api/v1/workflows", json=workflow_as_dict())
"""
from __future__ import annotations

from typing import Dict, List

from app.core.workflow_engine import StepType, Workflow, WorkflowStep, WorkflowEngine

# ── Config ─────────────────────────────────────────────────────────────────────
# Update these IDs to match your Docebo learning plan IDs.
# Admin → E-Learning → Learning Plans → click a plan → ID is in the URL.
LEARNING_PLAN_IDS: List[int] = [101, 202]

# Fixed IDs — keep stable so re-registration is idempotent
_WORKFLOW_ID = "sf-contact-creates-docebo-user"
_STEP_ID = "step-create-docebo-user"


def workflow_as_dict(learning_plan_ids: List[int] = None) -> Dict:
    """
    Return the workflow as a plain dict matching the POST /api/v1/workflows schema.

    Parameters
    ----------
    learning_plan_ids : list[int], optional
        Override the default LEARNING_PLAN_IDS defined at the top of this module.
        Pass an explicit list to configure different plans per environment
        without editing the source file.
    """
    plans = learning_plan_ids if learning_plan_ids is not None else LEARNING_PLAN_IDS
    return {
        "workflow_id": _WORKFLOW_ID,
        "name": "Create Docebo user when Salesforce Contact is created",
        "description": (
            "When a new Contact is created in Salesforce, automatically create "
            "a matching Docebo LMS user and enrol them in the configured learning plans."
        ),
        "status": "active",
        "trigger_service": "salesforce",
        "trigger_name": "on_contact_created",
        "steps": [
            {
                "step_id": _STEP_ID,
                "step_type": "action",
                "service_name": "docebo",
                "operation_name": "create_user_and_assign_plans",
                "input_mapping": {
                    # ── Dynamic — resolved from trigger event payload ──────────
                    # These field names must match the keys emitted by
                    # salesforce.on_contact_created (see integration.py).
                    "email":      "{{trigger.email}}",
                    "first_name": "{{trigger.first_name}}",
                    "last_name":  "{{trigger.last_name}}",
                    "username":   "{{trigger.email}}",   # use email as Docebo username

                    # ── Static — configured per organisation ──────────────────
                    # _map_data passes non-template values through unchanged,
                    # so a list literal here works correctly.
                    "learning_plan_ids": plans,

                    # Optional: uncomment and set your Docebo branch ID
                    # "branch_id": 5,
                },
                "on_error": "stop",
            }
        ],
    }


def register(engine: WorkflowEngine, learning_plan_ids: List[int] = None) -> Workflow:
    """
    Build and register the workflow with the in-memory WorkflowEngine.

    Call once during application startup.  If the workflow_id already exists
    it is replaced (idempotent re-registration).

    Parameters
    ----------
    engine : WorkflowEngine
        The singleton engine instance.
    learning_plan_ids : list[int], optional
        Override LEARNING_PLAN_IDS for this registration.
    """
    d = workflow_as_dict(learning_plan_ids=learning_plan_ids)
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
