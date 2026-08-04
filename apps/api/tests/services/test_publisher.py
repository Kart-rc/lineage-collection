from __future__ import annotations

from pathlib import Path

import pytest

from lineage_api.db import Database
from lineage_api.domain.errors import DomainError
from lineage_api.domain.proposals import ApprovalRecord, Proposal
from lineage_api.services.evidence_store import EvidenceStore


SOURCE = "urn:ldp:staging:snowflake:payments:raw.transactions#amount"
TARGET = "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue"
EDGE = {
    "schemaVersion": "1.0.0",
    "edgeKey": "edge-demo",
    "version": 2,
    "from": [SOURCE],
    "to": TARGET,
    "edgeType": "DERIVES",
    "band": "HIGH",
    "corroboration": "ELEMENT",
    "status": "PROPOSED",
    "transform": "SUM(amount)",
    "provenance": [],
    "autoPublishable": True,
    "system": "payments",
    "updatedAt": "2026-08-04T16:00:00Z",
}


def _publisher_type():
    try:
        from lineage_api.services.publisher import PublisherService
    except ModuleNotFoundError:
        pytest.fail("Publisher is not implemented")
    return PublisherService


@pytest.fixture
def publisher(tmp_path: Path):
    database = Database(tmp_path / "lineage.db")
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO graph_versions(env, version, state, checksum, created_at)
            VALUES ('staging', 'v1', 'ACTIVE', ?, '2026-08-04T12:00:00Z')
            """,
            ("0" * 64,),
        )
        connection.execute(
            """
            INSERT INTO pointers(env, active_version, fencing_token, updated_at)
            VALUES ('staging', 'v1', 0, '2026-08-04T12:00:00Z')
            """
        )
    store = EvidenceStore(
        database,
        tmp_path / "objects",
        clock=lambda: "2026-08-04T18:00:00Z",
    )
    service = _publisher_type()(
        database,
        store,
        clock=lambda: "2026-08-04T18:00:00Z",
    )
    return service, database, store


def _proposal() -> Proposal:
    return Proposal(
        proposal_id="proposal-demo",
        version=1,
        system="payments",
        state="APPROVED",
        expected_base_version="v1",
        diff={"added": [EDGE], "removed": [], "bandChanged": []},
        correlation_id="corr-001",
        created_at="2026-08-04T17:00:00Z",
        updated_at="2026-08-04T17:00:00Z",
        lock_version=2,
    )


def _approval(store: EvidenceStore) -> ApprovalRecord:
    reference = store.put(
        "approval",
        "payments/approval-demo",
        {"schemaVersion": "1.0.0", "decision": "APPROVED"},
        "1.0.0",
    )
    return ApprovalRecord(
        approval_id="approval-demo",
        proposal_id="proposal-demo",
        proposal_version=1,
        decision="APPROVED",
        actor="reviewer@example.test",
        rationale="verified",
        correlation_id="corr-001",
        decided_at="2026-08-04T17:00:00Z",
        reference=reference,
    )


def test_approved_manifest_stages_verifies_and_atomically_advances_pointer(publisher) -> None:
    service, database, store = publisher

    result = service.publish(_proposal(), _approval(store), env="staging")

    assert result.namespace_version == "v2"
    assert result.pointer.active_version == "v2"
    assert result.pointer.fencing_token == 1
    assert store.get(result.manifest_ref)["approvalId"] == "approval-demo"
    with database.connection() as connection:
        states = {
            row["version"]: row["state"]
            for row in connection.execute(
                "SELECT version, state FROM graph_versions WHERE env = 'staging'"
            )
        }
    assert states == {"v1": "PRIOR", "v2": "ACTIVE"}


def test_expired_publisher_cannot_advance_pointer(publisher) -> None:
    service, _, _ = publisher
    stale = service.reserve(env="staging", expected_prior="v1")
    staged = service.stage(stale, [EDGE], manifest_ref="manifest://stale")
    current = service.reserve(env="staging", expected_prior="v1")

    with pytest.raises(DomainError) as captured:
        service.activate(stale, staged)

    assert captured.value.code == "FENCE_LOST"
    assert current.token > stale.token
    assert service.pointer("staging").active_version == "v1"


def test_stage_verification_mismatch_never_swaps_pointer(publisher) -> None:
    service, database, _ = publisher
    reservation = service.reserve("staging", "v1")
    staged = service.stage(reservation, [EDGE], manifest_ref="manifest://demo")
    with database.transaction() as connection:
        connection.execute(
            """
            UPDATE graph_edges SET payload_json = '{}'
            WHERE env = 'staging' AND version = ?
            """,
            (staged.namespace_version,),
        )

    with pytest.raises(DomainError) as captured:
        service.activate(reservation, staged)

    assert captured.value.code == "VERIFY_MISMATCH"
    assert service.pointer("staging").active_version == "v1"


def test_expected_prior_conflict_is_refused(publisher) -> None:
    service, _, _ = publisher

    with pytest.raises(DomainError) as captured:
        service.reserve("staging", "v0")

    assert captured.value.code == "POINTER_CONFLICT"


def test_rollback_is_a_new_audited_pointer_event(publisher) -> None:
    service, database, store = publisher
    service.publish(_proposal(), _approval(store), env="staging")

    pointer = service.rollback(
        env="staging",
        target_version="v1",
        actor="operator@example.test",
        correlation_id="corr-rollback",
    )

    assert pointer.active_version == "v1"
    assert pointer.fencing_token == 2
    with database.connection() as connection:
        audit = connection.execute(
            "SELECT action FROM audit_events WHERE correlation_id = 'corr-rollback'"
        ).fetchone()
    assert audit["action"] == "PROJECTION_ROLLBACK"
