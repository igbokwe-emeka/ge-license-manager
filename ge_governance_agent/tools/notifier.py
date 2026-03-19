"""
Notifier tool: sends email notifications via the Gmail API.

Notifies:
  1. Inactive users whose license has been revoked.
  2. Organisation administrators with a summary report.

Required service-account scopes (domain-wide delegation):
  - https://www.googleapis.com/auth/gmail.send
"""

from __future__ import annotations

import base64
import logging
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ge_governance_agent.auth import get_credentials

logger = logging.getLogger('ge_governance_agent.' + __name__)

_GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gmail_service(sender_email: str):
    """Return a Gmail API service client impersonating *sender_email*."""
    creds = get_credentials(scopes=_GMAIL_SCOPES, subject=sender_email)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _build_message(
    sender: str,
    recipient: str,
    subject: str,
    body_html: str,
    body_text: str,
) -> dict[str, str]:
    """Encode an email message into the format expected by the Gmail API."""
    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw}

def _send_email(sender: str, recipient: str, subject: str, body_html: str, body_text: str) -> dict[str, Any]:
    logger.info("Sending email from %s to %s (Subject: %s)", sender, recipient, subject)
    try:
        service = _gmail_service(sender)
        message = _build_message(sender, recipient, subject, body_html, body_text)
        result = service.users().messages().send(userId="me", body=message).execute()
        logger.info("Email sent successfully. Message ID: %s", result.get("id"))
        return {"sent": True, "message_id": result.get("id"), "error": None}
    except HttpError as exc:
        logger.error("Failed to send email to %s: %s", recipient, exc)
        return {"sent": False, "message_id": None, "error": str(exc)}
    except Exception as e:
        logger.error("Unexpected error sending email to %s: %s", recipient, e)
        return {"sent": False, "message_id": None, "error": str(e)}


# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------

_USER_REVOCATION_HTML = """
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto;">
  <h2 style="color: #d93025;">Gemini Enterprise Access Revoked</h2>
  <p>Dear {display_name},</p>
  <p>
    Your <strong>Gemini Enterprise</strong> licence has been revoked because your account
    has had no recorded activity for more than <strong>{inactivity_days} days</strong>
    (last activity: <strong>{last_activity}</strong>).
  </p>
  <p>
    If you believe this is an error or you wish to regain access, please contact your
    IT administrator or open a support request through your organisation's helpdesk.
  </p>
  <hr/>
  <p style="font-size: 12px; color: #888;">
    This is an automated notification. Please do not reply to this email.
  </p>
</body>
</html>
"""

_USER_REVOCATION_TEXT = (
    "Your Gemini Enterprise licence has been revoked due to {inactivity_days} days of "
    "inactivity (last activity: {last_activity}). Contact your IT administrator to regain access."
)

_ADMIN_SUMMARY_HTML = """
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 700px; margin: auto;">
  <h2 style="color: #1a73e8;">Gemini Enterprise Licence Revocation Report</h2>
  <p>
    The automated licence-revocation agent ran on <strong>{run_date}</strong>
    and processed <strong>{total}</strong> inactive user(s)
    (threshold: <strong>{inactivity_days} days</strong> of inactivity).
  </p>
  <table border="1" cellpadding="6" cellspacing="0"
         style="border-collapse: collapse; width: 100%;">
    <thead style="background-color: #e8f0fe;">
      <tr>
        <th style="text-align: left;">User</th>
        <th style="text-align: left;">Last Activity</th>
        <th style="text-align: left;">Revoked</th>
        <th style="text-align: left;">Details</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
  <hr/>
  <p style="font-size: 12px; color: #888;">
    This is an automated report from the GE User Level Analytics agent.
  </p>
</body>
</html>
"""

_ADMIN_ROW_HTML = """
  <tr>
    <td>{user}</td>
    <td>{last_activity}</td>
    <td style="color: {color};">{revoked}</td>
    <td>{details}</td>
  </tr>
"""

_ADMIN_SUMMARY_TEXT = (
    "Gemini Enterprise Licence Revocation Report — {run_date}\n"
    "Inactivity threshold: {inactivity_days} days\n"
    "Users processed: {total}\n\n"
    "{rows}"
)


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------


def notify_inactive_user(
    user_email: str,
    last_activity: str,
    inactivity_days: int = 45,
    display_name: str | None = None,
) -> dict[str, Any]:
    """
    Send a licence-revocation notification to an individual user.

    Args:
        user_email:       Recipient's email address.
        last_activity:    ISO date of last recorded activity (or "never").
        inactivity_days:  Threshold that triggered revocation.
        display_name:     Optional friendly name; defaults to the email prefix.

    Returns:
        {"sent": bool, "message_id": str | None, "error": str | None}
    """
    sender = os.environ["NOTIFICATION_SENDER_EMAIL"]
    name = display_name or user_email.split("@")[0].replace(".", " ").title()

    html = _USER_REVOCATION_HTML.format(
        display_name=name,
        inactivity_days=inactivity_days,
        last_activity=last_activity,
    )
    text = _USER_REVOCATION_TEXT.format(
        inactivity_days=inactivity_days,
        last_activity=last_activity,
    )

    return _send_email(
        sender=sender,
        recipient=user_email,
        subject="Action Required: Your Gemini Enterprise Licence Has Been Revoked",
        body_html=html,
        body_text=text,
    )


def notify_admins(
    revocation_results: list[dict[str, Any]],
    inactivity_days: int = 45,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Send a summary revocation report to all configured org administrators.

    Args:
        revocation_results: List of per-user result dicts produced by
                            revoke_gemini_license(). Each entry must contain:
                            "user", "last_activity", "revoked", "message".
        inactivity_days:    Inactivity threshold used for this run.

    Returns:
        {"sent_to": [str, ...], "errors": [str, ...]}
    """
    import datetime as _dt

    sender = os.environ["NOTIFICATION_SENDER_EMAIL"]
    admin_emails_raw = os.environ.get("ORG_ADMIN_EMAILS", "")
    admin_emails = [e.strip() for e in admin_emails_raw.split(",") if e.strip()]

    if not admin_emails:
        return {"sent_to": [], "errors": ["ORG_ADMIN_EMAILS is not configured."]}

    run_date = _dt.date.today().isoformat()
    total = len(revocation_results)

    # Build HTML rows
    html_rows = ""
    text_rows = ""
    for r in revocation_results:
        revoked = r.get("revoked", False)
        color = "#1e8e3e" if revoked else "#d93025"
        html_rows += _ADMIN_ROW_HTML.format(
            user=r.get("user", ""),
            last_activity=r.get("last_activity", "unknown"),
            revoked="Yes" if revoked else "No",
            color=color,
            details=r.get("message", r.get("error", "")),
        )
        text_rows += (
            f"  {r.get('user', '')} | "
            f"last active: {r.get('last_activity', 'unknown')} | "
            f"revoked: {'yes' if revoked else 'no'} | "
            f"{r.get('message', '')}\n"
        )

    body_html = _ADMIN_SUMMARY_HTML.format(
        run_date=run_date,
        total=total,
        inactivity_days=inactivity_days,
        rows=html_rows,
    )
    body_text = _ADMIN_SUMMARY_TEXT.format(
        run_date=run_date,
        total=total,
        inactivity_days=inactivity_days,
        rows=text_rows,
    )

    sent_to: list[str] = []
    errors: list[str] = []

    if dry_run:
        logger.info("[DRY RUN] Skipping admin notifications for: %s", admin_emails)
        return {"sent_to": [], "errors": [f"[DRY RUN] Would notify {admin_emails}"]}

    for admin_email in admin_emails:
        result = _send_email(
            sender=sender,
            recipient=admin_email,
            subject=f"[Report] Gemini Enterprise Licence Revocations — {run_date}",
            body_html=body_html,
            body_text=body_text,
        )
        if result["sent"]:
            sent_to.append(admin_email)
        else:
            errors.append(f"{admin_email}: {result['error']}")

    return {"sent_to": sent_to, "errors": errors}
