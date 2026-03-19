:#!/usr/bin/env bash
# =============================================================================
# setup_and_deploy.sh — Automated setup and deployment for GE User Level Analytics Agent
#
# Usage:
#   chmod +x setup_and_deploy.sh
#   ./setup_and_deploy.sh
#
# What this script does:
#   1. Validates prerequisites (gcloud, python, pip)
#   2. Creates and configures a GCP service account
#   3. Enables all required GCP APIs
#   4. Enables Log Analytics on the _Default log bucket
#   5. Creates a GCS staging bucket
#   6. Sets up a Python virtual environment and installs dependencies
#   7. Deploys the agent to Vertex AI Agent Engine
#   8. Registers the agent in your Gemini Enterprise App
#   9. Prints a summary with next steps
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (gcloud auth login)
#   - Python 3.11+ installed
#   - GCP project with billing enabled
#   - Google Workspace with admin access (for domain-wide delegation — manual step)
# =============================================================================

set -euo pipefail

# ─────────────────────────────────────────────
# CONFIGURATION — Edit these values before running
# ─────────────────────────────────────────────
PROJECT_ID="${GCP_PROJECT_ID:-}"           # Override via env var or edit here
LOCATION="${GCP_LOCATION:-us-central1}"
GE_ENGINE_ID="${GE_ENGINE_ID:-}"           # Your Gemini Enterprise App engine ID
WORKSPACE_DOMAIN="${WORKSPACE_DOMAIN:-}"
WORKSPACE_ADMIN_EMAIL="${WORKSPACE_ADMIN_EMAIL:-}"
NOTIFICATION_SENDER_EMAIL="${NOTIFICATION_SENDER_EMAIL:-}"
ORG_ADMIN_EMAILS="${ORG_ADMIN_EMAILS:-}"
SA_NAME="ge-governance-agent"
AGENT_DISPLAY_NAME="ge-user-level-analytics-agent"

# ─────────────────────────────────────────────
# COLORS
# ─────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'; BOLD='\033[1m'

log()   { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
step()  { echo -e "\n${BOLD}${BLUE}══════════════════════════════════════${NC}"; echo -e "${BOLD} $*${NC}"; echo -e "${BOLD}${BLUE}══════════════════════════════════════${NC}"; }

# ─────────────────────────────────────────────
# PROMPT FOR MISSING CONFIG
# ─────────────────────────────────────────────
prompt_if_empty() {
  local var_name="$1"
  local prompt_msg="$2"
  local current="${!var_name:-}"
  if [[ -z "$current" ]]; then
    read -rp "  $prompt_msg: " value
    export "$var_name"="$value"
  fi
}

# ─────────────────────────────────────────────
# STEP 0 — Check prerequisites
# ─────────────────────────────────────────────
step "Step 0 — Checking prerequisites"

command -v gcloud >/dev/null 2>&1 || error "gcloud CLI is not installed. See https://cloud.google.com/sdk/docs/install"
command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1 || error "Python 3.11+ is required."
PYTHON=$(command -v python3 || command -v python)
PY_VER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
ok "gcloud found: $(gcloud --version | head -1)"
ok "Python found: $PYTHON ($PY_VER)"

# ─────────────────────────────────────────────
# STEP 1 — Collect configuration
# ─────────────────────────────────────────────
step "Step 1 — Configuration"

prompt_if_empty PROJECT_ID              "GCP Project ID (e.g. my-project-123)"
prompt_if_empty GE_ENGINE_ID            "Gemini Enterprise App Engine ID (e.g. outcome_1770956866236)"
prompt_if_empty WORKSPACE_DOMAIN        "Workspace domain (e.g. company.com)"
prompt_if_empty WORKSPACE_ADMIN_EMAIL   "Workspace admin email (for sending via Gmail API)"
prompt_if_empty NOTIFICATION_SENDER_EMAIL "Notification sender email (e.g. no-reply@company.com)"
prompt_if_empty ORG_ADMIN_EMAILS        "Org admin emails for reports (comma-separated)"

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
STAGING_BUCKET="gs://${PROJECT_ID}-agent-engine-staging"

log "Project         : $PROJECT_ID"
log "Location        : $LOCATION"
log "Service Account : $SA_EMAIL"
log "Staging Bucket  : $STAGING_BUCKET"
log "GE Engine ID    : $GE_ENGINE_ID"

# ─────────────────────────────────────────────
# STEP 2 — Enable GCP APIs
# ─────────────────────────────────────────────
step "Step 2 — Enabling GCP APIs"

gcloud services enable \
  discoveryengine.googleapis.com \
  bigquery.googleapis.com \
  logging.googleapis.com \
  admin.googleapis.com \
  gmail.googleapis.com \
  aiplatform.googleapis.com \
  storage.googleapis.com \
  iam.googleapis.com \
  --project="$PROJECT_ID"

ok "All required APIs enabled."

# ─────────────────────────────────────────────
# STEP 3 — Create and configure service account
# ─────────────────────────────────────────────
step "Step 3 — Service account setup"

if gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1; then
  warn "Service account $SA_EMAIL already exists. Skipping creation."
else
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="GE Governance Agent" \
    --project="$PROJECT_ID"
  ok "Service account created: $SA_EMAIL"
fi

log "Binding IAM roles to service account..."
for ROLE in \
  roles/bigquery.jobUser \
  roles/bigquery.dataViewer \
  roles/logging.logWriter \
  roles/storage.objectViewer \
  roles/aiplatform.user \
  roles/discoveryengine.editor; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$ROLE" \
    --condition=None \
    --quiet
  ok "  Bound: $ROLE"
done

# Create service account key
SA_KEY_FILE="service-account-key.json"
if [[ ! -f "$SA_KEY_FILE" ]]; then
  gcloud iam service-accounts keys create "$SA_KEY_FILE" \
    --iam-account="$SA_EMAIL" \
    --project="$PROJECT_ID"
  ok "Service account key saved: $SA_KEY_FILE"
else
  warn "Key file $SA_KEY_FILE already exists. Skipping key creation."
fi

# Get the service account numeric ID (needed for DWD)
SA_UNIQUE_ID=$(gcloud iam service-accounts describe "$SA_EMAIL" \
  --project="$PROJECT_ID" \
  --format="value(uniqueId)")

ok "Service account unique ID: $SA_UNIQUE_ID"

# ─────────────────────────────────────────────
# STEP 4 — Enable Log Analytics
# ─────────────────────────────────────────────
step "Step 4 — Enabling Log Analytics"

gcloud logging buckets update _Default \
  --location=global \
  --enable-analytics \
  --project="$PROJECT_ID" 2>&1 | grep -v "already enabled" || true

ok "Log Analytics enabled on _Default bucket."

# ─────────────────────────────────────────────
# STEP 5 — Create staging GCS bucket
# ─────────────────────────────────────────────
step "Step 5 — Staging bucket"

if gsutil ls "$STAGING_BUCKET" >/dev/null 2>&1; then
  warn "Staging bucket $STAGING_BUCKET already exists."
else
  gcloud storage buckets create "$STAGING_BUCKET" \
    --project="$PROJECT_ID" \
    --location="$LOCATION"
  ok "Staging bucket created: $STAGING_BUCKET"
fi

# ─────────────────────────────────────────────
# STEP 6 — Python virtual environment + dependencies
# ─────────────────────────────────────────────
step "Step 6 — Python environment setup"

if [[ ! -d ".venv" ]]; then
  $PYTHON -m venv .venv
  ok "Virtual environment created: .venv"
fi

# Activate
if [[ -f ".venv/Scripts/activate" ]]; then  # Windows Git Bash
  source .venv/Scripts/activate
else
  source .venv/bin/activate
fi

pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
ok "Dependencies installed."

# ─────────────────────────────────────────────
# STEP 7 — Write .env file
# ─────────────────────────────────────────────
step "Step 7 — Writing .env"

cat > .env <<EOF
# Google Cloud Project
GCP_PROJECT_ID=${PROJECT_ID}
GCP_LOCATION=${LOCATION}

# Log Analytics
LOG_BUCKET=_Default
LOG_VIEW=_AllLogs
INACTIVITY_THRESHOLD_DAYS=45

# Google Workspace
#WORKSPACE_DOMAIN=${WORKSPACE_DOMAIN}
#WORKSPACE_ADMIN_EMAIL=${WORKSPACE_ADMIN_EMAIL}
#GOOGLE_APPLICATION_CREDENTIALS=${SA_KEY_FILE}

# Gemini Enterprise License SKU (Discovery Engine)
#GEMINI_ENTERPRISE_PRODUCT_ID=Google-Gemini-Enterprise
#GEMINI_ENTERPRISE_SKU_ID=1010310006

# Notification settings
#NOTIFICATION_SENDER_EMAIL=${NOTIFICATION_SENDER_EMAIL}
#ORG_ADMIN_EMAILS=${ORG_ADMIN_EMAILS}

# Agent Engine
AGENT_ENGINE_DISPLAY_NAME=${AGENT_DISPLAY_NAME}
STAGING_BUCKET=${STAGING_BUCKET}
EOF

ok ".env written."

# ─────────────────────────────────────────────
# STEP 8 — Deploy to Agent Engine
# ─────────────────────────────────────────────
step "Step 8 — Deploying to Vertex AI Agent Engine"

log "This step takes 3-5 minutes. Please wait..."
export GOOGLE_APPLICATION_CREDENTIALS="$SA_KEY_FILE"
python deployment/deploy.py deploy

ok "Agent deployed to Agent Engine."
log "Resource name saved to: .last_resource_name"
RESOURCE_NAME=$(cat .last_resource_name)
log "Resource name: $RESOURCE_NAME"

# ─────────────────────────────────────────────
# STEP 9 — Register in Gemini Enterprise App
# ─────────────────────────────────────────────
step "Step 9 — Registering in Gemini Enterprise App"

python deployment/register_ge_app.py --engine-id "$GE_ENGINE_ID" register

ok "Agent registered in Gemini Enterprise App: $GE_ENGINE_ID"

# ─────────────────────────────────────────────
# STEP 10 — Final summary
# ─────────────────────────────────────────────
step "Deployment Complete!"

echo ""
echo -e "${BOLD}Summary${NC}"
echo "  GCP Project       : $PROJECT_ID"
echo "  Agent Region      : $LOCATION"
echo "  Service Account   : $SA_EMAIL"
echo "  Reasoning Engine  : $RESOURCE_NAME"
echo "  GE App Engine ID  : $GE_ENGINE_ID"
echo ""
echo -e "${BOLD}Required Manual Steps${NC}"
echo ""
echo -e "${YELLOW}⚠ Domain-Wide Delegation (required for Gmail notifications)${NC}"
echo "  1. Go to: https://admin.google.com → Security → API Controls → Domain-wide Delegation"
echo "  2. Click 'Add new'"
echo "  3. Client ID: $SA_UNIQUE_ID"
echo "  4. OAuth Scopes (paste exactly):"
echo "     https://www.googleapis.com/auth/gmail.send,"
echo "     https://www.googleapis.com/auth/admin.directory.user.readonly,"
echo "     https://www.googleapis.com/auth/cloud-platform,"
echo "     https://www.googleapis.com/auth/logging.write"
echo "  5. Click 'Authorize'"
echo ""
echo -e "${YELLOW}⚠ Audit Logging (required for Log Analytics queries)${NC}"
echo "  1. Go to: https://console.cloud.google.com/iam-admin/audit"
echo "  2. Find 'Cloud Discovery Engine API'"
echo "  3. Enable: Admin Read, Data Read, Data Write"
echo ""
echo -e "${BOLD}Access Control${NC}"
echo "  Agent is accessible to ALL users in your Workspace domain."
echo "  To restrict access, see DEPLOYMENT.md §8 — Control Who Can Access the Agent."
echo ""
echo -e "${BOLD}Run a dry-run test:${NC}"
echo "  python deployment/deploy.py test"
echo ""
echo -e "${GREEN}Done! ${NC}"
