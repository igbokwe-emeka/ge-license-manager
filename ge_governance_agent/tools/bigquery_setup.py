"""
BigQuery Setup tool: ensures Log Analytics is enabled on the _Default bucket
and creates a linked BigQuery dataset for advanced querying.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from google.api_core.exceptions import NotFound
from google.cloud.logging_v2.services.config_service_v2 import ConfigServiceV2Client
from google.cloud.logging_v2.types import Link
from google.protobuf import field_mask_pb2
from ge_governance_agent.auth import get_credentials

logger = logging.getLogger('ge_governance_agent.' + __name__)


def _get_config_client() -> ConfigServiceV2Client:
    credentials = get_credentials(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return ConfigServiceV2Client(credentials=credentials)

def setup_bigquery_log_analytics(dataset_id: str = "logging_analytics", bucket_id: str = "_Default", location: str = "global") -> dict[str, Any]:
    """
    Ensures Log Analytics is enabled on the specified log bucket and 
    creates a linked BigQuery dataset.

    Args:
        dataset_id: The ID of the linked BigQuery dataset to create. Defaults to "logging_analytics".
        bucket_id:  The ID of the log bucket to enable analytics on. Defaults to "_Default".
        location:   The location of the log bucket. Defaults to "global".

    Returns:
        A dict with keys:
          - analytics_enabled: bool
          - dataset_linked: bool
          - dataset_id: str
          - message: str
          - error: str | None
    """
    project_id = os.environ["GCP_PROJECT_ID"]
    config_client = _get_config_client()

    bucket_path = f"projects/{project_id}/locations/{location}/buckets/{bucket_id}"

    # 1. Enable Log Analytics via ConfigServiceV2Client
    try:
        logger.info("Enabling Log Analytics on bucket: %s", bucket_path)
        log_bucket = config_client.get_bucket(request={"name": bucket_path})
        analytics_enabled = log_bucket.analytics_enabled

        if not analytics_enabled:
            log_bucket.analytics_enabled = True
            config_client.update_bucket(
                request={
                    "bucket": log_bucket,
                    "update_mask": field_mask_pb2.FieldMask(paths=["analytics_enabled"]),
                }
            )
            analytics_enabled = True
            logger.info("Log Analytics successfully enabled.")
        else:
            logger.info("Log Analytics is already enabled.")

    except Exception as exc:
        logger.error("Failed to enable Log Analytics: %s", exc)
        return {
            "analytics_enabled": False,
            "dataset_linked": False,
            "dataset_id": dataset_id,
            "message": "Failed to enable Log Analytics",
            "error": str(exc),
        }

    # 2. Create Linked Dataset
    try:
        link_name = f"{bucket_path}/links/{dataset_id}"

        try:
            config_client.get_link(name=link_name)
            logger.info("Linked dataset %s already exists.", dataset_id)
            dataset_linked = True
            message = f"Log Analytics is active and dataset '{dataset_id}' is already linked."
        except NotFound:
            logger.info("Creating linked dataset: %s", dataset_id)
            link = Link(bigquery_dataset={"dataset_id": dataset_id})
            operation = config_client.create_link(parent=bucket_path, link=link, link_id=dataset_id)
            operation.result()
            logger.info("Linked dataset %s created successfully.", dataset_id)
            dataset_linked = True
            message = f"Log Analytics enabled and dataset '{dataset_id}' linked successfully."

    except Exception as exc:
        logger.error("Failed to link dataset: %s", exc)
        return {
            "analytics_enabled": True,
            "dataset_linked": False,
            "dataset_id": dataset_id,
            "message": "Log Analytics enabled but failed to link dataset.",
            "error": str(exc),
        }

    return {
        "analytics_enabled": True,
        "dataset_linked": True,
        "dataset_id": dataset_id,
        "message": message,
        "error": None
    }
