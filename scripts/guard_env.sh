#!/usr/bin/env bash
# Aborts unless BOTH the gcloud account and project match the hackathon values.
# `gcloud config configurations` does NOT isolate Application Default Credentials,
# so we also pin CLOUDSDK_CONFIG to an isolated directory.
set -euo pipefail

EXPECTED_ACCOUNT="${HACKATHON_ACCOUNT:-vishalsg42@gmail.com}"
EXPECTED_PROJECT="${HACKATHON_PROJECT:-}"

if [[ -z "${CLOUDSDK_CONFIG:-}" ]]; then
  echo "FAIL: CLOUDSDK_CONFIG is not set. Run 'source scripts/activate_env.sh' first." >&2
  exit 1
fi
if [[ -z "$EXPECTED_PROJECT" ]]; then
  echo "FAIL: HACKATHON_PROJECT is not set (see .env)." >&2
  exit 1
fi

ACTUAL_ACCOUNT="$(gcloud config get-value account 2>/dev/null || true)"
ACTUAL_PROJECT="$(gcloud config get-value project 2>/dev/null || true)"

fail=0
[[ "$ACTUAL_ACCOUNT" == "$EXPECTED_ACCOUNT" ]] || { echo "FAIL account: got '$ACTUAL_ACCOUNT', want '$EXPECTED_ACCOUNT'" >&2; fail=1; }
[[ "$ACTUAL_PROJECT" == "$EXPECTED_PROJECT" ]] || { echo "FAIL project: got '$ACTUAL_PROJECT', want '$EXPECTED_PROJECT'" >&2; fail=1; }

# Hard block on known production projects, whatever else is configured.
case "$ACTUAL_PROJECT" in
  nexkard-*|*-prod|*-production)
    echo "ABORT: '$ACTUAL_PROJECT' looks like a PRODUCTION project. Refusing." >&2
    exit 2 ;;
esac

[[ $fail -eq 0 ]] || exit 1
echo "OK  account=$ACTUAL_ACCOUNT  project=$ACTUAL_PROJECT  CLOUDSDK_CONFIG=$CLOUDSDK_CONFIG"
