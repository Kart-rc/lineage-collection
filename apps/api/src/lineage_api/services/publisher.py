from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from lineage_api.db import Database
from lineage_api.domain.errors import DomainError
from lineage_api.domain.evidence import EvidenceRef
from lineage_api.domain.proposals import ApprovalRecord, Proposal
from lineage_api.services.evidence_store import EvidenceStore


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Pointer:
    env: str
    active_version: str
    fencing_token: int
    updated_at: str


@dataclass(frozen=True, slots=True)
class PublishReservation:
    env: str
    token: int
    expected_prior: str
    reserved_at: str


@dataclass(frozen=True, slots=True)
class StagedNamespace:
    env: str
    namespace_version: str
    token: int
    expected_prior: str
    manifest_ref: str
    edge_checksum: str
    edge_count: int


@dataclass(frozen=True, slots=True)
class PublishResult:
    namespace_version: str
    pointer: Pointer
    manifest_ref: EvidenceRef


class PublisherService:
    def __init__(
        self,
        database: Database,
        store: EvidenceStore,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self._database = database
        self._store = store
        self._clock = clock

    def pointer(self, env: str) -> Pointer:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM pointers WHERE env = ?", (env,)
            ).fetchone()
        if row is None:
            raise DomainError("POINTER_NOT_FOUND", f"No active pointer for {env}", "system-publisher")
        return Pointer(env, row["active_version"], int(row["fencing_token"]), row["updated_at"])

    def reserve(self, env: str, expected_prior: str) -> PublishReservation:
        now = self._clock()
        with self._database.transaction() as connection:
            pointer = connection.execute(
                "SELECT * FROM pointers WHERE env = ?", (env,)
            ).fetchone()
            if pointer is None or pointer["active_version"] != expected_prior:
                actual = None if pointer is None else pointer["active_version"]
                raise DomainError(
                    "POINTER_CONFLICT",
                    "Active projection differs from the proposal base",
                    "system-publisher",
                    {"expected": expected_prior, "actual": actual},
                )
            prior_reservation = connection.execute(
                "SELECT token FROM publish_reservations WHERE env = ?", (env,)
            ).fetchone()
            last_token = int(pointer["fencing_token"])
            if prior_reservation is not None:
                last_token = max(last_token, int(prior_reservation["token"]))
            token = last_token + 1
            connection.execute(
                """
                INSERT INTO publish_reservations(env, token, expected_prior, status, reserved_at)
                VALUES (?, ?, ?, 'RESERVED', ?)
                ON CONFLICT(env) DO UPDATE SET
                    token = excluded.token,
                    expected_prior = excluded.expected_prior,
                    status = excluded.status,
                    reserved_at = excluded.reserved_at
                """,
                (env, token, expected_prior, now),
            )
        return PublishReservation(env, token, expected_prior, now)

    def stage(
        self,
        reservation: PublishReservation,
        edges: list[dict[str, Any]],
        manifest_ref: str | EvidenceRef,
    ) -> StagedNamespace:
        version = f"v{reservation.token + 1}"
        ordered_edges = sorted(edges, key=lambda edge: edge["edgeKey"])
        edge_checksum = _checksum(ordered_edges)
        reference_text = self._reference_text(manifest_ref)
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO graph_versions(env, version, state, manifest_ref, checksum, created_at)
                VALUES (?, ?, 'STAGING', ?, ?, ?)
                """,
                (reservation.env, version, reference_text, edge_checksum, self._clock()),
            )
            for edge in ordered_edges:
                connection.execute(
                    """
                    INSERT INTO graph_edges(env, version, edge_key, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (reservation.env, version, edge["edgeKey"], _canonical(edge)),
                )
        return StagedNamespace(
            reservation.env,
            version,
            reservation.token,
            reservation.expected_prior,
            reference_text,
            edge_checksum,
            len(ordered_edges),
        )

    def activate(
        self,
        reservation: PublishReservation,
        staged: StagedNamespace,
    ) -> Pointer:
        now = self._clock()
        with self._database.transaction() as connection:
            current_reservation = connection.execute(
                "SELECT * FROM publish_reservations WHERE env = ?", (reservation.env,)
            ).fetchone()
            if (
                current_reservation is None
                or int(current_reservation["token"]) != reservation.token
                or current_reservation["status"] != "RESERVED"
            ):
                raise DomainError(
                    "FENCE_LOST",
                    "A newer publisher owns the activation fence",
                    "system-publisher",
                    {"token": reservation.token},
                )
            pointer = connection.execute(
                "SELECT * FROM pointers WHERE env = ?", (reservation.env,)
            ).fetchone()
            if pointer is None or pointer["active_version"] != reservation.expected_prior:
                raise DomainError(
                    "POINTER_CONFLICT",
                    "Active pointer changed before activation",
                    "system-publisher",
                )
            rows = connection.execute(
                """
                SELECT payload_json FROM graph_edges
                WHERE env = ? AND version = ? ORDER BY edge_key
                """,
                (staged.env, staged.namespace_version),
            ).fetchall()
            try:
                edges = [json.loads(row["payload_json"]) for row in rows]
                actual_checksum = _checksum(edges)
                valid_keys = all(edge.get("edgeKey") for edge in edges)
            except (json.JSONDecodeError, AttributeError):
                actual_checksum, valid_keys = "", False
            if (
                not valid_keys
                or len(edges) != staged.edge_count
                or actual_checksum != staged.edge_checksum
            ):
                raise DomainError(
                    "VERIFY_MISMATCH",
                    "Staged projection does not match its manifest",
                    "system-publisher",
                    {"version": staged.namespace_version},
                )
            connection.execute(
                "UPDATE graph_versions SET state = 'PRIOR' WHERE env = ? AND state = 'ACTIVE'",
                (staged.env,),
            )
            connection.execute(
                "UPDATE graph_versions SET state = 'ACTIVE' WHERE env = ? AND version = ?",
                (staged.env, staged.namespace_version),
            )
            connection.execute(
                """
                UPDATE pointers SET active_version = ?, fencing_token = ?, updated_at = ?
                WHERE env = ?
                """,
                (staged.namespace_version, reservation.token, now, staged.env),
            )
            connection.execute(
                "UPDATE publish_reservations SET status = 'ACTIVATED' WHERE env = ?",
                (staged.env,),
            )
        return Pointer(staged.env, staged.namespace_version, reservation.token, now)

    def publish(self, proposal: Proposal, approval: ApprovalRecord, env: str) -> PublishResult:
        self._assert_approved(proposal, approval)
        reservation = self.reserve(env, proposal.expected_base_version)
        version = f"v{reservation.token + 1}"
        edges = self._projected_edges(env, proposal)
        manifest_body = {
            "schemaVersion": "1.0.0",
            "manifestId": f"manifest-{proposal.proposal_id}-v{proposal.version}",
            "namespaceVersion": version,
            "expectedPriorVersion": proposal.expected_base_version,
            "proposalId": proposal.proposal_id,
            "proposalVersion": proposal.version,
            "approvalId": approval.approval_id,
            "approvalRef": approval.reference.as_dict(),
            "edgeCount": len(edges),
            "edgeChecksum": _checksum(edges),
            "createdAt": self._clock(),
        }
        manifest_ref = self._store.put(
            "manifest", f"{env}/{version}", manifest_body, "1.0.0"
        )
        staged = self.stage(reservation, edges, manifest_ref)
        pointer = self.activate(reservation, staged)
        return PublishResult(version, pointer, manifest_ref)

    def promote_existing(
        self,
        *,
        env: str,
        target_version: str,
        expected_prior: str,
        expected_checksum: str,
        actor: str,
        correlation_id: str,
        action: str = "DEPLOYMENT_PROMOTE",
    ) -> Pointer:
        """Promote a prebuilt exact package using the same monotonic publication fence."""
        if action not in {"DEPLOYMENT_PROMOTE", "DEPLOYMENT_ROLLBACK"}:
            raise ValueError(f"unsupported deployment publication action: {action}")
        reservation = self.reserve(env, expected_prior)
        now = self._clock()
        with self._database.transaction() as connection:
            current_reservation = connection.execute(
                "SELECT * FROM publish_reservations WHERE env = ?", (env,)
            ).fetchone()
            if (
                current_reservation is None
                or int(current_reservation["token"]) != reservation.token
                or current_reservation["status"] != "RESERVED"
            ):
                raise DomainError(
                    "FENCE_LOST",
                    "A newer deployment promotion owns the activation fence",
                    correlation_id,
                    {"token": reservation.token},
                )
            pointer = connection.execute(
                "SELECT * FROM pointers WHERE env = ?", (env,)
            ).fetchone()
            if pointer is None or pointer["active_version"] != expected_prior:
                raise DomainError(
                    "POINTER_CONFLICT",
                    "Active pointer changed before deployment promotion",
                    correlation_id,
                    {
                        "expected": expected_prior,
                        "actual": None if pointer is None else pointer["active_version"],
                    },
                )
            target = connection.execute(
                """
                SELECT checksum FROM graph_versions
                WHERE env = ? AND version = ?
                """,
                (env, target_version),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT payload_json FROM graph_edges
                WHERE env = ? AND version = ? ORDER BY edge_key
                """,
                (env, target_version),
            ).fetchall()
            try:
                edges = [json.loads(row["payload_json"]) for row in rows]
                actual_checksum = _checksum(edges)
                valid_keys = all(edge.get("edgeKey") for edge in edges)
            except (json.JSONDecodeError, AttributeError):
                actual_checksum, valid_keys = "", False
            if (
                target is None
                or not valid_keys
                or target["checksum"] != expected_checksum
                or actual_checksum != expected_checksum
            ):
                raise DomainError(
                    "VERIFY_MISMATCH",
                    "Exact deployment package does not match the stored graph namespace",
                    correlation_id,
                    {"targetVersion": target_version},
                )

            connection.execute(
                "UPDATE graph_versions SET state = 'PRIOR' WHERE env = ? AND state = 'ACTIVE'",
                (env,),
            )
            connection.execute(
                "UPDATE graph_versions SET state = 'ACTIVE' WHERE env = ? AND version = ?",
                (env, target_version),
            )
            connection.execute(
                """
                UPDATE pointers SET active_version = ?, fencing_token = ?, updated_at = ?
                WHERE env = ?
                """,
                (target_version, reservation.token, now, env),
            )
            connection.execute(
                "UPDATE publish_reservations SET status = 'ACTIVATED' WHERE env = ?",
                (env,),
            )
            connection.execute(
                """
                INSERT INTO audit_events(
                    audit_id, actor, action, resource_type, resource_id,
                    correlation_id, payload_json, created_at
                ) VALUES (?, ?, ?, 'projection', ?, ?, ?, ?)
                """,
                (
                    f"audit-{uuid.uuid4().hex}",
                    actor,
                    action,
                    f"{env}:{target_version}",
                    correlation_id,
                    _canonical(
                        {
                            "from": expected_prior,
                            "to": target_version,
                            "graphChecksum": expected_checksum,
                            "fencingToken": reservation.token,
                        }
                    ),
                    now,
                ),
            )
        return Pointer(env, target_version, reservation.token, now)

    def rollback(
        self,
        env: str,
        target_version: str,
        actor: str,
        correlation_id: str,
    ) -> Pointer:
        now = self._clock()
        with self._database.transaction() as connection:
            pointer = connection.execute(
                "SELECT * FROM pointers WHERE env = ?", (env,)
            ).fetchone()
            target = connection.execute(
                "SELECT 1 FROM graph_versions WHERE env = ? AND version = ?",
                (env, target_version),
            ).fetchone()
            if pointer is None or target is None:
                raise DomainError(
                    "ROLLBACK_TARGET_NOT_FOUND",
                    "Rollback target is unavailable",
                    correlation_id,
                    {"targetVersion": target_version},
                )
            token = int(pointer["fencing_token"]) + 1
            connection.execute(
                "UPDATE graph_versions SET state = 'PRIOR' WHERE env = ? AND state = 'ACTIVE'",
                (env,),
            )
            connection.execute(
                "UPDATE graph_versions SET state = 'ACTIVE' WHERE env = ? AND version = ?",
                (env, target_version),
            )
            connection.execute(
                """
                UPDATE pointers SET active_version = ?, fencing_token = ?, updated_at = ?
                WHERE env = ?
                """,
                (target_version, token, now, env),
            )
            connection.execute(
                """
                INSERT INTO audit_events(
                    audit_id, actor, action, resource_type, resource_id,
                    correlation_id, payload_json, created_at
                ) VALUES (?, ?, 'PROJECTION_ROLLBACK', 'projection', ?, ?, ?, ?)
                """,
                (
                    f"audit-{uuid.uuid4().hex}",
                    actor,
                    f"{env}:{target_version}",
                    correlation_id,
                    _canonical({"from": pointer["active_version"], "to": target_version}),
                    now,
                ),
            )
        return Pointer(env, target_version, token, now)

    def _projected_edges(self, env: str, proposal: Proposal) -> list[dict[str, Any]]:
        current: dict[str, dict[str, Any]] = {}
        with self._database.connection() as connection:
            rows = connection.execute(
                "SELECT edge_key, payload_json FROM graph_edges WHERE env = ? AND version = ?",
                (env, proposal.expected_base_version),
            ).fetchall()
        for row in rows:
            current[row["edge_key"]] = json.loads(row["payload_json"])
        for removed in proposal.diff.get("removed", []):
            if isinstance(removed, str):
                current.pop(removed, None)
            else:
                current.pop(removed.get("edgeKey", ""), None)
        for edge in proposal.diff.get("added", []) + proposal.diff.get("bandChanged", []):
            published = {**edge, "status": "PUBLISHED"}
            current[published["edgeKey"]] = published
        return sorted(current.values(), key=lambda edge: edge["edgeKey"])

    @staticmethod
    def _assert_approved(proposal: Proposal, approval: ApprovalRecord) -> None:
        if (
            proposal.state != "APPROVED"
            or approval.decision != "APPROVED"
            or approval.proposal_id != proposal.proposal_id
            or approval.proposal_version != proposal.version
        ):
            raise DomainError(
                "APPROVAL_REQUIRED",
                "Publication requires a matching approved decision",
                proposal.correlation_id,
            )

    @staticmethod
    def _reference_text(reference: str | EvidenceRef) -> str:
        if isinstance(reference, str):
            return reference
        return _canonical(reference.as_dict())
