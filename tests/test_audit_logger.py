import pytest
import os
from unittest.mock import MagicMock, patch
from ge_governance_agent.tools.audit_logger import (
    log_revocation_action,
    log_run_summary,
    _get_logging_client
)

@pytest.fixture
def mock_creds():
    with patch("ge_governance_agent.tools.audit_logger.get_credentials") as mock:
        yield mock

@pytest.fixture
def mock_logging_init():
    with patch("ge_governance_agent.tools.audit_logger.cloud_logging.Client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client

def test_get_logging_client(mock_creds, mock_logging_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    client = _get_logging_client()
    assert client is not None
    mock_creds.assert_called_once()

def test_log_revocation_action_success(mock_logging_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    result = log_revocation_action(
        user_email="user@igboke.com",
        last_activity="2023-01-01",
        revoked=True,
        message="Success"
    )
    assert result["logged"] is True

def test_log_revocation_action_failure(mock_logging_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    mock_logging_init.logger.return_value.log_struct.side_effect = Exception("Log Fail")
    
    result = log_revocation_action(
        user_email="user@igboke.com",
        last_activity="2023-01-01",
        revoked=False,
        message="Fail",
        error="API Error"
    )
    assert result["logged"] is False
    assert result["error"] == "Log Fail"

def test_log_run_summary_success(mock_logging_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    result = log_run_summary(
        run_id="run-123",
        total_inactive=5,
        total_revoked=4,
        total_failed=1,
        inactivity_days=45
    )
    assert result["logged"] is True

def test_log_run_summary_failure(mock_logging_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    mock_logging_init.logger.return_value.log_struct.side_effect = Exception("Summary Fail")
    
    result = log_run_summary(
        run_id="run-123", total_inactive=0, total_revoked=0, total_failed=0, inactivity_days=45
    )
    assert result["logged"] is False
    assert result["error"] == "Summary Fail"
