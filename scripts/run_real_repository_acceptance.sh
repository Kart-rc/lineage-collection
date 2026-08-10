#!/usr/bin/env bash
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ -z "${LINEAGE_REAL_REPOSITORY_CHECKOUT:-}" ]]; then
  echo '{"evidenceClass":"LOCAL_REAL_REPOSITORY_REQUIRED","outcome":"INTEGRATION_REQUIRED","reasonCode":"LINEAGE_REAL_REPOSITORY_CHECKOUT_REQUIRED"}'
  exit 2
fi

uv run --project apps/api --extra dev python tests/integration/test_spring_petclinic_repository.py
