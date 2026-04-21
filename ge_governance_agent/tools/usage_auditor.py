"""
Usage Auditor tool: generates a user activity audit report and uploads it to GCS.

Data sources:
  - Cloud Log Analytics (BigQuery-backed) → per-entry activity rows with prompts
  - Discovery Engine UserLicense API       → licence assigned date and licence type
  - Discovery Engine LicenseConfig API     → subscription tier → product display name

Each row in the report captures one individual query/activity event:
  - user_email            : user principal (from principalEmail or workforce pool subject)
  - license_assigned_date : when the GE licence was first assigned (MM-DD-YYYY)
  - license_type          : resolved product name from subscription tier
  - activity_date         : date of this specific activity event (MM-DD-YYYY)
  - prompt                : the query text submitted, if captured in audit logs
"""

from __future__ import annotations

import csv
import io
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import google.auth.transport.requests
from google.cloud import bigquery, discoveryengine_v1 as discoveryengine, storage

from ge_governance_agent.auth import get_credentials

logger = logging.getLogger("ge_governance_agent." + __name__)

_DISCOVERY_ENGINE_SERVICE = "discoveryengine.googleapis.com"
_CSV_FIELDS = ["user_email", "license_assigned_date", "license_type", "last_used_date", "prompt"]

_DATE_FMT = "%m-%d-%Y"               # 04-12-2026
_TS_FMT   = "%m-%d-%Y %H:%M:%S UTC"  # 04-12-2026 21:00:00 UTC

# Discovery Engine subscription tier → human-readable product name.
_TIER_NAMES: dict[str, str] = {
    "SUBSCRIPTION_TIER_SEARCH_AND_ASSISTANT": "Gemini Enterprise",
    "SUBSCRIPTION_TIER_SEARCH":               "Enterprise Search",
    "SUBSCRIPTION_TIER_STANDARD":             "Standard",
    "SUBSCRIPTION_TIER_UNSPECIFIED":          "Gemini Enterprise",
    "SUBSCRIPTION_TIER_ENTERPRISE":           "Gemini Enterprise",
    "SUBSCRIPTION_TIER_ENTERPRISE_EMERGING":  "Gemini Enterprise (Emerging)",
    "SUBSCRIPTION_TIER_AGENTSPACE_BUSINESS":  "Gemini Business",
    "SUBSCRIPTION_TIER_AGENTSPACE_STARTER":   "Gemini Business Starter",
    "SUBSCRIPTION_TIER_EDU":                  "Gemini Education",
    "SUBSCRIPTION_TIER_EDU_PRO":              "Gemini Education Premium",
    "SUBSCRIPTION_TIER_FRONTLINE_WORKER":     "Gemini Frontline",
    "SUBSCRIPTION_TIER_FRONTLINE_STARTER":    "Gemini Frontline Starter",
}

# Fallback map for known numeric Workspace SKU IDs and string slugs.
_SKU_NAMES: dict[str, str] = {
    "1010310006":                  "Gemini Enterprise",
    "1010310007":                  "Gemini Business",
    "1010310008":                  "Gemini Education",
    "1010310009":                  "Gemini Education Premium",
    "1010310010":                  "Google AI Meetings and Messaging",
    "gemini-enterprise":           "Gemini Enterprise",
    "gemini-enterprise-add-on":    "Gemini Enterprise Add-On",
    "gemini-enterprise-starter":   "Gemini Enterprise Starter",
    "gemini-business":             "Gemini Business",
    "gemini-education":            "Gemini Education",
    "gemini-education-premium":    "Gemini Education Premium",
    "google-gemini-enterprise":    "Gemini Enterprise",
    "google-gemini-business":      "Gemini Business",
    "internal_only_agent_space":   "Gemini Enterprise (Internal)",
}

# User-facing Discovery Engine methods that represent real user queries.
_USER_METHODS = (
    "%StreamAssist%",
    "%SearchService.Search%",
    "%ConversationalSearchService.AnswerQuery%",
    "%ConversationalSearchService.ConverseConversation%",
)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _get_bq_client() -> bigquery.Client:
    project_id = os.environ["GCP_PROJECT_ID"]
    creds = get_credentials(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return bigquery.Client(project=project_id, credentials=creds)


def _get_gcs_client() -> storage.Client:
    project_id = os.environ["GCP_PROJECT_ID"]
    creds = get_credentials(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return storage.Client(project=project_id, credentials=creds)


def _log_table(project_id: str) -> str:
    dataset = os.environ.get("LOG_DATASET", "logging_analytics")
    view = os.environ.get("LOG_VIEW", "_AllLogs")
    return f"`{project_id}.{dataset}.{view}`"


def _resolve_sku_name(raw_id: str) -> str:
    """Resolve a raw config ID (numeric SKU or slug) to a human-readable name."""
    if not raw_id:
        return "Gemini Enterprise"
    lower = raw_id.lower().strip()
    if lower in _SKU_NAMES:
        return _SKU_NAMES[lower]
    return raw_id.replace("-", " ").replace("_", " ").title()


def _fetch_config_display_name(resource_name: str, session) -> str:
    """
    GET a single LicenseConfig resource from the global Discovery Engine endpoint
    and return its product display name derived from subscriptionTier.
    Falls back to slug resolution on any error.
    """
    try:
        url = f"https://discoveryengine.googleapis.com/v1/{resource_name}"
        resp = session.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            tier = data.get("subscriptionTier", "")
            if tier in _TIER_NAMES:
                return _TIER_NAMES[tier]
    except Exception as exc:
        logger.warning("Could not fetch LicenseConfig %s: %s", resource_name, exc)
    return _resolve_sku_name(resource_name.split("/")[-1])


def _build_config_name_map(config_refs: set[str], creds) -> dict[str, str]:
    """
    Resolve a set of license_config resource names to display names.
    Makes one GET per unique config (typically only 1–3 distinct configs).
    """
    if not config_refs:
        return {}
    session = google.auth.transport.requests.AuthorizedSession(creds)
    return {ref: _fetch_config_display_name(ref, session) for ref in config_refs if ref}


def _query_cloud_logging(
    project_id: str,
    since: datetime,
    until: datetime,
) -> list[dict[str, str]]:
    """
    Query Cloud Log Analytics for each user's most recent Discovery Engine
    activity in the given window, enriched with the query text from the
    gemini_enterprise_user_activity application log.

    The audit log provides user identity + timestamp; the activity log provides
    the actual query text (json_payload.request.query.parts[0].text). The two
    are joined on timestamp proximity (within 30 seconds).

    Returns:
        [{"user_email": str, "last_used_date": "MM-DD-YYYY", "prompt": str}, ...]
    one row per user, sorted by last_used_date descending.
    """
    table = _log_table(project_id)
    activity_log_name = (
        f"projects/{project_id}/logs/"
        "discoveryengine.googleapis.com%2Fgemini_enterprise_user_activity"
    )

    method_filter = " OR ".join(
        f"proto_payload.audit_log.method_name LIKE '{m}'" for m in _USER_METHODS
    )

    sql = f"""
        WITH audit_events AS (
            SELECT
                LOWER(COALESCE(
                    proto_payload.audit_log.authentication_info.principal_email,
                    REGEXP_EXTRACT(
                        proto_payload.audit_log.authentication_info.principal_subject,
                        r'/subject/(.+)$'
                    )
                )) AS user_email,
                timestamp
            FROM {table}
            WHERE
                proto_payload.audit_log.service_name = @service_name
                AND ({method_filter})
                AND timestamp >= @since
                AND timestamp <= @until
                AND COALESCE(
                    proto_payload.audit_log.authentication_info.principal_email,
                    REGEXP_EXTRACT(
                        proto_payload.audit_log.authentication_info.principal_subject,
                        r'/subject/(.+)$'
                    )
                ) IS NOT NULL
                AND NOT ENDS_WITH(
                    LOWER(COALESCE(
                        proto_payload.audit_log.authentication_info.principal_email,
                        REGEXP_EXTRACT(
                            proto_payload.audit_log.authentication_info.principal_subject,
                            r'/subject/(.+)$'
                        )
                    )),
                    '.gserviceaccount.com'
                )
        ),
        user_last_activity AS (
            SELECT
                user_email,
                MAX(timestamp) AS last_activity_ts
            FROM audit_events
            GROUP BY user_email
        ),
        activity_prompts AS (
            SELECT
                timestamp AS prompt_ts,
                JSON_VALUE(json_payload, '$.request.query.parts[0].text') AS prompt
            FROM {table}
            WHERE
                log_name = @activity_log_name
                AND timestamp >= @since
                AND timestamp <= @until
                AND JSON_VALUE(json_payload, '$.request.query.parts[0].text') IS NOT NULL
        ),
        matched AS (
            SELECT
                ula.user_email,
                ula.last_activity_ts,
                ap.prompt,
                ROW_NUMBER() OVER (
                    PARTITION BY ula.user_email
                    ORDER BY ABS(TIMESTAMP_DIFF(ula.last_activity_ts, ap.prompt_ts, MILLISECOND))
                ) AS rn
            FROM user_last_activity ula
            LEFT JOIN activity_prompts ap
            ON ABS(TIMESTAMP_DIFF(ula.last_activity_ts, ap.prompt_ts, MILLISECOND)) < 30000
        )
        SELECT
            user_email,
            last_activity_ts,
            COALESCE(prompt, '') AS prompt
        FROM matched
        WHERE rn = 1
        ORDER BY last_activity_ts DESC
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("service_name", "STRING", _DISCOVERY_ENGINE_SERVICE),
            bigquery.ScalarQueryParameter("since", "TIMESTAMP", since.isoformat()),
            bigquery.ScalarQueryParameter("until", "TIMESTAMP", until.isoformat()),
            bigquery.ScalarQueryParameter("activity_log_name", "STRING", activity_log_name),
        ]
    )

    client = _get_bq_client()
    rows = client.query(sql, job_config=job_config).result()

    entries: list[dict[str, str]] = []
    for row in rows:
        email = (row["user_email"] or "").strip()
        if not email or email.endswith(".gserviceaccount.com"):
            continue
        ts = row["last_activity_ts"]
        entries.append({
            "user_email": email,
            "last_used_date": ts.strftime(_DATE_FMT) if ts else "",
            "prompt": row["prompt"] or "",
        })

    return entries


def _fetch_license_details() -> dict[str, dict[str, str]]:
    """
    Return a dict keyed by lower-cased user email with licence metadata.

        {email: {"assigned_date": "MM-DD-YYYY", "license_type": "<product name>"}}
    """
    subs_env = os.environ.get("SUBSCRIPTION_IDS") or os.environ.get("SUBSCRIPTION_ID") or os.environ.get("GCP_PROJECT_ID")
    if not subs_env:
        raise ValueError("No SUBSCRIPTION_IDS or GCP_PROJECT_ID environment variable is set.")
    
    project_ids = [pid.strip() for pid in subs_env.split(",") if pid.strip()]
    parents = [f"projects/{pid}/locations/global/userStores/default_user_store" for pid in project_ids]
    
    creds = get_credentials(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    client = discoveryengine.UserLicenseServiceClient(credentials=creds)

    raw_licenses = []
    for parent in parents:
        try:
            raw_licenses.extend(list(client.list_user_licenses(
                request=discoveryengine.ListUserLicensesRequest(parent=parent)
            )))
        except Exception as exc:
            logger.warning("Could not fetch licence details for %s: %s", parent, exc)

    config_refs = {getattr(lic, "license_config", "") or "" for lic in raw_licenses}
    config_refs.discard("")
    config_name_map = _build_config_name_map(config_refs, creds)

    license_map: dict[str, dict[str, str]] = {}
    for lic in raw_licenses:
        email = lic.user_principal.lower()
        create_time = getattr(lic, "create_time", None)
        config_ref = getattr(lic, "license_config", "") or ""

        if config_ref in config_name_map:
            license_type = config_name_map[config_ref]
        elif config_ref:
            license_type = _resolve_sku_name(config_ref.split("/")[-1])
        else:
            license_type = "Gemini Enterprise"

        license_map[email] = {
            "assigned_date": create_time.strftime(_DATE_FMT) if create_time else "N/A",
            "license_type": license_type,
        }
    return license_map


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------


def query_user_activity(hours_back: int = 24) -> dict[str, Any]:
    """
    Fetch the last Gemini Enterprise activity event (StreamAssist, Search)
    for each user in the specified lookback window via Cloud Log Analytics, 
    enriched with licence metadata.

    Each row represents one user's most recent query/event.

    Args:
        hours_back: How many hours back to look. Defaults to 24.

    Returns:
        {
          "audit_rows": list[dict] — one entry per user with keys:
              user_email, license_assigned_date, license_type, last_used_date, prompt
          "window_start": str (MM-DD-YYYY HH:MM:SS UTC),
          "window_end":   str (MM-DD-YYYY HH:MM:SS UTC),
          "hours_back":   int,
          "row_count":    int  (total users found),
          "error":        str | None
        }
    """
    project_id = os.environ["GCP_PROJECT_ID"]
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours_back)

    try:
        activity_entries = _query_cloud_logging(project_id, since, now)
    except Exception as exc:
        logger.error("Cloud Logging query failed: %s", exc)
        return {
            "audit_rows": [],
            "window_start": since.strftime(_TS_FMT),
            "window_end": now.strftime(_TS_FMT),
            "hours_back": hours_back,
            "row_count": 0,
            "error": str(exc),
        }

    license_map = _fetch_license_details()

    audit_rows: list[dict[str, str]] = []
    for entry in activity_entries:
        email = entry["user_email"]
        lic = license_map.get(email, {})
        audit_rows.append({
            "user_email": email,
            "license_assigned_date": lic.get("assigned_date", "N/A"),
            "license_type": lic.get("license_type", "Gemini Enterprise"),
            "last_used_date": entry["last_used_date"],
            "prompt": entry.get("prompt", ""),
        })

    return {
        "audit_rows": audit_rows,
        "window_start": since.strftime(_TS_FMT),
        "window_end": now.strftime(_TS_FMT),
        "hours_back": hours_back,
        "row_count": len(audit_rows),
        "error": None,
    }


def upload_audit_to_gcs(audit_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Serialise audit rows to CSV and upload to GCS under a datetime-stamped path.

    The destination follows the pattern:
        gs://<AUDIT_BUCKET>/YYYY/MM/DD/HH-MM/usage_audit.csv

    Args:
        audit_rows: List of row dicts returned by query_user_activity().

    Returns:
        {"gcs_uri": str, "row_count": int, "error": str | None}
    """
    raw_bucket = os.environ["AUDIT_BUCKET"]
    bucket_name = raw_bucket.removeprefix("gs://")
    now = datetime.now(timezone.utc)
    blob_path = f"{now.strftime('%Y/%m/%d/%H-%M')}/usage_audit.csv"

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(audit_rows)
    csv_bytes = buf.getvalue().encode("utf-8")

    try:
        client = _get_gcs_client()
        blob = client.bucket(bucket_name).blob(blob_path)
        blob.upload_from_string(csv_bytes, content_type="text/csv")
        gcs_uri = f"gs://{bucket_name}/{blob_path}"
        logger.info("Audit report uploaded: %s (%d rows)", gcs_uri, len(audit_rows))
        return {"gcs_uri": gcs_uri, "row_count": len(audit_rows), "error": None}
    except Exception as exc:
        logger.error("GCS upload failed: %s", exc)
        return {"gcs_uri": None, "row_count": len(audit_rows), "error": str(exc)}
