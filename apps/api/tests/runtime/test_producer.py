from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest


def _runtime_types():
    try:
        from lineage_api.runtime.models import (
            DeliveryDisposition,
            DeliveryResult,
            LeaseContext,
            ProducerLimits,
        )
        from lineage_api.runtime.producer import RuntimeProducer
        from lineage_api.runtime.transport import CachedLeaseProvider, RetryableTransportError
    except ModuleNotFoundError:
        pytest.fail("bounded runtime producer package is not implemented")
    return (
        DeliveryDisposition,
        DeliveryResult,
        LeaseContext,
        ProducerLimits,
        RuntimeProducer,
        CachedLeaseProvider,
        RetryableTransportError,
    )


class ManualClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
        self.elapsed = 0.0
        self.monotonic_steps: list[float] = []

    def now(self) -> datetime:
        return self.current

    def monotonic(self) -> float:
        if self.monotonic_steps:
            self.elapsed = self.monotonic_steps.pop(0)
        return self.elapsed

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class MutableLeaseProvider:
    def __init__(self, lease) -> None:
        self.lease = lease

    def current(self):
        return self.lease


class ScriptedTransport:
    def __init__(self, scripts: list[object] | None = None) -> None:
        self.scripts = list(scripts or [])
        self.calls: list[tuple[object, ...]] = []

    def send(self, records):
        batch = tuple(records)
        self.calls.append(batch)
        if not self.scripts:
            _, DeliveryResult, *_ = _runtime_types()
            return DeliveryResult.acknowledge(record.observation_id for record in batch)
        result = self.scripts.pop(0)
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            return result(batch)
        return result


def _lease(*, state: str = "ACTIVE", ttl_seconds: int = 300):
    _, _, LeaseContext, *_ = _runtime_types()
    return LeaseContext(
        lease_id="runtime-lease-001",
        window_id="runtime-window-001",
        workload_id="payments-service",
        workload_identity="spiffe://lineage.local/workload/payments-service",
        artifact_digest="sha256:" + "a" * 64,
        mechanism="SDK",
        datasets=frozenset(
            {
                "snowflake://payments/raw.transactions",
                "snowflake://payments/analytics.daily_revenue",
            }
        ),
        allowed_attributes=frozenset({"lineage.alias"}),
        permitted_granularity=frozenset({"CONNECTIVITY", "DATASET", "ELEMENT"}),
        state=state,
        expires_at=datetime(2026, 8, 7, 12, 5, tzinfo=UTC),
    )


def _limits(**overrides):
    _, _, _, ProducerLimits, *_ = _runtime_types()
    values = {
        "max_records": 8,
        "max_bytes": 65536,
        "enqueue_timeout_ms": 2,
        "batch_size": 3,
        "max_retries": 2,
        "base_backoff_ms": 10,
    }
    values.update(overrides)
    return ProducerLimits(**values)


def _payload(index: int = 0) -> dict[str, object]:
    return {
        "operation": "DERIVE",
        "sourceDatasets": ["snowflake://payments/raw.transactions"],
        "targetDatasets": ["snowflake://payments/analytics.daily_revenue"],
        "aliases": {"source": f"raw-{index}"},
        "fieldMappings": [],
    }


def _producer(
    *,
    transport=None,
    lease=None,
    lease_provider=None,
    clock=None,
    limits=None,
    prior_snapshot=None,
):
    _, _, _, _, RuntimeProducer, CachedLeaseProvider, _ = _runtime_types()
    return RuntimeProducer(
        mechanism="SDK",
        lease_provider=lease_provider or CachedLeaseProvider(lease or _lease()),
        transport=transport or ScriptedTransport(),
        clock=clock or ManualClock(),
        limits=limits or _limits(),
        prior_snapshot=prior_snapshot,
    )


def test_observation_identity_and_batch_order_are_deterministic() -> None:
    transport = ScriptedTransport()
    first = _producer(transport=transport)
    results = [first.enqueue(_payload(index)) for index in range(3)]

    flush = first.flush(max_records=2)

    assert [result.accepted for result in results] == [True, True, True]
    assert [record.sequence for record in transport.calls[0]] == [0, 1]
    assert flush.sent == 2
    replay = _producer().enqueue(_payload(0))
    assert replay.observation_id == results[0].observation_id


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"value": "card-number"}, "UNKNOWN_FIELD"),
        ({"attributes": {"custom": "payload"}}, "UNKNOWN_FIELD"),
        ({"aliases": {"source": "Bearer secret-token"}}, "PROHIBITED_VALUE"),
        (
            {"sourceDatasets": ["snowflake://outside/scope"]},
            "DATASET_SCOPE_VIOLATION",
        ),
    ],
)
def test_metadata_is_closed_scoped_and_scanned_before_enqueue(mutation, reason: str) -> None:
    producer = _producer()
    payload = {**_payload(), **mutation}

    result = producer.enqueue(payload)

    assert (result.accepted, result.reason) == (False, reason)
    snapshot = producer.snapshot()
    assert (snapshot.attempted, snapshot.rejected, snapshot.buffered) == (1, 1, 0)


def test_metadata_work_is_hard_capped_before_canonicalization() -> None:
    producer = _producer(limits=_limits(max_string_bytes=32, max_event_bytes=256))

    result = producer.enqueue(
        {**_payload(), "aliases": {"source": "x" * 33}}
    )

    assert (result.accepted, result.reason) == (False, "METADATA_LIMIT_EXCEEDED")
    assert producer.snapshot().rejected == 1


def test_producer_requires_nonblocking_cached_lease_provider() -> None:
    *_, RuntimeProducer, _, _ = _runtime_types()

    with pytest.raises(ValueError, match="cached lease provider"):
        RuntimeProducer(
            mechanism="SDK",
            lease_provider=MutableLeaseProvider(_lease()),
            transport=ScriptedTransport(),
            clock=ManualClock(),
            limits=_limits(),
        )


@pytest.mark.parametrize("state", ["DISABLED", "REVOKED"])
def test_disabled_and_revoked_leases_are_cheap_noops_and_never_call_transport(state: str) -> None:
    transport = ScriptedTransport()
    producer = _producer(transport=transport, lease=_lease(state=state))

    result = producer.enqueue(_payload())
    producer.flush()

    assert (result.accepted, result.reason) == (False, f"LEASE_{state}")
    assert transport.calls == []
    assert producer.snapshot().lease_noops == 1


def test_expired_lease_is_a_noop_without_transport() -> None:
    transport = ScriptedTransport()
    clock = ManualClock()
    clock.advance(301)
    producer = _producer(transport=transport, clock=clock)

    result = producer.enqueue(_payload())
    producer.flush()

    assert (result.accepted, result.reason) == (False, "LEASE_EXPIRED")
    assert transport.calls == []
    assert producer.snapshot().lease_noops == 1


def test_buffer_overflow_and_enqueue_budget_never_block_or_claim_complete() -> None:
    producer = _producer(limits=_limits(max_records=2))
    for index in range(3):
        producer.enqueue(_payload(index))

    snapshot = producer.snapshot()
    assert (snapshot.attempted, snapshot.accepted, snapshot.buffered, snapshot.dropped) == (
        3,
        3,
        2,
        1,
    )
    assert producer.close_window().outcome == "INCOMPLETE"

    slow_clock = ManualClock()
    slow_clock.monotonic_steps = [0.0, 0.003]
    slow = _producer(clock=slow_clock)
    result = slow.enqueue(_payload())
    assert (result.accepted, result.reason) == (False, "ENQUEUE_TIMEOUT")
    assert (slow.snapshot().enqueue_timeouts, slow.snapshot().dropped) == (1, 1)


def test_queue_busy_accounting_is_deferred_atomically_until_snapshot() -> None:
    producer = _producer(limits=_limits(enqueue_timeout_ms=0))
    producer._lock.acquire()
    try:
        result = producer.enqueue(_payload())
    finally:
        producer._lock.release()

    snapshot = producer.snapshot()
    assert (result.accepted, result.reason) == (False, "QUEUE_BUSY")
    assert (snapshot.attempted, snapshot.accepted, snapshot.dropped) == (1, 1, 1)


def test_retry_rejection_ack_and_backoff_counters_are_exact() -> None:
    DeliveryDisposition, DeliveryResult, *_ = _runtime_types()

    def first_delivery(batch):
        return DeliveryResult(
            dispositions={
                batch[0].observation_id: DeliveryDisposition.ACKNOWLEDGED,
                batch[1].observation_id: DeliveryDisposition.RETRYABLE,
                batch[2].observation_id: DeliveryDisposition.REJECTED,
            }
        )

    transport = ScriptedTransport([first_delivery])
    producer = _producer(transport=transport)
    for index in range(3):
        producer.enqueue(_payload(index))

    first_flush = producer.flush()
    retained = transport.calls[0][1]
    transport.scripts.append(
        lambda batch: DeliveryResult.acknowledge(record.observation_id for record in batch)
    )
    second_flush = producer.flush()

    assert first_flush.next_retry_delay_ms == 10
    assert transport.calls[1] == (retained,)
    assert second_flush.next_retry_delay_ms is None
    snapshot = producer.snapshot()
    assert (
        snapshot.accepted,
        snapshot.drained,
        snapshot.retried,
        snapshot.transport_rejected,
        snapshot.dropped,
        snapshot.buffered,
    ) == (3, 2, 1, 1, 1, 0)
    assert producer.close_window().outcome == "INCOMPLETE"


def test_retry_exhaustion_and_transport_exception_use_schedule_without_sleeping() -> None:
    DeliveryDisposition, DeliveryResult, *_, RetryableTransportError = _runtime_types()
    transport = ScriptedTransport([RetryableTransportError("intake unavailable")])
    producer = _producer(transport=transport, limits=_limits(max_retries=1))
    result = producer.enqueue(_payload())

    first = producer.flush()
    original = transport.calls[0][0]
    transport.scripts.append(
        DeliveryResult(dispositions={result.observation_id: DeliveryDisposition.RETRYABLE})
    )
    second = producer.flush()

    assert first.next_retry_delay_ms == 10
    assert transport.calls[1][0] == original
    assert second.next_retry_delay_ms is None
    snapshot = producer.snapshot()
    assert (snapshot.retried, snapshot.retry_exhausted, snapshot.dropped) == (2, 1, 1)


def test_retry_record_bytes_cannot_be_mutated_under_stable_identity() -> None:
    DeliveryDisposition, DeliveryResult, *_ = _runtime_types()

    def mutate_then_retry(batch):
        payload = batch[0].payload
        payload["aliases"]["source"] = "mutated"
        return DeliveryResult(
            dispositions={
                batch[0].observation_id: DeliveryDisposition.RETRYABLE,
            }
        )

    transport = ScriptedTransport([mutate_then_retry])
    producer = _producer(transport=transport)
    producer.enqueue(_payload())
    producer.flush()
    transport.scripts.append(
        lambda batch: DeliveryResult.acknowledge(record.observation_id for record in batch)
    )
    producer.flush()

    first, replay = transport.calls
    assert first[0].observation_id == replay[0].observation_id
    assert replay[0].payload["aliases"]["source"] == "raw-0"


def test_malformed_transport_result_is_contained_as_conservative_retry() -> None:
    transport = ScriptedTransport([None])
    producer = _producer(transport=transport)
    producer.enqueue(_payload())

    flush = producer.flush()

    assert flush.next_retry_delay_ms == 10
    snapshot = producer.snapshot()
    assert (snapshot.internal_errors, snapshot.retried, snapshot.buffered) == (1, 1, 1)


def test_alias_secret_and_field_granularity_are_lease_scoped() -> None:
    no_alias = replace(_lease(), allowed_attributes=frozenset())
    assert _producer(lease=no_alias).enqueue(_payload()).reason == "ATTRIBUTE_SCOPE_VIOLATION"

    secret_key = _producer().enqueue(
        {**_payload(), "aliases": {"password": "hunter2"}}
    )
    assert secret_key.reason == "PROHIBITED_FIELD"

    dataset_only = replace(
        _lease(), permitted_granularity=frozenset({"CONNECTIVITY", "DATASET"})
    )
    field_payload = {
        **_payload(),
        "fieldMappings": [
            {
                "sourceDataset": "snowflake://payments/raw.transactions",
                "sourceField": "amount",
                "targetDataset": "snowflake://payments/analytics.daily_revenue",
                "targetField": "gross_revenue",
            }
        ],
    }
    assert _producer(lease=dataset_only).enqueue(field_payload).reason == (
        "GRANULARITY_SCOPE_VIOLATION"
    )


@pytest.mark.parametrize(
    ("granularity", "payload"),
    [
        (frozenset({"CONNECTIVITY"}), _payload()),
        (
            frozenset({"DATASET"}),
            {
                **_payload(),
                "operation": "CONNECT",
            },
        ),
        (
            frozenset({"DATASET"}),
            {
                **_payload(),
                "fieldMappings": [
                    {
                        "sourceDataset": "snowflake://payments/raw.transactions",
                        "sourceField": "amount",
                        "targetDataset": "snowflake://payments/analytics.daily_revenue",
                        "targetField": "gross_revenue",
                    }
                ],
            },
        ),
    ],
)
def test_operation_cannot_exceed_lease_granularity(granularity, payload) -> None:
    lease = replace(_lease(), permitted_granularity=granularity)

    result = _producer(lease=lease).enqueue(payload)

    assert (result.accepted, result.reason) == (False, "GRANULARITY_SCOPE_VIOLATION")


def test_rotated_lease_never_sends_obsolete_buffered_records() -> None:
    *_, CachedLeaseProvider, _ = _runtime_types()
    original = _lease()
    provider = CachedLeaseProvider(original)
    transport = ScriptedTransport()
    producer = _producer(transport=transport, lease_provider=provider)
    producer.enqueue(_payload())
    provider.update(
        replace(original, lease_id="runtime-lease-002", window_id="runtime-window-002")
    )

    producer.flush()

    assert transport.calls == []
    assert (producer.snapshot().buffered, producer.snapshot().dropped) == (0, 1)
    assert producer.close_window().outcome == "REVOKED"


@pytest.mark.parametrize(
    ("state", "outcome"),
    [("DISABLED", "DISABLED"), ("REVOKED", "REVOKED"), ("EXPIRED", "EXPIRED")],
)
def test_terminal_lease_state_overrides_counter_complete(state: str, outcome: str) -> None:
    *_, CachedLeaseProvider, _ = _runtime_types()
    active = _lease()
    provider = CachedLeaseProvider(active)
    producer = _producer(lease_provider=provider)
    producer.enqueue(_payload())
    producer.flush()
    provider.update(replace(active, state=state))

    assert producer.close_window().outcome == outcome


def test_clock_expiry_overrides_empty_counter_complete() -> None:
    clock = ManualClock()
    producer = _producer(clock=clock)
    clock.advance(301)

    assert producer.close_window().outcome == "EXPIRED"


def test_complete_requires_every_accepted_record_acknowledged_and_restart_reports_loss() -> None:
    producer = _producer()
    producer.enqueue(_payload(0))
    producer.enqueue(_payload(1))
    before_crash = producer.snapshot()

    restarted = _producer(prior_snapshot=before_crash)

    assert restarted.snapshot().lost_on_restart == 2
    assert (
        restarted.snapshot().attempted,
        restarted.snapshot().accepted,
        restarted.snapshot().dropped,
        restarted.snapshot().next_sequence,
    ) == (2, 2, 2, 2)
    resumed = restarted.enqueue(_payload(2))
    assert resumed.observation_id == producer.enqueue(_payload(2)).observation_id
    assert restarted.close_window().outcome == "INCOMPLETE"

    clean = _producer()
    clean.enqueue(_payload(0))
    clean.enqueue(_payload(1))
    clean.flush()
    closed = clean.close_window()
    assert (closed.outcome, closed.accepted, closed.drained, closed.buffered) == (
        "COMPLETE",
        2,
        2,
        0,
    )
