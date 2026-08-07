#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

for name in AWS_PROFILE AWS_REGION AWS_ACCOUNT_ID LINEAGE_EPHEMERAL_PREFIX LINEAGE_AWS_OUTPUTS_FILE; do
  if [[ -z "${!name:-}" ]]; then
    echo "AWS_REQUIRED: $name is required" >&2
    exit 2
  fi
done
if [[ "${ALLOW_LINEAGE_EPHEMERAL_AWS_SMOKE:-}" != "1" ]]; then
  echo "AWS_REQUIRED: set ALLOW_LINEAGE_EPHEMERAL_AWS_SMOKE=1 after approving the smoke run" >&2
  exit 2
fi
if [[ ! "$LINEAGE_EPHEMERAL_PREFIX" =~ ^lineage-e2e-[a-z0-9][a-z0-9-]{2,32}$ ]]; then
  echo "Refusing a non-ephemeral namespace" >&2
  exit 2
fi
if [[ ! -f "$LINEAGE_AWS_OUTPUTS_FILE" ]]; then
  echo "AWS_REQUIRED: CDK outputs file does not exist" >&2
  exit 2
fi
actual_account="$(aws --profile "$AWS_PROFILE" --region "$AWS_REGION" sts get-caller-identity --query Account --output text)"
if [[ "$actual_account" != "$AWS_ACCOUNT_ID" ]]; then
  echo "AWS caller account $actual_account does not match approved account $AWS_ACCOUNT_ID" >&2
  exit 2
fi

export ALLOW_LINEAGE_EPHEMERAL_AWS_TEST=1
uv run --project apps/api --extra dev --extra aws pytest -q tests/aws/test_ephemeral_workflow.py

