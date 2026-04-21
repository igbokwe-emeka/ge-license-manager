import pytest
import io
import csv
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from ge_governance_agent.tools.usage_auditor import (
    query_user_activity,
    upload_audit_to_gcs,
    _query_cloud_logging,
    _fetch_license_details,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_creds():
    with patch("ge_governance_agent.tools.usage_auditor.get_credentials") as m:
        m.return_value = MagicMock()
        yield m


@pytest.fixture
def mock_bq_client(mock_creds):
    with patch("ge_governance_agent.tools.usage_auditor._get_bq_client") as m:
        client = MagicMock()
        m.return_value = client
        yield client


@pytest.fixture
def mock_license_details():
    with patch("ge_governance_agent.tools.usage_auditor._fetch_license_details") as m:
        m.return_value = {
            "alice@example.com": {
                "assigned_date": "01-15-2024",
                "license_type": "Gemini Enterprise",
            }
        }
        yield m


@pytest.fixture
def mock_cloud_logging():
    with patch("ge_governance_agent.tools.usage_auditor._query_cloud_logging") as m:
        yield m


@pytest.fixture
def mock_gcs_client(mock_creds):
    with patch("ge_governance_agent.tools.usage_auditor._get_gcs_client") as m:
        client = MagicMock()
        m.return_value = client
        yield client


# ---------------------------------------------------------------------------
# _fetch_license_details
# ---------------------------------------------------------------------------


def test_fetch_license_details_success(mock_creds, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")

    mock_lic = MagicMock()
    mock_lic.user_principal = "Alice@Example.com"
    mock_lic.create_time = datetime(2024, 1, 15, tzinfo=timezone.utc)
    mock_lic.license_config = "projects/p/licenseConfigs/gemini-enterprise"

    with patch(
        "ge_governance_agent.tools.usage_auditor.discoveryengine.UserLicenseServiceClient"
    ) as mock_cls:
        mock_cls.return_value.list_user_licenses.return_value = [mock_lic]
        result = _fetch_license_details()

    assert "alice@example.com" in result
    assert result["alice@example.com"]["assigned_date"] == "01-15-2024"
    assert result["alice@example.com"]["license_type"] == "Gemini Enterprise"


def test_fetch_license_details_api_error(mock_creds, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")

    with patch(
        "ge_governance_agent.tools.usage_auditor.discoveryengine.UserLicenseServiceClient"
    ) as mock_cls:
        mock_cls.return_value.list_user_licenses.side_effect = Exception("API down")
        result = _fetch_license_details()

    assert result == {}


# ---------------------------------------------------------------------------
# _query_cloud_logging
# ---------------------------------------------------------------------------


def test_query_cloud_logging_extracts_email(mock_bq_client, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")

    mock_row = {
        "user_email": "alice@example.com",
        "last_activity_ts": datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
    }
    mock_bq_client.query.return_value.result.return_value = [mock_row]

    since = datetime(2026, 4, 8, 10, 0, tzinfo=timezone.utc)
    until = datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc)
    result = _query_cloud_logging("test-project", since, until)

    assert len(result) == 1
    assert result[0]["user_email"] == "alice@example.com"
    assert result[0]["last_used_date"] == "04-09-2026"


def test_query_cloud_logging_extracts_conversational(mock_bq_client, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")

    mock_row = {
        "user_email": "bob@example.com",
        "last_activity_ts": datetime(2026, 4, 9, 11, 0, tzinfo=timezone.utc),
    }
    mock_bq_client.query.return_value.result.return_value = [mock_row]

    result = _query_cloud_logging("test-project",
                                  datetime(2026, 4, 8, tzinfo=timezone.utc),
                                  datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc))

    assert len(result) == 1
    assert result[0]["user_email"] == "bob@example.com"
    assert result[0]["last_used_date"] == "04-09-2026"


def test_query_cloud_logging_skips_service_accounts(mock_bq_client, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")

    mock_row = {
        "user_email": "svc@project.iam.gserviceaccount.com",
        "last_activity_ts": datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
    }
    mock_bq_client.query.return_value.result.return_value = [mock_row]

    result = _query_cloud_logging("test-project",
                                  datetime(2026, 4, 8, tzinfo=timezone.utc),
                                  datetime(2026, 4, 9, tzinfo=timezone.utc))
    assert len(result) == 0


# ---------------------------------------------------------------------------
# query_user_activity
# ---------------------------------------------------------------------------


def test_query_user_activity_success(mock_cloud_logging, mock_license_details, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")

    mock_cloud_logging.return_value = [
        {
            "user_email": "alice@example.com",
            "last_used_date": "04-09-2026",
        }
    ]

    result = query_user_activity()

    assert result["error"] is None
    assert result["row_count"] == 1
    assert result["hours_back"] == 24
    row = result["audit_rows"][0]
    assert row["user_email"] == "alice@example.com"
    assert row["license_type"] == "Gemini Enterprise"
    assert row["last_used_date"] == "04-09-2026"


def test_query_user_activity_unknown_user_gets_defaults(mock_cloud_logging, mock_license_details, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")

    mock_cloud_logging.return_value = [
        {"user_email": "unknown@example.com", "last_used_date": "04-09-2026"}
    ]

    result = query_user_activity()
    row = result["audit_rows"][0]
    assert row["license_assigned_date"] == "N/A"
    assert row["license_type"] == "Gemini Enterprise"


def test_query_user_activity_empty(mock_cloud_logging, mock_license_details, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    mock_cloud_logging.return_value = []

    result = query_user_activity()
    assert result["row_count"] == 0
    assert result["audit_rows"] == []
    assert result["error"] is None


def test_query_user_activity_logging_error(mock_cloud_logging, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    mock_cloud_logging.side_effect = Exception("Logging API down")

    result = query_user_activity()
    assert result["error"] == "Logging API down"
    assert result["audit_rows"] == []
    assert result["hours_back"] == 24


def test_query_user_activity_custom_hours_back(mock_cloud_logging, mock_license_details, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    mock_cloud_logging.return_value = []

    result = query_user_activity(hours_back=168)
    assert result["hours_back"] == 168
    # _TS_FMT is "%m-%d-%Y %H:%M:%S UTC"
    window_start = datetime.strptime(result["window_start"], "%m-%d-%Y %H:%M:%S UTC").replace(tzinfo=timezone.utc)
    window_end = datetime.strptime(result["window_end"], "%m-%d-%Y %H:%M:%S UTC").replace(tzinfo=timezone.utc)
    assert (window_end - window_start).total_seconds() == pytest.approx(168 * 3600, abs=5)


# ---------------------------------------------------------------------------
# upload_audit_to_gcs
# ---------------------------------------------------------------------------

_SAMPLE_ROWS = [
    {
        "user_email": "alice@example.com",
        "license_assigned_date": "01-15-2024",
        "license_type": "Gemini Enterprise",
        "last_used_date": "04-09-2026",
    }
]


def test_upload_audit_success(mock_gcs_client, monkeypatch):
    monkeypatch.setenv("AUDIT_BUCKET", "gs://my-audit-bucket")
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    blob = MagicMock()
    mock_gcs_client.bucket.return_value.blob.return_value = blob

    result = upload_audit_to_gcs(_SAMPLE_ROWS)

    assert result["error"] is None
    assert result["row_count"] == 1
    assert result["gcs_uri"].startswith("gs://my-audit-bucket/")
    assert result["gcs_uri"].endswith("/usage_audit.csv")
    blob.upload_from_string.assert_called_once()


def test_upload_audit_strips_gs_prefix(mock_gcs_client, monkeypatch):
    monkeypatch.setenv("AUDIT_BUCKET", "gs://my-audit-bucket")
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    mock_gcs_client.bucket.return_value.blob.return_value = MagicMock()

    upload_audit_to_gcs(_SAMPLE_ROWS)
    mock_gcs_client.bucket.assert_called_once_with("my-audit-bucket")


def test_upload_audit_gcs_error(mock_gcs_client, monkeypatch):
    monkeypatch.setenv("AUDIT_BUCKET", "gs://my-audit-bucket")
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    mock_gcs_client.bucket.return_value.blob.return_value.upload_from_string.side_effect = Exception("GCS down")

    result = upload_audit_to_gcs(_SAMPLE_ROWS)
    assert result["gcs_uri"] is None
    assert result["error"] == "GCS down"


def test_upload_audit_csv_content(mock_gcs_client, monkeypatch):
    monkeypatch.setenv("AUDIT_BUCKET", "gs://my-audit-bucket")
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    blob = MagicMock()
    mock_gcs_client.bucket.return_value.blob.return_value = blob

    upload_audit_to_gcs(_SAMPLE_ROWS)

    csv_bytes = blob.upload_from_string.call_args[0][0]
    csv_text = csv_bytes.decode("utf-8")
    assert "user_email" in csv_text
    assert "alice@example.com" in csv_text
    assert "last_used_date" in csv_text
