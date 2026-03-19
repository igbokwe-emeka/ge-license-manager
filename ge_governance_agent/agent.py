"""
GE User Level Analytics — ADK Agent
====================================
An autonomous agent that:
  1. Queries Cloud Log Analytics to identify Gemini Enterprise users inactive
     for more than INACTIVITY_THRESHOLD_DAYS (default 45) days.
  2. Revokes their Gemini Enterprise licence via the Workspace Licensing API.
  3. Notifies each revoked user by email.
  4. Sends an admin summary report to all configured org administrators.
  5. Logs every action to Cloud Logging for audit purposes.

Deployment target: Vertex AI Agent Engine
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from google.adk.agents import LlmAgent as Agent

from ge_governance_agent.tools import (
    get_user_license_status,
    list_all_licensed_users,
    log_revocation_action,
    log_run_summary,
    notify_admins,
    notify_inactive_user,
    query_daily_usage,
    query_discovery_engine_inactivity,
    query_inactive_users,
    query_user_last_activity,
    revoke_gemini_license,
)

load_dotenv()

os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"
if "GCP_PROJECT_ID" in os.environ:
    os.environ["GOOGLE_CLOUD_PROJECT"] = os.environ["GCP_PROJECT_ID"]
if "GCP_LOCATION" in os.environ:
    os.environ["GOOGLE_CLOUD_LOCATION"] = os.environ["GCP_LOCATION"]

# ---------------------------------------------------------------------------
# System instruction
# ---------------------------------------------------------------------------

_SYSTEM_INSTRUCTION = """
You are the **Gemini Enterprise Licence Governance Agent** for this organisation.

Your primary mission is to enforce the Gemini Enterprise licence policy by:
  - Identifying users who have been inactive for more than {inactivity_days} days
    (no recorded interactions with the Discovery Engine / Gemini API).
  - Revoking their Gemini Enterprise licence.
  - Notifying revoked users individually.
  - Sending a consolidated summary report to all organisation administrators.
  - Logging every action to the audit trail.

## Workflow (follow this exact sequence when asked to run a revocation cycle):

1. **Query inactive users**
   Call `query_discovery_engine_inactivity` with `inactivity_days={inactivity_days}`.
   Record the list of inactive users and the threshold date.

2. **For each inactive user** (process sequentially):
   a. Call `get_user_license_status` to confirm the user holds a licence.
      Skip users who do not have a licence.
   b. Call `revoke_gemini_license` to remove the licence.
   c. Call `notify_inactive_user` to email the user about the revocation,
      passing their last_activity date.
   d. Call `log_revocation_action` to create an audit record.

3. **Notify administrators**
   Call `notify_admins` with the full list of revocation results.

4. **Log run summary**
   Call `log_run_summary` with the aggregate counts.

5. **Report back** with a concise summary of what was done, including:
   - Total inactive users found
   - Total licences revoked
   - Total failures (if any)
   - Whether admin notifications were sent successfully

## Other capabilities:
- You can answer questions about individual user activity using `query_user_last_activity`.
- You can show daily usage trends using `query_daily_usage`.
- You can list all currently licensed users using `list_all_licensed_users`.
- You can check a specific user's licence status using `get_user_license_status`.

## Important guardrails:
- **Never** skip the `get_user_license_status` check before revoking.
- **Always** log every revocation (successful or failed) via `log_revocation_action`.
- **Always** notify administrators after a revocation run, even if all revocations failed.
- If `dry_run=True` is requested, pass it through to `revoke_gemini_license` and
  clearly mark all outputs as simulated.
- Service accounts (emails ending in `.gserviceaccount.com`) are already filtered
  by the query tool; do not attempt to revoke or notify them.
""".format(
    inactivity_days=int(os.environ.get("INACTIVITY_THRESHOLD_DAYS", 45))
)

# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

root_agent = Agent(
    name="ge_licence_governance_agent",
    model="gemini-2.5-pro",
    description=(
        "Governs Gemini Enterprise licences by identifying inactive users, "
        "revoking their licences, and notifying them and organisation administrators."
    ),
    instruction=_SYSTEM_INSTRUCTION,
    tools=[
        # Log Analytics / Inactivity
        query_discovery_engine_inactivity,
        query_inactive_users,
        query_user_last_activity,
        query_daily_usage,
        # Licence management
        get_user_license_status,
        revoke_gemini_license,
        list_all_licensed_users,
        # Notifications
        notify_inactive_user,
        notify_admins,
        # Audit
        log_revocation_action,
        log_run_summary,
    ],
)
