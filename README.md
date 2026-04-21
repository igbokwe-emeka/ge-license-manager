# GE User Level Analytics — Gemini Enterprise Licence Governance Agent

An autonomous **Google ADK agent** deployed to **Vertex AI Agent Engine** that:

1.  Queries the Discovery Engine License API to identify inactive Gemini Enterprise users (>45 days)
2.  Revokes their Gemini Enterprise licence via `batchUpdateUserLicenses`
3.  Runs a **daily 24-hour usage audit**, writing a CSV report to GCS
4.  Writes a structured audit trail to Cloud Logging

---

## Architecture

```
ge-user-level-analytics/
├── ge_governance_agent/
│   ├── agent.py                  # ADK root_agent definition
│   ├── auth.py                   # Service account credential helper
│   └── tools/
│       ├── bigquery_setup.py     # BigQuery Log Analytics setup & linking
│       ├── license_manager.py    # Discovery Engine user license API
│       ├── log_analytics.py      # BigQuery Log Analytics queries
│       ├── usage_auditor.py      # 24-hour usage audit + GCS upload
│       └── audit_logger.py       # Cloud Logging audit trail
├── deployment/
│   ├── deploy.py                 # Deploy / manage Agent Engine
│   └── register_ge_app.py        # Register agent in Gemini Enterprise App
├── tests/                        # 100% unit test coverage (pytest)
├── setup_and_deploy.sh           # One-script automated setup
├── requirements.txt
├── pyproject.toml
└── .env.example
```

---

## Quick Start (Automated)

> **Prefer automation?** See [`setup_and_deploy.sh`](setup_and_deploy.sh) for a fully scripted setup.

---

## Quick Start (Manual)

### 1. Prerequisites
See [`DEPLOYMENT.md`](DEPLOYMENT.md) for the full list of required GCP permissions, APIs, and service account scopes.

### 2. Clone & configure

```bash
git clone https://github.com/YOUR-ORG/ge-user-level-analytics.git
cd ge-user-level-analytics

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
# ✏️ Edit .env with your project values
```

### 3. Enable GCP APIs

```bash
gcloud services enable \
  discoveryengine.googleapis.com \
  bigquery.googleapis.com \
  logging.googleapis.com \
  admin.googleapis.com \
  aiplatform.googleapis.com \
  storage.googleapis.com
```

### 4. Deploy to Agent Engine

```bash
python deployment/deploy.py deploy
```

### 5. Register in Gemini Enterprise App

```bash
python deployment/register_ge_app.py --engine-id YOUR_GE_APP_ENGINE_ID register
```

---

## Run Locally

```bash
# Interactive browser UI
adk web

# Single CLI turn (dry run — no real revocations)
adk run ge_governance_agent --message "Run a dry-run revocation cycle. Do not revoke any licences."
```

---

## Example Prompts

| Intent | Prompt |
|---|---|
| Setup BigQuery Logging | `"Setup Log Analytics and create a linked BigQuery dataset."` |
| Full revocation cycle | `"Run a full revocation cycle using the 45-day inactivity threshold."` |
| Dry run | `"Run a dry-run revocation cycle. Do not revoke any licences."` |
| Daily audit | `"Run the daily audit and upload the report to GCS."` |
| Check a user | `"What is the last activity date for alice@example.com?"` |
| List licensed users | `"List all users currently holding a Gemini Enterprise licence."` |
| Usage report | `"Show me daily usage for the last 30 days."` |
| Custom threshold | `"Find users inactive for more than 60 days."` |

---

## Daily Audit Report

The agent is scheduled to run daily. Each run queries the last 24 hours of
Discovery Engine activity and writes a CSV to GCS:

```
gs://<AUDIT_BUCKET>/YYYY/MM/DD/HH-MM/usage_audit.csv
```

Each row contains:

| Column | Description |
|---|---|
| `user_email` | User's principal email address |
| `license_assigned_date` | Date the GE licence was assigned (MM-DD-YYYY) |
| `license_type` | Licence configuration / product name |
| `last_used_date` | Date of the user's most recent API call in the window (MM-DD-YYYY) |

*Note: The agent can iterate across multiple subscriptions by setting a comma-separated list of project IDs in the `SUBSCRIPTION_IDS` environment variable.*

Configure the destination bucket via the `AUDIT_BUCKET` environment variable (see `.env.example`).

---

## Audit Trail

All revocations are logged to Cloud Logging under:
```
logName="projects/PROJECT_ID/logs/gemini-enterprise-revocation-audit"
```

---

## Test Coverage

```bash
pytest tests/ --cov=ge_governance_agent/tools --cov-report=term-missing
```
---

## Full Deployment Guide

  See **[DEPLOYMENT.md](DEPLOYMENT.md)** for:
- Detailed permission requirements
- Service account setup with domain-wide delegation
- Step-by-step Agent Engine deployment
- Gemini Enterprise App registration
- Access control (who can invoke the agent)
