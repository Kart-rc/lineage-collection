from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lineage_api.application.models import Command, Lease, StageIdentity, StageResult
from lineage_api.application.ports import ArtifactStorePort, ClockPort, CommandStorePort
from lineage_api.application.workflows.definitions import BASELINE
from lineage_api.domain.evidence import EvidenceRef


class BaselineFanoutExceeded(ValueError):
    """The repository contains more lineage-relevant paths than the configured bound."""


class BaselineWorkflow:
    """Runs Baseline stages through immutable, lease-fenced checkpoints."""

    def __init__(
        self,
        command: Command,
        lease: Lease,
        commands: CommandStorePort,
        artifacts: ArtifactStorePort,
        clock: ClockPort,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        if command.workflow_kind != "BASELINE":
            raise ValueError("BaselineWorkflow requires a BASELINE command")
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

    @staticmethod
    def plan_repository(repository_root: Path, *, max_fanout: int) -> dict[str, Any]:
        if max_fanout < 1:
            raise ValueError("max fanout must be positive")
        source_paths: list[str] = []
        skipped_paths: list[str] = []
        unsupported_paths: list[str] = []
        for path in sorted(item for item in repository_root.rglob("*") if item.is_file()):
            relative = path.relative_to(repository_root).as_posix()
            if path.suffix == ".py":
                source_paths.append(relative)
            elif path.suffix == ".md" or relative in {
                "expected-lineage.json",
                "repository-evidence.json",
            }:
                skipped_paths.append(relative)
            else:
                unsupported_paths.append(relative)

        expected_scope = sorted(source_paths + skipped_paths + unsupported_paths)
        if len(expected_scope) > max_fanout:
            raise BaselineFanoutExceeded(
                f"baseline fanout {len(expected_scope)} exceeds limit {max_fanout}"
            )
        fanout = []
        if source_paths:
            fanout.append({"pack": "python-ast", "paths": source_paths})
        return {
            "expectedScope": expected_scope,
            "recomputedScope": source_paths,
            "skippedScope": skipped_paths,
            "unsupportedScope": unsupported_paths,
            "fanout": fanout,
        }

    def _identity(self, stage_id: str) -> StageIdentity:
        if stage_id not in BASELINE.stage_ids:
            raise ValueError(f"unknown Baseline stage: {stage_id}")
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
        body = self.artifacts.get(
            EvidenceRef(
                kind=payload["kind"],
                key=payload["key"],
                checksum=payload["checksum"],
                schema_version=payload["schemaVersion"],
            )
        )
        if not isinstance(body, dict):
            raise TypeError("Baseline checkpoint must contain an object")
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
