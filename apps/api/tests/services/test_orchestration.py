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


def _delivery(event_id: str = "delivery-001", *, digest: str = "demo-digest-v2"):
    from lineage_api.services.intake import PushDelivery

    payload = {
        "eventId": event_id,
        "eventType": "repo.push",
        "repo": "payments-pipeline",
        "digest": digest,
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
    assert duplicate["command"]["status"] == "COMPLETED"
    assert duplicate["command"]["commandId"] == first["command"]["commandId"]
    assert duplicate["run"]["runId"] == first["run"]["runId"]
    assert duplicate["proposal"]["proposalId"] == first["proposal"]["proposalId"]
    assert len(services.orchestration.list_runs()) == 1


def test_accepted_delivery_drains_until_its_exact_command_is_terminal(tmp_path) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()
    older = services.orchestration._intake.accept(
        _delivery("delivery-older", digest="demo-digest-v1")
    )
    assert older.command is not None

    target = services.orchestration.process_push(_delivery("delivery-target"))

    target_command_id = (
        "command-" + hashlib.sha256(b"delivery-target").hexdigest()[:20]
    )
    assert target["command"]["commandId"] == target_command_id
    assert target["command"]["status"] == "COMPLETED"
    assert target["proposal"] is not None
    assert services.orchestration._command_store.get(older.command.command_id).status == (
        "COMPLETED"
    )


def test_queued_duplicate_is_processed_before_returning_duplicate_summary(tmp_path) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()
    queued = services.orchestration._intake.accept(_delivery("delivery-queued"))
    assert queued.command is not None
    assert queued.command.status == "QUEUED"

    duplicate = services.orchestration.process_push(_delivery("delivery-queued"))

    assert duplicate["outcome"] == "DUPLICATE"
    assert duplicate["command"]["commandId"] == queued.command.command_id
    assert duplicate["command"]["status"] == "COMPLETED"
    assert duplicate["proposal"] is not None


def test_active_target_lease_fails_bounded_without_duplicate_effects(tmp_path) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()
    accepted = services.orchestration._intake.accept(_delivery("delivery-concurrent"))
    assert accepted.command is not None
    services.orchestration._outbox_dispatcher.dispatch(limit=100)
    message = services.orchestration._broker.claim(
        "events", "concurrent-worker", visibility_timeout_seconds=60
    )
    assert message is not None
    lease = services.orchestration._command_store.claim(
        accepted.command.command_id, "concurrent-worker", lease_seconds=60
    )

    with pytest.raises(RuntimeError, match="did not reach a terminal state"):
        services.orchestration.process_push(_delivery("delivery-concurrent"))

    with services.database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    services.orchestration._command_store.fail(
        lease, "CONCURRENT_TEST_RELEASE", retryable=True
    )
    services.orchestration._broker.retry(
        message,
        available_at=services.orchestration._durable_clock.now(),
        error_code="CONCURRENT_TEST_RELEASE",
    )

    recovered = services.orchestration.process_push(_delivery("delivery-concurrent"))
    assert recovered["outcome"] == "DUPLICATE"
    assert recovered["command"]["status"] == "COMPLETED"
    with services.database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 1


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
        "scopeDispositionDigest": "sha256:" + "4" * 64,
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
        "scopeDispositionDigest": "sha256:" + "4" * 64,
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
        "scopeDispositionDigest": "sha256:" + "4" * 64,
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
