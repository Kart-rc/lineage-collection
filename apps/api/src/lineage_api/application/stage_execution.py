from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from lineage_api.application.stage_ownership import UnknownStageError, validate_stage_identity


MAX_STAGE_DOCUMENT_BYTES = 5_000_000
_ARTIFACT_KIND = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_REFERENCE_KEYS = frozenset({"bucket", "key", "versionId", "sha256", "sizeBytes"})


class StageTargetMismatchError(ValueError):
    """The invoked compute target is not allowed to execute the requested stage."""


class StageUseCaseMissingError(ValueError):
    """No application use case is registered for a versioned workflow stage."""


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > 2_048:
        raise ValueError(f"invalid {name}")
    return value


def _reference(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _REFERENCE_KEYS:
        raise ValueError("invalid stage input reference")
    reference = dict(value)
    for key in ("bucket", "key", "versionId"):
        _required_text(f"input reference {key}", reference[key])
    digest = reference["sha256"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("invalid stage input reference digest")
    size = reference["sizeBytes"]
    if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= 5_000_000_000_000:
        raise ValueError("invalid stage input reference size")
    return reference


@dataclass(frozen=True, slots=True)
class StageExecutionContext:
    target: str
    workflow_kind: str
    workflow_version: str
    stage_id: str
    stage_name: str
    command_id: str
    correlation_id: str
    input_reference: Mapping[str, Any]

    def __post_init__(self) -> None:
        for name in (
            "target",
            "workflow_kind",
            "workflow_version",
            "stage_id",
            "stage_name",
            "command_id",
            "correlation_id",
        ):
            _required_text(name, getattr(self, name))
        try:
            owner = validate_stage_identity(
                self.workflow_kind,
                self.workflow_version,
                self.stage_id,
                self.stage_name,
            )
        except UnknownStageError as error:
            raise StageTargetMismatchError("unknown or drifted workflow stage") from error
        if owner.value != self.target:
            raise StageTargetMismatchError(
                f"{self.target} does not own {self.workflow_kind}/{self.stage_id}"
            )
        object.__setattr__(self, "input_reference", _reference(self.input_reference))


@dataclass(frozen=True, slots=True)
class StageExecutionResult:
    artifact_kind: str
    schema_version: str
    document: object

    def __post_init__(self) -> None:
        if _ARTIFACT_KIND.fullmatch(self.artifact_kind) is None:
            raise ValueError("invalid stage artifact kind")
        _required_text("stage artifact schema version", self.schema_version)
        document = self.document
        if isinstance(document, Mapping) and (
            "target" in document or "inputDocument" in document
        ):
            raise ValueError("generic stage checkpoint documents are forbidden")
        encoded = json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        if len(encoded) > MAX_STAGE_DOCUMENT_BYTES:
            raise ValueError("stage result document exceeds the size limit")
        object.__setattr__(self, "document", json.loads(encoded))


class StageUseCase(Protocol):
    def execute(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult: ...


class StageDispatcher:
    def __init__(self, use_cases: Mapping[tuple[str, str], StageUseCase]) -> None:
        self._use_cases = dict(use_cases)

    def has_use_case(self, workflow_kind: str, stage_id: str) -> bool:
        return (workflow_kind, stage_id) in self._use_cases

    def execute(
        self,
        target: str,
        envelope: Mapping[str, Any],
        input_document: object,
    ) -> StageExecutionResult:
        context = StageExecutionContext(
            target=target,
            workflow_kind=str(envelope.get("workflowKind", "")),
            workflow_version=str(envelope.get("workflowVersion", "")),
            stage_id=str(envelope.get("stageId", "")),
            stage_name=str(envelope.get("stageName", "")),
            command_id=str(envelope.get("commandId", "")),
            correlation_id=str(envelope.get("correlationId", "")),
            input_reference=envelope.get("input", {}),
        )
        try:
            use_case = self._use_cases[(context.workflow_kind, context.stage_id)]
        except KeyError as error:
            raise StageUseCaseMissingError(
                f"no use case for {context.workflow_kind}/{context.stage_id}"
            ) from error
        return use_case.execute(input_document, context)


__all__ = [
    "MAX_STAGE_DOCUMENT_BYTES",
    "StageDispatcher",
    "StageExecutionContext",
    "StageExecutionResult",
    "StageTargetMismatchError",
    "StageUseCase",
    "StageUseCaseMissingError",
    "validate_artifact_reference",
]


validate_artifact_reference = _reference
