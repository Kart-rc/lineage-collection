from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal


WorkflowKind = Literal["BASELINE", "INCREMENTAL", "PR_GATE", "DEPLOYMENT", "NIGHTLY"]


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class StageIdentity:
    workflow_kind: WorkflowKind
    scope: str
    artifact_digest: str
    stage_name: str
    determinant_digest: str
    schema_version: str

    def idempotency_key(self) -> str:
        body = {
            "artifactDigest": self.artifact_digest,
            "determinantDigest": self.determinant_digest,
            "schemaVersion": self.schema_version,
            "scope": self.scope,
            "stageName": self.stage_name,
            "workflowKind": self.workflow_kind,
        }
        return f"stage:sha256:{hashlib.sha256(_canonical(body).encode()).hexdigest()}"


@dataclass(frozen=True, slots=True)
class Lease:
    command_id: str
    owner: str
    epoch: int
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.epoch < 1:
            raise ValueError("lease epoch must be positive")
        if self.expires_at.tzinfo is None:
            raise ValueError("lease expiry must include a timezone")

    def is_expired(self, now: datetime) -> bool:
        if now.tzinfo is None:
            raise ValueError("current time must include a timezone")
        return now.astimezone(UTC) >= self.expires_at.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Command:
    command_id: str
    idempotency_key: str
    workflow_kind: WorkflowKind
    workflow_version: str
    scope: str
    artifact_digest: str
    determinant_digest: str
    status: str
    attempt: int
    max_attempts: int
    input_ref: str
    correlation_id: str
    created_at: datetime
    deadline_at: datetime
    causation_id: str | None = None
    output_ref: str | None = None


@dataclass(frozen=True, slots=True)
class StageResult:
    identity: StageIdentity
    command_id: str
    output_ref: str
    output_checksum: str
    lease_epoch: int
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    outbox_id: str
    topic: str
    partition_key: str
    payload_ref: str
    status: str
    attempts: int
    available_at: datetime
    correlation_id: str
    created_at: datetime
    delivered_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DurableAcceptance:
    created: bool
    envelope_json: str
    command: Command
    outbox: OutboxEvent


@dataclass(frozen=True, slots=True)
class LaneMessage:
    message_id: str
    lane: str
    group_key: str
    payload_ref: str
    correlation_id: str
    attempt: int
    max_attempts: int
    delivery_epoch: int
    owner: str
    lease_expires_at: datetime
    supersession_key: str | None = None


@dataclass(frozen=True, slots=True)
class LineagePackage:
    package_id: str
    artifact_digest: str
    manifest_ref: str
    graph_version: str
    checksum: str
    approved: bool
