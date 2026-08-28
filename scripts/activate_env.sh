#!/usr/bin/env bash
# source scripts/activate_env.sh
# Isolates gcloud + ADC for this project so nothing can touch the production account.
_root="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
export CLOUDSDK_CONFIG="$_root/.gcloud"
mkdir -p "$CLOUDSDK_CONFIG"
[[ -f "$_root/.env" ]] && set -a && . "$_root/.env" && set +a
[[ -d "$_root/.venv" ]] && . "$_root/.venv/bin/activate"
echo "env isolated -> CLOUDSDK_CONFIG=$CLOUDSDK_CONFIG"
echo "project=${HACKATHON_PROJECT:-<unset>} region=${GCP_REGION:-<unset>}"
