# Deployment Guide — GE User Level Analytics Agent

This guide covers everything needed to deploy the agent from scratch in a new Google Cloud / Google Workspace environment.

---

## Table of Contents

1. [Required GCP IAM Permissions](#1-required-gcp-iam-permissions)
2. [Required APIs](#2-required-apis)
3. [Service Account Setup (Domain-Wide Delegation)](#3-service-account-setup-domain-wide-delegation)
4. [Cloud Logging — Log Analytics Setup](#4-cloud-logging--log-analytics-setup)
5. [Environment Configuration](#5-environment-configuration)
6. [Deploy to Vertex AI Agent Engine](#6-deploy-to-vertex-ai-agent-engine)
7. [Register in Gemini Enterprise App](#7-register-in-gemini-enterprise-app)
8. [Control Who Can Access the Agent](#8-control-who-can-access-the-agent)
9. [Verify the Deployment](#9-verify-the-deployment)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Required GCP IAM Permissions

### A. Person running the deployment (`gcloud` operator)

The person executing `setup_and_deploy.sh` / `deploy.py` must have:

| IAM Role | Purpose |
|---|---|
| `roles/aiplatform.admin` | Create / delete Reasoning Engines |
| `roles/storage.admin` | Create staging GCS bucket |
| `roles/iam.serviceAccountAdmin` | Create and manage service accounts |
| `roles/iam.serviceAccountKeyAdmin` | Create service account keys |
| `roles/serviceusage.serviceUsageAdmin` | Enable APIs |
| `roles/resourcemanager.projectIamAdmin` | Bind roles to service accounts |
| `roles/logging.admin` | Enable Log Analytics on log buckets |
| `roles/discoveryengine.admin` | Manage Discovery Engine resources |

> If you have `roles/owner`, you already have all of the above.

### B. Agent service account (runtime identity)

Create a dedicated service account (e.g. `ge-governance-agent@PROJECT_ID.iam.gserviceaccount.com`).

Bind the following GCP IAM roles on the project:

| IAM Role | Purpose |
|---|---|
| `roles/bigquery.jobUser` | Run BigQuery Log Analytics queries |
| `roles/bigquery.dataViewer` | Read log data from BigQuery |
| `roles/logging.logWriter` | Write audit entries to Cloud Logging |
| `roles/storage.objectViewer` | Read Agent Engine staging artefacts |
| `roles/aiplatform.user` | Query the Reasoning Engine |
| `roles/discoveryengine.editor` | List and revoke user licences |

```bash
# Create the service account
gcloud iam service-accounts create ge-governance-agent \
  --display-name="GE Governance Agent" \
  --project=YOUR_PROJECT_ID

SA_EMAIL="ge-governance-agent@YOUR_PROJECT_ID.iam.gserviceaccount.com"
SA_KEY_FILE="${HOME}/service-account-key.json"

# Bind all required roles
for ROLE in \
  roles/bigquery.jobUser \
  roles/bigquery.dataViewer \
  roles/logging.logWriter \
  roles/storage.objectViewer \
  roles/aiplatform.user \
  roles/discoveryengine.editor; do
  gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${ROLE}" \
    --condition=None
done
```

---

## 2. Required APIs

Enable all required APIs in one command:

```bash
gcloud services enable \
  discoveryengine.googleapis.com \
  bigquery.googleapis.com \
  logging.googleapis.com \
  admin.googleapis.com \
  gmail.googleapis.com \
  aiplatform.googleapis.com \
  storage.googleapis.com \
  iam.googleapis.com \
  --project=YOUR_PROJECT_ID
```

---

## 3. Service Account Setup (Domain-Wide Delegation)

The agent sends emails via the Gmail API and reads user directory data, which requires **domain-wide delegation** in Google Workspace. This allows the service account to impersonate a real mailbox.

### Step 1 — Create a service account key

```bash
gcloud iam service-accounts keys create $SA_KEY_FILE \
  --iam-account=${SA_EMAIL}
```

Store the key file securely. Set the path in your `.env`:
```
GOOGLE_APPLICATION_CREDENTIALS=${SA_KEY_FILE}
```
########EMAIL NOTIFICATIONS DISABLED#################

### Step 2 — Enable domain-wide delegation in GCP

```bash
gcloud iam service-accounts update ge-governance-agent@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --project=YOUR_PROJECT_ID
```

Then in the [GCP Console → IAM → Service Accounts](https://console.cloud.google.com/iam-admin/serviceaccounts):
1. Select your service account
2. Click **Edit** → **Show Advanced Settings**
3. Enable **Domain-wide Delegation**
4. Note the **Client ID** (a long number)

### Step 3 — Authorize in Google Workspace Admin

1. Go to [admin.google.com](https://admin.google.com) → **Security** → **API Controls** → **Domain-wide Delegation**
2. Click **Add new**
3. Enter the service account **Client ID**
4. Add the following **OAuth Scopes** (comma-separated):

```
https://www.googleapis.com/auth/gmail.send,
https://www.googleapis.com/auth/admin.directory.user.readonly,
https://www.googleapis.com/auth/cloud-platform,
https://www.googleapis.com/auth/logging.write
```

5. Click **Authorize**

####################################################

## 4. Cloud Logging — Log Analytics Setup

The `query_inactive_users` tool uses BigQuery-style queries on Log Analytics. This requires:

### Step 1 — Enable audit logging for Discovery Engine

In the [GCP Console → IAM & Admin → Audit Logs](https://console.cloud.google.com/iam-admin/audit):
1. Filter for **Cloud Discovery Engine API**
2. Enable: ✅ Admin Read, ✅ Data Read, ✅ Data Write

### Step 2 — Upgrade log bucket to Log Analytics

```bash
gcloud logging buckets update _Default \
  --location=global \
  --enable-analytics \
  --project=YOUR_PROJECT_ID
```

> **Note:** This is a one-way upgrade. Once enabled, you cannot revert.

---

## 5. Environment Configuration

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description | Example |
|---|---|---|
| `GCP_PROJECT_ID` | Your GCP Project ID | `my-project-123` |
| `GCP_LOCATION` | Agent Engine region | `us-central1` |
| `WORKSPACE_DOMAIN` | Your Workspace domain | `company.com` |
| `WORKSPACE_ADMIN_EMAIL` | Admin for DWD impersonation | `admin@company.com` |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to SA key JSON | `service-account-key.json` |
| `NOTIFICATION_SENDER_EMAIL` | Gmail sender (must be in domain) | `no-reply@company.com` |
| `ORG_ADMIN_EMAILS` | Comma-separated admin emails for reports | `it@company.com,sec@company.com` |
| `AGENT_ENGINE_DISPLAY_NAME` | Display name in Vertex AI | `ge-user-level-analytics-agent` |
| `STAGING_BUCKET` | GCS bucket for packaging | `gs://my-project-agent-staging` |
| `INACTIVITY_THRESHOLD_DAYS` | Days inactive before revocation | `45` |

set -a
source .env
set +a

---


## 6. Deploy to Vertex AI Agent Engine

```bash

# 0. Setup Environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Authenticate your operator account
gcloud auth application-default login

# Deploy (takes 3-5 minutes)
python deployment/deploy.py deploy

# The resource name is saved automatically to .last_resource_name
# Example: projects/PROJECT_ID/locations/us-central1/reasoningEngines/XXXXXXXXX

# List all deployments
python deployment/deploy.py list

# Delete a specific deployment
python deployment/deploy.py delete --resource-name projects/PROJECT_ID/locations/us-central1/reasoningEngines/XXXXXXXXX
```

---

## 7. Register in Gemini Enterprise App

After deploying, link the Reasoning Engine to your Gemini Enterprise application:

```bash
# Find your GE App Engine ID in the GCP Console:
# Vertex AI Search → Apps → your app → URL contains the engine ID

# List currently registered agents in the GE app
python deployment/register_ge_app.py --engine-id YOUR_GE_ENGINE_ID list

# Register the new agent (auto-reads .last_resource_name)
python deployment/register_ge_app.py --engine-id YOUR_GE_ENGINE_ID register

# Or specify the resource name explicitly
python deployment/register_ge_app.py --engine-id YOUR_GE_ENGINE_ID register \
  --reasoning-engine projects/PROJECT_ID/locations/us-central1/reasoningEngines/XXXXXXXXX

# Unregister (delete) a previously registered agent
python deployment/register_ge_app.py --engine-id YOUR_GE_ENGINE_ID delete \
  --agent-id AGENT_ID
```

> **Finding your GE Engine ID:**
> 1. Open the [GCP Console → Vertex AI → Agent Engine](https://console.cloud.google.com/vertex-ai/agents)
> 2. Click your Gemini Enterprise app
> 3. The engine ID is in the URL, e.g. `outcome_1770956866236`

---

## 8. Control Who Can Access the Agent

### Option A — GE App-level access (via Sharing Config)

By default the agent is registered with `scope: ALL_USERS`, meaning all users in your Workspace domain can invoke it through the GE chat UI.

To restrict access, modify `register_ge_app.py` before registering:

```python
# In register_ge_app.py, change sharingConfig.scope to PRIVATE or SPECIFIC_USERS
"sharingConfig": {
    "scope": "PRIVATE",  # Only the service account owner can access
},
```

Or for group-based access, use the Discovery Engine API directly:

```bash
# Grant access to a specific user or group
curl -X PATCH \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  "https://us-discoveryengine.googleapis.com/v1alpha/projects/PROJECT_ID/locations/us/collections/default_collection/engines/YOUR_GE_ENGINE_ID/assistants/default_assistant/agents/AGENT_ID" \
  -d '{
    "sharingConfig": {
      "scope": "SPECIFIC_USERS",
      "specificUsers": ["user1@company.com", "group@company.com"]
    }
  }'
```

### Option B — Vertex AI Agent Engine IAM (API-level access)

To control who can query the Reasoning Engine directly via the API:

```bash
# Grant invoke access to a specific user
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="user:it-admin@company.com" \
  --role="roles/aiplatform.user"

# Grant invoke access to a group
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="group:governance-team@company.com" \
  --role="roles/aiplatform.user"

# Grant read-only list access (no invoke)
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="user:viewer@company.com" \
  --role="roles/aiplatform.viewer"
```

### Option C — Restrict to Service Account only (most secure)

Remove all human user access and only allow the service account to invoke:

```bash
# Remove broader access if set
gcloud projects remove-iam-policy-binding YOUR_PROJECT_ID \
  --member="allUsers" \
  --role="roles/aiplatform.user"
```

---

## 9. Verify the Deployment

### Run the built-in test

```bash
python deployment/deploy.py test \
  --prompt "Run a DRY RUN of the revocation cycle. Do not revoke any licences. Report how many inactive users you find."
```

### Run the unit test suite

```bash
pytest tests/ --cov=ge_governance_agent/tools --cov-report=term-missing
```

Expected output:
```
TOTAL    256    0    100%
32 passed
```

---

## 10. Troubleshooting

| Error | Likely Cause | Fix |
|---|---|---|
| `PermissionDenied` on Discovery Engine | SA not bound to `roles/discoveryengine.editor` | Re-run IAM binding commands in §1B |
| `403` when listing user licences | Missing `x-goog-user-project` header or DWD not configured | Verify domain-wide delegation in Workspace Admin |
| `HttpError 403` sending email | Gmail DWD scope not authorized | Re-authorize scopes in Workspace Admin → Domain-wide Delegation |
| `BigQuery table not found` | Log Analytics not enabled on `_Default` bucket | Run `gcloud logging buckets update --enable-analytics` |
| `Agent Engine deployment fails` | Staging bucket missing or wrong region | Create bucket in same region as `GCP_LOCATION` |
| `ReasoningEngine has no attribute 'query'` | SDK version mismatch | Use `stream_query` — see `deploy.py test` command |
