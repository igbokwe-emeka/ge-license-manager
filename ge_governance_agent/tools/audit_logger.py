"""
Audit Logger tool: persists a structured record of every revocation action
to Google Cloud Logging so that administrators have a durable audit trail.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from google.cloud import logging as cloud_logging
from ge_governance_agent.auth import get_credentials

_LOG_NAME = "gemini-enterprise-revocation-audit"


def _get_logging_client() -> cloud_logging.Client:
    project_id = os.environ["GCP_PROJECT_ID"]
    credentials = get_credentials(scopes=["https://www.googleapis.com/auth/logging.write"])
    return cloud_logging.Client(project=project_id, credentials=credentials)


def log_revocation_action(
    user_email: str,
    last_activity: str,
    revoked: bool,
    message: str,
    dry_run: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    """
    Write a structured audit log entry to Cloud Logging for a licence revocation.

    Args:
        user_email:     The user whose licence was (or was not) revoked.
        last_activity:  ISO date of the user's last recorded activity.
        revoked:        Whether the revocation succeeded.
        message:        Human-readable outcome description.
        dry_run:        Whether this was a simulation (no real revocation).
        error:          Error string if the revocation failed, else None.

    Returns:
        {"logged": bool, "error": str | None}
    """
    client = _get_logging_client()
    logger = client.logger(_LOG_NAME)

    entry: dict[str, Any] = {
        "event": "license_revocation",
        "user": user_email,
        "last_activity": last_activity,
        "revoked": revoked,
        "dry_run": dry_run,
        "message": message,
        "error": error,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "ge-user-level-analytics",
    }

    severity = "WARNING" if not revoked and error else ("INFO" if revoked else "NOTICE")

    try:
        logger.log_struct(entry, severity=severity)
        return {"logged": True, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"logged": False, "error": str(exc)}


def log_run_summary(
    run_id: str,
    total_inactive: int,
    total_revoked: int,
    total_failed: int,
    inactivity_days: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Write a single summary entry at the end of an agent run.

    Args:
        run_id:           Unique identifier for this agent run.
        total_inactive:   Number of users identified as inactive.
        total_revoked:    Number of licences successfully revoked.
        total_failed:     Number of revocations that failed.
        inactivity_days:  Threshold used for this run.
        dry_run:          Whether the run was a simulation.

    Returns:
        {"logged": bool, "error": str | None}
    """
    client = _get_logging_client()
    logger = client.logger(_LOG_NAME)

    entry: dict[str, Any] = {
        "event": "run_summary",
        "run_id": run_id,
        "inactivity_threshold_days": inactivity_days,
        "total_inactive_users": total_inactive,
        "total_revoked": total_revoked,
        "total_failed": total_failed,
        "dry_run": dry_run,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "ge-user-level-analytics",
    }

    try:
        logger.log_struct(entry, severity="INFO")
        return {"logged": True, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"logged": False, "error": str(exc)}
