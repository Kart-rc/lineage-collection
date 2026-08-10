from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from lineage_api.config import Settings


PROJECT_ROOT = Path(__file__).parents[4]
SECRET = "test-lineage-secret"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=PROJECT_ROOT,
        data_directory=tmp_path,
        fixture_directory=PROJECT_ROOT / "fixtures",
        database_path=tmp_path / "lineage.db",
        object_directory=tmp_path / "objects",
        webhook_secret=SECRET,
    )


def _delivery(event_id: str = "delivery-001"):
    from lineage_api.services.intake import PushDelivery

    payload = {
        "eventId": event_id,
        "eventType": "repo.push",
        "repo": "payments-pipeline",
        "digest": "demo-digest-v2",
        "env": "staging",
        "system": "payments",
        "changedFiles": ["pipeline.py"],
        "receivedAt": "2026-08-04T16:00:00Z",
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return PushDelivery(payload, f"sha256={signature}")


def test_run_records_ordered_correlated_evidence_and_continues_after_approval(tmp_path) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()

    collected = services.orchestration.process_push(_delivery())

    assert collected["outcome"] == "ACCEPTED"
    assert collected["command"]["status"] == "COMPLETED"
    assert collected["run"]["state"] == "IN_REVIEW"
    assert collected["proposal"]["state"] == "IN_REVIEW"
    assert [stage["stage"] for stage in collected["run"]["stages"]] == [
        "QUEUED",
        "CLASSIFYING",
        "ANALYZING",
        "RESOLVING",
        "STORING_EVIDENCE",
        "MERGING",
        "PROPOSING",
        "IN_REVIEW",
    ]
    assert {stage["correlationId"] for stage in collected["run"]["stages"]} == {
        "corr-delivery-001"
    }
    storing = collected["run"]["stages"][4]["detail"]
    assert storing["scaEvidenceRef"]["checksum"]
    assert storing["runtimeEvidenceStatus"] == "NOT_PROVIDED"
    assert collected["coverageManifest"]["state"] == "COMPLETE"
    assert collected["evidenceManifest"]["runtime"] == {"status": "NOT_PROVIDED"}
    with services.database.connection() as connection:
        durable = connection.execute(
            "SELECT status, attempt FROM commands WHERE command_id = ?",
            (collected["command"]["commandId"],),
        ).fetchone()
        outbox = connection.execute("SELECT status FROM outbox_events").fetchone()
        message = connection.execute("SELECT status FROM lane_messages").fetchone()
    assert dict(durable) == {"status": "COMPLETED", "attempt": 1}
    assert outbox["status"] == "DELIVERED"
    assert message["status"] == "ACKED"

    proposal = collected["proposal"]
    approved = services.orchestration.approve(
        proposal["proposalId"],
        proposal["version"],
        actor="reviewer@example.test",
        rationale="Exact static evidence and complete runtime session agree.",
        expected_lock_version=proposal["lockVersion"],
    )

    assert approved["proposal"]["state"] == "FINALIZED"
    assert approved["pointer"]["activeVersion"] == "v2"
    assert approved["approvalRef"]["checksum"]
    assert approved["manifestRef"]["checksum"]
    assert approved["run"]["state"] == "PUBLISHED"
    assert [stage["stage"] for stage in approved["run"]["stages"][-2:]] == [
        "PUBLISHING",
        "PUBLISHED",
    ]


def test_duplicate_delivery_reuses_the_only_run(tmp_path) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()
    first = services.orchestration.process_push(_delivery())
    duplicate = services.orchestration.process_push(_delivery())

    assert duplicate["outcome"] == "DUPLICATE"
    assert duplicate["command"]["commandId"] == first["command"]["commandId"]
    assert duplicate["run"]["runId"] == first["run"]["runId"]
    assert len(services.orchestration.list_runs()) == 1


def test_unknown_classification_blocks_analysis(tmp_path) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()
    result = services.orchestration.process_push(
        _delivery("delivery-unknown"), classification_evidence=[]
    )

    assert result["outcome"] == "BLOCKED"
    assert result["reason"] == "UNKNOWN_CLASSIFICATION"
    assert result["run"]["state"] == "FAILED"
    assert result["run"]["failedStage"] == "CLASSIFYING"
    assert result["proposal"] is None


def test_closed_analyzer_registry_rejects_mismatched_determinants() -> None:
    from lineage_api.services.analyzer_registry import (
        AnalyzerRegistry,
        AnalyzerSelection,
        AnalyzerSelectionError,
    )

    registry = AnalyzerRegistry.default()
    accepted = AnalyzerSelection(
        analyzer_pack="java-spring-data-jpa-v1",
        ruleset="spring-data-rules-v1",
        source_kind="git-checkout",
        framework="spring-data-jpa",
        schema_profile="postgres",
    )

    assert registry.resolve(accepted).analyzer_pack == "java-spring-data-jpa-v1"
    cases = (
        ({"analyzer_pack": "unknown-v1"}, "UNKNOWN_ANALYZER_PACK"),
        ({"ruleset": "spring-data-rules-v2"}, "RULESET_MISMATCH"),
        ({"source_kind": "fixture"}, "SOURCE_KIND_MISMATCH"),
        ({"framework": "python-dataset-api"}, "FRAMEWORK_MISMATCH"),
        ({"schema_profile": "oracle"}, "PROFILE_MISMATCH"),
    )
    for overrides, code in cases:
        selection = AnalyzerSelection(
            **{
                **{
                    "analyzer_pack": accepted.analyzer_pack,
                    "ruleset": accepted.ruleset,
                    "source_kind": accepted.source_kind,
                    "framework": accepted.framework,
                    "schema_profile": accepted.schema_profile,
                },
                **overrides,
            }
        )
        with pytest.raises(AnalyzerSelectionError) as captured:
            registry.resolve(selection)
        assert captured.value.code == code
        assert len(str(captured.value)) <= 160


def test_checkout_event_identity_includes_environment_and_system() -> None:
    from lineage_api.services.analyzer_registry import deterministic_checkout_event_id

    metadata = {
        "sourceKind": "git-checkout",
        "origin": "https://example.com/acme/service",
        "revision": "1" * 40,
        "scopeDigest": "sha256:" + "2" * 64,
        "analyzerPack": "java-spring-data-jpa-v1",
        "ruleset": "spring-data-rules-v1",
        "framework": "spring-data-jpa",
        "schemaProfile": "postgres",
        "platform": "postgres",
    }

    staging = deterministic_checkout_event_id(
        metadata, "service", "staging", "orders"
    )

    assert staging != deterministic_checkout_event_id(
        metadata, "service", "production", "orders"
    )
    assert staging != deterministic_checkout_event_id(
        metadata, "service", "staging", "billing"
    )


def test_repository_source_metadata_is_signed_and_changes_command_determinant(tmp_path) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()
    source = {
        "sourceKind": "git-checkout",
        "origin": "https://example.com/acme/payments-pipeline",
        "revision": "1" * 40,
        "scopeDigest": "sha256:" + "2" * 64,
        "analyzerPack": "java-spring-data-jpa-v1",
        "ruleset": "spring-data-rules-v1",
        "framework": "spring-data-jpa",
        "schemaProfile": "postgres",
        "platform": "postgres",
    }
    first = _delivery("delivery-source-a")
    first.payload["repositorySource"] = source
    first = _resign(first)
    changed = _delivery("delivery-source-b")
    changed.payload["repositorySource"] = {**source, "scopeDigest": "sha256:" + "3" * 64}
    changed = _resign(changed)

    accepted = services.orchestration._intake.accept(first)
    other = services.orchestration._intake.accept(changed)

    assert accepted.envelope["repositorySource"] == source
    assert accepted.command.determinant_digest != other.command.determinant_digest


def test_conflicting_source_determinant_reuse_is_typed_and_rejected(tmp_path) -> None:
    from lineage_api.dependencies import build_services
    from lineage_api.infrastructure.sqlite_control import IdempotencyConflictError

    services = build_services(_settings(tmp_path))
    services.reset()
    source = {
        "sourceKind": "git-checkout",
        "origin": "https://example.com/acme/payments-pipeline",
        "revision": "1" * 40,
        "scopeDigest": "sha256:" + "2" * 64,
        "analyzerPack": "java-spring-data-jpa-v1",
        "ruleset": "spring-data-rules-v1",
        "framework": "spring-data-jpa",
        "schemaProfile": "postgres",
        "platform": "postgres",
    }
    first = _delivery("delivery-source-conflict")
    first.payload["repositorySource"] = source
    conflicting = _delivery("delivery-source-conflict")
    conflicting.payload["repositorySource"] = {
        **source,
        "scopeDigest": "sha256:" + "3" * 64,
    }

    services.orchestration._intake.accept(_resign(first))
    with pytest.raises(IdempotencyConflictError, match="event identity"):
        services.orchestration._intake.accept(_resign(conflicting))


def _resign(delivery):
    from lineage_api.services.intake import PushDelivery

    body = json.dumps(delivery.payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return PushDelivery(delivery.payload, f"sha256={signature}")
