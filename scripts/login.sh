#!/usr/bin/env bash
# One-time interactive login for the hackathon project.
#
# Everything happens inside ./.gcloud so the production account and its
# Application Default Credentials are untouched. `gcloud config configurations`
# alone does NOT isolate ADC, which is why CLOUDSDK_CONFIG is pinned here.
set -euo pipefail
cd "$(dirname "$0")/.."

export CLOUDSDK_CONFIG="$PWD/.gcloud"
set -a; . ./.env; set +a

echo "Using isolated config: $CLOUDSDK_CONFIG"
echo "Account: $HACKATHON_ACCOUNT   Project: $HACKATHON_PROJECT"
echo

gcloud auth login "$HACKATHON_ACCOUNT" --brief
gcloud config set project "$HACKATHON_PROJECT" --quiet
gcloud auth application-default login --quiet
gcloud auth application-default set-quota-project "$HACKATHON_PROJECT" --quiet

echo
echo "Enabling Vertex AI..."
gcloud services enable aiplatform.googleapis.com --quiet

echo
./scripts/guard_env.sh
