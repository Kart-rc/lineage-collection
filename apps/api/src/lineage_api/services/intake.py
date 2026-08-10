from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from lineage_api.application.models import Command, OutboxEvent, WorkflowKind, parse_utc
from lineage_api.application.ports import ClockPort, IntakeUnitOfWorkPort
from lineage_api.application.repository_sources import validate_repository_identity
from lineage_api.db import Database


LANE_POLICY_VERSION = "1.0.0"
LANE_POLICY = {
    "pr.updated": "interactive",
    "repo.push": "events",
    "deploy": "events",
    "baseline": "bulk",
    "backfill": "bulk",
    "nightly": "bulk",
    "llm.batch": "bulk",
}
WORKFLOW_POLICY: dict[str, WorkflowKind] = {
    "pr.updated": "PR_GATE",
    "repo.push": "INCREMENTAL",
    "deploy": "DEPLOYMENT",
    "baseline": "BASELINE",
    "backfill": "BASELINE",
    "nightly": "NIGHTLY",
    "llm.batch": "NIGHTLY",
}
LANE_DEADLINE_SECONDS = {
    "interactive": 120,
    "events": 900,
    "bulk": 86_400,
}
LANE_MAX_ATTEMPTS = {"interactive": 2, "events": 5, "bulk": 3}


@dataclass(frozen=True, slots=True)
class PushDelivery:
    payload: dict[str, Any]
    signature: str

    @property
    def canonical_body(self) -> bytes:
        return json.dumps(self.payload, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True, slots=True)
class IntakeResult:
    outcome: Literal["ACCEPTED", "DUPLICATE", "QUARANTINED"]
    event_id: str
    envelope: dict[str, Any] | None
    policy_version: str
    reason: str | None = None
    quarantine_id: str | None = None
    command: Command | None = None
    outbox: OutboxEvent | None = None


class IntakeService:
    def __init__(
        self,
        database: Database,
        webhook_secret: str,
        unit_of_work: IntakeUnitOfWorkPort,
        clock: ClockPort,
    ) -> None:
        self._database = database
        self._secret = webhook_secret.encode()
        self._unit_of_work = unit_of_work
        self._clock = clock

    def accept(self, delivery: PushDelivery) -> IntakeResult:
        payload = delivery.payload
        event_id = str(payload.get("eventId", ""))
        correlation_id = f"corr-{event_id or self._payload_digest(delivery.canonical_body)[:12]}"

        if not self._valid_signature(delivery):
            return self._quarantine(payload, event_id, correlation_id, "BAD_SIGNATURE")

        event_type = str(payload.get("eventType", ""))
        digest = str(payload.get("digest", ""))
        repo = str(payload.get("repo", ""))
        env = str(payload.get("env", ""))
        system = str(payload.get("system", ""))
        received_at = str(payload.get("receivedAt", ""))
        if not all((event_id, digest, repo, env, system, received_at)):
            return self._quarantine(payload, event_id, correlation_id, "MISSING_IDENTITY")
        if event_type not in LANE_POLICY:
            return self._quarantine(payload, event_id, correlation_id, "UNKNOWN_EVENT_TYPE")

        envelope = {
            "schemaVersion": "1.0.0",
            "eventId": event_id,
            "eventType": event_type,
            "correlationId": correlation_id,
            "repo": repo,
            "digest": digest,
            "env": env,
            "system": system,
            "lane": LANE_POLICY[event_type],
            "changedFiles": sorted(set(str(item) for item in payload.get("changedFiles", []))),
            "receivedAt": received_at,
        }
        runtime_observation = payload.get("runtimeObservation")
        if isinstance(runtime_observation, dict):
            envelope["runtimeObservation"] = runtime_observation
        repository_source = payload.get("repositorySource")
        if repository_source is not None:
            normalized_source = _repository_source(repository_source, repo)
            if normalized_source is None:
                return self._quarantine(
                    payload, event_id, correlation_id, "INVALID_REPOSITORY_SOURCE"
                )
            envelope["repositorySource"] = normalized_source
        envelope_json = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
        try:
            parse_utc(received_at)
        except ValueError:
            return self._quarantine(payload, event_id, correlation_id, "INVALID_TIMESTAMP")
        created_at = self._clock.now()
        command = self._command_for(envelope, created_at)
        outbox = self._outbox_for(envelope, command, created_at)
        durable = self._unit_of_work.accept(
            event_id,
            envelope_json,
            created_at,
            command,
            outbox,
        )
        stored_envelope = json.loads(durable.envelope_json)

        if not durable.created:
            return IntakeResult(
                outcome="DUPLICATE",
                event_id=event_id,
                envelope=stored_envelope,
                policy_version=LANE_POLICY_VERSION,
                reason="DUPLICATE",
                command=durable.command,
                outbox=durable.outbox,
            )

        return IntakeResult(
            outcome="ACCEPTED",
            event_id=event_id,
            envelope=stored_envelope,
            policy_version=LANE_POLICY_VERSION,
            command=durable.command,
            outbox=durable.outbox,
        )

    def event_count(self, event_id: str) -> int:
        with self._database.connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM events WHERE event_id = ?", (event_id,)
                ).fetchone()[0]
            )

    def run_count_for(self, event_id: str) -> int:
        with self._database.connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM runs WHERE event_id = ?", (event_id,)
                ).fetchone()[0]
            )

    def quarantines(self) -> list[dict[str, Any]]:
        with self._database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM quarantines ORDER BY created_at, quarantine_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def _valid_signature(self, delivery: PushDelivery) -> bool:
        expected = hmac.new(self._secret, delivery.canonical_body, hashlib.sha256).hexdigest()
        provided = delivery.signature.removeprefix("sha256=")
        return hmac.compare_digest(expected, provided)

    def _command_for(self, envelope: dict[str, Any], created_at: datetime) -> Command:
        determinant_body = {
            "changedFiles": envelope["changedFiles"],
            "environment": envelope["env"],
            "eventType": envelope["eventType"],
            "lanePolicyVersion": LANE_POLICY_VERSION,
            "system": envelope["system"],
            "runtimeObservationDigest": (
                self._payload_digest(self._canonical(envelope["runtimeObservation"]))
                if "runtimeObservation" in envelope
                else None
            ),
            "repositorySource": envelope.get("repositorySource"),
        }
        determinant = f"sha256:{self._payload_digest(self._canonical(determinant_body))}"
        workflow_kind = WORKFLOW_POLICY[envelope["eventType"]]
        identity = {
            "artifactDigest": envelope["digest"],
            "determinantDigest": determinant,
            "eventId": envelope["eventId"],
            "scope": f"repo:{envelope['repo']}",
            "workflowKind": workflow_kind,
            "workflowVersion": "1.0.0",
        }
        identity_digest = self._payload_digest(self._canonical(identity))
        lane = envelope["lane"]
        return Command(
            command_id=f"command-{hashlib.sha256(envelope['eventId'].encode()).hexdigest()[:20]}",
            idempotency_key=f"command:sha256:{identity_digest}",
            workflow_kind=workflow_kind,
            workflow_version="1.0.0",
            scope=f"repo:{envelope['repo']}",
            artifact_digest=envelope["digest"],
            determinant_digest=determinant,
            status="QUEUED",
            attempt=0,
            max_attempts=LANE_MAX_ATTEMPTS[lane],
            input_ref=f"event://{envelope['eventId']}",
            correlation_id=envelope["correlationId"],
            created_at=created_at,
            deadline_at=created_at + timedelta(seconds=LANE_DEADLINE_SECONDS[lane]),
        )

    def _outbox_for(
        self,
        envelope: dict[str, Any],
        command: Command,
        created_at: datetime,
    ) -> OutboxEvent:
        return OutboxEvent(
            outbox_id=f"outbox-{hashlib.sha256(command.command_id.encode()).hexdigest()[:20]}",
            topic=envelope["lane"],
            partition_key=command.scope,
            payload_ref=f"command://{command.command_id}",
            status="PENDING",
            attempts=0,
            available_at=created_at,
            correlation_id=command.correlation_id,
            created_at=created_at,
        )

    def _quarantine(
        self,
        payload: dict[str, Any],
        event_id: str,
        correlation_id: str,
        reason: str,
    ) -> IntakeResult:
        safe_payload = {
            key: payload.get(key)
            for key in ("eventId", "eventType", "repo", "digest", "env", "system")
            if payload.get(key) not in (None, "")
        }
        safe_json = json.dumps(safe_payload, sort_keys=True, separators=(",", ":"))
        identity = f"{reason}:{event_id}:{self._payload_digest(safe_json.encode())}"
        quarantine_id = f"q-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"
        created_at = str(payload.get("receivedAt") or "1970-01-01T00:00:00Z")
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO quarantines(
                    quarantine_id, kind, reason, raw_json, candidates_json,
                    correlation_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    quarantine_id,
                    "EVENT",
                    reason,
                    safe_json,
                    "[]",
                    correlation_id,
                    created_at,
                ),
            )
        return IntakeResult(
            outcome="QUARANTINED",
            event_id=event_id,
            envelope=None,
            policy_version=LANE_POLICY_VERSION,
            reason=reason,
            quarantine_id=quarantine_id,
        )

    @staticmethod
    def _payload_digest(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


_REPOSITORY_SOURCE_KEYS = frozenset(
    {
        "sourceKind",
        "origin",
        "revision",
        "scopeDigest",
        "scopeDispositionDigest",
        "analyzerPack",
        "ruleset",
        "framework",
        "schemaProfile",
        "platform",
    }
)
_SOURCE_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SOURCE_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_SOURCE_SCOPE = re.compile(r"sha256:[0-9a-f]{64}")


def _repository_source(value: object, repository: str) -> dict[str, str] | None:
    if not isinstance(value, dict) or set(value) != _REPOSITORY_SOURCE_KEYS:
        return None
    if not all(isinstance(value[key], str) for key in _REPOSITORY_SOURCE_KEYS):
        return None
    source = {key: str(value[key]) for key in sorted(_REPOSITORY_SOURCE_KEYS)}
    try:
        validate_repository_identity(source["origin"], repository)
    except ValueError:
        return None
    if (
        source["sourceKind"] != "git-checkout"
        or source["framework"] != "spring-data-jpa"
        or _SOURCE_REVISION.fullmatch(source["revision"]) is None
        or _SOURCE_SCOPE.fullmatch(source["scopeDigest"]) is None
        or _SOURCE_SCOPE.fullmatch(source["scopeDispositionDigest"]) is None
        or source["schemaProfile"] not in {"h2", "mysql", "postgres"}
        or source["platform"] != source["schemaProfile"]
        or any(
            _SOURCE_TEXT.fullmatch(source[field]) is None
            for field in ("analyzerPack", "ruleset")
        )
    ):
        return None
    return source
