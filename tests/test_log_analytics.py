import pytest
import os
from unittest.mock import MagicMock, patch
from datetime import datetime, date, timedelta, timezone
from ge_governance_agent.tools.log_analytics import (
    query_inactive_users,
    query_discovery_engine_inactivity,
    query_user_last_activity,
    query_daily_usage,
    _get_bq_client
)

@pytest.fixture
def mock_creds():
    with patch("ge_governance_agent.tools.log_analytics.get_credentials") as mock:
        yield mock

@pytest.fixture
def mock_bq_init():
    with patch("ge_governance_agent.tools.log_analytics.bigquery.Client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client

def test_get_bq_client(mock_creds, mock_bq_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    client = _get_bq_client()
    assert client is not None
    mock_creds.assert_called_once()

def test_query_inactive_users_success(mock_bq_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    mock_row = {"user": "user-igboke@igbokwe.altostrat.com", "last_activity": date.today() - timedelta(days=50)}
    mock_bq_init.query.return_value.result.return_value = [mock_row]
    
    result = query_inactive_users(inactivity_days=45)
    assert len(result["inactive_users"]) == 1

def test_query_inactive_users_filter_service_account(mock_bq_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    mock_rows = [
        {"user": "human-igboke@igbokwe.altostrat.com", "last_activity": date.today() - timedelta(days=50)},
        {"user": "service-igboke@igbokwe.altostrat.com.gserviceaccount.com", "last_activity": date.today() - timedelta(days=50)}
    ]
    mock_bq_init.query.return_value.result.return_value = mock_rows
    
    result = query_inactive_users(inactivity_days=45)
    assert len(result["inactive_users"]) == 1
    assert result["inactive_users"][0]["user"] == "human-igboke@igbokwe.altostrat.com"

def test_query_discovery_engine_inactivity_full(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    with patch("ge_governance_agent.tools.log_analytics.list_all_licensed_users") as mock_list:
        mock_list.return_value = {
            "licensed_users": [
                {"user": "active@igboke.com", "state": "ASSIGNED", "last_login": datetime.now(timezone.utc).isoformat()},
                {"user": "dormant@igboke.com", "state": "ASSIGNED", "last_login": (datetime.now(timezone.utc) - timedelta(days=50)).isoformat()},
                {"user": "never@igboke.com", "state": "ASSIGNED", "last_login": None},
                {"user": "unassigned@igboke.com", "state": "UNASSIGNED", "last_login": None}
            ]
        }
        result = query_discovery_engine_inactivity(inactivity_days=45)
        # Dormant and Never should be in inactive_users. Unassigned is skipped. Active is skipped.
        assert len(result["inactive_users"]) == 2
        users = [u["user"] for u in result["inactive_users"]]
        assert "dormant@igboke.com" in users
        assert "never@igboke.com" in users

def test_query_discovery_engine_inactivity_list_error(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    with patch("ge_governance_agent.tools.log_analytics.list_all_licensed_users") as mock_list:
        mock_list.return_value = {"error": "API Error"}
        result = query_discovery_engine_inactivity()
        assert result["error"] == "API Error"

def test_query_user_last_activity_success(mock_bq_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    mock_row = {"last_activity": date.today() - timedelta(days=10), "last_method": "Search"}
    mock_bq_init.query.return_value.result.return_value = [mock_row]
    
    result = query_user_last_activity("user-igboke@igbokwe.altostrat.com")
    assert result["last_method"] == "Search"
    assert result["days_since_activity"] == 10

def test_query_user_last_activity_never(mock_bq_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    mock_bq_init.query.return_value.result.return_value = []
    
    result = query_user_last_activity("never-igboke@igbokwe.altostrat.com")
    assert result["last_activity"] == "never"

def test_query_inactive_users_with_linked_dataset(mock_bq_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    monkeypatch.setenv("LOG_DATASET", "my_linked_ds")
    mock_row = {"user": "user@igboke.com", "last_activity": date.today()}
    mock_bq_init.query.return_value.result.return_value = [mock_row]
    
    query_inactive_users()
    
    # Verify the SQL uses the linked dataset
    sql = mock_bq_init.query.call_args[0][0]
    assert "`igbokwe.my_linked_ds._AllLogs`" in sql

def test_query_daily_usage_success(mock_bq_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    mock_row = {
        "date": "2023-01-01", "engine_id": "eng1", "data_source": "ds1",
        "daily_active_users": 10.0, "search_count": 5.0, "answer_count": 2.0,
        "seats_purchased": 100.0, "seats_claimed": 50.0
    }
    mock_bq_init.query.return_value.result.return_value = [mock_row]
    
    result = query_daily_usage(days_back=7)
    assert len(result["usage_records"]) == 1
    assert result["usage_records"][0]["engine_id"] == "eng1"
