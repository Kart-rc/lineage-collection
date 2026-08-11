from __future__ import annotations

from typing import Protocol, Sequence

from lineage_api.runtime.models import DeliveryResult, LeaseContext, ObservationRecord


class RetryableTransportError(RuntimeError):
    """The complete batch may be retried with identical observation identities."""


class RejectedTransportError(RuntimeError):
    """The complete batch was permanently rejected by intake."""


class RuntimeTransport(Protocol):
    def send(self, records: Sequence[ObservationRecord]) -> DeliveryResult: ...


class LeaseProvider(Protocol):
    def current(self) -> LeaseContext: ...


class CachedLeaseProvider:
    """I/O-free lease snapshot updated by a background control-plane client."""

    def __init__(self, lease: LeaseContext) -> None:
        self._lease = lease

    def current(self) -> LeaseContext:
        return self._lease

    def update(self, lease: LeaseContext) -> None:
        self._lease = lease
