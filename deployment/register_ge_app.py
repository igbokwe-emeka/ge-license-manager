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
    "queries usage analytics, revokes licences, and notifies users and org administrators."
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


def register(args: argparse.Namespace) -> None:
    reasoning_engine = args.reasoning_engine
    if not reasoning_engine and os.path.exists(".last_resource_name"):
        with open(".last_resource_name") as f:
            reasoning_engine = f.read().strip()
        print(f"Using saved resource name: {reasoning_engine}")

    if not reasoning_engine:
        print("ERROR: --reasoning-engine is required (or deploy first to save it automatically).")
        sys.exit(1)

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
