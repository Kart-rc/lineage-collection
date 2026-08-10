#!/usr/bin/env bash
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ -z "${LINEAGE_REAL_REPOSITORY_CHECKOUT:-}" ]]; then
  echo '{"evidenceClass":"LOCAL_REAL_REPOSITORY_REQUIRED","outcome":"INTEGRATION_REQUIRED","reasonCode":"LINEAGE_REAL_REPOSITORY_CHECKOUT_REQUIRED"}'
  exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
  echo '{"evidenceClass":"LOCAL_REAL_REPOSITORY_REQUIRED","outcome":"INTEGRATION_REQUIRED","reasonCode":"RUNNER_ENVIRONMENT_UNAVAILABLE"}'
  exit 2
fi

runner_output="$(
  uv run --offline --frozen --no-sync --project apps/api --extra dev \
    python tests/integration/test_spring_petclinic_repository.py 2>/dev/null
)"
runner_status=$?

if [[ ${#runner_output} -gt 2048 || "$runner_output" == *"$LINEAGE_REAL_REPOSITORY_CHECKOUT"* ]]; then
  echo '{"evidenceClass":"LOCAL_REAL_REPOSITORY_REQUIRED","outcome":"INTEGRATION_REQUIRED","reasonCode":"RUNNER_OUTPUT_INVALID"}'
  exit 2
fi
if [[ "$runner_status" -ne 0 && ! "$runner_output" =~ ^\{.*\}$ ]]; then
  echo '{"evidenceClass":"LOCAL_REAL_REPOSITORY_REQUIRED","outcome":"INTEGRATION_REQUIRED","reasonCode":"RUNNER_ENVIRONMENT_UNAVAILABLE"}'
  exit 2
fi
if [[ -z "$runner_output" || ! "$runner_output" =~ ^\{.*\}$ ]]; then
  echo '{"evidenceClass":"LOCAL_REAL_REPOSITORY_REQUIRED","outcome":"INTEGRATION_REQUIRED","reasonCode":"RUNNER_OUTPUT_INVALID"}'
  exit 2
fi

printf '%s\n' "$runner_output"
if [[ "$runner_status" -ne 0 ]]; then
  exit 2
fi
