from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lineage_api.application.workflows.deployment import (
    DeploymentEvent,
    DeploymentPackage,
    DeploymentWorkflow,
)
from lineage_api.db import Database
from lineage_api.infrastructure.sqlite_deployment import SQLiteDeploymentStore
from lineage_api.services.evidence_store import EvidenceStore
from lineage_api.services.publisher import PublisherService


EDGE = {
    "edgeKey": "edge-deployment",
    "from": ["urn:source"],
    "to": "urn:target",
    "edgeType": "DERIVES",
    "status": "PUBLISHED",
}


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _checksum(edges: list[dict[str, object]]) -> str:
    return hashlib.sha256(_canonical(sorted(edges, key=lambda edge: edge["edgeKey"])).encode()).hexdigest()


def _event(
    event_id: str,
    sequence: int,
    artifact: str | None = "sha256:artifact-v2",
    *,
    event_type: str = "DEPLOYMENT",
    outcome: str = "SUCCEEDED",
    attempt: int = 1,
    correlation_id: str | None = None,
) -> DeploymentEvent:
    return DeploymentEvent(
        event_id=event_id,
        event_type=event_type,
        provider="test-deployer",
        provider_sequence=sequence,
        attempt=attempt,
        system="payments",
        environment="staging",
        outcome=outcome,
        artifact_digest=artifact,
        correlation_id=correlation_id or f"corr-{event_id}",
        audit_ref=f"provider-audit://{event_id}",
        occurred_at="2026-08-05T12:00:00Z",
    )


def _package(artifact: str, version: str, checksum: str) -> DeploymentPackage:
    return DeploymentPackage(
        package_digest=f"sha256:package-{artifact.removeprefix('sha256:')}",
        system="payments",
        environment="staging",
        artifact_digest=artifact,
        graph_version=version,
        graph_checksum=checksum,
        manifest_ref=f"object://manifest/{version}",
        approval_ref=f"object://approval/{version}",
        approved=True,
    )


@pytest.fixture
def deployment(tmp_path: Path):
    database = Database(tmp_path / "lineage.db")
    database.initialize()
    v1_checksum = _checksum([])
    v2_checksum = _checksum([EDGE])
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO graph_versions(env, version, state, checksum, created_at)
            VALUES ('staging', 'v1', 'ACTIVE', ?, '2026-08-05T10:00:00Z')
            """,
            (v1_checksum,),
        )
        connection.execute(
            """
            INSERT INTO graph_versions(env, version, state, checksum, created_at)
            VALUES ('staging', 'v2', 'PACKAGE', ?, '2026-08-05T11:00:00Z')
            """,
            (v2_checksum,),
        )
        connection.execute(
            """
            INSERT INTO graph_edges(env, version, edge_key, payload_json)
            VALUES ('staging', 'v2', ?, ?)
            """,
            (EDGE["edgeKey"], _canonical(EDGE)),
        )
        connection.execute(
            """
            INSERT INTO pointers(env, active_version, fencing_token, updated_at)
            VALUES ('staging', 'v1', 0, '2026-08-05T10:00:00Z')
            """
        )
    store = SQLiteDeploymentStore(database, clock=lambda: "2026-08-05T12:01:00Z")
    store.register_package(_package("sha256:artifact-v1", "v1", v1_checksum))
    store.register_package(_package("sha256:artifact-v2", "v2", v2_checksum))
    publisher = PublisherService(
        database,
        EvidenceStore(database, tmp_path / "objects"),
        clock=lambda: "2026-08-05T12:01:00Z",
    )
    workflow = DeploymentWorkflow(
        store=store,
        publisher=publisher,
        authenticator=lambda _payload, signature: signature == "valid",
    )
    return workflow, store, publisher, database


def test_success_promotes_only_the_exact_artifact_package_and_reads_it_back(deployment) -> None:
    workflow, store, publisher, _ = deployment

    result = workflow.handle(_event("deploy-2", 2), signature="valid")

    assert result["state"] == "PROMOTED"
    assert result["deployedArtifactDigest"] == "sha256:artifact-v2"
    assert result["lineagePackageDigest"] == "sha256:package-artifact-v2"
    assert result["graphVersion"] == "v2"
    assert publisher.pointer("staging").active_version == "v2"
    persisted = store.state("payments", "staging")
    assert persisted is not None
    assert persisted.deployed_artifact_digest == "sha256:artifact-v2"
    assert persisted.lineage_package_digest == "sha256:package-artifact-v2"
    assert persisted.audit_ref == "provider-audit://deploy-2"


def test_failed_deployment_records_outcome_without_changing_pointer_or_deployed_digest(
    deployment,
) -> None:
    workflow, store, publisher, _ = deployment

    result = workflow.handle(
        _event("deploy-failed", 3, artifact=None, outcome="FAILED"), signature="valid"
    )

    assert result["state"] == "FAILED_NO_CHANGE"
    assert publisher.pointer("staging").active_version == "v1"
    state = store.state("payments", "staging")
    assert state is not None
    assert state.deployed_artifact_digest is None
    assert state.graph_version is None


def test_explicit_rollback_uses_the_target_artifact_package_and_is_audited(deployment) -> None:
    workflow, store, publisher, database = deployment
    workflow.handle(_event("deploy-2", 2), signature="valid")

    result = workflow.handle(
        _event(
            "rollback-3",
            3,
            artifact="sha256:artifact-v1",
            event_type="ROLLBACK",
        ),
        signature="valid",
    )

    assert result["state"] == "ROLLED_BACK"
    assert publisher.pointer("staging").active_version == "v1"
    assert store.state("payments", "staging").deployed_artifact_digest == "sha256:artifact-v1"
    with database.connection() as connection:
        audit = connection.execute(
            "SELECT action, correlation_id FROM audit_events WHERE correlation_id = 'corr-rollback-3'"
        ).fetchone()
    assert dict(audit) == {
        "action": "DEPLOYMENT_ROLLBACK",
        "correlation_id": "corr-rollback-3",
    }


def test_duplicate_event_returns_stored_result_without_advancing_the_fence_twice(deployment) -> None:
    workflow, _, publisher, database = deployment
    event = _event("deploy-duplicate", 4)

    first = workflow.handle(event, signature="valid")
    second = workflow.handle(event, signature="valid")

    assert first["state"] == second["state"] == "PROMOTED"
    assert second["duplicate"] is True
    assert publisher.pointer("staging").fencing_token == 1
    with database.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM deployment_events WHERE event_id = ?", (event.event_id,)
        ).fetchone()[0] == 1


def test_concurrent_redelivery_of_same_event_cannot_overwrite_success(deployment) -> None:
    _, store, publisher, _ = deployment
    event = _event("deploy-concurrent-duplicate", 5)

    class RedeliveringPublisher:
        def __init__(self) -> None:
            self.workflow: DeploymentWorkflow | None = None
            self.redelivered = False

        def pointer(self, environment: str):
            return publisher.pointer(environment)

        def promote_existing(self, **kwargs):
            if not self.redelivered:
                self.redelivered = True
                assert self.workflow is not None
                redelivery = self.workflow.handle(event, signature="valid")
                assert redelivery["state"] == "PROMOTED"
            return publisher.promote_existing(**kwargs)

    redelivering = RedeliveringPublisher()
    workflow = DeploymentWorkflow(
        store=store,
        publisher=redelivering,
        authenticator=lambda _payload, signature: signature == "valid",
    )
    redelivering.workflow = workflow

    result = workflow.handle(event, signature="valid")

    assert result["state"] == "PROMOTED"
    assert publisher.pointer("staging").fencing_token == 1
    assert store.state("payments", "staging").state == "PROMOTED"


def test_older_provider_order_is_recorded_as_stale_and_cannot_move_pointer(deployment) -> None:
    workflow, store, publisher, _ = deployment
    workflow.handle(_event("deploy-new", 10), signature="valid")

    stale = workflow.handle(
        _event("deploy-old", 9, artifact="sha256:artifact-v1"), signature="valid"
    )

    assert stale["state"] == "FAILED_NO_CHANGE"
    assert stale["reason"] == "STALE_EVENT"
    assert publisher.pointer("staging").active_version == "v2"
    assert store.state("payments", "staging").provider_sequence == 10


def test_newer_concurrent_promotion_wins_and_stale_writer_cannot_overwrite_state(
    deployment,
) -> None:
    _, store, publisher, database = deployment
    v3_edge = {**EDGE, "edgeKey": "edge-v3", "to": "urn:target-v3"}
    v3_checksum = _checksum([v3_edge])
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO graph_versions(env, version, state, checksum, created_at)
            VALUES ('staging', 'v3', 'PACKAGE', ?, '2026-08-05T11:30:00Z')
            """,
            (v3_checksum,),
        )
        connection.execute(
            """
            INSERT INTO graph_edges(env, version, edge_key, payload_json)
            VALUES ('staging', 'v3', ?, ?)
            """,
            (v3_edge["edgeKey"], _canonical(v3_edge)),
        )
    store.register_package(_package("sha256:artifact-v3", "v3", v3_checksum))

    class RacingPublisher:
        def __init__(self) -> None:
            self.workflow: DeploymentWorkflow | None = None
            self.raced = False

        def pointer(self, environment: str):
            return publisher.pointer(environment)

        def promote_existing(self, **kwargs):
            if kwargs["correlation_id"] == "corr-deploy-old" and not self.raced:
                self.raced = True
                assert self.workflow is not None
                newer = self.workflow.handle(
                    _event("deploy-new", 21, artifact="sha256:artifact-v3"),
                    signature="valid",
                )
                assert newer["state"] == "PROMOTED"
            return publisher.promote_existing(**kwargs)

    racing_publisher = RacingPublisher()
    workflow = DeploymentWorkflow(
        store=store,
        publisher=racing_publisher,
        authenticator=lambda _payload, signature: signature == "valid",
    )
    racing_publisher.workflow = workflow

    old = workflow.handle(_event("deploy-old", 20), signature="valid")

    assert old["state"] == "FAILED_NO_CHANGE"
    assert old["reason"] in {"POINTER_CONFLICT", "FENCE_LOST", "SUPERSEDED"}
    assert publisher.pointer("staging").active_version == "v3"
    state = store.state("payments", "staging")
    assert state.provider_sequence == 21
    assert state.deployed_artifact_digest == "sha256:artifact-v3"


def test_missing_exact_package_is_visible_out_of_sync_and_retains_last_good_graph(
    deployment,
) -> None:
    workflow, store, publisher, _ = deployment

    result = workflow.handle(
        _event("deploy-missing", 30, artifact="sha256:not-packaged"), signature="valid"
    )

    assert result["state"] == "LINEAGE_OUT_OF_SYNC"
    assert result["deployedArtifactDigest"] == "sha256:not-packaged"
    assert result["lineagePackageDigest"] is None
    assert publisher.pointer("staging").active_version == "v1"
    state = store.state("payments", "staging")
    assert state.deployed_artifact_digest == "sha256:not-packaged"
    assert state.lineage_package_digest is None
    assert state.graph_version == "v1"


def test_invalid_package_checksum_fails_terminally_without_pointer_change(deployment) -> None:
    workflow, store, publisher, database = deployment
    with database.transaction() as connection:
        connection.execute(
            "UPDATE lineage_packages SET graph_checksum = ? WHERE artifact_digest = ?",
            ("0" * 64, "sha256:artifact-v2"),
        )

    result = workflow.handle(_event("deploy-invalid-package", 31), signature="valid")

    assert result["state"] == "FAILED_TERMINAL"
    assert result["reason"] == "VERIFY_MISMATCH"
    assert publisher.pointer("staging").active_version == "v1"
    assert store.state("payments", "staging").deployed_artifact_digest == "sha256:artifact-v2"


def test_merge_event_and_failed_authentication_do_not_mutate_deployment_state(deployment) -> None:
    workflow, _, _, database = deployment
    before = database.snapshot(("deployment_events", "deployment_state", "pointers"))

    merge = workflow.handle(
        _event("merge-only", 40, event_type="MERGE", artifact=None), signature="valid"
    )
    unauthenticated = workflow.handle(_event("deploy-unauthenticated", 41), signature="bad")

    assert merge == {
        "schemaVersion": "1.0.0",
        "eventId": "merge-only",
        "state": "FAILED_NO_CHANGE",
        "reason": "NON_DEPLOYMENT_EVENT",
        "duplicate": False,
    }
    assert unauthenticated["reason"] == "AUTHENTICATION_FAILED"
    assert database.snapshot(("deployment_events", "deployment_state", "pointers")) == before
