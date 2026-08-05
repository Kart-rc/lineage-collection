from __future__ import annotations

import hmac
import json
import sqlite3
from typing import Callable

from lineage_api.application.workflows.deployment import (
    DeploymentClaim,
    DeploymentEvent,
    DeploymentPackage,
    DeploymentState,
)
from lineage_api.db import Database
from lineage_api.domain.errors import DomainError


class HmacDeploymentAuthenticator:
    def __init__(self, secret: str) -> None:
        self._secret = secret.encode()

    def __call__(self, payload: dict[str, object], signature: str) -> bool:
        import hashlib

        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        expected = "sha256=" + hmac.new(self._secret, canonical, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


class SQLiteDeploymentStore:
    def __init__(self, database: Database, clock: Callable[[], str]) -> None:
        self._database = database
        self._clock = clock

    def register_package(self, package: DeploymentPackage) -> DeploymentPackage:
        if not package.approved:
            raise DomainError(
                "PACKAGE_NOT_APPROVED",
                "Only approved immutable lineage packages may be registered",
                "package-registration",
            )
        now = self._clock()
        try:
            with self._database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO lineage_packages(
                        package_digest, system, environment, artifact_digest, graph_version,
                        graph_checksum, manifest_ref, approval_ref, approved, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        package.package_digest,
                        package.system,
                        package.environment,
                        package.artifact_digest,
                        package.graph_version,
                        package.graph_checksum,
                        package.manifest_ref,
                        package.approval_ref,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as error:
            existing = self.package_for(
                package.system,
                package.environment,
                package.artifact_digest,
            )
            if existing != package:
                raise DomainError(
                    "PACKAGE_IMMUTABILITY_CONFLICT",
                    "An artifact is already bound to a different lineage package",
                    "package-registration",
                    {"artifactDigest": package.artifact_digest},
                ) from error
        return package

    def package_for(
        self, system: str, environment: str, artifact_digest: str
    ) -> DeploymentPackage | None:
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM lineage_packages
                WHERE system = ? AND environment = ? AND artifact_digest = ?
                """,
                (system, environment, artifact_digest),
            ).fetchone()
        if row is None:
            return None
        return DeploymentPackage(
            package_digest=row["package_digest"],
            system=row["system"],
            environment=row["environment"],
            artifact_digest=row["artifact_digest"],
            graph_version=row["graph_version"],
            graph_checksum=row["graph_checksum"],
            manifest_ref=row["manifest_ref"],
            approval_ref=row["approval_ref"],
            approved=bool(row["approved"]),
        )

    def claim(self, event: DeploymentEvent) -> DeploymentClaim:
        now = self._clock()
        with self._database.transaction() as connection:
            existing = connection.execute(
                "SELECT status, result_json FROM deployment_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if existing is not None:
                if existing["result_json"] is not None:
                    result = json.loads(existing["result_json"])
                    result["duplicate"] = True
                    return DeploymentClaim("DUPLICATE", result)
                return DeploymentClaim("RESUME")

            current = connection.execute(
                "SELECT * FROM deployment_state WHERE system = ? AND environment = ?",
                (event.system, event.environment),
            ).fetchone()
            if current is not None and (
                int(current["provider_sequence"]), int(current["attempt"])
            ) >= (event.provider_sequence, event.attempt):
                result = self._result(
                    event,
                    state="FAILED_NO_CHANGE",
                    reason="STALE_EVENT",
                    duplicate=False,
                    deployed_artifact_digest=current["deployed_artifact_digest"],
                    lineage_package_digest=current["lineage_package_digest"],
                    graph_version=current["graph_version"],
                )
                try:
                    self._insert_event(connection, event, "STALE", result, now)
                except sqlite3.IntegrityError:
                    pass
                return DeploymentClaim("STALE", result)

            try:
                self._insert_event(connection, event, "PROCESSING", None, now)
            except sqlite3.IntegrityError:
                result = self._result(
                    event,
                    state="FAILED_NO_CHANGE",
                    reason="STALE_EVENT",
                    duplicate=False,
                    deployed_artifact_digest=None if current is None else current["deployed_artifact_digest"],
                    lineage_package_digest=None if current is None else current["lineage_package_digest"],
                    graph_version=None if current is None else current["graph_version"],
                )
                return DeploymentClaim("STALE", result)

            previous = (None, None, None) if current is None else (
                current["deployed_artifact_digest"],
                current["lineage_package_digest"],
                current["graph_version"],
            )
            connection.execute(
                """
                INSERT INTO deployment_state(
                    system, environment, provider, provider_sequence, attempt, event_id,
                    deployed_artifact_digest, lineage_package_digest, graph_version, state,
                    audit_ref, correlation_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PROCESSING', ?, ?, ?)
                ON CONFLICT(system, environment) DO UPDATE SET
                    provider = excluded.provider,
                    provider_sequence = excluded.provider_sequence,
                    attempt = excluded.attempt,
                    event_id = excluded.event_id,
                    state = excluded.state,
                    audit_ref = excluded.audit_ref,
                    correlation_id = excluded.correlation_id,
                    updated_at = excluded.updated_at
                """,
                (
                    event.system,
                    event.environment,
                    event.provider,
                    event.provider_sequence,
                    event.attempt,
                    event.event_id,
                    *previous,
                    event.audit_ref,
                    event.correlation_id,
                    now,
                ),
            )
        return DeploymentClaim("CLAIMED")

    def complete(
        self,
        event: DeploymentEvent,
        *,
        state: str,
        reason: str | None,
        package: DeploymentPackage | None = None,
        graph_version: str | None = None,
    ) -> dict[str, object]:
        now = self._clock()
        with self._database.transaction() as connection:
            completed_event = connection.execute(
                "SELECT result_json FROM deployment_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if completed_event is not None and completed_event["result_json"] is not None:
                completed = json.loads(completed_event["result_json"])
                completed["duplicate"] = True
                return completed
            current = connection.execute(
                "SELECT * FROM deployment_state WHERE system = ? AND environment = ?",
                (event.system, event.environment),
            ).fetchone()
            authoritative = current is not None and current["event_id"] == event.event_id
            if not authoritative:
                state, reason = "FAILED_NO_CHANGE", "SUPERSEDED"
            elif state in {"PROMOTED", "ROLLED_BACK"}:
                connection.execute(
                    """
                    UPDATE deployment_state SET
                        deployed_artifact_digest = ?, lineage_package_digest = ?,
                        graph_version = ?, state = ?, updated_at = ?
                    WHERE system = ? AND environment = ? AND event_id = ?
                    """,
                    (
                        event.artifact_digest,
                        package.package_digest if package is not None else None,
                        graph_version,
                        state,
                        now,
                        event.system,
                        event.environment,
                        event.event_id,
                    ),
                )
            elif state == "LINEAGE_OUT_OF_SYNC":
                connection.execute(
                    """
                    UPDATE deployment_state SET
                        deployed_artifact_digest = ?, lineage_package_digest = NULL,
                        graph_version = ?, state = ?, updated_at = ?
                    WHERE system = ? AND environment = ? AND event_id = ?
                    """,
                    (
                        event.artifact_digest,
                        graph_version,
                        state,
                        now,
                        event.system,
                        event.environment,
                        event.event_id,
                    ),
                )
            elif state == "FAILED_TERMINAL" and event.outcome == "SUCCEEDED":
                connection.execute(
                    """
                    UPDATE deployment_state SET
                        deployed_artifact_digest = ?, lineage_package_digest = NULL,
                        graph_version = COALESCE(?, graph_version), state = ?, updated_at = ?
                    WHERE system = ? AND environment = ? AND event_id = ?
                    """,
                    (
                        event.artifact_digest,
                        graph_version,
                        state,
                        now,
                        event.system,
                        event.environment,
                        event.event_id,
                    ),
                )
            elif authoritative:
                connection.execute(
                    """
                    UPDATE deployment_state SET state = ?, updated_at = ?
                    WHERE system = ? AND environment = ? AND event_id = ?
                    """,
                    (state, now, event.system, event.environment, event.event_id),
                )

            persisted = connection.execute(
                "SELECT * FROM deployment_state WHERE system = ? AND environment = ?",
                (event.system, event.environment),
            ).fetchone()
            result = self._result(
                event,
                state=state,
                reason=reason,
                duplicate=False,
                deployed_artifact_digest=(
                    event.artifact_digest
                    if authoritative and state in {"PROMOTED", "ROLLED_BACK", "LINEAGE_OUT_OF_SYNC"}
                    else None if persisted is None else persisted["deployed_artifact_digest"]
                ),
                lineage_package_digest=(
                    package.package_digest
                    if authoritative and state in {"PROMOTED", "ROLLED_BACK"} and package is not None
                    else None if persisted is None else persisted["lineage_package_digest"]
                ),
                graph_version=(
                    graph_version
                    if authoritative and state in {"PROMOTED", "ROLLED_BACK", "LINEAGE_OUT_OF_SYNC"}
                    else None if persisted is None else persisted["graph_version"]
                ),
            )
            connection.execute(
                """
                UPDATE deployment_events SET status = ?, result_json = ?, updated_at = ?
                WHERE event_id = ?
                """,
                (state, self._canonical(result), now, event.event_id),
            )
        return result

    def state(self, system: str, environment: str) -> DeploymentState | None:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM deployment_state WHERE system = ? AND environment = ?",
                (system, environment),
            ).fetchone()
        if row is None:
            return None
        return DeploymentState(
            system=row["system"],
            environment=row["environment"],
            provider=row["provider"],
            provider_sequence=int(row["provider_sequence"]),
            attempt=int(row["attempt"]),
            event_id=row["event_id"],
            deployed_artifact_digest=row["deployed_artifact_digest"],
            lineage_package_digest=row["lineage_package_digest"],
            graph_version=row["graph_version"],
            state=row["state"],
            audit_ref=row["audit_ref"],
            correlation_id=row["correlation_id"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _insert_event(connection, event, status, result, now) -> None:
        connection.execute(
            """
            INSERT INTO deployment_events(
                event_id, event_type, provider, provider_sequence, attempt, system,
                environment, outcome, artifact_digest, correlation_id, audit_ref,
                occurred_at, status, result_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.event_type,
                event.provider,
                event.provider_sequence,
                event.attempt,
                event.system,
                event.environment,
                event.outcome,
                event.artifact_digest,
                event.correlation_id,
                event.audit_ref,
                event.occurred_at,
                status,
                None if result is None else SQLiteDeploymentStore._canonical(result),
                now,
                now,
            ),
        )

    @staticmethod
    def _result(
        event: DeploymentEvent,
        *,
        state: str,
        reason: str | None,
        duplicate: bool,
        deployed_artifact_digest: str | None,
        lineage_package_digest: str | None,
        graph_version: str | None,
    ) -> dict[str, object]:
        return {
            "schemaVersion": "1.0.0",
            "eventId": event.event_id,
            "system": event.system,
            "environment": event.environment,
            "providerSequence": event.provider_sequence,
            "attempt": event.attempt,
            "state": state,
            "reason": reason,
            "deployedArtifactDigest": deployed_artifact_digest,
            "lineagePackageDigest": lineage_package_digest,
            "graphVersion": graph_version,
            "auditRef": event.audit_ref,
            "correlationId": event.correlation_id,
            "duplicate": duplicate,
        }

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
