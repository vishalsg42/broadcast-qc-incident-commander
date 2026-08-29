#!/usr/bin/env bash
# Waits for ./scripts/login.sh to complete, then immediately probes Vertex AI.
# Exists so the interactive login is the ONLY manual step.
set -uo pipefail
cd "$(dirname "$0")/.."
export CLOUDSDK_CONFIG="$PWD/.gcloud"
set -a; . ./.env; set +a

ADC="$CLOUDSDK_CONFIG/application_default_credentials.json"
DEADLINE=$(( $(date +%s) + 1800 ))

until [ -f "$ADC" ] && [ -n "$(gcloud config get-value account 2>/dev/null)" ]; do
  [ "$(date +%s)" -ge "$DEADLINE" ] && { echo "TIMEOUT: login not completed within 30 min"; exit 2; }
  sleep 10
done

echo "=== credentials detected ==="
./scripts/guard_env.sh || exit 1

echo
echo "=== Vertex AI reachable? ==="
gcloud services list --enabled --filter=aiplatform --format="value(config.name)" 2>&1 | head -2

echo
echo "=== probe: output_schema + tools on this model ==="
.venv/bin/python scripts/probe_output_schema_with_tools.py 2>&1 | tail -14
