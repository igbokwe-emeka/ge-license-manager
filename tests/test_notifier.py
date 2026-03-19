import pytest
import os
from unittest.mock import MagicMock, patch
from googleapiclient.errors import HttpError
from ge_governance_agent.tools.notifier import (
    notify_inactive_user,
    notify_admins,
    _send_email
)

@pytest.fixture
def mock_gmail_service():
    with patch("ge_governance_agent.tools.notifier.build") as mock_build:
        service = MagicMock()
        mock_build.return_value = service
        yield service

@pytest.fixture
def mock_creds():
    with patch("ge_governance_agent.tools.notifier.get_credentials") as mock:
        yield mock

def test_send_email_exception(mock_creds, mock_gmail_service):
    # Trigger an exception in the build or send process
    mock_gmail_service.users().messages().send.side_effect = Exception("Email Crash")
    
    result = _send_email(
        sender="no-reply@igboke.com",
        recipient="user@igboke.com",
        subject="Test",
        body_text="Test",
        body_html="<html></html>"
    )
    assert result["sent"] is False
    assert "Email Crash" in result["error"]

def test_notify_inactive_user_success(mock_gmail_service, monkeypatch, mock_creds):
    monkeypatch.setenv("NOTIFICATION_SENDER_EMAIL", "no-reply@igboke.com")
    mock_gmail_service.users().messages().send().execute.return_value = {"id": "12345"}
    
    result = notify_inactive_user(
        user_email="user-igboke@igbokwe.altostrat.com",
        last_activity="2023-01-01",
        inactivity_days=45
    )
    assert result["sent"] is True

def test_notify_inactive_user_failure(mock_gmail_service, monkeypatch, mock_creds):
    monkeypatch.setenv("NOTIFICATION_SENDER_EMAIL", "no-reply@igboke.com")
    mock_resp = MagicMock()
    mock_resp.status = 500
    mock_gmail_service.users().messages().send().execute.side_effect = HttpError(resp=mock_resp, content=b"err")

    result = notify_inactive_user("fail-igboke@igbokwe.altostrat.com", "never")
    assert result["sent"] is False

def test_notify_admins_success(mock_gmail_service, monkeypatch, mock_creds):
    monkeypatch.setenv("NOTIFICATION_SENDER_EMAIL", "no-reply@igboke.com")
    monkeypatch.setenv("ORG_ADMIN_EMAILS", "admin@igboke.com")
    mock_gmail_service.users().messages().send().execute.return_value = {"id": "adm-123"}
    
    rev = [{"user": "u1", "last_activity": "never", "revoked": True, "message": "Ok"}]
    result = notify_admins(rev)
    assert "admin@igboke.com" in result["sent_to"]

def test_notify_admins_no_config(monkeypatch):
    monkeypatch.delenv("ORG_ADMIN_EMAILS", raising=False)
    result = notify_admins([{"user": "test"}])
    assert "not configured" in result["errors"][0]

def test_notify_admins_dry_run(monkeypatch):
    monkeypatch.setenv("ORG_ADMIN_EMAILS", "admin@igboke.com")
    result = notify_admins([{"user": "u1"}], dry_run=True)
    assert "DRY RUN" in result["errors"][0]

def test_notify_admins_partial_failure(mock_gmail_service, monkeypatch, mock_creds):
    monkeypatch.setenv("NOTIFICATION_SENDER_EMAIL", "no-reply@igboke.com")
    monkeypatch.setenv("ORG_ADMIN_EMAILS", "fail@igboke.com")
    # Return failure
    mock_gmail_service.users().messages().send.side_effect = Exception("Admin Fail")
    
    result = notify_admins([{"user": "u1"}])
    assert "fail@igboke.com" in result["errors"][0]
    assert "Admin Fail" in result["errors"][0]
