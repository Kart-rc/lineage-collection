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
        edge_batch_size: int = 100,
        staging_failure_policy: str = "RETAIN",
    ) -> None:
        if edge_batch_size < 1:
            raise ValueError("edge_batch_size must be at least one")
        if staging_failure_policy not in {"RETAIN", "DISCARD"}:
            raise ValueError("staging_failure_policy must be RETAIN or DISCARD")
        self._database = database
        self._store = store
        self._clock = clock
        self._edge_batch_size = edge_batch_size
        self._staging_failure_policy = staging_failure_policy
        self._fault_injector: Callable[[str], None] | None = None

    def set_fault_injector(self, injector: Callable[[str], None] | None) -> None:
        """Install a test-only hook called after each durable publication boundary."""
        self._fault_injector = injector

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
        edges = self._projected_edges(env, proposal)
        operation_id = self._operation_id(env, proposal, approval)
        operation = self._ensure_operation(operation_id, env, proposal, approval, edges)
        if operation["stage"] == "COMPLETED":
            return self._result_from_operation(operation)
        if operation["stage"].startswith("FAILED_VERIFY_"):
            raise DomainError(
                "VERIFY_MISMATCH",
                "Staged projection does not match its manifest",
                proposal.correlation_id,
                {"version": operation["namespace_version"]},
            )

        if operation["stage"] == "CREATED":
            self._reserve_operation(operation_id)
            self._after_boundary("RESERVATION_ACQUIRED")
            operation = self._load_operation(operation_id)
        if operation["stage"] == "RESERVED":
            self._write_operation_manifest(operation, proposal, approval, edges)
            self._after_boundary("MANIFEST_WRITTEN")
            operation = self._load_operation(operation_id)
        if operation["stage"] == "MANIFEST_WRITTEN":
            self._create_operation_namespace(operation)
            self._after_boundary("NAMESPACE_CREATED")
            operation = self._load_operation(operation_id)
        if operation["stage"] in {"NAMESPACE_CREATED", "EDGES_WRITING"}:
            self._write_operation_edges(operation_id, edges)
            operation = self._load_operation(operation_id)
        if operation["stage"] == "EDGES_WRITTEN":
            self._verify_operation(operation)
            self._after_boundary("VERIFIED")
            operation = self._load_operation(operation_id)
        if operation["stage"] == "VERIFIED":
            self._activate_operation(operation, approval)
            self._after_boundary("POINTER_ACTIVATED")
            operation = self._load_operation(operation_id)
        if operation["stage"] == "ACTIVATED":
            self._deliver_operation_outbox(operation)
            self._after_boundary("OUTBOX_DELIVERED")
            operation = self._load_operation(operation_id)
        if operation["stage"] == "DELIVERY_CONFIRMED":
            with self._database.transaction() as connection:
                connection.execute(
                    """
                    UPDATE publication_operations
                    SET stage = 'COMPLETED', terminal_outcome = 'SUCCEEDED',
                        terminal_at = ?, updated_at = ?
                    WHERE operation_id = ? AND stage = 'DELIVERY_CONFIRMED'
                    """,
                    (self._clock(), self._clock(), operation_id),
                )
            operation = self._load_operation(operation_id)
        return self._result_from_operation(operation)

    def _ensure_operation(
        self,
        operation_id: str,
        env: str,
        proposal: Proposal,
        approval: ApprovalRecord,
        edges: list[dict[str, Any]],
    ):
        now = self._clock()
        proposal_digest = _checksum(proposal.as_dict())
        edge_checksum = _checksum(edges)
        approval_ref = _canonical(approval.reference.as_dict())
        package_digest = _checksum(
            {
                "proposalDigest": proposal_digest,
                "approvalChecksum": approval.reference.checksum,
                "edgeChecksum": edge_checksum,
            }
        )
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO publication_operations(
                    operation_id, proposal_id, proposal_version, proposal_digest,
                    approval_id, approval_ref, env, expected_prior, package_digest,
                    stage, edge_checksum, edge_count, next_edge_index, failure_policy,
                    correlation_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'CREATED', ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    proposal.proposal_id,
                    proposal.version,
                    proposal_digest,
                    approval.approval_id,
                    approval_ref,
                    env,
                    proposal.expected_base_version,
                    package_digest,
                    edge_checksum,
                    len(edges),
                    self._staging_failure_policy,
                    proposal.correlation_id,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM publication_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("publication operation was not persisted")
        if (
            row["proposal_digest"] != proposal_digest
            or row["approval_ref"] != approval_ref
            or row["edge_checksum"] != edge_checksum
            or int(row["edge_count"]) != len(edges)
        ):
            raise DomainError(
                "PUBLICATION_IDEMPOTENCY_CONFLICT",
                "Publication identity was reused with different approved content",
                proposal.correlation_id,
                {"operationId": operation_id},
            )
        return row

    def _reserve_operation(self, operation_id: str) -> None:
        now = self._clock()
        with self._database.transaction() as connection:
            operation = connection.execute(
                "SELECT * FROM publication_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if operation is None or operation["stage"] != "CREATED":
                return
            pointer = connection.execute(
                "SELECT * FROM pointers WHERE env = ?", (operation["env"],)
            ).fetchone()
            if pointer is None or pointer["active_version"] != operation["expected_prior"]:
                raise DomainError(
                    "POINTER_CONFLICT",
                    "Active projection differs from the proposal base",
                    operation["correlation_id"],
                    {
                        "expected": operation["expected_prior"],
                        "actual": None if pointer is None else pointer["active_version"],
                    },
                )
            prior = connection.execute(
                "SELECT token FROM publish_reservations WHERE env = ?", (operation["env"],)
            ).fetchone()
            last_token = int(pointer["fencing_token"])
            if prior is not None:
                last_token = max(last_token, int(prior["token"]))
            token = last_token + 1
            namespace_version = f"v{token + 1}"
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
                (operation["env"], token, operation["expected_prior"], now),
            )
            connection.execute(
                """
                UPDATE publication_operations
                SET token = ?, namespace_version = ?, stage = 'RESERVED', updated_at = ?
                WHERE operation_id = ?
                """,
                (token, namespace_version, now, operation_id),
            )

    def _write_operation_manifest(
        self,
        operation,
        proposal: Proposal,
        approval: ApprovalRecord,
        edges: list[dict[str, Any]],
    ) -> None:
        version = operation["namespace_version"]
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
            "packageDigest": operation["package_digest"],
            "operationId": operation["operation_id"],
            "createdAt": operation["created_at"],
        }
        manifest_ref = self._store.put(
            "manifest", f"{operation['env']}/{version}", manifest_body, "1.0.0"
        )
        with self._database.transaction() as connection:
            connection.execute(
                """
                UPDATE publication_operations
                SET manifest_ref = ?, stage = 'MANIFEST_WRITTEN', updated_at = ?
                WHERE operation_id = ? AND stage = 'RESERVED'
                """,
                (
                    self._reference_text(manifest_ref),
                    self._clock(),
                    operation["operation_id"],
                ),
            )

    def _create_operation_namespace(self, operation) -> None:
        with self._database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT manifest_ref, checksum FROM graph_versions
                WHERE env = ? AND version = ?
                """,
                (operation["env"], operation["namespace_version"]),
            ).fetchone()
            if existing is not None and (
                existing["manifest_ref"] != operation["manifest_ref"]
                or existing["checksum"] != operation["edge_checksum"]
            ):
                raise DomainError(
                    "NAMESPACE_CONFLICT",
                    "Publication namespace already contains different content",
                    operation["correlation_id"],
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO graph_versions(
                    env, version, state, manifest_ref, checksum, created_at
                ) VALUES (?, ?, 'STAGING', ?, ?, ?)
                """,
                (
                    operation["env"],
                    operation["namespace_version"],
                    operation["manifest_ref"],
                    operation["edge_checksum"],
                    self._clock(),
                ),
            )
            next_stage = "EDGES_WRITTEN" if int(operation["edge_count"]) == 0 else "NAMESPACE_CREATED"
            connection.execute(
                """
                UPDATE publication_operations SET stage = ?, updated_at = ?
                WHERE operation_id = ? AND stage = 'MANIFEST_WRITTEN'
                """,
                (next_stage, self._clock(), operation["operation_id"]),
            )

    def _write_operation_edges(
        self,
        operation_id: str,
        edges: list[dict[str, Any]],
    ) -> None:
        ordered_edges = sorted(edges, key=lambda edge: edge["edgeKey"])
        while True:
            operation = self._load_operation(operation_id)
            start = int(operation["next_edge_index"])
            if start >= len(ordered_edges):
                with self._database.transaction() as connection:
                    connection.execute(
                        """
                        UPDATE publication_operations SET stage = 'EDGES_WRITTEN', updated_at = ?
                        WHERE operation_id = ? AND stage IN ('NAMESPACE_CREATED', 'EDGES_WRITING')
                        """,
                        (self._clock(), operation_id),
                    )
                return
            end = min(start + self._edge_batch_size, len(ordered_edges))
            batch = ordered_edges[start:end]
            with self._database.transaction() as connection:
                for edge in batch:
                    payload = _canonical(edge)
                    existing = connection.execute(
                        """
                        SELECT payload_json FROM graph_edges
                        WHERE env = ? AND version = ? AND edge_key = ?
                        """,
                        (operation["env"], operation["namespace_version"], edge["edgeKey"]),
                    ).fetchone()
                    if existing is not None and existing["payload_json"] != payload:
                        raise DomainError(
                            "NAMESPACE_CONFLICT",
                            "Staged edge differs from the approved package",
                            operation["correlation_id"],
                            {"edgeKey": edge["edgeKey"]},
                        )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO graph_edges(env, version, edge_key, payload_json)
                        VALUES (?, ?, ?, ?)
                        """,
                        (operation["env"], operation["namespace_version"], edge["edgeKey"], payload),
                    )
                next_stage = "EDGES_WRITTEN" if end == len(ordered_edges) else "EDGES_WRITING"
                connection.execute(
                    """
                    UPDATE publication_operations
                    SET next_edge_index = ?, stage = ?, updated_at = ?
                    WHERE operation_id = ?
                    """,
                    (end, next_stage, self._clock(), operation_id),
                )
            batch_number = start // self._edge_batch_size + 1
            self._after_boundary(f"EDGE_BATCH_WRITTEN:{batch_number}")
            if end == len(ordered_edges):
                return

    def _verify_operation(self, operation) -> None:
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM graph_edges
                WHERE env = ? AND version = ? ORDER BY edge_key
                """,
                (operation["env"], operation["namespace_version"]),
            ).fetchall()
        try:
            edges = [json.loads(row["payload_json"]) for row in rows]
            valid_keys = all(edge.get("edgeKey") for edge in edges)
            actual_checksum = _checksum(edges)
        except (json.JSONDecodeError, AttributeError):
            edges, valid_keys, actual_checksum = [], False, ""
        if (
            not valid_keys
            or len(edges) != int(operation["edge_count"])
            or actual_checksum != operation["edge_checksum"]
        ):
            self._mark_verification_failure(operation)
            raise DomainError(
                "VERIFY_MISMATCH",
                "Staged projection does not match its manifest",
                operation["correlation_id"],
                {"version": operation["namespace_version"]},
            )
        with self._database.transaction() as connection:
            connection.execute(
                """
                UPDATE publication_operations SET stage = 'VERIFIED', updated_at = ?
                WHERE operation_id = ? AND stage = 'EDGES_WRITTEN'
                """,
                (self._clock(), operation["operation_id"]),
            )

    def _mark_verification_failure(self, operation) -> None:
        discarded = operation["failure_policy"] == "DISCARD"
        stage = "FAILED_VERIFY_DISCARDED" if discarded else "FAILED_VERIFY_RETAINED"
        with self._database.transaction() as connection:
            if discarded:
                connection.execute(
                    """
                    UPDATE graph_versions SET state = 'DISCARDED'
                    WHERE env = ? AND version = ? AND state = 'STAGING'
                    """,
                    (operation["env"], operation["namespace_version"]),
                )
            connection.execute(
                """
                UPDATE publication_operations
                SET stage = ?, terminal_outcome = 'VERIFY_FAILED',
                    terminal_at = ?, updated_at = ?
                WHERE operation_id = ?
                """,
                (stage, self._clock(), self._clock(), operation["operation_id"]),
            )

    def _activate_operation(self, operation, approval: ApprovalRecord) -> None:
        now = self._clock()
        token = int(operation["token"])
        result_payload = {
            "namespaceVersion": operation["namespace_version"],
            "pointer": {
                "env": operation["env"],
                "activeVersion": operation["namespace_version"],
                "fencingToken": token,
                "updatedAt": now,
            },
            "manifestRef": json.loads(operation["manifest_ref"]),
        }
        with self._database.transaction() as connection:
            current_operation = connection.execute(
                "SELECT stage FROM publication_operations WHERE operation_id = ?",
                (operation["operation_id"],),
            ).fetchone()
            if current_operation is None:
                raise RuntimeError("publication operation is missing during activation")
            if current_operation["stage"] != "VERIFIED":
                return
            reservation = connection.execute(
                "SELECT * FROM publish_reservations WHERE env = ?", (operation["env"],)
            ).fetchone()
            if (
                reservation is None
                or int(reservation["token"]) != token
                or reservation["status"] != "RESERVED"
            ):
                raise DomainError(
                    "FENCE_LOST",
                    "A newer publisher owns the activation fence",
                    operation["correlation_id"],
                    {"token": token},
                )
            pointer = connection.execute(
                "SELECT * FROM pointers WHERE env = ?", (operation["env"],)
            ).fetchone()
            if pointer is None or pointer["active_version"] != operation["expected_prior"]:
                raise DomainError(
                    "POINTER_CONFLICT",
                    "Active pointer changed before activation",
                    operation["correlation_id"],
                )
            connection.execute(
                "UPDATE graph_versions SET state = 'PRIOR' WHERE env = ? AND state = 'ACTIVE'",
                (operation["env"],),
            )
            connection.execute(
                """
                UPDATE graph_versions SET state = 'ACTIVE'
                WHERE env = ? AND version = ? AND state = 'STAGING'
                """,
                (operation["env"], operation["namespace_version"]),
            )
            connection.execute(
                """
                UPDATE pointers SET active_version = ?, fencing_token = ?, updated_at = ?
                WHERE env = ?
                """,
                (operation["namespace_version"], token, now, operation["env"]),
            )
            connection.execute(
                "UPDATE publish_reservations SET status = 'ACTIVATED' WHERE env = ?",
                (operation["env"],),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO audit_events(
                    audit_id, actor, action, resource_type, resource_id,
                    correlation_id, payload_json, created_at
                ) VALUES (?, ?, 'PROJECTION_ACTIVATED', 'projection', ?, ?, ?, ?)
                """,
                (
                    f"audit-{operation['operation_id']}",
                    approval.actor,
                    f"{operation['env']}:{operation['namespace_version']}",
                    operation["correlation_id"],
                    _canonical(
                        {
                            "operationId": operation["operation_id"],
                            "proposalId": operation["proposal_id"],
                            "proposalVersion": int(operation["proposal_version"]),
                            "approvalId": operation["approval_id"],
                            "approvalRef": json.loads(operation["approval_ref"]),
                            "from": operation["expected_prior"],
                            "to": operation["namespace_version"],
                            "packageDigest": operation["package_digest"],
                            "graphChecksum": operation["edge_checksum"],
                            "fencingToken": token,
                        }
                    ),
                    now,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO outbox_events(
                    outbox_id, topic, partition_key, payload_ref, status, attempts,
                    available_at, correlation_id, created_at
                ) VALUES (?, 'PUBLICATION_ACTIVATED', ?, ?, 'PENDING', 0, ?, ?, ?)
                """,
                (
                    f"outbox-{operation['operation_id']}",
                    operation["env"],
                    f"publication://{operation['operation_id']}",
                    now,
                    operation["correlation_id"],
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE publication_operations
                SET stage = 'ACTIVATED', result_json = ?, updated_at = ?
                WHERE operation_id = ? AND stage = 'VERIFIED'
                """,
                (_canonical(result_payload), now, operation["operation_id"]),
            )

    def _deliver_operation_outbox(self, operation) -> None:
        now = self._clock()
        outbox_id = f"outbox-{operation['operation_id']}"
        with self._database.transaction() as connection:
            event = connection.execute(
                "SELECT status FROM outbox_events WHERE outbox_id = ?", (outbox_id,)
            ).fetchone()
            if event is None:
                raise RuntimeError("publication activation outbox event is missing")
            if event["status"] != "DELIVERED":
                connection.execute(
                    """
                    UPDATE outbox_events
                    SET status = 'DELIVERED', attempts = attempts + 1, delivered_at = ?
                    WHERE outbox_id = ? AND status != 'DELIVERED'
                    """,
                    (now, outbox_id),
                )
            connection.execute(
                """
                UPDATE publication_operations SET stage = 'DELIVERY_CONFIRMED', updated_at = ?
                WHERE operation_id = ? AND stage = 'ACTIVATED'
                """,
                (now, operation["operation_id"]),
            )

    def _load_operation(self, operation_id: str):
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM publication_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("publication operation is missing")
        return row

    @staticmethod
    def _operation_id(env: str, proposal: Proposal, approval: ApprovalRecord) -> str:
        digest = _checksum(
            {
                "env": env,
                "proposalId": proposal.proposal_id,
                "proposalVersion": proposal.version,
                "approvalId": approval.approval_id,
            }
        )
        return f"publication-{digest[:24]}"

    @staticmethod
    def _result_from_operation(operation) -> PublishResult:
        if operation["stage"] != "COMPLETED" or not operation["result_json"]:
            raise RuntimeError(f"publication stopped at unexpected stage {operation['stage']}")
        payload = json.loads(operation["result_json"])
        pointer_payload = payload["pointer"]
        reference_payload = payload["manifestRef"]
        return PublishResult(
            namespace_version=payload["namespaceVersion"],
            pointer=Pointer(
                env=pointer_payload["env"],
                active_version=pointer_payload["activeVersion"],
                fencing_token=int(pointer_payload["fencingToken"]),
                updated_at=pointer_payload["updatedAt"],
            ),
            manifest_ref=EvidenceRef(
                kind=reference_payload["kind"],
                key=reference_payload["key"],
                checksum=reference_payload["checksum"],
                schema_version=reference_payload["schemaVersion"],
            ),
        )

    def _after_boundary(self, boundary: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(boundary)

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
