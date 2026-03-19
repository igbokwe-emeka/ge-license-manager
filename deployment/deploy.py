:w"""
Deploy the GE User Level Analytics agent to Vertex AI Agent Engine.

Usage:
    # Authenticate first (one-time)
    gcloud auth application-default login

    # Deploy (or re-deploy) the agent
    python deployment/deploy.py deploy

    # List deployed agents
    python deployment/deploy.py list

    # Delete a deployed agent
    python deployment/deploy.py delete --resource-name RESOURCE_NAME

    # Test the deployed agent with a prompt
    python deployment/deploy.py test --resource-name RESOURCE_NAME

Prerequisites:
    pip install -r requirements.txt
    Set all required environment variables (see .env / .env.example).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration (resolved after load_dotenv)
# ---------------------------------------------------------------------------

PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
STAGING_BUCKET = os.environ.get(
    "STAGING_BUCKET", f"gs://{PROJECT_ID}-agent-engine-staging"
)
DISPLAY_NAME = os.environ.get(
    "AGENT_ENGINE_DISPLAY_NAME", "ge-user-level-analytics-agent"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_staging_bucket() -> None:
    """Create the staging GCS bucket if it does not already exist."""
    bucket_name = STAGING_BUCKET.removeprefix("gs://")
    print(f"Ensuring staging bucket exists: {STAGING_BUCKET}")
    try:
        from google.cloud import storage  # noqa: PLC0415

        client = storage.Client(project=PROJECT_ID)
        bucket = client.lookup_bucket(bucket_name)
        if bucket is None:
            bucket = client.create_bucket(bucket_name, location=LOCATION)
            print(f"  Created bucket: gs://{bucket.name}")
        else:
            print(f"  Bucket already exists: gs://{bucket.name}")
    except Exception as exc:  # noqa: BLE001
        print(f"  WARNING: Could not verify/create bucket via SDK ({exc}).")
        print(f"  Attempting via gcloud …")
        result = subprocess.run(
            [
                "gcloud",
                "storage",
                "buckets",
                "create",
                STAGING_BUCKET,
                "--project",
                PROJECT_ID,
                "--location",
                LOCATION,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 and "already exists" not in result.stderr:
            print(f"  gcloud error: {result.stderr.strip()}")
            sys.exit(1)
        print(f"  Bucket ready.")


def _init_vertexai() -> None:
    import vertexai  # noqa: PLC0415

    vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=STAGING_BUCKET)


def _build_app():
    """Build the ADK AdkApp wrapper required by Agent Engine."""
    from vertexai.preview.reasoning_engines import AdkApp  # noqa: PLC0415

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ge_governance_agent.agent import root_agent  # noqa: PLC0415

    return AdkApp(
        agent=root_agent,
        env_vars={
            "GCP_PROJECT_ID": PROJECT_ID,
            "GCP_LOCATION": LOCATION,
            "LOG_BUCKET": os.environ.get("LOG_BUCKET", "_Default"),
            "LOG_VIEW": os.environ.get("LOG_VIEW", "_AllLogs"),
            "INACTIVITY_THRESHOLD_DAYS": os.environ.get("INACTIVITY_THRESHOLD_DAYS", "45"),
            #"WORKSPACE_DOMAIN": os.environ.get("WORKSPACE_DOMAIN", ""),
            #"WORKSPACE_ADMIN_EMAIL": os.environ.get("WORKSPACE_ADMIN_EMAIL", ""),
            #"GEMINI_ENTERPRISE_PRODUCT_ID": os.environ.get(
#"GEMINI_ENTERPRISE_PRODUCT_ID", "Google-Gemini-Enterprise"
            ),
            #"GEMINI_ENTERPRISE_SKU_ID": os.environ.get("GEMINI_ENTERPRISE_SKU_ID", "1010310006"),
            #"NOTIFICATION_SENDER_EMAIL": os.environ.get("NOTIFICATION_SENDER_EMAIL", ""),
            #"ORG_ADMIN_EMAILS": os.environ.get("ORG_ADMIN_EMAILS", ""),
        },
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def deploy(args: argparse.Namespace) -> None:
    """Package and deploy the agent to Agent Engine."""
    _ensure_staging_bucket()
    _init_vertexai()

    from vertexai.preview import reasoning_engines  # noqa: PLC0415

    app = _build_app()

    print(f"\nDeploying '{DISPLAY_NAME}' to {PROJECT_ID} / {LOCATION} …")
    print("(This typically takes 3-5 minutes)\n")

    remote_app = reasoning_engines.ReasoningEngine.create(
        app,
        requirements=[
            "google-adk>=1.0.0",
            "google-cloud-bigquery>=3.10.0",
            "google-cloud-discoveryengine>=0.11.0",
            "google-api-python-client>=2.100.0",
            "google-auth>=2.20.0",
            "google-auth-httplib2>=0.2.0",
            "google-cloud-logging>=3.5.0",
            "google-cloud-storage>=2.10.0",
            "python-dotenv>=1.0.0",
            "pydantic>=2.0.0",
            "deprecated>=1.2.14",
        ],
        display_name=DISPLAY_NAME,
        description=(
            "Governs Gemini Enterprise licences: identifies inactive users (>45 days), "
            "revokes their licences, and notifies users and org administrators."
        ),
        service_account=f"ge-governance-agent@{PROJECT_ID}.iam.gserviceaccount.com"
        extra_packages=["./ge_governance_agent"],
    )

    print("\nDeployment successful!")
    print(f"  Resource name : {remote_app.resource_name}")
    print(f"  Display name  : {DISPLAY_NAME}")
    print(f"\nTo run a test:")
    print(f"  python deployment/deploy.py test --resource-name {remote_app.resource_name}")

    # Persist resource name for convenience
    with open(".last_resource_name", "w") as f:
        f.write(remote_app.resource_name)
    print(f"\nResource name saved to .last_resource_name")


def list_agents(args: argparse.Namespace) -> None:
    """List all Agent Engine deployments in the project."""
    _init_vertexai()
    from vertexai.preview import reasoning_engines  # noqa: PLC0415

    agents = reasoning_engines.ReasoningEngine.list()
    if not agents:
        print("No Agent Engine deployments found.")
        return
    print(f"{'Resource Name':<70}  Display Name")
    print("-" * 100)
    for agent in agents:
        print(f"  {agent.resource_name:<68}  {agent.display_name}")


def delete(args: argparse.Namespace) -> None:
    """Delete a deployed agent by resource name."""
    _init_vertexai()
    from vertexai.preview import reasoning_engines  # noqa: PLC0415

    resource_name: str = args.resource_name
    print(f"Deleting agent: {resource_name} …")
    agent = reasoning_engines.ReasoningEngine(resource_name)
    agent.delete()
    print("Deleted.")


def test_agent(args: argparse.Namespace) -> None:
    """Send a test prompt to a deployed agent and stream the response."""
    _init_vertexai()
    from vertexai.preview import reasoning_engines  # noqa: PLC0415

    # Resolve resource name from arg or saved file
    resource_name: str = args.resource_name
    if not resource_name and os.path.exists(".last_resource_name"):
        with open(".last_resource_name") as f:
            resource_name = f.read().strip()
        print(f"Using saved resource name: {resource_name}")

    if not resource_name:
        print("ERROR: --resource-name is required (or deploy first to save it automatically).")
        sys.exit(1)

    prompt: str = args.prompt
    print(f"Sending prompt to {resource_name} …")
    print(f"Prompt: {prompt}\n")
    print("=" * 60)

    remote_app = reasoning_engines.ReasoningEngine(resource_name)

    # Manually register stream-mode methods (the SDK raises on 'async' modes
    # before it can register 'stream' modes, so we do it selectively here).
    if not hasattr(remote_app, "stream_query"):
        import types  # noqa: PLC0415
        from vertexai.reasoning_engines._reasoning_engines import (  # noqa: PLC0415
            _wrap_stream_query_operation,
        )
        for schema in remote_app.operation_schemas():
            if schema.get("api_mode") == "stream":
                method = _wrap_stream_query_operation(
                    method_name=schema["name"],
                    doc=schema.get("description", ""),
                )
                setattr(remote_app, schema["name"], types.MethodType(method, remote_app))

    # Create a session
    session = remote_app.create_session(user_id="test-user")
    session_id = (
        session.get("id")
        or session.get("session_id")
        or session.get("name", "")
    )

    # Stream the response
    for chunk in remote_app.stream_query(
        user_id="test-user",
        session_id=session_id,
        message=prompt,
    ):
        chunk_type = chunk.get("type", "")
        if chunk_type == "text":
            print(chunk["text"], end="", flush=True)
        elif chunk_type == "tool_call":
            tool = chunk.get("name", "unknown")
            print(f"\n[tool call: {tool}]", flush=True)
        elif chunk_type == "tool_result":
            tool = chunk.get("name", "unknown")
            print(f"[tool result: {tool}]", flush=True)

    print("\n" + "=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage the GE User Level Analytics agent on Vertex AI Agent Engine"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("deploy", help="Deploy (or re-deploy) the agent")
    sub.add_parser("list", help="List deployed agents in the project")

    del_p = sub.add_parser("delete", help="Delete a deployed agent")
    del_p.add_argument("--resource-name", required=True, help="Agent Engine resource name")

    test_p = sub.add_parser("test", help="Send a test prompt to a deployed agent")
    test_p.add_argument(
        "--resource-name",
        default="",
        help="Agent Engine resource name (omit to use .last_resource_name)",
    )
    test_p.add_argument(
        "--prompt",
        default=(
            "Run a DRY RUN of the revocation cycle using the 45-day inactivity threshold. "
            "Do not actually revoke any licences. Report how many inactive users you find."
        ),
        help="Prompt to send to the agent",
    )

    args = parser.parse_args()
    dispatch = {
        "deploy": deploy,
        "list": list_agents,
        "delete": delete,
        "test": test_agent,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
