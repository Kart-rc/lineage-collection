#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "AWS_REQUIRED: $name is required" >&2
    exit 2
  fi
}

for name in \
  AWS_PROFILE AWS_REGION AWS_ACCOUNT_ID LINEAGE_EPHEMERAL_PREFIX \
  LINEAGE_SECONDARY_REGION LINEAGE_PRIMARY_AVAILABILITY_ZONES \
  LINEAGE_ENTERPRISE_ENDPOINT LINEAGE_ENTERPRISE_ENDPOINT_SERVICE_NAME \
  LINEAGE_PAGING_TOPIC_ARN; do
  require_env "$name"
done

if [[ "${ALLOW_LINEAGE_EPHEMERAL_AWS:-}" != "1" ]]; then
  echo "AWS_REQUIRED: set ALLOW_LINEAGE_EPHEMERAL_AWS=1 after approving the account and cost" >&2
  exit 2
fi
if [[ ! "$AWS_ACCOUNT_ID" =~ ^[0-9]{12}$ ]]; then
  echo "AWS_ACCOUNT_ID must be exactly 12 digits" >&2
  exit 2
fi
if [[ ! "$AWS_REGION" =~ ^[a-z]{2}(-gov)?-[a-z]+-[0-9]$ ]] ||
   [[ ! "$LINEAGE_SECONDARY_REGION" =~ ^[a-z]{2}(-gov)?-[a-z]+-[0-9]$ ]]; then
  echo "AWS regions are invalid" >&2
  exit 2
fi
if [[ "$AWS_REGION" == "$LINEAGE_SECONDARY_REGION" ]]; then
  echo "The recovery region must differ from AWS_REGION" >&2
  exit 2
fi
if [[ ! "$LINEAGE_EPHEMERAL_PREFIX" =~ ^lineage-e2e-[a-z0-9][a-z0-9-]{2,32}$ ]]; then
  echo "LINEAGE_EPHEMERAL_PREFIX must match lineage-e2e-[a-z0-9-]+" >&2
  exit 2
fi
if [[ ! "$LINEAGE_PRIMARY_AVAILABILITY_ZONES" =~ ^${AWS_REGION}[a-z],${AWS_REGION}[a-z],${AWS_REGION}[a-z]$ ]]; then
  echo "LINEAGE_PRIMARY_AVAILABILITY_ZONES must contain three comma-separated zones in AWS_REGION" >&2
  exit 2
fi
if [[ ! "$LINEAGE_ENTERPRISE_ENDPOINT" =~ ^https://[^[:space:]]+$ ]]; then
  echo "LINEAGE_ENTERPRISE_ENDPOINT must be HTTPS" >&2
  exit 2
fi
if [[ ! "$LINEAGE_ENTERPRISE_ENDPOINT_SERVICE_NAME" =~ ^com\.amazonaws\.vpce\..+\.vpce-svc-[a-z0-9]+$ ]]; then
  echo "LINEAGE_ENTERPRISE_ENDPOINT_SERVICE_NAME must identify an approved PrivateLink service" >&2
  exit 2
fi
if [[ ! "$LINEAGE_PAGING_TOPIC_ARN" =~ ^arn:aws[a-z-]*:sns:${AWS_REGION}:${AWS_ACCOUNT_ID}:.+$ ]]; then
  echo "LINEAGE_PAGING_TOPIC_ARN must belong to the approved primary account and region" >&2
  exit 2
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Refusing an AWS deployment from a dirty worktree; commit and verify the exact source first" >&2
  exit 2
fi

actual_account="$(aws --profile "$AWS_PROFILE" --region "$AWS_REGION" sts get-caller-identity --query Account --output text)"
if [[ "$actual_account" != "$AWS_ACCOUNT_ID" ]]; then
  echo "AWS caller account $actual_account does not match approved account $AWS_ACCOUNT_ID" >&2
  exit 2
fi

npm run package:aws
npm run build --workspace apps/web
metadata="$repo_root/infra/dist/runtime-build-metadata.json"
lambda_digest="$(node -e 'const m=require(process.argv[1]); process.stdout.write(m.images.lambda.digest)' "$metadata")"
sca_digest="$(node -e 'const m=require(process.argv[1]); process.stdout.write(m.images.sca.digest)' "$metadata")"
source_revision="$(git rev-parse HEAD)"
source_date_epoch="$(git show -s --format=%ct "$source_revision")"

context=(
  -c environment=ephemeral
  -c "resourcePrefix=$LINEAGE_EPHEMERAL_PREFIX"
  -c "account=$AWS_ACCOUNT_ID"
  -c "primaryRegion=$AWS_REGION"
  -c "secondaryRegion=$LINEAGE_SECONDARY_REGION"
  -c "primaryAvailabilityZones=$LINEAGE_PRIMARY_AVAILABILITY_ZONES"
  -c truthRetentionDays=1
  -c archiveRetentionDays=1
  -c monthlyBudgetUsd=25
  -c baselineMapConcurrency=4
  -c lambdaReservedConcurrency=intake=6,control-stage=2,classification=2,runtime-validation=2,consolidation=2,coverage=2,proposal=2,publication=1,deployment=1,product-api=4
  -c "enterpriseEndpoint=$LINEAGE_ENTERPRISE_ENDPOINT"
  -c "enterpriseEndpointServiceName=$LINEAGE_ENTERPRISE_ENDPOINT_SERVICE_NAME"
  -c "lambdaImageDigest=$lambda_digest"
  -c "scaImageDigest=$sca_digest"
  -c "pagingTopicArn=$LINEAGE_PAGING_TOPIC_ARN"
  -c "sourceRevision=$source_revision"
)

deploy_cdk() {
  (
    cd "$repo_root/infra"
    npx cdk deploy "$@" \
      --app "npx tsx bin/lineage-platform.ts" \
      --require-approval never \
      "${context[@]}"
  )
}

# Create the immutable repositories before publishing either runtime image.
deploy_cdk LineageRecovery LineageNetwork LineageData

registry="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
aws --profile "$AWS_PROFILE" --region "$AWS_REGION" ecr get-login-password |
  docker login --username AWS --password-stdin "$registry"

deploy_tmp="$(mktemp -d)"
trap 'rm -rf "$deploy_tmp"' EXIT

push_exact_image() {
  local name="$1"
  local platform="$2"
  local dockerfile="$3"
  local digest="$4"
  local repository="$LINEAGE_EPHEMERAL_PREFIX/${name}-runtime"
  local tag="${digest#sha256:}"
  local metadata_file="$deploy_tmp/$name.json"
  if aws --profile "$AWS_PROFILE" --region "$AWS_REGION" ecr describe-images \
      --repository-name "$repository" --image-ids "imageDigest=$digest" >/dev/null 2>&1; then
    echo "$name image $digest is already present"
    return
  fi
  docker buildx build \
    --platform "$platform" \
    --file "$repo_root/$dockerfile" \
    --tag "$registry/$repository:$tag" \
    --push \
    --metadata-file "$metadata_file" \
    --build-arg "SOURCE_DATE_EPOCH=$source_date_epoch" \
    --provenance=false \
    --sbom=false \
    "$repo_root"
  local pushed_digest
  pushed_digest="$(node -e 'const m=require(process.argv[1]); process.stdout.write(m["containerimage.digest"] || m["containerimage.descriptor"].digest)' "$metadata_file")"
  if [[ "$pushed_digest" != "$digest" ]]; then
    echo "$name pushed digest $pushed_digest does not match packaged digest $digest" >&2
    exit 1
  fi
}

push_exact_image lambda linux/amd64 infra/assets/lambda/Dockerfile "$lambda_digest"
push_exact_image sca linux/arm64 infra/assets/sca/Dockerfile "$sca_digest"

output_dir="$repo_root/output/ephemeral"
mkdir -p "$output_dir"
outputs_file="$output_dir/$LINEAGE_EPHEMERAL_PREFIX.json"
(
  cd "$repo_root/infra"
  npx cdk deploy --all \
    --app "npx tsx bin/lineage-platform.ts" \
    --require-approval never \
    --outputs-file "$outputs_file" \
    "${context[@]}"
)

echo "Ephemeral outputs: $outputs_file"
echo "Smoke: ALLOW_LINEAGE_EPHEMERAL_AWS_SMOKE=1 LINEAGE_AWS_OUTPUTS_FILE=$outputs_file ./scripts/smoke_ephemeral_aws.sh"
echo "Cleanup: ALLOW_LINEAGE_EPHEMERAL_AWS_CLEANUP=DESTROY ./scripts/cleanup_ephemeral_aws.sh"
