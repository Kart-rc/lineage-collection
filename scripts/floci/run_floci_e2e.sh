#!/usr/bin/env bash
# Full floci-backed E2E: clean emulator -> provision -> baseline/incremental/
# deployment/PR-gate suite for spring-petclinic -> evidence under data/acceptance/.
# Requires: Docker, JDK 21 at /opt/homebrew/opt/openjdk@21, a pinned checkout at
# /tmp/spring-petclinic (88e37c15), and `uv sync --project apps/api --extra aws --extra dev`.
set -euo pipefail
cd "$(dirname "$0")/../.."

bash scripts/floci/down.sh
bash scripts/floci/up.sh
uv run --project apps/api python scripts/floci/provision_floci.py
rm -rf data/floci

ALLOW_LINEAGE_FLOCI_E2E=1 uv run --project apps/api pytest \
  tests/integration/floci/test_petclinic_floci_e2e.py -x -q "$@"
