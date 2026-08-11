from __future__ import annotations

import hashlib
import json
import queue
import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping

from lineage_api.runtime.models import (
    DeliveryDisposition,
    DeliveryResult,
    EmitResult,
    FlushResult,
    LeaseContext,
    ObservationRecord,
    ProducerLimits,
    ProducerSnapshot,
    WindowClose,
)
from lineage_api.runtime.transport import (
    CachedLeaseProvider,
    LeaseProvider,
    RejectedTransportError,
    RetryableTransportError,
    RuntimeTransport,
)


_PAYLOAD_FIELDS = {
    "operation",
    "sourceDatasets",
    "targetDatasets",
    "aliases",
    "fieldMappings",
}
_OPERATIONS = {"READ", "WRITE", "DERIVE", "CONNECT"}
_SECRET_MARKERS = ("bearer ", "password=", "secret=", "token=", "sk-")
_PROHIBITED_KEYS = {
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
    "value",
    "values",
}


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode()).hexdigest()


def _utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("runtime producer clock must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class _BufferedRecord:
    record: ObservationRecord
    size_bytes: int
    retry_count: int = 0


class RuntimeProducer:
    """Bounded, metadata-only runtime producer with explicit flush scheduling."""

    def __init__(
        self,
        *,
        mechanism: str,
        lease_provider: LeaseProvider,
        transport: RuntimeTransport,
        clock: object,
        limits: ProducerLimits,
        prior_snapshot: ProducerSnapshot | None = None,
    ) -> None:
        if mechanism not in {"SDK", "OTEL", "OPENLINEAGE", "DASK"}:
            raise ValueError("unsupported runtime producer mechanism")
        if not isinstance(lease_provider, CachedLeaseProvider):
            raise ValueError("runtime producer requires a cached lease provider")
        self._mechanism = mechanism
        self._lease_provider = lease_provider
        self._transport = transport
        self._clock = clock
        self._limits = limits
        self._queue: deque[_BufferedRecord] = deque()
        self._buffered_bytes = 0
        self._window_lease = lease_provider.current()
        if self._window_lease.mechanism != mechanism:
            raise ValueError("runtime producer mechanism must match its cached lease")
        if prior_snapshot is not None and any(
            (
                prior_snapshot.lease_id != self._window_lease.lease_id,
                prior_snapshot.window_id != self._window_lease.window_id,
                prior_snapshot.artifact_digest != self._window_lease.artifact_digest,
                prior_snapshot.mechanism != mechanism,
            )
        ):
            raise ValueError("runtime producer restart snapshot belongs to another lease window")
        self._next_sequence = prior_snapshot.next_sequence if prior_snapshot is not None else 0
        self._lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._deferred_counts: queue.SimpleQueue[dict[str, int]] = queue.SimpleQueue()
        lost_now = prior_snapshot.buffered if prior_snapshot is not None else 0
        self._counts = {
            "attempted": prior_snapshot.attempted if prior_snapshot else 0,
            "accepted": prior_snapshot.accepted if prior_snapshot else 0,
            "rejected": prior_snapshot.rejected if prior_snapshot else 0,
            "duplicates": prior_snapshot.duplicates if prior_snapshot else 0,
            "retried": prior_snapshot.retried if prior_snapshot else 0,
            "dropped": (prior_snapshot.dropped if prior_snapshot else 0) + lost_now,
            "drained": prior_snapshot.drained if prior_snapshot else 0,
            "transport_rejected": prior_snapshot.transport_rejected if prior_snapshot else 0,
            "retry_exhausted": prior_snapshot.retry_exhausted if prior_snapshot else 0,
            "enqueue_timeouts": prior_snapshot.enqueue_timeouts if prior_snapshot else 0,
            "lease_noops": prior_snapshot.lease_noops if prior_snapshot else 0,
            "internal_errors": prior_snapshot.internal_errors if prior_snapshot else 0,
        }
        self._lost_on_restart = (
            lost_now + prior_snapshot.lost_on_restart
            if prior_snapshot is not None
            else 0
        )

    def enqueue(self, payload: dict[str, object]) -> EmitResult:
        started = self._monotonic()
        lease = self._lease_provider.current()
        inactive_reason = self._inactive_reason(lease)
        if inactive_reason is not None:
            self._record_counts({"lease_noops": 1})
            return EmitResult(False, None, inactive_reason)
        if not self._same_lease(lease):
            self._record_counts({"lease_noops": 1})
            return EmitResult(False, None, "LEASE_ROTATED")
        preflight_reason, payload_json = self._preflight_payload(payload)
        if preflight_reason is not None:
            return self._reject(preflight_reason)
        if lease.mechanism != self._mechanism:
            return self._reject("LEASE_MECHANISM_MISMATCH")
        invalid = self._validate_payload(payload, lease)
        if invalid is not None:
            return self._reject(invalid)

        elapsed_before_lock_ms = (self._monotonic() - started) * 1000
        remaining_seconds = max(
            0.0,
            (self._limits.enqueue_timeout_ms - elapsed_before_lock_ms) / 1000,
        )
        acquired = self._lock.acquire(timeout=remaining_seconds)
        if not acquired:
            return self._drop_without_lock("QUEUE_BUSY")
        try:
            self._drain_deferred_locked()
            sequence = self._next_sequence
            self._next_sequence += 1
            if payload_json is None:
                raise RuntimeError("runtime producer preflight produced no canonical payload")
            payload_checksum = "sha256:" + hashlib.sha256(payload_json.encode()).hexdigest()
            identity = {
                "leaseId": lease.lease_id,
                "windowId": lease.window_id,
                "workloadId": lease.workload_id,
                "sequence": sequence,
                "payloadChecksum": payload_checksum,
            }
            observation_id = "runtime-observation-" + hashlib.sha256(
                _canonical(identity).encode()
            ).hexdigest()
            record = ObservationRecord(
                observation_id=observation_id,
                lease_id=lease.lease_id,
                window_id=lease.window_id,
                workload_id=lease.workload_id,
                workload_identity=lease.workload_identity,
                artifact_digest=lease.artifact_digest,
                mechanism=self._mechanism,
                sequence=sequence,
                payload_checksum=payload_checksum,
                payload_json=payload_json,
                observed_at=_utc(self._now()),
            )
            size_bytes = len(_canonical(record.as_payload()).encode())
            self._counts["attempted"] += 1
            self._counts["accepted"] += 1
            elapsed_ms = (self._monotonic() - started) * 1000
            if elapsed_ms > self._limits.enqueue_timeout_ms:
                self._counts["enqueue_timeouts"] += 1
                self._counts["dropped"] += 1
                return EmitResult(False, observation_id, "ENQUEUE_TIMEOUT")
            if (
                len(self._queue) >= self._limits.max_records
                or self._buffered_bytes + size_bytes > self._limits.max_bytes
            ):
                self._counts["dropped"] += 1
                return EmitResult(False, observation_id, "BUFFER_FULL")
            self._queue.append(_BufferedRecord(record=record, size_bytes=size_bytes))
            self._buffered_bytes += size_bytes
            return EmitResult(True, observation_id, None)
        finally:
            self._lock.release()

    def flush(self, *, max_records: int | None = None) -> FlushResult:
        if not self._flush_lock.acquire(blocking=False):
            return FlushResult(0, 0, 0, 0, None)
        try:
            lease = self._lease_provider.current()
            if self._inactive_reason(lease) is not None or not self._same_lease(lease):
                self._discard_buffered_for_inactive_lease()
                return FlushResult(0, 0, 0, 0, None)
            if max_records is not None and max_records < 1:
                raise ValueError("runtime flush size must be positive")
            size = min(
                max_records if max_records is not None else self._limits.batch_size,
                self._limits.batch_size,
            )
            with self._lock:
                batch = tuple(list(self._queue)[:size])
            if not batch:
                return FlushResult(0, 0, 0, 0, None)
            records = tuple(item.record for item in batch)
            try:
                result = self._transport.send(records)
                if not isinstance(result, DeliveryResult):
                    raise TypeError("runtime transport returned an invalid delivery result")
            except RetryableTransportError:
                result = DeliveryResult(
                    dispositions={
                        record.observation_id: DeliveryDisposition.RETRYABLE
                        for record in records
                    }
                )
            except RejectedTransportError:
                result = DeliveryResult(
                    dispositions={
                        record.observation_id: DeliveryDisposition.REJECTED
                        for record in records
                    }
                )
            except Exception:
                result = DeliveryResult(
                    dispositions={
                        record.observation_id: DeliveryDisposition.RETRYABLE
                        for record in records
                    }
                )
                with self._lock:
                    self._counts["internal_errors"] += 1
            return self._apply_delivery(batch, result)
        finally:
            self._flush_lock.release()

    def snapshot(self) -> ProducerSnapshot:
        with self._lock:
            self._drain_deferred_locked()
            return ProducerSnapshot(
                **self._counts,
                buffered=len(self._queue),
                buffered_bytes=self._buffered_bytes,
                lost_on_restart=self._lost_on_restart,
                next_sequence=self._next_sequence,
                lease_id=self._window_lease.lease_id,
                window_id=self._window_lease.window_id,
                artifact_digest=self._window_lease.artifact_digest,
                mechanism=self._mechanism,
            )

    def close_window(self) -> WindowClose:
        snapshot = self.snapshot()
        current = self._lease_provider.current()
        terminal_outcome: str | None = None
        if current.state in {"DISABLED", "REVOKED", "EXPIRED"}:
            terminal_outcome = current.state
        elif self._now() >= current.expires_at.astimezone(UTC):
            terminal_outcome = "EXPIRED"
        elif not self._same_lease(current):
            terminal_outcome = "REVOKED"
        complete = (
            snapshot.accepted == snapshot.drained
            and snapshot.buffered == 0
            and snapshot.rejected == 0
            and snapshot.dropped == 0
            and snapshot.transport_rejected == 0
            and snapshot.retry_exhausted == 0
            and snapshot.enqueue_timeouts == 0
            and snapshot.lease_noops == 0
            and snapshot.internal_errors == 0
            and snapshot.lost_on_restart == 0
        )
        return WindowClose(
            outcome=terminal_outcome or ("COMPLETE" if complete else "INCOMPLETE"),
            attempted=snapshot.attempted,
            accepted=snapshot.accepted,
            rejected=snapshot.rejected,
            duplicates=snapshot.duplicates,
            retried=snapshot.retried,
            buffered=snapshot.buffered,
            dropped=snapshot.dropped,
            drained=snapshot.drained,
            transport_rejected=snapshot.transport_rejected,
            retry_exhausted=snapshot.retry_exhausted,
            enqueue_timeouts=snapshot.enqueue_timeouts,
            lease_noops=snapshot.lease_noops,
            internal_errors=snapshot.internal_errors,
            lost_on_restart=snapshot.lost_on_restart,
        )

    def _apply_delivery(
        self,
        batch: tuple[_BufferedRecord, ...],
        result: DeliveryResult,
    ) -> FlushResult:
        sent_ids = {item.record.observation_id for item in batch}
        unknown = set(result.dispositions) - sent_ids
        missing = sent_ids - set(result.dispositions)
        if unknown or missing:
            dispositions = {
                observation_id: DeliveryDisposition.RETRYABLE
                for observation_id in sent_ids
            }
            with self._lock:
                self._counts["internal_errors"] += 1
        else:
            dispositions = {
                observation_id: result.dispositions.get(
                    observation_id, DeliveryDisposition.RETRYABLE
                )
                for observation_id in sent_ids
            }
        acknowledged = 0
        retryable = 0
        rejected = 0
        next_delays: list[int] = []
        with self._lock:
            self._drain_deferred_locked()
            prefix_ids = {item.record.observation_id for item in batch}
            remainder = [item for item in self._queue if item.record.observation_id not in prefix_ids]
            retained: list[_BufferedRecord] = []
            for item in batch:
                disposition = dispositions[item.record.observation_id]
                if disposition == DeliveryDisposition.ACKNOWLEDGED:
                    acknowledged += 1
                    self._counts["drained"] += 1
                    self._buffered_bytes -= item.size_bytes
                elif disposition == DeliveryDisposition.REJECTED:
                    rejected += 1
                    self._counts["transport_rejected"] += 1
                    self._counts["dropped"] += 1
                    self._buffered_bytes -= item.size_bytes
                else:
                    retryable += 1
                    item.retry_count += 1
                    self._counts["retried"] += 1
                    if item.retry_count > self._limits.max_retries:
                        self._counts["retry_exhausted"] += 1
                        self._counts["dropped"] += 1
                        self._buffered_bytes -= item.size_bytes
                    else:
                        retained.append(item)
                        next_delays.append(
                            self._limits.base_backoff_ms * (2 ** (item.retry_count - 1))
                        )
            self._queue = deque(retained + remainder)
        return FlushResult(
            sent=len(batch),
            acknowledged=acknowledged,
            retryable=retryable,
            rejected=rejected,
            next_retry_delay_ms=max(next_delays) if next_delays else None,
        )

    def _reject(self, reason: str) -> EmitResult:
        self._record_counts({"attempted": 1, "rejected": 1})
        return EmitResult(False, None, reason)

    def _drop_without_lock(self, reason: str) -> EmitResult:
        self._deferred_counts.put(
            {"attempted": 1, "accepted": 1, "dropped": 1, "enqueue_timeouts": 1}
        )
        return EmitResult(False, None, reason)

    def _record_counts(self, changes: dict[str, int]) -> None:
        self._deferred_counts.put(changes)

    def _drain_deferred_locked(self) -> None:
        while True:
            try:
                changes = self._deferred_counts.get_nowait()
            except queue.Empty:
                return
            for name, amount in changes.items():
                self._counts[name] += amount

    def _discard_buffered_for_inactive_lease(self) -> None:
        with self._lock:
            self._drain_deferred_locked()
            count = len(self._queue)
            self._queue.clear()
            self._buffered_bytes = 0
            self._counts["dropped"] += count
            if count:
                self._counts["lease_noops"] += 1

    def _inactive_reason(self, lease: LeaseContext) -> str | None:
        if lease.state != "ACTIVE":
            return f"LEASE_{lease.state}"
        if self._now() >= lease.expires_at.astimezone(UTC):
            return "LEASE_EXPIRED"
        return None

    def _same_lease(self, lease: LeaseContext) -> bool:
        return (
            lease.lease_id == self._window_lease.lease_id
            and lease.window_id == self._window_lease.window_id
            and lease.workload_id == self._window_lease.workload_id
            and lease.workload_identity == self._window_lease.workload_identity
            and lease.artifact_digest == self._window_lease.artifact_digest
            and lease.mechanism == self._window_lease.mechanism
            and lease.datasets == self._window_lease.datasets
            and lease.allowed_attributes == self._window_lease.allowed_attributes
            and lease.permitted_granularity == self._window_lease.permitted_granularity
        )

    def _preflight_payload(
        self, payload: object
    ) -> tuple[str | None, str | None]:
        if not isinstance(payload, dict) or len(payload) != len(_PAYLOAD_FIELDS):
            return "UNKNOWN_FIELD", None
        if set(payload) != _PAYLOAD_FIELDS:
            return "UNKNOWN_FIELD", None
        operation = payload.get("operation")
        if not isinstance(operation, str) or operation not in _OPERATIONS:
            return "OPERATION_INVALID", None
        sources = payload.get("sourceDatasets")
        targets = payload.get("targetDatasets")
        if not isinstance(sources, list) or not isinstance(targets, list):
            return "DATASET_METADATA_INVALID", None
        if len(sources) + len(targets) > self._limits.max_datasets:
            return "METADATA_LIMIT_EXCEEDED", None
        if not self._plain_string_list(sources) or not self._plain_string_list(targets):
            return "DATASET_METADATA_INVALID", None
        if not self._bounded_string_list(sources) or not self._bounded_string_list(targets):
            return "METADATA_LIMIT_EXCEEDED", None
        if any(self._contains_secret(value) for value in (*sources, *targets)):
            return "PROHIBITED_VALUE", None
        aliases = payload.get("aliases")
        if not isinstance(aliases, dict):
            return "ALIAS_METADATA_INVALID", None
        if len(aliases) > self._limits.max_aliases:
            return "METADATA_LIMIT_EXCEEDED", None
        for key, value in aliases.items():
            if not isinstance(key, str) or not key or not isinstance(value, str) or not value:
                return "ALIAS_METADATA_INVALID", None
            if not self._bounded_string(key) or not self._bounded_string(value):
                return "METADATA_LIMIT_EXCEEDED", None
            if self._prohibited_key(key):
                return "PROHIBITED_FIELD", None
            if self._contains_secret(value):
                return "PROHIBITED_VALUE", None
        mappings = payload.get("fieldMappings")
        if not isinstance(mappings, list):
            return "FIELD_MAPPING_INVALID", None
        if len(mappings) > self._limits.max_field_mappings:
            return "METADATA_LIMIT_EXCEEDED", None
        for mapping in mappings:
            if not isinstance(mapping, dict) or len(mapping) != 4 or set(mapping) != {
                "sourceDataset",
                "sourceField",
                "targetDataset",
                "targetField",
            }:
                return "FIELD_MAPPING_INVALID", None
            if not all(isinstance(value, str) and value for value in mapping.values()):
                return "FIELD_MAPPING_INVALID", None
            if not all(self._bounded_string(value) for value in mapping.values()):
                return "METADATA_LIMIT_EXCEEDED", None
            if any(self._contains_secret(value) for value in mapping.values()):
                return "PROHIBITED_VALUE", None
        payload_json = _canonical(payload)
        if len(payload_json.encode()) > self._limits.max_event_bytes:
            return "METADATA_LIMIT_EXCEEDED", None
        return None, payload_json

    @staticmethod
    def _validate_payload(payload: Mapping[str, object], lease: LeaseContext) -> str | None:
        operation = payload.get("operation")
        sources = payload.get("sourceDatasets")
        targets = payload.get("targetDatasets")
        source_values = tuple(sources) if isinstance(sources, list) else ()
        target_values = tuple(targets) if isinstance(targets, list) else ()
        required_shape = {
            "READ": (bool(source_values), not target_values),
            "WRITE": (not source_values, bool(target_values)),
            "DERIVE": (bool(source_values), bool(target_values)),
            "CONNECT": (bool(source_values), bool(target_values)),
        }[str(operation)]
        if not all(required_shape):
            return "OPERATION_SHAPE_INVALID"
        if not set(source_values) | set(target_values) <= lease.datasets:
            return "DATASET_SCOPE_VIOLATION"
        aliases = payload.get("aliases")
        if isinstance(aliases, dict) and aliases and "lineage.alias" not in lease.allowed_attributes:
            return "ATTRIBUTE_SCOPE_VIOLATION"
        mappings = payload.get("fieldMappings")
        required_granularity = (
            {"CONNECTIVITY"} if operation == "CONNECT" else {"DATASET"}
        )
        if isinstance(mappings, list) and mappings:
            required_granularity.add("ELEMENT")
        if not required_granularity <= lease.permitted_granularity:
            return "GRANULARITY_SCOPE_VIOLATION"
        for mapping in mappings:
            if (
                mapping["sourceDataset"] not in source_values
                or mapping["targetDataset"] not in target_values
            ):
                return "FIELD_MAPPING_SCOPE_VIOLATION"
        return None

    def _bounded_string_list(self, value: list[object]) -> bool:
        return (
            all(self._bounded_string(item) for item in value)
            and len(value) == len(set(value))
        )

    @staticmethod
    def _plain_string_list(value: list[object]) -> bool:
        return all(isinstance(item, str) and item for item in value) and len(value) == len(
            set(value)
        )

    def _bounded_string(self, value: object) -> bool:
        return (
            isinstance(value, str)
            and bool(value)
            and len(value) <= self._limits.max_string_bytes
            and len(value.encode()) <= self._limits.max_string_bytes
        )

    @staticmethod
    def _prohibited_key(value: str) -> bool:
        normalized = "".join(character for character in value.casefold() if character.isalnum())
        return any(
            normalized == prohibited or normalized.endswith(prohibited)
            for prohibited in _PROHIBITED_KEYS
        )

    @staticmethod
    def _contains_secret(value: object) -> bool:
        if isinstance(value, Mapping):
            return any(
                RuntimeProducer._contains_secret(key)
                or RuntimeProducer._contains_secret(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(RuntimeProducer._contains_secret(item) for item in value)
        if isinstance(value, str):
            lowered = value.casefold()
            return any(marker in lowered for marker in _SECRET_MARKERS)
        return False

    def _now(self) -> datetime:
        value = self._clock.now()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("runtime producer clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def _monotonic(self) -> float:
        value = self._clock.monotonic()
        if not isinstance(value, (int, float)):
            raise ValueError("runtime producer monotonic clock must be numeric")
        return float(value)
