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

artifact_proofs=(
  tests/integration/aws
  apps/api/tests/application/test_pr_gate_stage_execution.py::test_pr_gate_stages_pin_read_only_facts_and_emit_one_stable_pass_check
  apps/api/tests/application/test_nightly_stage_execution.py::test_nightly_chain_raises_a_reference_based_proposal_and_alerts_for_drift
  apps/api/tests/application/test_sca_execution.py::test_sca_stages_materialize_pinned_source_and_emit_deterministic_assertion_refs
  apps/api/tests/application/test_stage_execution.py::test_deployment_d1_through_d6_promotes_only_the_authenticated_exact_package
  apps/api/tests/infrastructure/aws/test_composition.py::test_final_stage_preserves_terminal_outcome_on_success_and_checkpoint_replay
  apps/api/tests/infrastructure/aws/test_composition.py::test_sca_executor_verifies_the_persisted_result_before_checkpointing
  apps/api/tests/infrastructure/aws/test_composition.py::test_sca_aggregation_executor_checkpoints_an_exact_immutable_result
  apps/api/tests/infrastructure/aws/test_adapter_contracts.py::test_s3_uses_versioned_checksum_references_and_immutable_puts
)
pytest_status=0
uv run --project apps/api --extra dev pytest -q "${artifact_proofs[@]}" tests/acceptance || pytest_status=$?

real_repository_status="LOCAL_REAL_REPOSITORY_REQUIRED"
real_repository_exit=0
if [[ -n "${LINEAGE_REAL_REPOSITORY_CHECKOUT:-}" ]]; then
  ./scripts/run_real_repository_acceptance.sh || real_repository_exit=$?
  if [[ "$real_repository_exit" -eq 0 ]]; then
    real_repository_status="LOCAL_REAL_REPOSITORY_PASS"
  else
    real_repository_status="LOCAL_REAL_REPOSITORY_FAIL"
  fi
else
  echo '{"evidenceClass":"LOCAL_REAL_REPOSITORY_REQUIRED","outcome":"INTEGRATION_REQUIRED","reasonCode":"LINEAGE_REAL_REPOSITORY_CHECKOUT_REQUIRED"}'
fi

summary_status=0
uv run --project apps/api python -m lineage_api.testing.evidence summarize "$output_root" || summary_status=$?
echo "Acceptance evidence: $output_root"
hermetic_status="HERMITIC_LOCAL_PASS"
if [[ "$pytest_status" -ne 0 || "$summary_status" -ne 0 ]]; then
  hermetic_status="HERMITIC_LOCAL_FAIL"
fi
echo "Acceptance classes: $hermetic_status; $real_repository_status; RUNTIME_NOT_PROVIDED; AWS_REQUIRED"

if [[ "$pytest_status" -ne 0 ]]; then
  exit "$pytest_status"
fi
if [[ "$summary_status" -ne 0 ]]; then
  echo "Acceptance evidence contains FAIL or is missing" >&2
  exit "$summary_status"
fi
if [[ "$real_repository_exit" -ne 0 ]]; then
  exit "$real_repository_exit"
fi
