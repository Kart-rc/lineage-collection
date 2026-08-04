from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Callable

from lineage_api.db import Database
from lineage_api.domain.errors import DomainError
from lineage_api.domain.proposals import (
    ApprovalRecord,
    Proposal,
    ReviewDecisionResult,
    assert_transition,
)
from lineage_api.services.evidence_store import EvidenceStore


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ReviewService:
    def __init__(
        self,
        database: Database,
        evidence_store: EvidenceStore,
        env: str,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self._database = database
        self._store = evidence_store
        self._env = env
        self._clock = clock

    def create(
        self,
        edges: list[dict[str, Any]],
        expected_base_version: str,
        correlation_id: str,
    ) -> Proposal:
        if not edges:
            raise DomainError(
                "EMPTY_PROPOSAL",
                "A proposal requires at least one edge",
                correlation_id,
            )
        ordered_edges = sorted(edges, key=lambda edge: edge["edgeKey"])
        systems = {str(edge["system"]) for edge in ordered_edges}
        if len(systems) != 1:
            raise DomainError(
                "MIXED_PROPOSAL_OWNERSHIP",
                "A proposal must be routed to exactly one owning system",
                correlation_id,
            )
        system = next(iter(systems))
        identity = json.dumps(
            {
                "edgeKeys": [edge["edgeKey"] for edge in ordered_edges],
                "edgeVersions": [edge["version"] for edge in ordered_edges],
                "expectedBaseVersion": expected_base_version,
                "correlationId": correlation_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        proposal_id = f"proposal-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"

        with self._database.transaction() as connection:
            pointer = connection.execute(
                "SELECT active_version FROM pointers WHERE env = ?", (self._env,)
            ).fetchone()
            active_version = pointer["active_version"] if pointer else None
            if active_version != expected_base_version:
                raise DomainError(
                    "STALE_BASE_VERSION",
                    "Proposal base is not the active graph version",
                    correlation_id,
                    {"expected": expected_base_version, "active": active_version},
                )
            existing = connection.execute(
                "SELECT payload_json FROM proposals WHERE proposal_id = ? AND version = 1",
                (proposal_id,),
            ).fetchone()
            if existing is not None:
                return Proposal.from_dict(json.loads(existing["payload_json"]))

            now = self._clock()
            proposal = Proposal(
                proposal_id=proposal_id,
                version=1,
                system=system,
                state="IN_REVIEW",
                expected_base_version=expected_base_version,
                diff={"added": ordered_edges, "removed": [], "bandChanged": []},
                correlation_id=correlation_id,
                created_at=now,
                updated_at=now,
                lock_version=1,
                auto_publish_override="MANUAL_REVIEW_FOR_M1_DEMO",
            )
            connection.execute(
                """
                INSERT INTO proposals(
                    proposal_id, version, system, state, expected_base_version,
                    payload_json, lock_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.proposal_id,
                    proposal.version,
                    proposal.system,
                    proposal.state,
                    proposal.expected_base_version,
                    self._serialize(proposal),
                    proposal.lock_version,
                    proposal.created_at,
                    proposal.updated_at,
                ),
            )

        self._store.put(
            "proposal",
            f"{system}/{proposal_id}/v1",
            proposal.as_dict(),
            "1.0.0",
        )
        return proposal

    def get(self, proposal_id: str, version: int | None = None) -> Proposal:
        version_clause = "AND version = ?" if version is not None else "ORDER BY version DESC LIMIT 1"
        parameters: tuple[Any, ...] = (proposal_id, version) if version is not None else (proposal_id,)
        with self._database.connection() as connection:
            row = connection.execute(
                f"SELECT payload_json FROM proposals WHERE proposal_id = ? {version_clause}",
                parameters,
            ).fetchone()
        if row is None:
            raise DomainError(
                "PROPOSAL_NOT_FOUND",
                "Proposal does not exist",
                "unknown",
                {"proposalId": proposal_id, "version": version},
            )
        return Proposal.from_dict(json.loads(row["payload_json"]))

    def list_queue(self, state: str = "IN_REVIEW") -> list[Proposal]:
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM proposals
                WHERE state = ? ORDER BY created_at, proposal_id, version
                """,
                (state,),
            ).fetchall()
        return [Proposal.from_dict(json.loads(row["payload_json"])) for row in rows]

    def approve(
        self,
        proposal_id: str,
        version: int,
        actor: str,
        rationale: str,
        expected_lock_version: int,
    ) -> ReviewDecisionResult:
        return self._decide(
            proposal_id,
            version,
            actor,
            rationale,
            expected_lock_version,
            "APPROVED",
        )

    def reject(
        self,
        proposal_id: str,
        version: int,
        actor: str,
        rationale: str,
        expected_lock_version: int,
    ) -> ReviewDecisionResult:
        return self._decide(
            proposal_id,
            version,
            actor,
            rationale,
            expected_lock_version,
            "REJECTED",
        )

    def correct(
        self,
        proposal_id: str,
        version: int,
        corrected_edges: list[dict[str, Any]],
        actor: str,
        rationale: str,
        expected_lock_version: int,
    ) -> Proposal:
        with self._database.transaction() as connection:
            current = self._locked_proposal(
                connection, proposal_id, version, expected_lock_version
            )
            assert_transition(current.state, "SUPERSEDED", current.correlation_id)
            now = self._clock()
            successor_version = version + 1
            successor_ref = f"{proposal_id}:v{successor_version}"
            current_ref = f"{proposal_id}:v{version}"
            superseded = replace(
                current,
                state="SUPERSEDED",
                updated_at=now,
                lock_version=current.lock_version + 1,
                superseded_by=successor_ref,
                decision={
                    "actor": actor,
                    "rationale": rationale,
                    "decision": "CORRECTED",
                    "decidedAt": now,
                },
            )
            ordered_edges = sorted(corrected_edges, key=lambda edge: edge["edgeKey"])
            successor = Proposal(
                proposal_id=proposal_id,
                version=successor_version,
                system=current.system,
                state="IN_REVIEW",
                expected_base_version=current.expected_base_version,
                diff={"added": ordered_edges, "removed": [], "bandChanged": []},
                correlation_id=current.correlation_id,
                created_at=now,
                updated_at=now,
                lock_version=1,
                auto_publish_override=current.auto_publish_override,
                supersedes=current_ref,
            )
            connection.execute(
                """
                UPDATE proposals SET state = ?, payload_json = ?, lock_version = ?, updated_at = ?
                WHERE proposal_id = ? AND version = ? AND lock_version = ?
                """,
                (
                    superseded.state,
                    self._serialize(superseded),
                    superseded.lock_version,
                    now,
                    proposal_id,
                    version,
                    expected_lock_version,
                ),
            )
            connection.execute(
                """
                INSERT INTO proposals(
                    proposal_id, version, system, state, expected_base_version,
                    payload_json, lock_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    successor.proposal_id,
                    successor.version,
                    successor.system,
                    successor.state,
                    successor.expected_base_version,
                    self._serialize(successor),
                    successor.lock_version,
                    successor.created_at,
                    successor.updated_at,
                ),
            )
            self._insert_audit(
                connection,
                actor,
                "PROPOSAL_CORRECTED",
                proposal_id,
                current.correlation_id,
                {"fromVersion": version, "toVersion": successor_version, "rationale": rationale},
                now,
            )

        self._store.put(
            "proposal",
            f"{successor.system}/{proposal_id}/v{successor.version}",
            successor.as_dict(),
            "1.0.0",
        )
        return successor

    def finalize(self, proposal_id: str, version: int) -> Proposal:
        current = self.get(proposal_id, version)
        assert_transition(current.state, "FINALIZED", current.correlation_id)
        finalized = replace(
            current,
            state="FINALIZED",
            lock_version=current.lock_version + 1,
            updated_at=self._clock(),
        )
        with self._database.transaction() as connection:
            connection.execute(
                """
                UPDATE proposals SET state = ?, payload_json = ?, lock_version = ?, updated_at = ?
                WHERE proposal_id = ? AND version = ? AND lock_version = ?
                """,
                (
                    finalized.state,
                    self._serialize(finalized),
                    finalized.lock_version,
                    finalized.updated_at,
                    proposal_id,
                    version,
                    current.lock_version,
                ),
            )
        return finalized

    def proposal_count(self, proposal_id: str) -> int:
        with self._database.connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM proposals WHERE proposal_id = ?", (proposal_id,)
                ).fetchone()[0]
            )

    def _decide(
        self,
        proposal_id: str,
        version: int,
        actor: str,
        rationale: str,
        expected_lock_version: int,
        decision: str,
    ) -> ReviewDecisionResult:
        with self._database.transaction() as connection:
            current = self._locked_proposal(
                connection, proposal_id, version, expected_lock_version
            )
            assert_transition(current.state, decision, current.correlation_id)
            now = self._clock()
            approval_identity = f"{proposal_id}:{version}:{decision}:{actor}:{rationale}"
            approval_id = f"approval-{hashlib.sha256(approval_identity.encode()).hexdigest()[:20]}"
            decision_payload = {
                "approvalId": approval_id,
                "actor": actor,
                "rationale": rationale,
                "decision": decision,
                "decidedAt": now,
            }
            updated = replace(
                current,
                state=decision,
                updated_at=now,
                lock_version=current.lock_version + 1,
                decision=decision_payload,
            )
            changed = connection.execute(
                """
                UPDATE proposals SET state = ?, payload_json = ?, lock_version = ?, updated_at = ?
                WHERE proposal_id = ? AND version = ? AND lock_version = ?
                """,
                (
                    updated.state,
                    self._serialize(updated),
                    updated.lock_version,
                    now,
                    proposal_id,
                    version,
                    expected_lock_version,
                ),
            ).rowcount
            if changed != 1:
                raise DomainError(
                    "CONCURRENT_DECISION",
                    "Another reviewer changed the proposal",
                    current.correlation_id,
                )
            self._insert_audit(
                connection,
                actor,
                f"PROPOSAL_{decision}",
                proposal_id,
                current.correlation_id,
                {"version": version, "rationale": rationale},
                now,
            )

        record_payload = {
            "schemaVersion": "1.0.0",
            "approvalId": approval_id,
            "proposalId": proposal_id,
            "proposalVersion": version,
            "decision": decision,
            "actor": actor,
            "rationale": rationale,
            "correlationId": updated.correlation_id,
            "decidedAt": now,
        }
        reference = self._store.put(
            "approval",
            f"{updated.system}/{approval_id}",
            record_payload,
            "1.0.0",
        )
        approval = ApprovalRecord(
            approval_id=approval_id,
            proposal_id=proposal_id,
            proposal_version=version,
            decision=decision,
            actor=actor,
            rationale=rationale,
            correlation_id=updated.correlation_id,
            decided_at=now,
            reference=reference,
        )
        return ReviewDecisionResult(proposal=updated, approval=approval)

    def _locked_proposal(
        self,
        connection,
        proposal_id: str,
        version: int,
        expected_lock_version: int,
    ) -> Proposal:
        row = connection.execute(
            """
            SELECT payload_json, lock_version FROM proposals
            WHERE proposal_id = ? AND version = ?
            """,
            (proposal_id, version),
        ).fetchone()
        if row is None:
            raise DomainError(
                "PROPOSAL_NOT_FOUND",
                "Proposal does not exist",
                "unknown",
                {"proposalId": proposal_id, "version": version},
            )
        current = Proposal.from_dict(json.loads(row["payload_json"]))
        if int(row["lock_version"]) != expected_lock_version:
            raise DomainError(
                "CONCURRENT_DECISION",
                "Another reviewer changed the proposal",
                current.correlation_id,
                {"expectedLockVersion": expected_lock_version, "actual": row["lock_version"]},
            )
        return current

    @staticmethod
    def _insert_audit(
        connection,
        actor: str,
        action: str,
        resource_id: str,
        correlation_id: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        identity = f"{actor}:{action}:{resource_id}:{created_at}:{json.dumps(payload, sort_keys=True)}"
        audit_id = f"audit-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"
        connection.execute(
            """
            INSERT INTO audit_events(
                audit_id, actor, action, resource_type, resource_id,
                correlation_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                actor,
                action,
                "PROPOSAL",
                resource_id,
                correlation_id,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                created_at,
            ),
        )

    @staticmethod
    def _serialize(proposal: Proposal) -> str:
        return json.dumps(proposal.as_dict(), sort_keys=True, separators=(",", ":"))
