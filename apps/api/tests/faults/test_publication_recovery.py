from __future__ import annotations

import json
from pathlib import Path

import pytest

from lineage_api.db import Database
from lineage_api.domain.errors import DomainError
from lineage_api.domain.proposals import ApprovalRecord, Proposal
from lineage_api.services.evidence_store import EvidenceStore
from lineage_api.services.publisher import PublisherService


SOURCE = "urn:ldp:staging:snowflake:payments:raw.transactions#amount"
TARGET = "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue"


def _edge(edge_key: str, target: str) -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "edgeKey": edge_key,
        "version": 2,
        "from": [SOURCE],
        "to": target,
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


EDGES = [
    _edge("edge-demo-1", TARGET),
    _edge(
        "edge-demo-2",
        "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#net_revenue",
    ),
]


@pytest.fixture
def publication(tmp_path: Path):
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
    return database, store


def _proposal() -> Proposal:
    return Proposal(
        proposal_id="proposal-recovery",
        version=3,
        system="payments",
        state="APPROVED",
        expected_base_version="v1",
        diff={"added": EDGES, "removed": [], "bandChanged": []},
        correlation_id="corr-recovery",
        created_at="2026-08-04T17:00:00Z",
        updated_at="2026-08-04T17:00:00Z",
        lock_version=2,
    )


def _approval(store: EvidenceStore) -> ApprovalRecord:
    reference = store.put(
        "approval",
        "payments/approval-recovery",
        {"schemaVersion": "1.0.0", "decision": "APPROVED"},
        "1.0.0",
    )
    return ApprovalRecord(
        approval_id="approval-recovery",
        proposal_id="proposal-recovery",
        proposal_version=3,
        decision="APPROVED",
        actor="reviewer@example.test",
        rationale="verified",
        correlation_id="corr-recovery",
        decided_at="2026-08-04T17:00:00Z",
        reference=reference,
    )


@pytest.mark.parametrize(
    "crash_boundary",
    [
        "MANIFEST_WRITTEN",
        "RESERVATION_ACQUIRED",
        "NAMESPACE_CREATED",
        "EDGE_BATCH_WRITTEN:1",
        "EDGE_BATCH_WRITTEN:2",
        "VERIFIED",
        "POINTER_ACTIVATED",
        "OUTBOX_DELIVERED",
    ],
)
def test_redrive_resumes_every_publication_crash_boundary_exactly_once(
    publication,
    crash_boundary: str,
) -> None:
    database, store = publication
    service = PublisherService(
        database,
        store,
        clock=lambda: "2026-08-04T18:00:00Z",
        edge_batch_size=1,
    )
    crashed = False

    def fail_once(boundary: str) -> None:
        nonlocal crashed
        if boundary == crash_boundary and not crashed:
            crashed = True
            raise RuntimeError(f"simulated crash after {boundary}")

    service.set_fault_injector(fail_once)
    with pytest.raises(RuntimeError, match="simulated crash"):
        service.publish(_proposal(), _approval(store), env="staging")

    service.set_fault_injector(None)
    resumed = service.publish(_proposal(), _approval(store), env="staging")
    replayed = service.publish(_proposal(), _approval(store), env="staging")

    assert resumed == replayed
    assert resumed.namespace_version == "v2"
    assert resumed.pointer.active_version == "v2"
    manifest = store.get(resumed.manifest_ref)
    assert manifest["approvalId"] == "approval-recovery"
    assert manifest["approvalRef"] == _approval(store).reference.as_dict()

    with database.connection() as connection:
        active_versions = connection.execute(
            "SELECT version FROM graph_versions WHERE env = 'staging' AND state = 'ACTIVE'"
        ).fetchall()
        operations = connection.execute(
            """
            SELECT stage, terminal_outcome, approval_id, namespace_version
            FROM publication_operations
            """
        ).fetchall()
        audits = connection.execute(
            "SELECT payload_json FROM audit_events WHERE action = 'PROJECTION_ACTIVATED'"
        ).fetchall()
        outbox = connection.execute(
            "SELECT status, attempts FROM outbox_events WHERE topic = 'PUBLICATION_ACTIVATED'"
        ).fetchall()

    assert [row["version"] for row in active_versions] == ["v2"]
    assert [dict(row) for row in operations] == [
        {
            "stage": "COMPLETED",
            "terminal_outcome": "SUCCEEDED",
            "approval_id": "approval-recovery",
            "namespace_version": "v2",
        }
    ]
    assert len(audits) == 1
    assert json.loads(audits[0]["payload_json"])["approvalId"] == "approval-recovery"
    assert [dict(row) for row in outbox] == [{"status": "DELIVERED", "attempts": 1}]


@pytest.mark.parametrize(
    ("failure_policy", "expected_state", "expected_stage"),
    [
        ("RETAIN", "STAGING", "FAILED_VERIFY_RETAINED"),
        ("DISCARD", "DISCARDED", "FAILED_VERIFY_DISCARDED"),
    ],
)
def test_verification_failure_retains_or_discards_staging_by_explicit_policy(
    publication,
    failure_policy: str,
    expected_state: str,
    expected_stage: str,
) -> None:
    database, store = publication
    service = PublisherService(
        database,
        store,
        clock=lambda: "2026-08-04T18:00:00Z",
        edge_batch_size=1,
        staging_failure_policy=failure_policy,
    )

    def corrupt_last_batch(boundary: str) -> None:
        if boundary == "EDGE_BATCH_WRITTEN:2":
            with database.transaction() as connection:
                connection.execute(
                    """
                    UPDATE graph_edges SET payload_json = '{}'
                    WHERE env = 'staging' AND version = 'v2' AND edge_key = 'edge-demo-2'
                    """
                )

    service.set_fault_injector(corrupt_last_batch)
    with pytest.raises(DomainError) as captured:
        service.publish(_proposal(), _approval(store), env="staging")

    assert captured.value.code == "VERIFY_MISMATCH"
    assert service.pointer("staging").active_version == "v1"
    with database.connection() as connection:
        version = connection.execute(
            "SELECT state FROM graph_versions WHERE env = 'staging' AND version = 'v2'"
        ).fetchone()
        operation = connection.execute(
            "SELECT stage FROM publication_operations"
        ).fetchone()
    assert version["state"] == expected_state
    assert operation["stage"] == expected_stage
