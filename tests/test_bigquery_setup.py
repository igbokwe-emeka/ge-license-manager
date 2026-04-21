import pytest
import os
from unittest.mock import MagicMock, patch
from ge_governance_agent.tools.bigquery_setup import setup_bigquery_log_analytics

@pytest.fixture
def mock_logging_client():
    with patch("ge_governance_agent.tools.bigquery_setup.logging_v2.Client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client

@pytest.fixture
def mock_config_client():
    with patch("ge_governance_agent.tools.bigquery_setup.ConfigServiceV2Client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client

def test_setup_bigquery_log_analytics_already_setup(mock_logging_client, mock_config_client, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    
    # Mock bucket already having analytics enabled
    mock_bucket = MagicMock()
    mock_bucket.analytics_enabled = True
    mock_logging_client.get_bucket.return_value = mock_bucket
    
    # Mock link already exists
    mock_config_client.get_link.return_value = MagicMock()
    
    result = setup_bigquery_log_analytics(dataset_id="test_ds")
    
    assert result["analytics_enabled"] is True
    assert result["dataset_linked"] is True
    assert "already linked" in result["message"]
    mock_bucket.update.assert_not_called()
    mock_config_client.create_link.assert_not_called()

def test_setup_bigquery_log_analytics_success(mock_logging_client, mock_config_client, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    
    # Mock bucket needing analytics enabled
    mock_bucket = MagicMock()
    mock_bucket.analytics_enabled = False
    mock_logging_client.get_bucket.return_value = mock_bucket
    
    # Mock link doesn't exist (NotFound)
    from google.api_core.exceptions import NotFound
    mock_config_client.get_link.side_effect = NotFound("Link not found")
    
    # Mock create_link success
    mock_operation = MagicMock()
    mock_config_client.create_link.return_value = mock_operation
    
    result = setup_bigquery_log_analytics(dataset_id="new_ds")
    
    assert result["analytics_enabled"] is True
    assert result["dataset_linked"] is True
    assert "linked successfully" in result["message"]
    mock_bucket.update.assert_called_once()
    mock_config_client.create_link.assert_called_once()

def test_setup_bigquery_log_analytics_enable_fails(mock_logging_client, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    
    mock_logging_client.get_bucket.side_effect = Exception("Auth Error")
    
    result = setup_bigquery_log_analytics()
    
    assert result["analytics_enabled"] is False
    assert result["error"] == "Auth Error"
