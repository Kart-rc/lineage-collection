from __future__ import annotations

from pathlib import Path

import pytest

from lineage_api.db import Database
from lineage_api.domain.errors import DomainError
from lineage_api.services.evidence_store import EvidenceStore


EDGE = {
    "schemaVersion": "1.0.0",
    "edgeKey": "edge-demo",
    "version": 2,
    "from": ["urn:ldp:staging:snowflake:payments:raw.transactions#amount"],
    "to": "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue",
    "edgeType": "DERIVES",
    "band": "HIGH",
    "corroboration": "ELEMENT",
    "status": "PROPOSED",
    "transform": "SUM(amount)",
    "provenance": [
        {
            "provenanceId": "prov-sca",
            "mechanism": "SCA",
            "evidenceRef": {"kind": "sca", "key": "demo", "checksum": "a" * 64},
        }
    ],
    "autoPublishable": True,
    "system": "payments",
    "updatedAt": "2026-08-04T16:00:00Z"
}


def _review_type():
    try:
        from lineage_api.services.review import ReviewService
    except ModuleNotFoundError:
        pytest.fail("Review service is not implemented")
    return ReviewService


@pytest.fixture
def review(tmp_path: Path):
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
        clock=lambda: "2026-08-04T17:00:00Z",
    )
    service = _review_type()(
        database,
        store,
        env="staging",
        clock=lambda: "2026-08-04T17:00:00Z",
    )
    return service, database, store


def test_proposal_creation_is_deterministic_and_exposes_diff(review) -> None:
    service, _, _ = review

    first = service.create([EDGE], expected_base_version="v1", correlation_id="corr-001")
    replay = service.create([EDGE], expected_base_version="v1", correlation_id="corr-001")

    assert first == replay
    assert first.state == "IN_REVIEW"
    assert first.version == 1
    assert first.lock_version == 1
    assert first.diff == {"added": [EDGE], "removed": [], "bandChanged": []}
    assert first.auto_publish_override == "MANUAL_REVIEW_FOR_M1_DEMO"
    assert service.proposal_count(first.proposal_id) == 1


def test_approval_writes_immutable_record_and_audit_event(review) -> None:
    service, database, store = review
    proposal = service.create([EDGE], "v1", "corr-002")

    result = service.approve(
        proposal.proposal_id,
        version=1,
        actor="reviewer@example.test",
        rationale="Evidence and transform verified",
        expected_lock_version=1,
    )

    assert result.proposal.state == "APPROVED"
    assert result.proposal.lock_version == 2
    assert result.approval.actor == "reviewer@example.test"
    assert result.approval.rationale == "Evidence and transform verified"
    assert result.approval.proposal_id == proposal.proposal_id
    assert store.get(result.approval.reference)["decision"] == "APPROVED"
    with database.connection() as connection:
        audit = connection.execute("SELECT * FROM audit_events").fetchone()
    assert audit["action"] == "PROPOSAL_APPROVED"
    assert audit["actor"] == "reviewer@example.test"


def test_concurrent_decision_loser_receives_typed_conflict(review) -> None:
    service, _, _ = review
    proposal = service.create([EDGE], "v1", "corr-003")
    service.approve(proposal.proposal_id, 1, "reviewer-a", "looks good", 1)

    with pytest.raises(DomainError) as captured:
        service.reject(proposal.proposal_id, 1, "reviewer-b", "disagree", 1)

    assert captured.value.code == "CONCURRENT_DECISION"


def test_correction_creates_linked_successor_and_preserves_original(review) -> None:
    service, _, _ = review
    proposal = service.create([EDGE], "v1", "corr-004")
    corrected = dict(EDGE)
    corrected["version"] = 3
    corrected["transform"] = "SUM(COALESCE(amount, 0))"

    successor = service.correct(
        proposal.proposal_id,
        version=1,
        corrected_edges=[corrected],
        actor="reviewer@example.test",
        rationale="Account for null amounts",
        expected_lock_version=1,
    )

    original = service.get(proposal.proposal_id, 1)
    assert original.state == "SUPERSEDED"
    assert original.superseded_by == f"{proposal.proposal_id}:v2"
    assert successor.version == 2
    assert successor.state == "IN_REVIEW"
    assert successor.supersedes == f"{proposal.proposal_id}:v1"
    assert successor.diff["added"][0]["transform"] == "SUM(COALESCE(amount, 0))"
    assert service.proposal_count(proposal.proposal_id) == 2


def test_stale_base_version_is_refused(review) -> None:
    service, _, _ = review

    with pytest.raises(DomainError) as captured:
        service.create([EDGE], expected_base_version="v0", correlation_id="corr-005")

    assert captured.value.code == "STALE_BASE_VERSION"
