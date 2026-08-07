#!/usr/bin/env bash
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

run_id="${LINEAGE_ACCEPTANCE_RUN_ID:-local-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
if [[ ! "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$ ]]; then
  echo "LINEAGE_ACCEPTANCE_RUN_ID must be a bounded file-safe identifier" >&2
  exit 2
fi
output_root="${LINEAGE_ACCEPTANCE_OUTPUT:-$repo_root/data/acceptance/$run_id}"
mkdir -p "$output_root"
export LINEAGE_ACCEPTANCE_OUTPUT="$output_root"

pytest_status=0
uv run --project apps/api --extra dev pytest -q tests/acceptance || pytest_status=$?

summary_status=0
uv run --project apps/api python -m lineage_api.testing.evidence summarize "$output_root" || summary_status=$?
echo "Acceptance evidence: $output_root"

if [[ "$pytest_status" -ne 0 ]]; then
  exit "$pytest_status"
fi
if [[ "$summary_status" -ne 0 ]]; then
  echo "Acceptance evidence contains FAIL or is missing" >&2
  exit "$summary_status"
fi

