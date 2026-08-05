from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol, runtime_checkable

from lineage_api.application.models import (
    Command,
    CoverageManifest,
    LaneMessage,
    Lease,
    LineagePackage,
    OutboxEvent,
    StageIdentity,
    StageResult,
)


@runtime_checkable
class ClockPort(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class ReceiptPort(Protocol):
    def accept(self, event_id: str, payload_ref: str, correlation_id: str) -> bool: ...


@runtime_checkable
class CommandStorePort(Protocol):
    def submit(self, command: Command) -> Command: ...

    def get(self, command_id: str) -> Command | None: ...

    def claim(self, command_id: str, owner: str, lease_seconds: int) -> Lease: ...

    def renew(self, lease: Lease, lease_seconds: int) -> Lease: ...

    def complete(self, lease: Lease, result: StageResult) -> Command: ...

    def fail(self, lease: Lease, error_code: str, retryable: bool) -> Command: ...

    def completed_stage(self, identity: StageIdentity) -> StageResult | None: ...


@runtime_checkable
class ArtifactStorePort(Protocol):
    def put(self, kind: str, key: str, body: object, schema_version: str) -> object: ...

    def get(self, reference: object) -> object: ...


@runtime_checkable
class OutboxPort(Protocol):
    def append(self, event: OutboxEvent) -> OutboxEvent: ...

    def pending(self, limit: int) -> Iterable[OutboxEvent]: ...

    def mark_delivered(self, outbox_id: str, delivered_at: datetime) -> None: ...


@runtime_checkable
class LaneBrokerPort(Protocol):
    def publish(
        self,
        lane: str,
        group_key: str,
        payload_ref: str,
        correlation_id: str,
        *,
        message_id: str,
        max_attempts: int = 5,
        supersession_key: str | None = None,
    ) -> str: ...

    def claim(
        self, lane: str, owner: str, visibility_timeout_seconds: int = 30
    ) -> LaneMessage | None: ...

    def acknowledge(self, message: LaneMessage) -> None: ...

    def retry(self, message: LaneMessage, available_at: datetime, error_code: str) -> None: ...

    def redrive(self, message_id: str, available_at: datetime) -> None: ...


@runtime_checkable
class CatalogSnapshotPort(Protocol):
    def active_snapshot_id(self) -> str: ...

    def snapshot_ref(self, snapshot_id: str) -> str: ...


@runtime_checkable
class CoveragePort(Protocol):
    def put(self, manifest: CoverageManifest) -> CoverageManifest: ...

    def get(self, manifest_id: str) -> CoverageManifest | None: ...


@runtime_checkable
class ProposalPort(Protocol):
    def create(self, payload: object, expected_base_version: str, correlation_id: str) -> object: ...

    def get(self, proposal_id: str, version: int | None = None) -> object: ...


@runtime_checkable
class PublicationPort(Protocol):
    def publish(self, package: LineagePackage, expected_prior: str) -> object: ...

    def active_pointer(self, environment: str) -> object: ...


@runtime_checkable
class ProjectionPort(Protocol):
    def stage(self, package: LineagePackage, fence: int) -> object: ...

    def verify(self, staged: object, expected_checksum: str) -> bool: ...


@runtime_checkable
class TelemetryPort(Protocol):
    def count(self, name: str, value: int = 1, **attributes: str) -> None: ...

    def observe(self, name: str, value: float, **attributes: str) -> None: ...
