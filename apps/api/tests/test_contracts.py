from __future__ import annotations

from pathlib import Path

import pytest


CONTRACTS_DIR = Path(__file__).parents[3] / "packages" / "contracts"


def _contract_registry_type():
    try:
        from lineage_api.contracts import ContractRegistry
    except ModuleNotFoundError:
        pytest.fail("ContractRegistry is not implemented")
    return ContractRegistry


def test_contract_registry_loads_all_platform_contracts() -> None:
    contract_registry = _contract_registry_type()

    registry = contract_registry(CONTRACTS_DIR)

    assert registry.names() == {
        "acceptance-evidence-manifest",
        "accepted-manifest",
        "consolidated-edge",
        "coverage-manifest",
        "durable-command",
        "event-envelope",
        "evidence-ref",
        "impact-response",
        "outbox-event",
        "proposal",
        "stage-execution",
    }


def test_contracts_have_versioned_strict_object_roots() -> None:
    contract_registry = _contract_registry_type()
    registry = contract_registry(CONTRACTS_DIR)

    for name in registry.names():
        schema = registry.schema(name)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].endswith(f"/{name}/1.0.0")
        assert schema["title"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


def test_event_envelope_requires_correlation_contract() -> None:
    contract_registry = _contract_registry_type()
    registry = contract_registry(CONTRACTS_DIR)

    errors = registry.validate("event-envelope", {"eventId": "delivery-1"})

    assert {error.path for error in errors} >= {
        "eventType",
        "correlationId",
        "repo",
        "digest",
        "env",
        "system",
    }


def test_acceptance_evidence_manifest_is_strict_and_versioned() -> None:
    contract_registry = _contract_registry_type()
    registry = contract_registry(CONTRACTS_DIR)
    valid = {
        "schemaVersion": "1.0.0",
        "buildId": "B04",
        "acceptanceId": "B04-AC-001",
        "artifactDigest": "sha256:demo",
        "outcome": "PASS",
        "evidenceRefs": ["object://acceptance/B04-AC-001"],
    }

    assert registry.validate("acceptance-evidence-manifest", valid) == []
    invalid = {**valid, "unexpected": True}
    assert {error.path for error in registry.validate("acceptance-evidence-manifest", invalid)} == {
        ""
    }


def test_acceptance_evidence_manifest_distinguishes_unproven_environment_claims() -> None:
    contract_registry = _contract_registry_type()
    registry = contract_registry(CONTRACTS_DIR)
    base = {
        "schemaVersion": "1.0.0",
        "buildId": "B16",
        "acceptanceId": "B16-AC-001",
        "artifactDigest": "sha256:demo",
        "evidenceRefs": ["object://acceptance/B16-AC-001"],
    }

    assert registry.validate(
        "acceptance-evidence-manifest", {**base, "outcome": "AWS_REQUIRED"}
    ) == []
    assert registry.validate(
        "acceptance-evidence-manifest", {**base, "outcome": "NOT_CONFIGURED"}
    ) == []


def _stage_execution_fixture(status: str = "RUNNING") -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "executionId": "stage-run-001-analyze",
        "commandId": "command-001",
        "idempotencyKey": "stage-key-001",
        "workflowKind": "INCREMENTAL",
        "workflowVersion": "1.0.0",
        "stageName": "ANALYZE",
        "scope": "repo:payments-pipeline",
        "artifactDigest": "sha256:source-v2",
        "determinantDigest": "sha256:determinants-v1",
        "status": status,
        "leaseOwner": "worker-a",
        "leaseEpoch": 1,
        "attempt": 1,
        "inputRef": "object://commands/command-001",
        "correlationId": "corr-001",
        "startedAt": "2026-08-05T12:00:00Z",
    }


def _coverage_manifest_fixture(state: str = "COMPLETE") -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "manifestId": "coverage-run-001",
        "workflowKind": "INCREMENTAL",
        "scope": "repo:payments-pipeline",
        "artifactDigest": "sha256:source-v2",
        "determinantDigest": "sha256:determinants-v1",
        "state": state,
        "expectedScope": ["pipeline.py", "models/revenue.sql"],
        "completedScope": ["pipeline.py"],
        "reusedScope": ["models/revenue.sql"],
        "skippedScope": [],
        "unsupportedScope": [],
        "quarantinedScope": [],
        "failedScope": [],
        "createdAt": "2026-08-05T12:00:00Z",
    }


def test_stage_execution_requires_output_and_completion_time_when_completed() -> None:
    contract_registry = _contract_registry_type()
    registry = contract_registry(CONTRACTS_DIR)
    running = _stage_execution_fixture()

    assert registry.validate("stage-execution", running) == []
    completed = {**running, "status": "COMPLETED", "completedAt": "2026-08-05T12:01:00Z"}
    assert {error.path for error in registry.validate("stage-execution", completed)} == {
        "outputRef"
    }
    completed["outputRef"] = "object://stages/analyze/result"
    assert registry.validate("stage-execution", completed) == []


def test_coverage_manifest_accounts_for_each_expected_scope_exactly_once() -> None:
    contract_registry = _contract_registry_type()
    registry = contract_registry(CONTRACTS_DIR)
    valid = _coverage_manifest_fixture()

    assert registry.validate("coverage-manifest", valid) == []
    unaccounted = {**valid, "reusedScope": []}
    assert {error.path for error in registry.validate("coverage-manifest", unaccounted)} == {
        "expectedScope"
    }
    duplicated = {**valid, "reusedScope": ["models/revenue.sql", "pipeline.py"]}
    assert {error.path for error in registry.validate("coverage-manifest", duplicated)} == {
        "expectedScope"
    }


def test_planned_coverage_manifest_may_have_unaccounted_expected_scope() -> None:
    contract_registry = _contract_registry_type()
    registry = contract_registry(CONTRACTS_DIR)
    planned = {
        **_coverage_manifest_fixture(state="PLANNED"),
        "completedScope": [],
        "reusedScope": [],
    }

    assert registry.validate("coverage-manifest", planned) == []


def test_complete_coverage_manifest_rejects_failed_or_unsupported_scope() -> None:
    contract_registry = _contract_registry_type()
    registry = contract_registry(CONTRACTS_DIR)
    complete = _coverage_manifest_fixture()
    invalid = {
        **complete,
        "completedScope": [],
        "failedScope": ["pipeline.py"],
    }

    assert {error.path for error in registry.validate("coverage-manifest", invalid)} == {
        "state"
    }
    invalid["state"] = "INCOMPLETE"
    assert registry.validate("coverage-manifest", invalid) == []


def test_durable_command_and_outbox_contracts_require_stable_delivery_identity() -> None:
    contract_registry = _contract_registry_type()
    registry = contract_registry(CONTRACTS_DIR)
    command = {
        "schemaVersion": "1.0.0",
        "commandId": "command-001",
        "idempotencyKey": "incremental:payments:source-v2",
        "workflowKind": "INCREMENTAL",
        "workflowVersion": "1.0.0",
        "scope": "repo:payments-pipeline",
        "artifactDigest": "sha256:source-v2",
        "determinantDigest": "sha256:determinants-v1",
        "status": "QUEUED",
        "attempt": 0,
        "maxAttempts": 5,
        "inputRef": "object://commands/command-001",
        "correlationId": "corr-001",
        "createdAt": "2026-08-05T12:00:00Z",
        "deadlineAt": "2026-08-05T12:30:00Z",
    }
    outbox = {
        "schemaVersion": "1.0.0",
        "outboxId": "outbox-001",
        "topic": "lineage.commands",
        "partitionKey": "payments-pipeline:staging",
        "payloadRef": "object://commands/command-001",
        "status": "PENDING",
        "attempts": 0,
        "availableAt": "2026-08-05T12:00:00Z",
        "correlationId": "corr-001",
        "createdAt": "2026-08-05T12:00:00Z",
    }

    assert registry.validate("durable-command", command) == []
    assert registry.validate("outbox-event", outbox) == []
    assert "idempotencyKey" in {
        error.path for error in registry.validate("durable-command", {**command, "idempotencyKey": ""})
    }
    delivered = {**outbox, "status": "DELIVERED"}
    assert {error.path for error in registry.validate("outbox-event", delivered)} == {
        "deliveredAt"
    }
