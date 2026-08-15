#!/usr/bin/env bash
# Stop and remove the floci AWS emulator container.
set -euo pipefail
docker rm -f floci >/dev/null 2>&1 || true
echo "floci stopped"
