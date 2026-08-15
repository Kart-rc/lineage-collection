#!/usr/bin/env bash
# Start the floci AWS emulator (LocalStack-compatible) for the lineage E2E run.
set -euo pipefail

docker rm -f floci >/dev/null 2>&1 || true
docker run -d --name floci -p 4566:4566 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  floci/floci:latest >/dev/null

for _ in $(seq 1 30); do
  if curl -sf http://localhost:4566/_localstack/health >/dev/null 2>&1; then
    echo "floci is healthy at http://localhost:4566"
    exit 0
  fi
  sleep 1
done
echo "floci failed to become healthy within 30s" >&2
docker logs floci | tail -20 >&2
exit 1
