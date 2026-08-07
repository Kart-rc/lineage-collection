from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping


class DeliveryDisposition(str, Enum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RETRYABLE = "RETRYABLE"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    dispositions: Mapping[str, DeliveryDisposition]

    def __post_init__(self) -> None:
        normalized: dict[str, DeliveryDisposition] = {}
        for observation_id, disposition in self.dispositions.items():
            if not observation_id:
                raise ValueError("delivery result observation identity must be non-empty")
            normalized[str(observation_id)] = DeliveryDisposition(disposition)
        object.__setattr__(self, "dispositions", MappingProxyType(normalized))

    @classmethod
    def acknowledge(cls, observation_ids: Iterable[str]) -> DeliveryResult:
        return cls(
            dispositions={
                observation_id: DeliveryDisposition.ACKNOWLEDGED
                for observation_id in observation_ids
            }
        )


@dataclass(frozen=True, slots=True)
class LeaseContext:
    lease_id: str
    window_id: str
    workload_id: str
    workload_identity: str
    artifact_digest: str
    mechanism: str
    datasets: frozenset[str]
    allowed_attributes: frozenset[str]
    permitted_granularity: frozenset[str]
    state: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if not all(
            (
                self.lease_id,
                self.window_id,
                self.workload_id,
                self.workload_identity,
                self.artifact_digest,
                self.mechanism,
            )
        ):
            raise ValueError("runtime lease context identity must be non-empty")
        if not self.datasets:
            raise ValueError("runtime lease context requires dataset scope")
        if not self.permitted_granularity or not self.permitted_granularity <= {
            "CONNECTIVITY",
            "DATASET",
            "ELEMENT",
        }:
            raise ValueError("runtime lease context granularity is invalid")
        if self.expires_at.tzinfo is None:
            raise ValueError("runtime lease expiry must be timezone-aware")
        if self.state not in {"ACTIVE", "DISABLED", "REVOKED", "EXPIRED"}:
            raise ValueError("unsupported runtime lease state")


@dataclass(frozen=True, slots=True)
class ProducerLimits:
    max_records: int
    max_bytes: int
    enqueue_timeout_ms: float
    batch_size: int
    max_retries: int
    base_backoff_ms: int
    max_event_bytes: int = 16384
    max_string_bytes: int = 2048
    max_datasets: int = 64
    max_aliases: int = 32
    max_field_mappings: int = 256

    def __post_init__(self) -> None:
        positive = (
            self.max_records,
            self.max_bytes,
            self.batch_size,
            self.base_backoff_ms,
            self.max_event_bytes,
            self.max_string_bytes,
            self.max_datasets,
            self.max_aliases,
            self.max_field_mappings,
        )
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in positive):
            raise ValueError("runtime producer positive limits must be positive integers")
        if (
            not isinstance(self.max_retries, int)
            or isinstance(self.max_retries, bool)
            or self.max_retries < 0
        ):
            raise ValueError("runtime producer max_retries must be non-negative")
        if (
            not isinstance(self.enqueue_timeout_ms, (int, float))
            or isinstance(self.enqueue_timeout_ms, bool)
            or self.enqueue_timeout_ms < 0
        ):
            raise ValueError("runtime producer enqueue timeout must be non-negative")


@dataclass(frozen=True, slots=True)
class FieldMapping:
    source_dataset: str
    source_field: str
    target_dataset: str
    target_field: str

    def __post_init__(self) -> None:
        if not all(
            (self.source_dataset, self.source_field, self.target_dataset, self.target_field)
        ):
            raise ValueError("runtime field mapping metadata must be non-empty")

    def as_payload(self) -> dict[str, str]:
        return {
            "sourceDataset": self.source_dataset,
            "sourceField": self.source_field,
            "targetDataset": self.target_dataset,
            "targetField": self.target_field,
        }


@dataclass(frozen=True, slots=True)
class EmitResult:
    accepted: bool
    observation_id: str | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    observation_id: str
    lease_id: str
    window_id: str
    workload_id: str
    workload_identity: str
    artifact_digest: str
    mechanism: str
    sequence: int
    payload_checksum: str
    payload_json: str
    observed_at: str

    @property
    def payload(self) -> dict[str, object]:
        """Return a disposable copy; canonical retry bytes remain immutable."""
        return json.loads(self.payload_json)

    def as_payload(self) -> dict[str, object]:
        return {
            "schemaVersion": "1.0.0",
            "observationId": self.observation_id,
            "leaseId": self.lease_id,
            "windowId": self.window_id,
            "workloadId": self.workload_id,
            "workloadIdentity": self.workload_identity,
            "artifactDigest": self.artifact_digest,
            "mechanism": self.mechanism,
            "sequence": self.sequence,
            "payloadChecksum": self.payload_checksum,
            "payload": self.payload,
            "observedAt": self.observed_at,
        }


@dataclass(frozen=True, slots=True)
class ProducerSnapshot:
    attempted: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    retried: int = 0
    buffered: int = 0
    buffered_bytes: int = 0
    dropped: int = 0
    drained: int = 0
    transport_rejected: int = 0
    retry_exhausted: int = 0
    enqueue_timeouts: int = 0
    lease_noops: int = 0
    internal_errors: int = 0
    lost_on_restart: int = 0
    next_sequence: int = 0
    lease_id: str = ""
    window_id: str = ""
    artifact_digest: str = ""
    mechanism: str = ""


@dataclass(frozen=True, slots=True)
class FlushResult:
    sent: int
    acknowledged: int
    retryable: int
    rejected: int
    next_retry_delay_ms: int | None


@dataclass(frozen=True, slots=True)
class WindowClose:
    outcome: str
    attempted: int
    accepted: int
    rejected: int
    duplicates: int
    retried: int
    buffered: int
    dropped: int
    drained: int
    transport_rejected: int
    retry_exhausted: int
    enqueue_timeouts: int
    lease_noops: int
    internal_errors: int
    lost_on_restart: int
