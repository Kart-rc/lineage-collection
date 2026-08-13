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
        "deployment-event",
        "event-envelope",
        "evidence-ref",
        "impact-response",
        "outbox-event",
        "pr-gate-result",
        "proposal",
        "repository-checkout",
        "runtime-observation",
        "runtime-instrumentation-profile",
        "runtime-lease",
        "runtime-session-manifest",
        "runtime-window-manifest",
        "stage-execution",
    }


def test_repository_checkout_contract_is_closed_and_exact() -> None:
    registry = _contract_registry_type()(CONTRACTS_DIR)
    checkout = {
        "schemaVersion": "1.0.0",
        "origin": "https://github.com/spring-projects/spring-petclinic",
        "repository": "spring-petclinic",
        "revision": "88e37c15cf6fc8490b01bc3e8e2c800cec1ac272",
        "checkoutRoot": "/tmp/spring-petclinic",
        "environment": "test",
        "platform": "local",
        "system": "petclinic",
        "analyzerPack": "java-spring-data-jpa-v1",
        "ruleset": "java-spring-v1",
    }

    assert registry.validate("repository-checkout", checkout) == []
    assert registry.validate("repository-checkout", {**checkout, "revision": "a" * 64}) == []
    assert registry.validate("repository-checkout", {**checkout, "unknown": True})
    assert registry.validate(
        "repository-checkout",
        {**checkout, "origin": "https://user:secret@github.com/acme/repo"},
    )
    assert registry.validate(
        "repository-checkout", {**checkout, "origin": "http://github.com/acme/repo"}
    )
    assert registry.validate(
        "repository-checkout",
        {**checkout, "origin": "https://GitHub.com/spring-projects/spring-petclinic.git"},
    )
    assert registry.validate("repository-checkout", {**checkout, "revision": "A" * 40})
    assert registry.validate("repository-checkout", {**checkout, "revision": "a" * 39})
    missing_ruleset = dict(checkout)
    missing_ruleset.pop("ruleset")
    assert {error.path for error in registry.validate("repository-checkout", missing_ruleset)} == {
        "ruleset"
    }


@pytest.mark.parametrize(
    ("changes", "expected_path"),
    [
        ({"origin": "https://github.com:443/spring-projects/spring-petclinic"}, "origin"),
        ({"origin": "https://github.com:99999/spring-projects/spring-petclinic"}, "origin"),
        ({"origin": "https://github.com:0/spring-projects/spring-petclinic"}, "origin"),
        ({"origin": "https://github.com//spring-projects/spring-petclinic"}, "origin"),
        ({"origin": "https://github.com/./spring-projects/spring-petclinic"}, "origin"),
        ({"repository": "different-repository"}, "repository"),
    ],
)
def test_repository_checkout_contract_matches_runtime_identity_validation(
    changes: dict[str, str], expected_path: str
) -> None:
    registry = _contract_registry_type()(CONTRACTS_DIR)
    checkout = {
        "schemaVersion": "1.0.0",
        "origin": "https://github.com/spring-projects/spring-petclinic",
        "repository": "spring-petclinic",
        "revision": "88e37c15cf6fc8490b01bc3e8e2c800cec1ac272",
        "checkoutRoot": "/tmp/spring-petclinic",
        "environment": "test",
        "platform": "local",
        "system": "petclinic",
        "analyzerPack": "java-spring-data-jpa-v1",
        "ruleset": "java-spring-v1",
    }

    assert expected_path in {
        error.path for error in registry.validate("repository-checkout", {**checkout, **changes})
    }


def test_runtime_contracts_are_metadata_only_strict_and_completeness_explicit() -> None:
    registry = _contract_registry_type()(CONTRACTS_DIR)
    observation = {
        "schemaVersion": "1.0.0",
        "observationId": "runtime-observation-1",
        "sessionId": "runtime-session-1",
        "sequence": 1,
        "artifactDigest": "sha256:artifact-v1",
        "mechanism": "SDK",
        "granularity": "ELEMENT",
        "sourceDatasets": ["snowflake://payments/raw.transactions"],
        "targetDataset": "snowflake://payments/analytics.daily_revenue",
        "sourceFields": ["amount"],
        "targetField": "gross_revenue",
        "edgeType": "DERIVES",
        "transform": "SUM(amount)",
        "exact": True,
        "observedAt": "2026-08-06T12:00:00Z",
    }
    manifest = {
        "schemaVersion": "1.0.0",
        "sessionId": "runtime-session-1",
        "repo": "payments-pipeline",
        "environment": "staging",
        "artifactDigest": "sha256:artifact-v1",
        "outcome": "COMPLETE",
        "attempted": 1,
        "accepted": 1,
        "rejected": 0,
        "duplicates": 0,
        "buffered": 0,
        "dropped": 0,
        "drained": 1,
        "observationChecksum": "sha256:checksum",
        "closedAt": "2026-08-06T12:01:00Z",
    }

    assert registry.validate("runtime-observation", observation) == []
    assert registry.validate("runtime-session-manifest", manifest) == []
    assert registry.validate("runtime-observation", {**observation, "value": "secret"})
    assert registry.validate("runtime-session-manifest", {**manifest, "unknown": True})


def test_runtime_observation_contract_accepts_the_additive_optional_endpoint_field() -> None:
    # Task 6c: a service-anchored element edge's normalized observation mirrors the
    # source dataset/field into targetDataset/targetField and additionally carries
    # `endpoint` (the service URN) -- purely additive, so every existing observation
    # (without `endpoint`) still validates unchanged.
    registry = _contract_registry_type()(CONTRACTS_DIR)
    observation = {
        "schemaVersion": "1.0.0",
        "observationId": "runtime-observation-endpoint-1",
        "sessionId": "runtime-session-1",
        "sequence": 1,
        "artifactDigest": "sha256:artifact-v1",
        "mechanism": "SDK",
        "granularity": "ELEMENT",
        "sourceDatasets": ["mysql://petclinic/visits"],
        "targetDataset": "mysql://petclinic/visits",
        "sourceFields": ["pet_id"],
        "targetField": "pet_id",
        "endpoint": "service://spring-petclinic-microservices/"
        "org.springframework.samples.petclinic.visits.web.VisitResource#read",
        "edgeType": "READS",
        "exact": True,
        "observedAt": "2026-08-12T12:00:00Z",
    }

    assert registry.validate("runtime-observation", observation) == []


def test_runtime_production_control_contracts_are_closed_and_loss_explicit() -> None:
    registry = _contract_registry_type()(CONTRACTS_DIR)
    profile = {
        "schemaVersion": "1.0.0",
        "profileId": "payments-service-otel",
        "profileVersion": "1.0.0",
        "owner": "payments-platform",
        "workloadId": "payments-service",
        "workloadIdentity": "spiffe://lineage.local/workload/payments-service",
        "repo": "payments-service",
        "environment": "production",
        "environmentClass": "PRODUCTION",
        "mechanism": "OTEL",
        "installMode": "SHARED_COLLECTOR",
        "framework": {"name": "fastapi", "versionRange": ">=0.116,<0.117"},
        "allowedDatasets": ["snowflake://payments/raw.transactions"],
        "allowedAttributes": ["db.system.name", "db.namespace", "db.collection.name"],
        "artifactIdentityStrategy": "DIGEST",
        "permittedGranularity": ["CONNECTIVITY", "DATASET"],
        "parserContracts": [],
        "bufferBudget": {
            "maxRecords": 1000,
            "maxBytes": 1048576,
            "enqueueTimeoutMs": 2,
            "maxRetries": 3,
            "drainTimeoutSeconds": 10,
        },
        "overheadBudget": {
            "maxCpuPercent": 5,
            "maxMemoryBytes": 67108864,
            "maxP95EnqueueMillis": 2,
        },
        "deploymentCriticality": "REQUIRED",
        "canaryPercent": 5,
        "packageDigest": "sha256:" + "b" * 64,
    }
    lease = {
        "schemaVersion": "1.0.0",
        "leaseId": "runtime-lease-001",
        "profileId": profile["profileId"],
        "profileVersion": profile["profileVersion"],
        "profileDigest": "sha256:" + "e" * 64,
        "workloadId": profile["workloadId"],
        "workloadIdentity": profile["workloadIdentity"],
        "repo": profile["repo"],
        "environment": profile["environment"],
        "artifactDigest": "sha256:" + "a" * 64,
        "mechanism": profile["mechanism"],
        "datasets": profile["allowedDatasets"],
        "permittedGranularity": profile["permittedGranularity"],
        "issuedAt": "2026-08-07T12:00:00Z",
        "expiresAt": "2026-08-07T12:05:00Z",
        "windowId": "runtime-window-001",
        "policyEpoch": 1,
        "state": "ACTIVE",
        "token": "short-lived-signed-token",
    }
    manifest = {
        "schemaVersion": "1.0.0",
        "manifestId": "runtime-window-manifest-001",
        "windowId": "runtime-window-001",
        "leaseId": lease["leaseId"],
        "profileId": profile["profileId"],
        "profileVersion": profile["profileVersion"],
        "workloadId": profile["workloadId"],
        "repo": profile["repo"],
        "environment": profile["environment"],
        "artifactDigest": lease["artifactDigest"],
        "mechanism": profile["mechanism"],
        "outcome": "INCOMPLETE",
        "attempted": 11,
        "accepted": 9,
        "rejected": 1,
        "duplicates": 1,
        "retried": 2,
        "buffered": 1,
        "dropped": 1,
        "quarantined": 1,
        "drained": 9,
        "sourceChecksum": "sha256:" + "f" * 64,
        "observationChecksum": "sha256:" + "1" * 64,
        "reasons": [
            "BUFFER_NOT_DRAINED",
            "DROPPED_OBSERVATION",
            "QUARANTINED_OBSERVATION",
            "REJECTED_OBSERVATION",
        ],
        "reasonCounts": {
            "BUFFER_NOT_DRAINED": 1,
            "DROPPED_OBSERVATION": 1,
            "QUARANTINED_OBSERVATION": 1,
            "REJECTED_OBSERVATION": 1,
        },
        "emitterCounts": {
            "otel-collector-v1": {
                "attempted": 11,
                "accepted": 9,
                "rejected": 1,
                "duplicates": 1,
                "retried": 2,
                "buffered": 1,
                "dropped": 1,
                "quarantined": 1,
                "drained": 9,
            }
        },
        "closedAt": "2026-08-07T12:05:00Z",
    }

    assert registry.validate("runtime-instrumentation-profile", profile) == []
    assert registry.validate("runtime-lease", lease) == []
    assert registry.validate("runtime-window-manifest", manifest) == []
    assert registry.validate(
        "runtime-instrumentation-profile", {**profile, "unexpected": True}
    )
    assert registry.validate("runtime-lease", {**lease, "datasets": ["outside-scope"]}) == []
    assert registry.validate("runtime-window-manifest", {**manifest, "unknown": True})
    invalid_complete = {
        **manifest,
        "outcome": "COMPLETE",
        "buffered": 0,
        "rejected": 0,
        "quarantined": 0,
        "drained": 9,
    }
    assert {error.path for error in registry.validate("runtime-window-manifest", invalid_complete)} >= {
        "outcome"
    }
    complete = {
        **invalid_complete,
        "dropped": 0,
        "reasons": [],
        "reasonCounts": {},
        "emitterCounts": {
            "otel-collector-v1": {
                "attempted": 9,
                "accepted": 9,
                "rejected": 0,
                "duplicates": 0,
                "retried": 0,
                "buffered": 0,
                "dropped": 0,
                "quarantined": 0,
                "drained": 9,
            }
        },
        "attempted": 9,
        "duplicates": 0,
        "retried": 0,
    }
    assert registry.validate("runtime-window-manifest", complete) == []

    invalid_emitter_arithmetic = {
        **complete,
        "emitterCounts": {
            "otel-a": {
                "attempted": 1,
                "accepted": 2,
                "rejected": 0,
                "duplicates": 0,
                "retried": 0,
                "buffered": 0,
                "dropped": 0,
                "quarantined": 0,
                "drained": 2,
            },
            "otel-b": {
                "attempted": 8,
                "accepted": 7,
                "rejected": 0,
                "duplicates": 0,
                "retried": 0,
                "buffered": 0,
                "dropped": 0,
                "quarantined": 0,
                "drained": 7,
            },
        },
    }
    assert "emitterCounts.otel-a.attempted" in {
        error.path
        for error in registry.validate("runtime-window-manifest", invalid_emitter_arithmetic)
    }

    forged_reason_total = {
        **manifest,
        "reasonCounts": {
            **manifest["reasonCounts"],
            "DROPPED_OBSERVATION": 999,
        },
    }
    assert "reasonCounts" in {
        error.path for error in registry.validate("runtime-window-manifest", forged_reason_total)
    }

    expired_without_reason = {**complete, "outcome": "EXPIRED"}
    assert "reasons" in {
        error.path for error in registry.validate("runtime-window-manifest", expired_without_reason)
    }

    assert "expiresAt" in {
        error.path
        for error in registry.validate("runtime-lease", {**lease, "expiresAt": "not-a-time"})
    }
    assert "closedAt" in {
        error.path
        for error in registry.validate(
            "runtime-window-manifest", {**complete, "closedAt": "not-a-time"}
        )
    }


def test_deployment_event_is_strict_ordered_and_requires_digest_for_success() -> None:
    registry = _contract_registry_type()(CONTRACTS_DIR)
    event = {
        "schemaVersion": "1.0.0",
        "eventId": "deployment-001",
        "eventType": "DEPLOYMENT",
        "provider": "github-deployments",
        "providerSequence": 42,
        "attempt": 1,
        "system": "payments",
        "environment": "production",
        "outcome": "SUCCEEDED",
        "artifactDigest": "sha256:artifact-v42",
        "correlationId": "corr-deployment-001",
        "auditRef": "github://deployment/001",
        "occurredAt": "2026-08-05T12:00:00Z",
    }

    assert registry.validate("deployment-event", event) == []
    assert registry.validate("deployment-event", {**event, "unexpected": True})
    missing_digest = dict(event)
    missing_digest.pop("artifactDigest")
    assert {error.path for error in registry.validate("deployment-event", missing_digest)} == {
        "artifactDigest"
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


def test_event_envelope_closes_exact_repository_source_disposition() -> None:
    registry = _contract_registry_type()(CONTRACTS_DIR)
    source = {
        "sourceKind": "git-checkout",
        "origin": "https://example.com/acme/spring-service",
        "revision": "1" * 40,
        "scopeDigest": "sha256:" + "2" * 64,
        "scopeDispositionDigest": "sha256:" + "3" * 64,
        "analyzerPack": "java-spring-data-jpa-v1",
        "ruleset": "spring-data-rules-v1",
        "framework": "spring-data-jpa",
        "schemaProfile": "postgres",
        "platform": "postgres",
    }
    envelope = {
        "schemaVersion": "1.0.0",
        "eventId": "checkout-001",
        "eventType": "repo.push",
        "correlationId": "corr-checkout-001",
        "repo": "spring-service",
        "digest": "1" * 40,
        "env": "staging",
        "system": "orders",
        "lane": "events",
        "changedFiles": ["pom.xml"],
        "repositorySource": source,
        "receivedAt": "2026-08-10T12:00:00Z",
    }

    assert registry.validate("event-envelope", envelope) == []
    invalid = {
        **envelope,
        "repositorySource": {
            **source,
            "scopeDispositionDigest": "sha256:not-a-digest",
        },
    }
    assert {error.path for error in registry.validate("event-envelope", invalid)} == {
        "repositorySource.scopeDispositionDigest"
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


def test_pr_gate_result_is_strict_versioned_and_freshness_pinned() -> None:
    contract_registry = _contract_registry_type()
    registry = contract_registry(CONTRACTS_DIR)
    result = {
        "schemaVersion": "1.0.0",
        "checkId": "pr-check-001",
        "repo": "payments-pipeline",
        "prNumber": 42,
        "headSha": "head-abc",
        "environment": "staging",
        "environmentVersion": "v1",
        "environmentFence": 7,
        "deployedArtifactDigest": "sha256:deployed",
        "candidateArtifactDigest": "sha256:candidate",
        "policyVersion": "1.0.0",
        "verdict": "PASS",
        "reasons": [],
        "truncated": False,
        "evaluatedChangeTypes": ["COLUMN_DROP"],
        "evaluatedAt": "2026-08-05T12:00:00Z",
    }

    assert registry.validate("pr-gate-result", result) == []
    assert registry.validate("pr-gate-result", {**result, "unexpected": True})


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
        "sourceScopeDispositionDigest": "sha256:" + "a" * 64,
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
