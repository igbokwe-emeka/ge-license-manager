"""
Log Analytics tool: queries Cloud Logging (BigQuery-backed) to find
inactive Gemini Enterprise users based on Discovery Engine API audit logs.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

from google.cloud import bigquery
from ge_governance_agent.auth import get_credentials
from ge_governance_agent.tools.license_manager import list_all_licensed_users

logger = logging.getLogger('ge_governance_agent.' + __name__)

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

_DISCOVERY_ENGINE_SERVICE = "discoveryengine.googleapis.com"

_ACTIVE_METHODS = [
    "google.cloud.discoveryengine.v1main.AssistantService.StreamAssist",
    "google.cloud.discoveryengine.v1main.SearchService.Search",
    "google.cloud.discoveryengine.v1.AssistantService.StreamAssist",
    "google.cloud.discoveryengine.v1.SearchService.Search",
]


def _get_bq_client() -> bigquery.Client:
    project_id = os.environ["GCP_PROJECT_ID"]
    credentials = get_credentials(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return bigquery.Client(project=project_id, credentials=credentials)


def _log_table(project_id: str) -> str:
    dataset = os.environ.get("LOG_DATASET", "logging_analytics")
    view = os.environ.get("LOG_VIEW", "_AllLogs")
    return f"`{project_id}.{dataset}.{view}`"


# ---------------------------------------------------------------------------
# Public tool functions (called by the ADK agent)
# ---------------------------------------------------------------------------


def query_inactive_users(inactivity_days: int = 45) -> dict[str, Any]:
    """
    Query Cloud Log Analytics for users who have not interacted with the
    Gemini Enterprise (Discovery Engine) API within `inactivity_days` days.

    Args:
        inactivity_days: Number of days of inactivity that triggers revocation.
                         Defaults to 45.

    Returns:
        A dict with keys:
          - inactive_users: list of {"user": str, "last_activity": str (ISO date)}
          - threshold_date: ISO date string used as the cutoff
          - queried_at: ISO timestamp of when the query ran
    """
    project_id = os.environ["GCP_PROJECT_ID"]
    table = _log_table(project_id)
    threshold = date.today() - timedelta(days=inactivity_days)

    sql = f"""
        SELECT
            proto_payload.audit_log.authentication_info.principal_email AS user,
            MAX(DATE(timestamp)) AS last_activity
        FROM
            {table}
        WHERE
            proto_payload.audit_log.service_name = @service_name
        GROUP BY
            1
        HAVING
            MAX(DATE(timestamp)) < @threshold
            OR MAX(DATE(timestamp)) IS NULL
        ORDER BY
            last_activity ASC
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("service_name", "STRING", _DISCOVERY_ENGINE_SERVICE),
            bigquery.ScalarQueryParameter("threshold", "DATE", threshold.isoformat()),
        ]
    )

    logger.info("Querying inactive users with threshold: %d days (%s)", inactivity_days, threshold.isoformat())
    client = _get_bq_client()
    query_job = client.query(sql, job_config=job_config)
    rows = query_job.result()
    logger.debug("BigQuery job %s completed.", query_job.job_id)

    inactive: list[dict[str, str]] = []
    for row in rows:
        email = row["user"]
        last_act = row["last_activity"]
        # Skip service accounts and internal system principals
        if not email or email.endswith(".gserviceaccount.com"):
            continue
        inactive.append(
            {
                "user": email,
                "last_activity": last_act.isoformat() if last_act else "never",
            }
        )

    return {
        "inactive_users": inactive,
        "threshold_date": threshold.isoformat(),
        "queried_at": datetime.now(timezone.utc).isoformat(),
    }


def query_discovery_engine_inactivity(inactivity_days: int = 45) -> dict[str, Any]:
    """
    Find inactive users by checking their lastLoginTime as reported by the 
    Discovery Engine License API. This is the "correct approach" for identify 
    dormant Gemini Enterprise seats.

    Args:
        inactivity_days: Days of inactivity threshold.

    Returns:
        A dict with inactive_users list and metadata.
    """
    result = list_all_licensed_users()
    if result.get("error"):
        return {"inactive_users": [], "error": result["error"]}

    threshold = datetime.now(timezone.utc) - timedelta(days=inactivity_days)
    inactive: list[dict[str, str]] = []

    for user in result.get("licensed_users", []):
        if user["state"] != "ASSIGNED":
            continue
        
        last_login_str = user.get("last_login")
        if not last_login_str:
            # Never logged in? Consider inactive.
            inactive.append({
                "user": user["user"],
                "last_activity": "never",
            })
            continue

        last_login = datetime.fromisoformat(last_login_str)
        if last_login < threshold:
            inactive.append({
                "user": user["user"],
                "last_activity": last_login.isoformat(),
            })

    return {
        "inactive_users": inactive,
        "threshold_date": threshold.isoformat(),
        "queried_at": datetime.now(timezone.utc).isoformat(),
        "source": "Discovery Engine License API"
    }


def query_user_last_activity(user_email: str) -> dict[str, Any]:
    """
    Return the last activity date and method for a specific user.

    Args:
        user_email: The principal email address to look up.

    Returns:
        A dict with keys:
          - user: str
          - last_activity: ISO date string or "never"
          - last_method: most recent API method called, or null
          - days_since_activity: int
    """
    project_id = os.environ["GCP_PROJECT_ID"]
    table = _log_table(project_id)

    sql = f"""
        SELECT
            proto_payload.audit_log.authentication_info.principal_email AS user,
            MAX(DATE(timestamp))                                         AS last_activity,
            ARRAY_AGG(
                proto_payload.audit_log.method_name
                ORDER BY timestamp DESC
                LIMIT 1
            )[OFFSET(0)]                                                 AS last_method
        FROM
            {table}
        WHERE
            proto_payload.audit_log.service_name = @service_name
            AND proto_payload.audit_log.authentication_info.principal_email = @email
        GROUP BY
            1
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("service_name", "STRING", _DISCOVERY_ENGINE_SERVICE),
            bigquery.ScalarQueryParameter("email", "STRING", user_email),
        ]
    )

    logger.info("Querying last activity for user: %s", user_email)
    client = _get_bq_client()
    rows = list(client.query(sql, job_config=job_config).result())

    if not rows:
        return {
            "user": user_email,
            "last_activity": "never",
            "last_method": None,
            "days_since_activity": None,
        }

    row = rows[0]
    last_act: date | None = row["last_activity"]
    days_since = (date.today() - last_act).days if last_act else None

    return {
        "user": user_email,
        "last_activity": last_act.isoformat() if last_act else "never",
        "last_method": row["last_method"],
        "days_since_activity": days_since,
    }


def query_daily_usage(days_back: int = 30) -> dict[str, Any]:
    """
    Return a daily usage breakdown from the Discovery Engine / Gemini Enterprise
    analytics export in BigQuery ({project_id}.geminienterprise.analytics).

    Args:
        days_back: Number of past days to include in the report. Default 30.

    Returns:
        A dict with key "usage_records": list of
          {"date": str, "engine_id": str, "data_source": str,
           "daily_active_users": float|None, "search_count": float|None,
           "answer_count": float|None, "seats_purchased": float|None,
           "seats_claimed": float|None}
    """
    project_id = os.environ["GCP_PROJECT_ID"]
    since = date.today() - timedelta(days=days_back)

    sql = f"""
        SELECT
            date,
            engine_id,
            data_source,
            SUM(daily_active_user_count)  AS daily_active_users,
            SUM(search_count)             AS search_count,
            SUM(answer_count)             AS answer_count,
            MAX(seats_purchased)          AS seats_purchased,
            MAX(seats_claimed)            AS seats_claimed
        FROM
            `{project_id}.geminienterprise.analytics`
        WHERE
            date >= @since
            AND date IS NOT NULL
        GROUP BY
            1, 2, 3
        ORDER BY
            1 DESC, 2, 3
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("since", "STRING", since.isoformat()),
        ]
    )

    client = _get_bq_client()
    rows = client.query(sql, job_config=job_config).result()

    records = [
        {
            "date": row["date"],
            "engine_id": row["engine_id"],
            "data_source": row["data_source"],
            "daily_active_users": row["daily_active_users"],
            "search_count": row["search_count"],
            "answer_count": row["answer_count"],
            "seats_purchased": row["seats_purchased"],
            "seats_claimed": row["seats_claimed"],
        }
        for row in rows
    ]

    return {"usage_records": records}
