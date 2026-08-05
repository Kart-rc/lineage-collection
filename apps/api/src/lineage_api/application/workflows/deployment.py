from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Literal, Protocol

from lineage_api.application.models import parse_utc
from lineage_api.domain.errors import DomainError


DeploymentEventType = Literal["DEPLOYMENT", "ROLLBACK", "MERGE"]
DeploymentOutcome = Literal["SUCCEEDED", "FAILED"]


@dataclass(frozen=True, slots=True)
class DeploymentEvent:
    event_id: str
    event_type: DeploymentEventType
    provider: str
    provider_sequence: int
    attempt: int
    system: str
    environment: str
    outcome: DeploymentOutcome
    artifact_digest: str | None
    correlation_id: str
    audit_ref: str
    occurred_at: str

    def __post_init__(self) -> None:
        required = (
            self.event_id,
            self.provider,
            self.system,
            self.environment,
            self.correlation_id,
            self.audit_ref,
        )
        if not all(required):
            raise ValueError("deployment identity fields must be non-empty")
        if self.provider_sequence < 1 or self.attempt < 1:
            raise ValueError("deployment order and attempt must be positive")
        if self.event_type not in {"DEPLOYMENT", "ROLLBACK", "MERGE"}:
            raise ValueError(f"unsupported deployment event type: {self.event_type}")
        if self.outcome not in {"SUCCEEDED", "FAILED"}:
            raise ValueError(f"unsupported deployment outcome: {self.outcome}")
        if self.event_type == "ROLLBACK" and self.outcome != "SUCCEEDED":
            raise ValueError("rollback must be an explicit successful outcome")
        if (
            self.event_type in {"DEPLOYMENT", "ROLLBACK"}
            and self.outcome == "SUCCEEDED"
            and not self.artifact_digest
        ):
            raise ValueError("successful deployment outcomes require an artifact digest")
        parse_utc(self.occurred_at)

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schemaVersion": "1.0.0",
            "eventId": self.event_id,
            "eventType": self.event_type,
            "provider": self.provider,
            "providerSequence": self.provider_sequence,
            "attempt": self.attempt,
            "system": self.system,
            "environment": self.environment,
            "outcome": self.outcome,
            "correlationId": self.correlation_id,
            "auditRef": self.audit_ref,
            "occurredAt": self.occurred_at,
        }
        if self.artifact_digest is not None:
            payload["artifactDigest"] = self.artifact_digest
        return payload


@dataclass(frozen=True, slots=True)
class DeploymentPackage:
    package_digest: str
    system: str
    environment: str
    artifact_digest: str
    graph_version: str
    graph_checksum: str
    manifest_ref: str
    approval_ref: str
    approved: bool


@dataclass(frozen=True, slots=True)
class DeploymentState:
    system: str
    environment: str
    provider: str
    provider_sequence: int
    attempt: int
    event_id: str
    deployed_artifact_digest: str | None
    lineage_package_digest: str | None
    graph_version: str | None
    state: str
    audit_ref: str
    correlation_id: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class DeploymentClaim:
    disposition: Literal["CLAIMED", "RESUME", "DUPLICATE", "STALE"]
    result: dict[str, object] | None = None


class DeploymentStorePort(Protocol):
    def register_package(self, package: DeploymentPackage) -> DeploymentPackage: ...

    def claim(self, event: DeploymentEvent) -> DeploymentClaim: ...

    def package_for(
        self, system: str, environment: str, artifact_digest: str
    ) -> DeploymentPackage | None: ...

    def complete(
        self,
        event: DeploymentEvent,
        *,
        state: str,
        reason: str | None,
        package: DeploymentPackage | None = None,
        graph_version: str | None = None,
    ) -> dict[str, object]: ...

    def state(self, system: str, environment: str) -> DeploymentState | None: ...


class DeploymentPublisherPort(Protocol):
    def pointer(self, env: str) -> object: ...

    def promote_existing(
        self,
        *,
        env: str,
        target_version: str,
        expected_prior: str,
        expected_checksum: str,
        actor: str,
        correlation_id: str,
        action: str,
    ) -> object: ...


class DeploymentWorkflow:
    """D1-D6 deployment promotion bound to an exact immutable lineage package."""

    def __init__(
        self,
        *,
        store: DeploymentStorePort,
        publisher: DeploymentPublisherPort,
        authenticator: Callable[[dict[str, object], str], bool],
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._authenticator = authenticator

    def handle(self, event: DeploymentEvent, signature: str) -> dict[str, object]:
        payload = event.as_dict()
        if not self._authenticator(payload, signature):
            return self._unchanged(event, "AUTHENTICATION_FAILED")
        if event.event_type == "MERGE":
            return self._unchanged(event, "NON_DEPLOYMENT_EVENT")

        claim = self._store.claim(event)
        if claim.disposition in {"DUPLICATE", "STALE"}:
            if claim.result is None:  # pragma: no cover - store contract invariant
                raise RuntimeError("terminal deployment claim did not include a result")
            return claim.result

        if event.outcome == "FAILED":
            return self._store.complete(
                event,
                state="FAILED_NO_CHANGE",
                reason="DEPLOYMENT_FAILED",
            )

        artifact_digest = event.artifact_digest
        if artifact_digest is None:  # guarded by DeploymentEvent; keeps the port defensive
            return self._store.complete(
                event,
                state="FAILED_TERMINAL",
                reason="MISSING_ARTIFACT_DIGEST",
            )
        package = self._store.package_for(event.system, event.environment, artifact_digest)
        if package is None:
            pointer = self._publisher.pointer(event.environment)
            return self._store.complete(
                event,
                state="LINEAGE_OUT_OF_SYNC",
                reason="EXACT_PACKAGE_NOT_FOUND",
                graph_version=str(pointer.active_version),
            )

        prior = self._publisher.pointer(event.environment)
        action = "DEPLOYMENT_ROLLBACK" if event.event_type == "ROLLBACK" else "DEPLOYMENT_PROMOTE"
        try:
            promoted = self._publisher.promote_existing(
                env=event.environment,
                target_version=package.graph_version,
                expected_prior=str(prior.active_version),
                expected_checksum=package.graph_checksum,
                actor=event.provider,
                correlation_id=event.correlation_id,
                action=action,
            )
        except DomainError as error:
            current = self._store.state(event.system, event.environment)
            reason = (
                "SUPERSEDED"
                if current is not None and current.event_id != event.event_id
                else error.code
            )
            return self._store.complete(
                event,
                state="FAILED_NO_CHANGE" if reason == "SUPERSEDED" else "FAILED_TERMINAL",
                reason=reason,
                graph_version=str(self._publisher.pointer(event.environment).active_version),
            )

        read_back = self._publisher.pointer(event.environment)
        if (
            str(promoted.active_version) != package.graph_version
            or str(read_back.active_version) != package.graph_version
            or int(read_back.fencing_token) != int(promoted.fencing_token)
        ):
            return self._store.complete(
                event,
                state="FAILED_TERMINAL",
                reason="READ_AFTER_WRITE_MISMATCH",
                graph_version=str(read_back.active_version),
            )

        terminal_state = "ROLLED_BACK" if event.event_type == "ROLLBACK" else "PROMOTED"
        result = self._store.complete(
            event,
            state=terminal_state,
            reason=None,
            package=package,
            graph_version=package.graph_version,
        )
        persisted = self._store.state(event.system, event.environment)
        if persisted is None or (
            persisted.event_id == event.event_id
            and (
                persisted.deployed_artifact_digest != artifact_digest
                or persisted.lineage_package_digest != package.package_digest
                or persisted.graph_version != package.graph_version
                or persisted.correlation_id != event.correlation_id
            )
        ):
            raise DomainError(
                "DEPLOYMENT_READBACK_MISMATCH",
                "Deployment state did not retain the exact promoted package",
                event.correlation_id,
            )
        return result

    @staticmethod
    def canonical_payload(event: DeploymentEvent) -> str:
        return json.dumps(event.as_dict(), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _unchanged(event: DeploymentEvent, reason: str) -> dict[str, object]:
        return {
            "schemaVersion": "1.0.0",
            "eventId": event.event_id,
            "state": "FAILED_NO_CHANGE",
            "reason": reason,
            "duplicate": False,
        }
