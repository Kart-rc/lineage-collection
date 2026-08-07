from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from lineage_api.application.models import Command, Lease, StageIdentity, StageResult
from lineage_api.application.ports import ArtifactStorePort, ClockPort, CommandStorePort
from lineage_api.application.workflows.definitions import INCREMENTAL
from lineage_api.domain.evidence import EvidenceRef


class IncrementalWorkflow:
    """Runs Incremental stages through immutable, lease-fenced checkpoints."""

    def __init__(
        self,
        command: Command,
        lease: Lease,
        commands: CommandStorePort,
        artifacts: ArtifactStorePort,
        clock: ClockPort,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        if command.workflow_kind != "INCREMENTAL":
            raise ValueError("IncrementalWorkflow requires an INCREMENTAL command")
        self.command = command
        self.lease = lease
        self.commands = commands
        self.artifacts = artifacts
        self.clock = clock
        self.fault_injector = fault_injector
        self.reused_stage_ids: list[str] = []

    def checkpoint(
        self,
        stage_id: str,
        operation: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        identity = self._identity(stage_id)
        existing = self.commands.completed_stage(identity)
        if existing is not None:
            self.reused_stage_ids.append(stage_id)
            return self._load(existing.output_ref)

        body = operation()
        reference = self.artifacts.put(
            "manifest",
            f"workflow/{self.command.command_id}/{stage_id.lower()}",
            body,
            self.command.workflow_version,
        )
        reference_payload = self._reference_payload(reference)
        result = StageResult(
            identity=identity,
            command_id=self.command.command_id,
            output_ref=json.dumps(reference_payload, sort_keys=True, separators=(",", ":")),
            output_checksum=f"sha256:{reference_payload['checksum']}",
            lease_epoch=self.lease.epoch,
            completed_at=self.clock.now(),
        )
        self.commands.record_stage(self.lease, result)
        if self.fault_injector is not None:
            self.fault_injector(stage_id)
        return body

    def load(self, stage_id: str) -> dict[str, Any] | None:
        result = self.commands.completed_stage(self._identity(stage_id))
        return None if result is None else self._load(result.output_ref)

    @staticmethod
    def runtime_coverage(runtime: dict[str, Any]) -> dict[str, object]:
        body = runtime["runtime"]
        return {
            "status": str(body["status"]),
            "sessionIds": sorted(set(str(value) for value in body.get("sessionIds", []))),
        }

    def _identity(self, stage_id: str) -> StageIdentity:
        if stage_id not in INCREMENTAL.stage_ids:
            raise ValueError(f"unknown Incremental stage: {stage_id}")
        return StageIdentity(
            workflow_kind=self.command.workflow_kind,
            scope=self.command.scope,
            artifact_digest=self.command.artifact_digest,
            stage_name=stage_id,
            determinant_digest=self.command.determinant_digest,
            schema_version=self.command.workflow_version,
        )

    def _load(self, output_ref: str) -> dict[str, Any]:
        payload = json.loads(output_ref)
        reference = EvidenceRef(
            kind=payload["kind"],
            key=payload["key"],
            checksum=payload["checksum"],
            schema_version=payload["schemaVersion"],
        )
        body = self.artifacts.get(reference)
        if not isinstance(body, dict):
            raise TypeError("Incremental checkpoint must contain an object")
        return body

    @staticmethod
    def _reference_payload(reference: object) -> dict[str, str]:
        if hasattr(reference, "as_dict"):
            payload = reference.as_dict()
        elif isinstance(reference, dict):
            payload = reference
        else:
            raise TypeError("artifact store returned an unsupported reference")
        return {
            "schemaVersion": str(payload["schemaVersion"]),
            "kind": str(payload["kind"]),
            "key": str(payload["key"]),
            "checksum": str(payload["checksum"]),
        }
