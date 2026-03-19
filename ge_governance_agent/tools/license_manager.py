"""
License Manager tool: uses the Google Discovery Engine API to check and
manage Gemini Enterprise licenses for individual users.

Authoritative Resource:
  projects/*/locations/global/userStores/default_user_store/userLicenses
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from google.cloud import discoveryengine_v1 as discoveryengine
from google.api_core.exceptions import NotFound

from ge_governance_agent.auth import get_credentials

logger = logging.getLogger('ge_governance_agent.' + __name__)

def _get_user_client() -> discoveryengine.UserLicenseServiceClient:
    """Build Discovery Engine UserLicenseServiceClient."""
    # Note: Discovery Engine API uses standard cloud-platform scope.
    creds = get_credentials(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return discoveryengine.UserLicenseServiceClient(credentials=creds)

def _get_parent_resource() -> str:
    """Construct parent resource name for user licenses."""
    project_id = os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        raise ValueError("GCP_PROJECT_ID environment variable is not set.")
    return f"projects/{project_id}/locations/global/userStores/default_user_store"

# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------

def get_user_license_status(user_email: str) -> dict[str, Any]:
    """
    Check whether a user currently holds a Gemini Enterprise license using Discovery Engine API.

    Args:
        user_email: The user's principal email address.

    Returns:
        A dict with keys:
          - user: str
          - has_license: bool
          - state: str (e.g. ASSIGNED, UNASSIGNED)
          - last_login: str (ISO timestamp) | None
          - error: str | None
    """
    client = _get_user_client()
    parent = _get_parent_resource()
    logger.info("Checking Discovery Engine license status for user: %s", user_email)
    
    try:
        # Note: Discovery Engine UserLicense doesn't have a direct 'get' by user_email as a sub-resource ID.
        # We must list or use a filter. For reliability, we list with a filter if supported, or filter locally.
        request = discoveryengine.ListUserLicensesRequest(parent=parent)
        page_result = client.list_user_licenses(request=request)
        
        for response in page_result:
            if response.user_principal.lower() == user_email.lower():
                has_license = (response.license_assignment_state == discoveryengine.UserLicense.LicenseAssignmentState.ASSIGNED)
                return {
                    "user": user_email,
                    "has_license": has_license,
                    "state": response.license_assignment_state.name,
                    "last_login": response.last_login_time.isoformat() if response.last_login_time else None,
                    "error": None,
                }
        
        return {
            "user": user_email,
            "has_license": False,
            "state": "NOT_FOUND",
            "last_login": None,
            "error": None,
        }
        
    except Exception as exc:
        logger.error("API error checking license for %s: %s", user_email, exc)
        return {
            "user": user_email,
            "has_license": False,
            "state": "ERROR",
            "last_login": None,
            "error": str(exc),
        }

def list_all_licensed_users() -> dict[str, Any]:
    """
    List every user currently assigned a Gemini Enterprise license in the Discovery Engine store.

    Returns:
        A dict with key "licensed_users": list of {"user": str, "state": str, "last_login": str}
    """
    client = _get_user_client()
    parent = _get_parent_resource()
    logger.info("Listing all Discovery Engine licensed users...")
    
    licensed: list[dict[str, Any]] = []
    try:
        request = discoveryengine.ListUserLicensesRequest(parent=parent)
        page_result = client.list_user_licenses(request=request)
        
        for response in page_result:
            licensed.append({
                "user": response.user_principal,
                "state": response.license_assignment_state.name,
                "last_login": response.last_login_time.isoformat() if response.last_login_time else None,
            })
        
        return {"licensed_users": licensed, "error": None}
    except Exception as exc:
        logger.error("API error listing licensed users: %s", exc)
        return {"licensed_users": [], "error": str(exc)}

def revoke_gemini_license(user_email: str, dry_run: bool = False) -> dict[str, Any]:
    """
    Revoke the Gemini Enterprise license (unassign from Discovery Engine UserStore).

    Args:
        user_email: The user's principal email address.
        dry_run:    If True, simulate the revocation.

    Returns:
        A dict with keys:
          - user: str
          - revoked: bool
          - message: str
          - error: str | None
    """
    logger.info("Revoking Discovery Engine license for: %s (dry_run=%s)", user_email, dry_run)
    
    # Verify current status
    status = get_user_license_status(user_email)
    if not status["has_license"]:
        return {
            "user": user_email,
            "revoked": False,
            "message": f"User does not have an active license (State: {status['state']}).",
            "error": status.get("error"),
        }

    if dry_run:
        return {
            "user": user_email,
            "revoked": False,
            "message": f"[DRY RUN] Would unassign license from {user_email}.",
            "error": None,
        }

    client = _get_user_client()
    parent = _get_parent_resource()
    
    try:
        # Create a UserLicense object to unassign
        unassigned_license = discoveryengine.UserLicense(
            user_principal=user_email,
            license_assignment_state=discoveryengine.UserLicense.LicenseAssignmentState.UNASSIGNED
        )
        
        # Batch update logic: unassign the user.
        # delete_unassigned_user_licenses=True ensures the record is cleaned up if preferred.
        request = discoveryengine.BatchUpdateUserLicensesRequest(
            parent=parent,
            inline_source=discoveryengine.BatchUpdateUserLicensesRequest.InlineSource(
                user_licenses=[unassigned_license]
            ),
            delete_unassigned_user_licenses=True
        )
        
        client.batch_update_user_licenses(request=request)
        
        logger.info("Successfully revoked license for %s", user_email)
        return {
            "user": user_email,
            "revoked": True,
            "message": f"Successfully revoked Gemini Enterprise license from {user_email}.",
            "error": None,
        }
    except Exception as exc:
        logger.error("API error revoking license for %s: %s", user_email, exc)
        return {
            "user": user_email,
            "revoked": False,
            "message": "License revocation failed.",
            "error": str(exc),
        }
