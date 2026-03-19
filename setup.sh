#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# setup.sh — One-shot setup, deploy, and test for GE User Level Analytics
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh              # full flow: auth → enable APIs → deploy → test
#   ./setup.sh --dry-run    # skip deploy, just authenticate and validate
# ---------------------------------------------------------------------------
set -euo pipefail

DRY_RUN=false
for arg in "$@"; do
  [[ "$arg" == "--dry-run" ]] && DRY_RUN=true
done

# Load .env if present
if [[ -f .env ]]; then
  set -o allexport
  source .env
  set +o allexport
fi

PROJECT_ID="${GCP_PROJECT_ID:-}"
LOCATION="${GCP_LOCATION:-us-central1}"
STAGING_BUCKET="${STAGING_BUCKET:-gs://${PROJECT_ID}-agent-engine-staging}"

echo "================================================================"
echo "  GE User Level Analytics — Agent Engine Setup"
echo "  Project : $PROJECT_ID"
echo "  Location: $LOCATION"
echo "  Bucket  : $STAGING_BUCKET"
echo "================================================================"
echo ""

# ---- 1. Install Python dependencies ----------------------------------------
echo "[1/5] Installing Python dependencies …"
pip install -q \
  "google-adk>=1.0.0" \
  "google-cloud-bigquery>=3.10.0" \
  "google-api-python-client>=2.100.0" \
  "google-auth>=2.20.0" \
  "google-auth-httplib2>=0.2.0" \
  "google-cloud-logging>=3.5.0" \
  "google-cloud-aiplatform[reasoningengine]>=1.140.0" \
  "google-cloud-discoveryengine>=0.11.0" \
  "google-cloud-storage>=2.10.0" \
  "python-dotenv>=1.0.0" \
  "pydantic>=2.0.0" \
  "deprecated>=1.2.14" \
  --ignore-installed 2>&1 | grep -v "^WARNING" || true
echo "   Done."

# ---- 2. Authenticate --------------------------------------------------------
echo ""
echo "[2/5] Authenticating to Google Cloud …"
if gcloud auth application-default print-access-token &>/dev/null; then
  echo "   Already authenticated (ADC token valid)."
else
  echo "   Opening browser for gcloud auth application-default login …"
  gcloud auth application-default login --project="$PROJECT_ID"
fi
gcloud config set project "$PROJECT_ID" --quiet

# ---- 3. Enable required APIs -----------------------------------------------
echo ""
echo "[3/5] Enabling required Google Cloud APIs …"
gcloud services enable \
  aiplatform.googleapis.com \
  logging.googleapis.com \
  bigquery.googleapis.com \
  admin.googleapis.com \
  licensing.googleapis.com \
  gmail.googleapis.com \
  discoveryengine.googleapis.com \
  storage.googleapis.com \
  --project="$PROJECT_ID" --quiet
echo "   APIs enabled."

# ---- 4. Validate agent imports ----------------------------------------------
echo ""
echo "[4/5] Validating agent …"
python3 -c "
import sys; sys.path.insert(0, '.')
from ge_governance_agent.agent import root_agent
print(f'   Agent OK: {root_agent.name}  ({len(root_agent.tools)} tools)')
"

if $DRY_RUN; then
  echo ""
  echo "Dry run complete. Run without --dry-run to deploy."
  exit 0
fi

# ---- 5. Deploy to Agent Engine ---------------------------------------------
echo ""
echo "[5/5] Deploying to Vertex AI Agent Engine …"
cd "$(dirname "$0")"
python3 deployment/deploy.py deploy

echo ""
echo "================================================================"
echo "  Deployment complete!"
echo "  Resource name saved to .last_resource_name"
echo ""
echo "  To run a test:"
echo "    python3 deployment/deploy.py test"
echo ""
echo "  To list all deployed agents:"
echo "    python3 deployment/deploy.py list"
echo "================================================================"
