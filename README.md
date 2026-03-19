# GE User Level Analytics — Gemini Enterprise Licence Governance Agent

An autonomous **Google ADK agent** deployed to **Vertex AI Agent Engine** that:

1.  Queries the Discovery Engine License API to identify inactive Gemini Enterprise users (>45 days)
2.  Revokes their Gemini Enterprise licence via `batchUpdateUserLicenses`
3.  Emails each revoked user with an explanation
4.  Emails all org administrators a consolidated report
5.  Writes a structured audit trail to Cloud Logging

---

## Architecture

```
ge-user-level-analytics/
├── ge_governance_agent/
│   ├── agent.py                  # ADK root_agent definition
│   ├── auth.py                   # Service account credential helper
│   └── tools/
│       ├── license_manager.py    # Discovery Engine user license API
│       ├── log_analytics.py      # BigQuery Log Analytics queries
│       ├── notifier.py           # Gmail API (user + admin emails)
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
  gmail.googleapis.com \
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
| Full revocation cycle | `"Run a full revocation cycle using the 45-day inactivity threshold."` |
| Dry run | `"Run a dry-run revocation cycle. Do not revoke any licences."` |
| Check a user | `"What is the last activity date for alice@example.com?"` |
| List licensed users | `"List all users currently holding a Gemini Enterprise licence."` |
| Usage report | `"Show me daily usage for the last 30 days."` |
| Custom threshold | `"Find users inactive for more than 60 days."` |

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
