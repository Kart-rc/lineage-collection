#!/bin/bash -p
set -uo pipefail

script_directory="${BASH_SOURCE[0]%/*}"
if [[ "$script_directory" == "${BASH_SOURCE[0]}" ]]; then
  script_directory="."
fi
repo_root="$(CDPATH= cd -- "$script_directory/.." && pwd -P)"
cd "$repo_root"

if [[ -z "${LINEAGE_REAL_REPOSITORY_CHECKOUT:-}" ]]; then
  echo '{"evidenceClass":"LOCAL_REAL_REPOSITORY_REQUIRED","outcome":"INTEGRATION_REQUIRED","reasonCode":"LINEAGE_REAL_REPOSITORY_CHECKOUT_REQUIRED"}'
  exit 2
fi

trusted_python="$repo_root/apps/api/.venv/bin/python"
supervisor="$repo_root/scripts/real_repository_acceptance_supervisor.py"
if [[ ! -x "$trusted_python" || ! -f "$supervisor" ]]; then
  echo '{"evidenceClass":"LOCAL_REAL_REPOSITORY_REQUIRED","outcome":"INTEGRATION_REQUIRED","reasonCode":"RUNNER_ENVIRONMENT_UNAVAILABLE"}'
  exit 2
fi

clean_environment=(
  /usr/bin/env -i
  LANG=C
  LC_ALL=C
  PATH=/usr/bin:/bin
  "LINEAGE_REAL_REPOSITORY_CHECKOUT=$LINEAGE_REAL_REPOSITORY_CHECKOUT"
)
if [[ -n "${LINEAGE_ACCEPTANCE_OUTPUT:-}" ]]; then
  clean_environment+=("LINEAGE_ACCEPTANCE_OUTPUT=$LINEAGE_ACCEPTANCE_OUTPUT")
fi
if [[ -n "${LINEAGE_ACCEPTANCE_RUN_ID:-}" ]]; then
  clean_environment+=("LINEAGE_ACCEPTANCE_RUN_ID=$LINEAGE_ACCEPTANCE_RUN_ID")
fi

exec "${clean_environment[@]}" "$trusted_python" -I "$supervisor" "$repo_root"
