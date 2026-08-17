#!/usr/bin/env bash
# Visualize the LIVE floci emulator state: product API (8000) + control room UI
# (5173) + DynamoDB browser (8001), all against the already-running floci.
#
#   ./scripts/floci/serve_floci_ui.sh          # start everything
#   ./scripts/floci/serve_floci_ui.sh stop     # stop everything (floci keeps running)
#
# Prereqs: floci up + provisioned (scripts/floci/up.sh + provision_floci.py),
# `uv sync --project apps/api --extra aws --extra dev`, `npm ci`.
set -euo pipefail
cd "$(dirname "$0")/../.."

if [[ "${1:-}" == "stop" ]]; then
  pkill -f "floci_product_api" 2>/dev/null || true
  pkill -f "vite --host 127.0.0.1 --port 5173" 2>/dev/null || true
  docker rm -f floci-ddb-admin >/dev/null 2>&1 || true
  echo "product API, web UI and DynamoDB browser stopped (floci still running)"
  exit 0
fi

curl -sf http://localhost:4566/_localstack/health >/dev/null \
  || { echo "floci is not running — start it with scripts/floci/up.sh" >&2; exit 1; }

mkdir -p data/floci

docker rm -f floci-ddb-admin >/dev/null 2>&1 || true
docker run -d --name floci-ddb-admin -p 8001:8001 \
  -e DYNAMO_ENDPOINT=http://host.docker.internal:4566 \
  -e AWS_REGION=us-east-1 -e AWS_ACCESS_KEY_ID=test -e AWS_SECRET_ACCESS_KEY=test \
  aaronshaf/dynamodb-admin >/dev/null

pkill -f "floci_product_api" 2>/dev/null || true
nohup uv run --project apps/api uvicorn --app-dir scripts/floci \
  floci_product_api:app --host 127.0.0.1 --port 8000 \
  > data/floci/product-api.log 2>&1 &

if ! pgrep -f "vite --host 127.0.0.1 --port 5173" >/dev/null; then
  nohup npm run dev --workspace apps/web > data/floci/web-dev.log 2>&1 &
fi

for _ in $(seq 1 20); do
  curl -sf http://127.0.0.1:8000/healthz >/dev/null 2>&1 && break; sleep 1
done

cat <<URLS

  Control room UI      http://127.0.0.1:5173        (Runs · Review queue · Lineage explorer · Impact)
  Product API          http://127.0.0.1:8000/api/overview
  DynamoDB browser     http://localhost:8001        (lineage-control · ledger · proposal · pointer)

  Seed a fresh IN_REVIEW proposal for the approval UI:
    uv run --project apps/api python scripts/floci/seed_pending_proposal.py

URLS
