"""
Register the deployed Agent Engine agent in a Gemini Enterprise app.

Usage:
    python deployment/register_ge_app.py register \
        --engine-id outcome-devtest_1772673946815 \
        --reasoning-engine projects/1084954470957/locations/us-central1/reasoningEngines/5168292889268060160

    python deployment/register_ge_app.py list --engine-id outcome-devtest_1772673946815

    python deployment/register_ge_app.py delete \
        --engine-id outcome-devtest_1772673946815 \
        --agent-id 2704851260087417677
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import google.auth
import google.auth.transport.requests
import requests
from dotenv import load_dotenv

load_dotenv()

PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
#ENGINE_ID = os.environ.get("ENGINE_ID")
LOCATION = os.environ.get("LOCATION")
BASE_URL = f"https://{LOCATION}-discoveryengine.googleapis.com/v1alpha"
ASSISTANT_ID = "default_assistant"

AGENT_DISPLAY_NAME = os.environ.get("AGENT_ENGINE_DISPLAY_NAME", "GE User Level Analytics")
AGENT_DESCRIPTION = (
    "Governs Gemini Enterprise licences: identifies inactive users (>45 days), "
    "queries usage analytics, revokes licences, runs daily usage audits, and logs every action."
)

SCHEDULER_JOB_ID = "ge-license-manager-daily-audit"
SCHEDULER_PROMPT = (
    "Run the full daily licence governance cycle: "
    "identify inactive users (>45 days), revoke their licences, "
    "and generate the daily usage audit report."
)


def _get_token() -> str:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "x-goog-user-project": PROJECT_ID,
        "Content-Type": "application/json",
    }


def _agents_url(engine_id: str) -> str:
    parent = (
        f"projects/{PROJECT_ID}/locations/{LOCATION}/collections/default_collection"
        f"/engines/{engine_id}/assistants/{ASSISTANT_ID}"
    )
    return f"{BASE_URL}/{parent}/agents"


def _cleanup_old_registrations(engine_id: str) -> None:
    """Delete any existing agent registrations with the same display name."""
    url = _agents_url(engine_id)
    try:
        resp = requests.get(url, headers=_headers())
        resp.raise_for_status()
    except Exception as exc:
        print(f"  WARNING: could not list existing agents: {exc}")
        return

    agents = resp.json().get("agents", [])
    old = [a for a in agents if a.get("displayName") == AGENT_DISPLAY_NAME]

    if not old:
        print("  No existing registrations to clean up.")
        return

    for a in old:
        agent_resource = a["name"]
        agent_id = agent_resource.split("/")[-1]
        print(f"  Deleting old registration: agent {agent_id}")
        try:
            del_resp = requests.delete(
                f"{BASE_URL}/{agent_resource}", headers=_headers()
            )
            del_resp.raise_for_status()
            print(f"    Deleted.")
        except Exception as exc:  # noqa: BLE001
            print(f"    WARNING: could not delete agent {agent_id}: {exc}")


def _create_scheduler_job(reasoning_engine: str) -> None:
    """Create or update a Cloud Scheduler job to run the agent daily at 21:00 UTC."""
    try:
        from google.api_core.exceptions import NotFound  # noqa: PLC0415
        from google.cloud import scheduler_v1  # noqa: PLC0415
    except ImportError:
        print(
            "  WARNING: google-cloud-scheduler is not installed — skipping Cloud Scheduler setup.\n"
            "  Install it with: pip install google-cloud-scheduler"
        )
        return

    scheduler_location = (
        os.environ.get("SCHEDULER_LOCATION")
        or os.environ.get("GCP_LOCATION")
        or LOCATION
    )
    sa_email = os.environ.get("SCHEDULER_SERVICE_ACCOUNT", "")

    # Extract the GCP location from the resource name so the Vertex AI URL is always correct.
    # Resource format: projects/{project}/locations/{location}/reasoningEngines/{id}
    parts = reasoning_engine.split("/")
    ai_location = parts[3] if len(parts) >= 4 else LOCATION

    target_url = (
        f"https://{ai_location}-aiplatform.googleapis.com/v1/{reasoning_engine}:query"
    )
    body_bytes = json.dumps({
        "input": {
            "messages": [{"role": "user", "content": SCHEDULER_PROMPT}]
        }
    }).encode("utf-8")

    http_target = scheduler_v1.HttpTarget(
        uri=target_url,
        http_method=scheduler_v1.HttpMethod.POST,
        body=body_bytes,
        headers={"Content-Type": "application/json"},
    )
    if sa_email:
        http_target.oidc_token = scheduler_v1.OidcToken(
            service_account_email=sa_email,
            audience=target_url,
        )

    client = scheduler_v1.CloudSchedulerClient()
    parent = f"projects/{PROJECT_ID}/locations/{scheduler_location}"
    job_name = f"{parent}/jobs/{SCHEDULER_JOB_ID}"

    job = scheduler_v1.Job(
        name=job_name,
        description="Daily GE licence governance run — 21:00 UTC",
        http_target=http_target,
        schedule="0 21 * * *",
        time_zone="UTC",
    )

    try:
        client.get_job(request={"name": job_name})
        updated = client.update_job(request={"job": job})
        print(f"  Updated existing Cloud Scheduler job : {updated.name}")
        print(f"  Schedule                              : {updated.schedule} (UTC)")
    except NotFound:
        created = client.create_job(request={"parent": parent, "job": job})
        print(f"  Created Cloud Scheduler job : {created.name}")
        print(f"  Schedule                    : {created.schedule} (UTC)")
    except Exception as exc:  # noqa: BLE001
        print(f"  WARNING: could not create/update Cloud Scheduler job: {exc}")


def register(args: argparse.Namespace) -> None:
    reasoning_engine = args.reasoning_engine
    if not reasoning_engine and os.path.exists(".last_resource_name"):
        with open(".last_resource_name") as f:
            reasoning_engine = f.read().strip()
        print(f"Using saved resource name: {reasoning_engine}")

    if not reasoning_engine:
        print("ERROR: --reasoning-engine is required (or deploy first to save it automatically).")
        sys.exit(1)

    print(f"Cleaning up old registrations in engine: {args.engine_id} …")
    _cleanup_old_registrations(args.engine_id)

    agent_body = {
        "displayName": AGENT_DISPLAY_NAME,
        "description": AGENT_DESCRIPTION,
        "adkAgentDefinition": {
            "provisionedReasoningEngine": {
                "reasoningEngine": reasoning_engine,
            },
            "toolSettings": {},
        },
        "state": "ENABLED",
        "sharingConfig": {
            "scope": "ALL_USERS",
        },
    }

    url = _agents_url(args.engine_id)
    print(f"Registering agent in engine: {args.engine_id} …")
    resp = requests.post(url, headers=_headers(), json=agent_body)
    resp.raise_for_status()
    result = resp.json()

    agent_id = result["name"].split("/")[-1]
    print("\nAgent registered successfully!")
    print(f"  Agent ID      : {agent_id}")
    print(f"  Display name  : {result['displayName']}")
    print(f"  State         : {result['state']}")
    print(f"  Sharing scope : {result.get('sharingConfig', {}).get('scope', 'N/A')}")
    print(f"  Reasoning eng : {reasoning_engine}")

    print("\nConfiguring Cloud Scheduler (daily run at 21:00 UTC) …")
    _create_scheduler_job(reasoning_engine)


def list_agents(args: argparse.Namespace) -> None:
    url = _agents_url(args.engine_id)
    resp = requests.get(url, headers=_headers())
    resp.raise_for_status()
    agents = resp.json().get("agents", [])
    if not agents:
        print("No agents found.")
        return
    print(f"{'Agent ID':<25}  {'Display Name':<30}  State      Sharing")
    print("-" * 90)
    for a in agents:
        aid = a["name"].split("/")[-1]
        sharing = a.get("sharingConfig", {}).get("scope", "PRIVATE")
        print(f"  {aid:<23}  {a['displayName']:<30}  {a.get('state','?'):<10} {sharing}")


def delete(args: argparse.Namespace) -> None:
    agent_path = (
        f"projects/{PROJECT_ID}/locations/{LOCATION}/collections/default_collection"
        f"/engines/{args.engine_id}/assistants/{ASSISTANT_ID}/agents/{args.agent_id}"
    )
    url = f"{BASE_URL}/{agent_path}"
    print(f"Deleting agent {args.agent_id} …")
    resp = requests.delete(url, headers=_headers())
    resp.raise_for_status()
    print("Deleted.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register the GE analytics agent in a Gemini Enterprise app"
    )
    parser.add_argument(
        "--engine-id",
        default="outcome_1770956866236",
        help="Discovery Engine / GE app engine ID",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    reg = sub.add_parser("register", help="Register agent in the GE app")
    reg.add_argument(
        "--reasoning-engine",
        default="",
        help="Agent Engine resource name (omit to use .last_resource_name)",
    )

    sub.add_parser("list", help="List agents in the GE app")

    del_p = sub.add_parser("delete", help="Delete an agent from the GE app")
    del_p.add_argument("--agent-id", required=True, help="Agent ID to delete")

    args = parser.parse_args()
    dispatch = {"register": register, "list": list_agents, "delete": delete}
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
