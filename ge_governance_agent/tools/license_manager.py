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

def _get_parent_resources() -> list[str]:
    """Construct parent resource names for user licenses across all subscriptions."""
    # Support comma-separated list of subscription IDs. Fall back to SUBSCRIPTION_ID then GCP_PROJECT_ID
    subs_env = os.environ.get("SUBSCRIPTION_IDS") or os.environ.get("SUBSCRIPTION_ID") or os.environ.get("GCP_PROJECT_ID")
    if not subs_env:
        raise ValueError("No SUBSCRIPTION_IDS or GCP_PROJECT_ID environment variable is set.")
    
    project_ids = [pid.strip() for pid in subs_env.split(",") if pid.strip()]
    return [f"projects/{pid}/locations/global/userStores/default_user_store" for pid in project_ids]

# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------

_DATE_FMT = "%m-%d-%Y"

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
          - assigned_date: str (MM-DD-YYYY) | "N/A"
          - last_used_date: str (MM-DD-YYYY) | "N/A"
          - parent_resource: str | None
          - error: str | None
    """
    client = _get_user_client()
    parents = _get_parent_resources()
    logger.info("Checking Discovery Engine license status for user: %s", user_email)
    
    errors = []
    
    for parent in parents:
        try:
            # Note: Discovery Engine UserLicense doesn't have a direct 'get' by user_email as a sub-resource ID.
            # We must list or use a filter. For reliability, we list with a filter if supported, or filter locally.
            request = discoveryengine.ListUserLicensesRequest(parent=parent)
            page_result = client.list_user_licenses(request=request)
            
            for response in page_result:
                if response.user_principal.lower() == user_email.lower():
                    has_license = (response.license_assignment_state == discoveryengine.UserLicense.LicenseAssignmentState.ASSIGNED)
                    assigned_date = response.create_time.strftime(_DATE_FMT) if getattr(response, "create_time", None) else "N/A"
                    last_used_date = response.last_login_time.strftime(_DATE_FMT) if getattr(response, "last_login_time", None) else "N/A"
                    
                    if has_license:
                        return {
                            "user": user_email,
                            "has_license": has_license,
                            "state": response.license_assignment_state.name,
                            "assigned_date": assigned_date,
                            "last_used_date": last_used_date,
                            "parent_resource": parent,
                            "error": None,
                        }
                    else:
                        # Found the user but not ASSIGNED, keep looking in other subscriptions just in case
                        # though typically they'd only be in one. We'll return this if we don't find an ASSIGNED one.
                        unassigned_status = {
                            "user": user_email,
                            "has_license": False,
                            "state": response.license_assignment_state.name,
                            "assigned_date": assigned_date,
                            "last_used_date": last_used_date,
                            "parent_resource": parent,
                            "error": None,
                        }
        except Exception as exc:
            logger.error("API error checking license for %s in %s: %s", user_email, parent, exc)
            errors.append(str(exc))
            
    if 'unassigned_status' in locals():
        return unassigned_status
        
    return {
        "user": user_email,
        "has_license": False,
        "state": "ERROR" if errors else "NOT_FOUND",
        "assigned_date": "N/A",
        "last_used_date": "N/A",
        "parent_resource": None,
        "error": " | ".join(errors) if errors else None,
    }

def list_all_licensed_users() -> dict[str, Any]:
    """
    List every user currently assigned a Gemini Enterprise license in the Discovery Engine store.

    Returns:
        A dict with key "licensed_users": list of {"user": str, "state": str, "assigned_date": str, "last_used_date": str, "parent_resource": str}
    """
    client = _get_user_client()
    parents = _get_parent_resources()
    logger.info("Listing all Discovery Engine licensed users...")
    
    licensed: list[dict[str, Any]] = []
    errors = []
    
    for parent in parents:
        try:
            request = discoveryengine.ListUserLicensesRequest(parent=parent)
            page_result = client.list_user_licenses(request=request)
            
            for response in page_result:
                if response.license_assignment_state != discoveryengine.UserLicense.LicenseAssignmentState.ASSIGNED:
                    continue
                assigned_date = response.create_time.strftime(_DATE_FMT) if getattr(response, "create_time", None) else "N/A"
                last_used_date = response.last_login_time.strftime(_DATE_FMT) if getattr(response, "last_login_time", None) else "N/A"
                licensed.append({
                    "user": response.user_principal,
                    "state": response.license_assignment_state.name,
                    "assigned_date": assigned_date,
                    "last_used_date": last_used_date,
                    "parent_resource": parent,
                })
        except Exception as exc:
            logger.error("API error listing licensed users in %s: %s", parent, exc)
            errors.append(str(exc))
            
    if errors and not licensed:
         return {"licensed_users": [], "error": " | ".join(errors)}
    return {"licensed_users": licensed, "error": None}

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
            "message": f"User does not have an active license (State: {status.get('state')}).",
            "error": status.get("error"),
        }

    parent = status.get("parent_resource")
    if not parent:
        return {
             "user": user_email,
             "revoked": False,
             "message": "License parent resource not found.",
             "error": "Parent resource is missing from status."
        }

    if dry_run:
        return {
            "user": user_email,
            "revoked": False,
            "message": f"[DRY RUN] Would unassign license from {user_email} in {parent}.",
            "error": None,
        }

    client = _get_user_client()
    
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
