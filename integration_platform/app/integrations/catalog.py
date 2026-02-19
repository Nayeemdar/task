"""
Integration Workflow Catalog — THE single place for all cross-system integrations.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HOW TO ADD A NEW INTEGRATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Add a @workflow method to IntegrationCatalog below.
  2. Done.  No other files need changing.

  Template:

      @workflow(
          trigger="<source_service>.<trigger_method_name>",
          name="<Human readable name>",
          description="<What it does>",
      )
      async def your_new_integration(self, event: dict):
          result = await self.<target_service>.<action_method_name>({
              "field": event["trigger_field"],
              "other": "static_value",
          })
          return result

  Service names match the BaseIntegration.service_name class attribute:
      self.salesforce   self.docebo   self.jira   self.ukg   self.sage_intact
  Action / trigger method names match the @action / @trigger decorated methods
  on those connector classes.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HOW THE FRAMEWORK WORKS (read once, then forget)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  EventBus (asyncio queue)
    │
    ├── ServiceBusProcessor    — injects cloud events (Azure Service Bus)
    ├── PollingService         — injects polled events every N seconds
    │
    └── WorkflowCatalog subscriber  ← catalog.start() registers this once
          │
          ├── on every event:  check service_name + trigger_name
          ├── if match:        call the @workflow method with event.payload
          └── self.<service>.<action>(payload) → registry.execute_action()
                                                 → connector authenticates
                                                 → HTTP call to external API
                                                 → ActionResult returned

  Errors in a @workflow method are caught, logged, and swallowed — they
  never crash the EventBus or block other workflows.
"""
from __future__ import annotations

from app.core.workflow_catalog import WorkflowCatalog, workflow


class IntegrationCatalog(WorkflowCatalog):
    """
    All cross-system integration workflows live here as methods.

    Each @workflow method IS the complete integration:
      • The trigger binding (which event starts it) is declared in @workflow.
      • The business logic (what to do) is the method body.
      • Service calls use self.<service>.<action>(payload).

    ──────────────────────────────────────────────────────────────────────────
    EXISTING INTEGRATIONS
    ──────────────────────────────────────────────────────────────────────────
    """

    # ── 1. Salesforce Contact created → Docebo LMS user + learning plans ──────

    @workflow(
        trigger="salesforce.on_contact_created",
        name="SF Contact → Docebo LMS User",
        description=(
            "When a Contact is created in Salesforce, create a matching Docebo "
            "LMS user account and enrol them in the configured onboarding "
            "learning plans."
        ),
    )
    async def contact_created_create_docebo_user(self, event: dict):
        """
        event keys (from salesforce.on_contact_created):
            contact_id, email, first_name, last_name,
            title, phone, account_id, created_date
        """
        return await self.docebo.create_user_and_assign_plans({
            "email":             event.get("email", ""),
            "first_name":        event.get("first_name", ""),
            "last_name":         event.get("last_name", ""),
            "username":          event.get("email", ""),   # Docebo username = SF email
            # ↓ Update these to your real Docebo learning plan IDs
            # Find them in: Admin → E-Learning → Learning Plans → URL contains the ID
            "learning_plan_ids": [101, 202],
        })

    # ── 2. Jira Initiative fully closed → close linked Salesforce Case ─────────

    @workflow(
        trigger="jira.on_initiative_fully_closed",
        name="Jira Initiative Closed → Close SF Case",
        description=(
            "When a Jira Initiative transitions to Done AND every one of its "
            "direct child issues (Epics) is also Done, close the linked "
            "Salesforce Case matched via the Jira_Initiative_Key__c custom field."
        ),
    )
    async def initiative_fully_closed_close_sf_case(self, event: dict):
        """
        event keys (from jira.on_initiative_fully_closed):
            initiative_key, initiative_summary, initiative, children_count

        Salesforce prerequisite:
            Custom Text field on Case object:
              Label:  Jira Initiative Key
              API:    Jira_Initiative_Key__c
              ☑ External ID
        """
        return await self.salesforce.close_case_by_field({
            "lookup_field": "Jira_Initiative_Key__c",
            "lookup_value": event.get("initiative_key", ""),
            # "close_status": "Resolved",  # uncomment to override default "Closed"
        })

    # ──────────────────────────────────────────────────────────────────────────
    # ADD NEW INTEGRATIONS BELOW THIS LINE
    # ──────────────────────────────────────────────────────────────────────────
    #
    # Copy the template at the top of this file and add your method here.
    # No other file in the project needs to change.
    #
    # Examples of what you might add next:
    #   • UKG employee created  → create Salesforce Contact
    #   • Salesforce Opportunity won → create Sage Intacct invoice
    #   • Docebo course completed   → update UKG training record
    #   • Jira Epic closed          → notify Slack channel
    #
    # ──────────────────────────────────────────────────────────────────────────
