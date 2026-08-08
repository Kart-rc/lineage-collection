from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from lineage_api.application.models import parse_utc
from lineage_api.db import Database
from lineage_api.domain.errors import DomainError


Mechanism = Literal["OPENLINEAGE", "SDK", "OTEL"]
_PROHIBITED_KEYS = {
    "authorization",
    "bind",
    "db.query.text",
    "db.statement",
    "literalvalue",
    "password",
    "secret",
    "token",
    "value",
    "values",
}
_SECRET_MARKERS = ("bearer ", "password=", "secret=", "sk-")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _checksum(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode()).hexdigest()


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("runtime timestamps require a timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class RuntimeLineageService:
    """Signed, non-production, metadata-only runtime lineage session coordinator."""

    def __init__(
        self,
        database: Database,
        *,
        signing_secret: str,
        clock: object,
        approved_otel_parsers: set[str] | None = None,
    ) -> None:
        self._database = database
        self._secret = signing_secret.encode()
        self._clock = clock
        self._approved_otel_parsers = frozenset(approved_otel_parsers or ())

    def grant_session(
        self,
        *,
        repo: str,
        environment: str,
        artifact_digest: str,
        datasets: tuple[str, ...],
        ttl_seconds: int,
        actor: str,
    ) -> dict[str, object]:
        if environment.lower().startswith("prod"):
            raise DomainError(
                "RUNTIME_PRODUCTION_DENIED",
                "Runtime collection sessions are denied for production targets",
                "runtime-grant",
            )
        scope = tuple(sorted(set(datasets)))
        if not all((repo, environment, artifact_digest, actor)) or not scope:
            raise ValueError("runtime grant identity and dataset scope must be non-empty")
        if ttl_seconds < 1 or ttl_seconds > 3600:
            raise ValueError("runtime session TTL must be between 1 and 3600 seconds")
        now = self._now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        session_id = f"runtime-session-{uuid.uuid4().hex}"
        claims = {
            "sessionId": session_id,
            "repo": repo,
            "environment": environment,
            "artifactDigest": artifact_digest,
            "datasets": list(scope),
            "expiresAt": _utc_text(expires_at),
        }
        token = self._sign(claims)
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO runtime_sessions(
                    session_id, token_digest, repo, environment, artifact_digest,
                    datasets_json, state, expires_at, actor, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'GRANT', ?, ?, ?, ?)
                """,
                (
                    session_id,
                    hashlib.sha256(token.encode()).hexdigest(),
                    repo,
                    environment,
                    artifact_digest,
                    _canonical(scope),
                    _utc_text(expires_at),
                    actor,
                    _utc_text(now),
                    _utc_text(now),
                ),
            )
        return {
            "schemaVersion": "1.0.0",
            **claims,
            "state": "GRANT",
            "token": token,
        }

    def mark_ready(self, session_id: str, token: str) -> dict[str, object]:
        row = self._active_session(session_id, token, allowed_states={"GRANT", "READY"})
        if row["state"] == "GRANT":
            with self._database.transaction() as connection:
                connection.execute(
                    "UPDATE runtime_sessions SET state = 'READY', updated_at = ? WHERE session_id = ?",
                    (_utc_text(self._now()), session_id),
                )
        return {"sessionId": session_id, "state": "READY"}

    def observe(
        self,
        session_id: str,
        token: str,
        mechanism: Mechanism,
        payload: dict[str, object],
    ) -> dict[str, object]:
        session = self._active_session(
            session_id,
            token,
            allowed_states={"READY", "OBSERVING"},
        )
        self._increment(session_id, "attempted")
        try:
            observations = self._parse_observation(session, mechanism, payload)
            results = self._store_observations(session, observations)
        except DomainError:
            self._increment(session_id, "rejected")
            raise
        if len(results) == 1:
            return results[0]
        return {
            "observations": results,
            "duplicate": bool(results) and all(bool(item["duplicate"]) for item in results),
        }

    def begin_drain(self, session_id: str, token: str) -> dict[str, object]:
        row = self._active_session(
            session_id,
            token,
            allowed_states={"READY", "OBSERVING", "DRAIN"},
        )
        if row["state"] != "DRAIN":
            with self._database.transaction() as connection:
                connection.execute(
                    "UPDATE runtime_sessions SET state = 'DRAIN', updated_at = ? WHERE session_id = ?",
                    (_utc_text(self._now()), session_id),
                )
        return {"sessionId": session_id, "state": "DRAIN"}

    def close(
        self,
        session_id: str,
        token: str,
        *,
        expected_observations: int,
        drained: bool,
        buffered: int = 0,
        dropped: int = 0,
    ) -> dict[str, object]:
        if min(expected_observations, buffered, dropped) < 0:
            raise ValueError("runtime closing counts cannot be negative")
        with self._database.connection() as connection:
            existing = connection.execute(
                "SELECT * FROM runtime_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if existing is None:
            raise DomainError(
                "RUNTIME_SESSION_NOT_FOUND",
                "Runtime session does not exist",
                session_id,
            )
        self._verify_token(existing, token)
        if existing["state"] == "CLOSED":
            return json.loads(existing["manifest_json"])
        row = self._active_session(session_id, token, allowed_states={"DRAIN"})
        observations = self._observation_payloads(session_id)
        complete = (
            drained
            and int(row["accepted"]) == expected_observations
            and int(row["rejected"]) == 0
            and buffered == 0
            and dropped == 0
        )
        outcome = "COMPLETE" if complete else "INCOMPLETE"
        manifest = self._manifest(
            row,
            outcome=outcome,
            buffered=buffered,
            dropped=dropped,
            drained=int(row["accepted"]) if drained else 0,
            observations=observations,
        )
        self._persist_manifest(session_id, outcome, manifest)
        return manifest

    def revoke(self, session_id: str, *, actor: str, reason: str) -> dict[str, object]:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise DomainError(
                "RUNTIME_SESSION_NOT_FOUND",
                "Runtime session does not exist",
                session_id,
            )
        if row["state"] == "CLOSED":
            return json.loads(row["manifest_json"])
        observations = self._observation_payloads(session_id)
        manifest = self._manifest(
            row,
            outcome="REVOKED",
            buffered=0,
            dropped=0,
            drained=0,
            observations=observations,
        )
        self._persist_manifest(session_id, "REVOKED", manifest)
        audit_id = "audit-" + hashlib.sha256(
            f"runtime-revoke:{session_id}".encode()
        ).hexdigest()[:20]
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO audit_events(
                    audit_id, actor, action, resource_type, resource_id,
                    correlation_id, payload_json, created_at
                ) VALUES (?, ?, 'RUNTIME_SESSION_REVOKED', 'runtime_session', ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    actor,
                    session_id,
                    session_id,
                    _canonical({"reason": reason}),
                    _utc_text(self._now()),
                ),
            )
        return manifest

    def session_manifest(self, session_id: str) -> dict[str, object]:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT manifest_json FROM runtime_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise DomainError(
                "RUNTIME_SESSION_NOT_FOUND",
                "Runtime session does not exist",
                session_id,
            )
        if row["manifest_json"] is None:
            raise DomainError(
                "RUNTIME_SESSION_OPEN",
                "Runtime session has no closing manifest",
                session_id,
            )
        return json.loads(row["manifest_json"])

    def completed_evidence(
        self,
        *,
        repo: str,
        environment: str,
        artifact_digest: str,
    ) -> list[dict[str, object]]:
        with self._database.connection() as connection:
            sessions = connection.execute(
                """
                SELECT session_id, manifest_json FROM runtime_sessions
                WHERE repo = ? AND environment = ? AND artifact_digest = ?
                  AND outcome = 'COMPLETE'
                ORDER BY updated_at, session_id
                """,
                (repo, environment, artifact_digest),
            ).fetchall()
        return [
            {
                "manifest": json.loads(row["manifest_json"]),
                "observations": self._observation_payloads(row["session_id"]),
            }
            for row in sessions
        ]

    def evidence_status(
        self,
        *,
        repo: str,
        environment: str,
        artifact_digest: str,
    ) -> dict[str, object]:
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT session_id, state, outcome FROM runtime_sessions
                WHERE repo = ? AND environment = ? AND artifact_digest = ?
                ORDER BY updated_at, session_id
                """,
                (repo, environment, artifact_digest),
            ).fetchall()
        if not rows:
            return {"status": "NOT_PROVIDED", "sessionIds": []}
        complete_ids = [row["session_id"] for row in rows if row["outcome"] == "COMPLETE"]
        if complete_ids:
            return {"status": "VALIDATED", "sessionIds": complete_ids}
        session_ids = [row["session_id"] for row in rows]
        return {
            "status": "INCOMPLETE",
            "reason": "SESSION_INCOMPLETE",
            "sessionIds": session_ids,
        }

    def _active_session(
        self,
        session_id: str,
        token: str,
        *,
        allowed_states: set[str],
    ):
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise DomainError(
                "RUNTIME_SESSION_NOT_FOUND",
                "Runtime session does not exist",
                session_id,
            )
        self._verify_token(row, token)
        if row["state"] == "CLOSED":
            code = {
                "EXPIRED": "RUNTIME_SESSION_EXPIRED",
                "REVOKED": "RUNTIME_SESSION_REVOKED",
            }.get(row["outcome"], "RUNTIME_SESSION_CLOSED")
            raise DomainError(code, "Runtime session is closed", session_id)
        if self._now() >= parse_utc(row["expires_at"]):
            self._expire(row)
            raise DomainError(
                "RUNTIME_SESSION_EXPIRED",
                "Runtime session has expired",
                session_id,
            )
        if row["state"] not in allowed_states:
            raise DomainError(
                "RUNTIME_STATE_CONFLICT",
                "Runtime session is not in the required lifecycle state",
                session_id,
                {"state": row["state"], "allowed": sorted(allowed_states)},
            )
        return row

    @staticmethod
    def _verify_token(row, token: str) -> None:
        actual_digest = hashlib.sha256(token.encode()).hexdigest()
        if not hmac.compare_digest(row["token_digest"], actual_digest):
            raise DomainError(
                "RUNTIME_SIGNATURE_INVALID",
                "Runtime session token signature is invalid",
                row["session_id"],
            )

    def _parse_observation(
        self,
        session,
        mechanism: str,
        payload: dict[str, object],
    ) -> list[dict[str, object]]:
        if mechanism not in {"OPENLINEAGE", "SDK", "OTEL"}:
            raise DomainError(
                "RUNTIME_MECHANISM_UNSUPPORTED",
                "Runtime observation mechanism is unsupported",
                session["session_id"],
            )
        self._assert_metadata_only(payload, session["session_id"])
        if payload.get("artifactDigest") != session["artifact_digest"]:
            raise DomainError(
                "RUNTIME_ARTIFACT_MISMATCH",
                "Runtime observation is not bound to the session artifact",
                session["session_id"],
            )
        parser = {
            "OPENLINEAGE": self._parse_openlineage,
            "SDK": self._parse_sdk,
            "OTEL": self._parse_otel,
        }[mechanism]
        parsed = parser(payload, session["session_id"])
        parsed_observations = parsed if isinstance(parsed, list) else [parsed]
        scoped = set(json.loads(session["datasets_json"]))
        normalized: list[dict[str, object]] = []
        for index, observation in enumerate(parsed_observations):
            observed_datasets = set(observation["sourceDatasets"]) | {
                str(observation["targetDataset"])
            }
            if not observed_datasets <= scoped:
                raise DomainError(
                    "RUNTIME_SCOPE_VIOLATION",
                    "Runtime observation references a dataset outside the signed session scope",
                    session["session_id"],
                    {"outsideScope": sorted(observed_datasets - scoped)},
                )
            normalized.append(
                {
                    "schemaVersion": "1.0.0",
                    "observationId": str(
                        observation.get(
                            "observationId",
                            payload["observationId"]
                            if len(parsed_observations) == 1
                            else f"{payload['observationId']}:{index}",
                        )
                    ),
                    "sessionId": session["session_id"],
                    "sequence": int(payload["sequence"]),
                    "artifactDigest": session["artifact_digest"],
                    "mechanism": mechanism,
                    **{
                        key: value
                        for key, value in observation.items()
                        if key != "observationId"
                    },
                }
            )
        return normalized

    def _parse_sdk(self, payload: dict[str, object], correlation_id: str) -> dict[str, object]:
        allowed = {
            "schemaVersion",
            "observationId",
            "sequence",
            "artifactDigest",
            "source",
            "target",
            "edgeType",
            "transform",
            "observedAt",
        }
        self._assert_allowed(payload, allowed, correlation_id)
        source = self._object(payload, "source", correlation_id)
        target = self._object(payload, "target", correlation_id)
        self._assert_allowed(source, {"dataset", "field"}, correlation_id)
        self._assert_allowed(target, {"dataset", "field"}, correlation_id)
        return {
            "granularity": "ELEMENT",
            "sourceDatasets": [str(source["dataset"])],
            "targetDataset": str(target["dataset"]),
            "sourceFields": [str(source["field"])],
            "targetField": str(target["field"]),
            "edgeType": str(payload["edgeType"]),
            "transform": str(payload["transform"]),
            "exact": True,
            "observedAt": str(payload["observedAt"]),
        }

    def _parse_openlineage(
        self, payload: dict[str, object], correlation_id: str
    ) -> list[dict[str, object]]:
        from lineage_api.runtime.adapters.openlineage import (
            OpenLineageAdapter,
            OpenLineageProfile,
        )

        allowed = {
            "schemaURL",
            "producer",
            "schemaVersion",
            "observationId",
            "sequence",
            "artifactDigest",
            "eventType",
            "eventTime",
            "run",
            "job",
            "inputs",
            "outputs",
        }
        self._assert_allowed(payload, allowed, correlation_id)
        result = OpenLineageAdapter().normalize(
            payload,
            OpenLineageProfile(
                supported_schema_urls=frozenset(
                    {
                        "",
                        "https://openlineage.io/spec/2-0-2/OpenLineage.json",
                    }
                ),
                supported_facet_schema_urls=frozenset(
                    {
                        "https://openlineage.io/spec/facets/1-0-1/ParentRunFacet.json",
                        "https://openlineage.io/spec/facets/1-2-0/ColumnLineageDatasetFacet.json",
                    }
                ),
                permitted_granularity=frozenset({"DATASET", "ELEMENT"}),
                control=None,
                supported_producer_prefixes=frozenset(
                    {
                        "https://github.com/OpenLineage/OpenLineage/tree/1.39.0/integration/spark"
                    }
                ),
            ),
        )
        if result.quarantined:
            raise DomainError(
                "RUNTIME_SHAPE_INVALID",
                "OpenLineage event could not be normalized",
                correlation_id,
                {"code": result.quarantined[0].code},
            )
        if result.unsupported:
            raise DomainError(
                "RUNTIME_COVERAGE_UNSUPPORTED",
                "OpenLineage event contains unsupported versioned coverage",
                correlation_id,
                {"code": result.unsupported[0].code},
            )
        body_fields = {
            "granularity",
            "sourceDatasets",
            "targetDataset",
            "sourceFields",
            "targetField",
            "edgeType",
            "transform",
            "exact",
            "observedAt",
        }
        return [
            {
                "observationId": observation["observationId"],
                **{
                    key: value
                    for key, value in observation.items()
                    if key in body_fields
                },
            }
            for observation in result.observations
        ]

    def _parse_otel(self, payload: dict[str, object], correlation_id: str) -> dict[str, object]:
        from lineage_api.runtime.adapters.otel import normalize_legacy_span

        allowed = {
            "schemaVersion",
            "observationId",
            "sequence",
            "artifactDigest",
            "traceId",
            "spanId",
            "attributes",
            "observedAt",
            "parserContract",
            "fieldMapping",
        }
        self._assert_allowed(payload, allowed, correlation_id)
        attributes = self._object(payload, "attributes", correlation_id)
        self._assert_allowed(
            attributes,
            {
                "db.system",
                "db.namespace",
                "db.operation.name",
                "lineage.source.dataset",
                "lineage.target.dataset",
            },
            correlation_id,
        )
        if payload.get("fieldMapping") is not None:
            mapping = self._object(payload, "fieldMapping", correlation_id)
            self._assert_allowed(mapping, {"sourceField", "targetField"}, correlation_id)
        result, issue = normalize_legacy_span(
            payload,
            approved_parser_contracts=self._approved_otel_parsers,
        )
        if issue is not None:
            code = (
                "RUNTIME_PARSER_NOT_APPROVED"
                if issue.code == "OTEL_PARSER_NOT_APPROVED"
                else "RUNTIME_SHAPE_INVALID"
            )
            raise DomainError(code, "OTel span could not be normalized", correlation_id)
        if result is None:
            raise DomainError(
                "RUNTIME_SHAPE_INVALID", "OTel span produced no evidence", correlation_id
            )
        return result

    def _store_observation(
        self, session, observation: dict[str, object]
    ) -> dict[str, object]:
        return self._store_observations(session, [observation])[0]

    def _store_observations(
        self, session, observations: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        now = _utc_text(self._now())
        with self._database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT sequence, datasets_json FROM runtime_observations
                WHERE session_id = ?
                """,
                (session["session_id"],),
            ).fetchall()
            results: list[dict[str, object] | None] = [None] * len(observations)
            pending: list[tuple[int, dict[str, object], str, list[str]]] = []
            duplicate_count = 0
            for index, observation in enumerate(observations):
                payload_checksum = _checksum(observation)
                dataset_scope = sorted(
                    set(observation["sourceDatasets"])
                    | {str(observation["targetDataset"])}
                )
                existing = connection.execute(
                    """
                    SELECT payload_checksum, payload_json FROM runtime_observations
                    WHERE session_id = ? AND observation_id = ?
                    """,
                    (session["session_id"], observation["observationId"]),
                ).fetchone()
                if existing is not None:
                    if existing["payload_checksum"] != payload_checksum:
                        raise DomainError(
                            "RUNTIME_OBSERVATION_CONFLICT",
                            "Observation identity was reused with different metadata",
                            session["session_id"],
                        )
                    results[index] = {
                        **json.loads(existing["payload_json"]),
                        "duplicate": True,
                    }
                    duplicate_count += 1
                    continue
                for row in rows:
                    if set(json.loads(row["datasets_json"])) & set(dataset_scope) and int(
                        observation["sequence"]
                    ) <= int(row["sequence"]):
                        raise DomainError(
                            "RUNTIME_SEQUENCE_CONFLICT",
                            "Runtime sequence must increase for every observed dataset",
                            session["session_id"],
                        )
                pending.append((index, observation, payload_checksum, dataset_scope))
            current = connection.execute(
                "SELECT state FROM runtime_sessions WHERE session_id = ?",
                (session["session_id"],),
            ).fetchone()
            if pending and (
                current is None or current["state"] not in {"READY", "OBSERVING"}
            ):
                raise DomainError(
                    "RUNTIME_STATE_CONFLICT",
                    "Runtime session stopped accepting observations",
                    session["session_id"],
                )
            for index, observation, payload_checksum, dataset_scope in pending:
                connection.execute(
                    """
                    INSERT INTO runtime_observations(
                        session_id, observation_id, sequence, mechanism, granularity,
                        datasets_json, payload_checksum, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session["session_id"],
                        observation["observationId"],
                        observation["sequence"],
                        observation["mechanism"],
                        observation["granularity"],
                        _canonical(dataset_scope),
                        payload_checksum,
                        _canonical(observation),
                        now,
                    ),
                )
                results[index] = {**observation, "duplicate": False}
            connection.execute(
                """
                UPDATE runtime_sessions
                SET state = CASE WHEN ? > 0 THEN 'OBSERVING' ELSE state END,
                    accepted = accepted + ?, duplicates = duplicates + ?, updated_at = ?
                WHERE session_id = ?
                """,
                (
                    len(pending),
                    len(pending),
                    duplicate_count,
                    now,
                    session["session_id"],
                ),
            )
        if any(result is None for result in results):
            raise RuntimeError("runtime observation batch produced an incomplete result")
        return [result for result in results if result is not None]

    def _expire(self, row) -> None:
        observations = self._observation_payloads(row["session_id"])
        manifest = self._manifest(
            row,
            outcome="EXPIRED",
            buffered=0,
            dropped=0,
            drained=0,
            observations=observations,
        )
        self._persist_manifest(row["session_id"], "EXPIRED", manifest)

    def _manifest(
        self,
        row,
        *,
        outcome: str,
        buffered: int,
        dropped: int,
        drained: int,
        observations: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "schemaVersion": "1.0.0",
            "sessionId": row["session_id"],
            "repo": row["repo"],
            "environment": row["environment"],
            "artifactDigest": row["artifact_digest"],
            "outcome": outcome,
            "attempted": int(row["attempted"]),
            "accepted": int(row["accepted"]),
            "rejected": int(row["rejected"]),
            "duplicates": int(row["duplicates"]),
            "buffered": buffered,
            "dropped": dropped,
            "drained": drained,
            "observationChecksum": _checksum(observations),
            "closedAt": _utc_text(self._now()),
        }

    def _persist_manifest(
        self, session_id: str, outcome: str, manifest: dict[str, object]
    ) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                """
                UPDATE runtime_sessions
                SET state = 'CLOSED', outcome = ?, manifest_json = ?, updated_at = ?
                WHERE session_id = ? AND state != 'CLOSED'
                """,
                (outcome, _canonical(manifest), _utc_text(self._now()), session_id),
            )

    def _observation_payloads(self, session_id: str) -> list[dict[str, object]]:
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM runtime_observations
                WHERE session_id = ? ORDER BY sequence, observation_id
                """,
                (session_id,),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def _increment(self, session_id: str, counter: str) -> None:
        if counter not in {"attempted", "rejected"}:
            raise ValueError(f"unsupported runtime counter: {counter}")
        with self._database.transaction() as connection:
            connection.execute(
                f"UPDATE runtime_sessions SET {counter} = {counter} + 1, updated_at = ? WHERE session_id = ?",
                (_utc_text(self._now()), session_id),
            )

    def _sign(self, claims: dict[str, object]) -> str:
        encoded = base64.urlsafe_b64encode(_canonical(claims).encode()).decode().rstrip("=")
        signature = hmac.new(self._secret, encoded.encode(), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def _now(self) -> datetime:
        value = self._clock.now()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("runtime clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _object(
        payload: dict[str, object], key: str, correlation_id: str
    ) -> dict[str, object]:
        value = payload.get(key)
        if not isinstance(value, dict):
            raise DomainError(
                "RUNTIME_SHAPE_INVALID",
                f"Runtime field {key} must be an object",
                correlation_id,
            )
        return value

    @staticmethod
    def _dataset(payload: dict[str, object]) -> str:
        return f"{str(payload['namespace']).rstrip('/')}/{payload['name']}"

    @staticmethod
    def _assert_allowed(
        payload: dict[str, object], allowed: set[str], correlation_id: str
    ) -> None:
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise DomainError(
                "RUNTIME_UNKNOWN_FIELD",
                "Runtime payload contains fields outside its closed schema",
                correlation_id,
                {"fields": unknown},
            )

    def _assert_metadata_only(self, payload: object, correlation_id: str) -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key.lower() in _PROHIBITED_KEYS:
                    raise DomainError(
                        "RUNTIME_PROHIBITED_FIELD",
                        "Runtime payload contains a prohibited value or secret field",
                        correlation_id,
                        {"field": key},
                    )
                self._assert_metadata_only(value, correlation_id)
        elif isinstance(payload, list):
            for value in payload:
                self._assert_metadata_only(value, correlation_id)
        elif isinstance(payload, str) and any(
            marker in payload.lower() for marker in _SECRET_MARKERS
        ):
            raise DomainError(
                "RUNTIME_PROHIBITED_FIELD",
                "Runtime payload appears to contain a secret value",
                correlation_id,
            )
