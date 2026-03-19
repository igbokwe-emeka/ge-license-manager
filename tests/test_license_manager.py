import pytest
import os
from unittest.mock import MagicMock, patch
from google.cloud import discoveryengine_v1 as discoveryengine
from ge_governance_agent.tools.license_manager import (
    list_all_licensed_users,
    get_user_license_status,
    revoke_gemini_license,
    _get_user_client,
    _get_parent_resource
)

@pytest.fixture
def mock_creds():
    with patch("ge_governance_agent.tools.license_manager.get_credentials") as mock:
        yield mock

@pytest.fixture
def mock_client_init():
    with patch("ge_governance_agent.tools.license_manager.discoveryengine.UserLicenseServiceClient") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client

def test_get_user_client(mock_creds, mock_client_init):
    client = _get_user_client()
    assert client is not None
    mock_creds.assert_called_once()

def test_get_parent_resource_success(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    parent = _get_parent_resource()
    assert "test-project" in parent

def test_get_parent_resource_failure(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    with pytest.raises(ValueError, match="GCP_PROJECT_ID"):
        _get_parent_resource()

def test_list_all_licensed_users_success(mock_client_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    mock_license = MagicMock()
    mock_license.user_principal = "admin-igboke@igbokwe.altostrat.com"
    mock_license.license_assignment_state.name = "ASSIGNED"
    mock_license.last_login_time = None
    mock_client_init.list_user_licenses.return_value = [mock_license]
    
    result = list_all_licensed_users()
    assert len(result["licensed_users"]) == 1
    assert result["licensed_users"][0]["user"] == "admin-igboke@igbokwe.altostrat.com"

def test_list_all_licensed_users_error(mock_client_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    mock_client_init.list_user_licenses.side_effect = Exception("List Fail")
    result = list_all_licensed_users()
    assert result["error"] == "List Fail"

def test_get_user_license_status_assigned(mock_client_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    mock_license = MagicMock()
    mock_license.user_principal = "user1-igboke@igbokwe.altostrat.com"
    mock_license.license_assignment_state = discoveryengine.UserLicense.LicenseAssignmentState.ASSIGNED
    mock_license.last_login_time = None
    mock_client_init.list_user_licenses.return_value = [mock_license]
    
    status = get_user_license_status("user1-igboke@igbokwe.altostrat.com")
    assert status["has_license"] is True
    assert status["state"] == "ASSIGNED"

def test_get_user_license_status_not_found(mock_client_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    mock_client_init.list_user_licenses.return_value = []
    status = get_user_license_status("missing@igboke.com")
    assert status["state"] == "NOT_FOUND"
    assert status["has_license"] is False

def test_get_user_license_status_exception(mock_client_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    mock_client_init.list_user_licenses.side_effect = Exception("Status Fail")
    status = get_user_license_status("error@igboke.com")
    assert status["state"] == "ERROR"
    assert "Status Fail" in status["error"]

def test_revoke_gemini_license_success(mock_client_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    with patch("ge_governance_agent.tools.license_manager.get_user_license_status") as mock_status:
        mock_status.return_value = {"has_license": True, "state": "ASSIGNED", "error": None}
        result = revoke_gemini_license("user-igboke@igbokwe.altostrat.com")
        assert result["revoked"] is True
        assert "Successfully revoked" in result["message"]

def test_revoke_gemini_license_no_license(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    with patch("ge_governance_agent.tools.license_manager.get_user_license_status") as mock_status:
        mock_status.return_value = {"has_license": False, "state": "NOT_FOUND", "error": None}
        result = revoke_gemini_license("user-igboke@igbokwe.altostrat.com")
        assert result["revoked"] is False
        assert "does not have an active license" in result["message"]

def test_revoke_gemini_license_dry_run(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    with patch("ge_governance_agent.tools.license_manager.get_user_license_status") as mock_status:
        mock_status.return_value = {"has_license": True, "state": "ASSIGNED", "error": None}
        result = revoke_gemini_license("user-igboke@igbokwe.altostrat.com", dry_run=True)
        assert "[DRY RUN]" in result["message"]
        assert result["revoked"] is False

def test_revoke_gemini_license_exception(mock_client_init, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "igbokwe")
    with patch("ge_governance_agent.tools.license_manager.get_user_license_status") as mock_status:
        mock_status.return_value = {"has_license": True, "state": "ASSIGNED", "error": None}
        mock_client_init.batch_update_user_licenses.side_effect = Exception("Revoke Fail")
        result = revoke_gemini_license("user-igboke@igbokwe.altostrat.com")
        assert result["revoked"] is False
        assert result["error"] == "Revoke Fail"
