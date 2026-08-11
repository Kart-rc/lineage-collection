#!/usr/bin/env bash
set -euo pipefail

for name in AWS_PROFILE AWS_REGION AWS_ACCOUNT_ID LINEAGE_EPHEMERAL_PREFIX LINEAGE_SECONDARY_REGION; do
  if [[ -z "${!name:-}" ]]; then
    echo "$name is required" >&2
    exit 2
  fi
done
if [[ "${ALLOW_LINEAGE_EPHEMERAL_AWS_CLEANUP:-}" != "DESTROY" ]]; then
  echo "Set ALLOW_LINEAGE_EPHEMERAL_AWS_CLEANUP=DESTROY to remove the exact namespace" >&2
  exit 2
fi
if [[ ! "$AWS_ACCOUNT_ID" =~ ^[0-9]{12}$ ]] ||
   [[ ! "$LINEAGE_EPHEMERAL_PREFIX" =~ ^lineage-e2e-[a-z0-9][a-z0-9-]{2,32}$ ]]; then
  echo "Refusing cleanup: invalid account or namespace" >&2
  exit 2
fi
actual_account="$(aws --profile "$AWS_PROFILE" --region "$AWS_REGION" sts get-caller-identity --query Account --output text)"
if [[ "$actual_account" != "$AWS_ACCOUNT_ID" ]]; then
  echo "Refusing cleanup in unapproved account $actual_account" >&2
  exit 2
fi

delete_stack() {
  local region="$1"
  local stack="$2"
  if ! aws --profile "$AWS_PROFILE" --region "$region" cloudformation describe-stacks \
      --stack-name "$stack" >/dev/null 2>&1; then
    return
  fi
  echo "Deleting $stack in $region"
  aws --profile "$AWS_PROFILE" --region "$region" cloudformation delete-stack --stack-name "$stack"
  aws --profile "$AWS_PROFILE" --region "$region" cloudformation wait stack-delete-complete \
    --stack-name "$stack"
}

for component in operations api intake orchestration publication runtime engines data network; do
  delete_stack "$AWS_REGION" "$LINEAGE_EPHEMERAL_PREFIX-$component"
done
delete_stack "$LINEAGE_SECONDARY_REGION" "$LINEAGE_EPHEMERAL_PREFIX-recovery"
echo "Removed the exact ephemeral namespace $LINEAGE_EPHEMERAL_PREFIX from account $AWS_ACCOUNT_ID"

