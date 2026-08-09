from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Mapping, cast

from lineage_api.application.classification import (
    ClassificationEvidence,
    evaluate_classification,
)
from lineage_api.application.stage_execution import (
    MAX_STAGE_DOCUMENT_BYTES,
    StageExecutionContext,
    StageExecutionResult,
    StageUseCase,
    validate_artifact_reference,
)
from lineage_api.application.ports import (
    ArtifactStorePort,
    DeploymentControlPort,
    ImpactProjectionPort,
    NightlyControlPort,
    PrGateControlPort,
    ProposalStorePort,
    PublicationControlPort,
    SourceArchivePort,
    StageProjectionPort,
)
from lineage_api.application.nightly_execution import NightlyStageUseCase
from lineage_api.application.pr_gate_execution import PrGateStageUseCase
from lineage_api.application.sca_execution import ScaStageUseCase
from lineage_api.application.consolidation import derive_consolidation, edge_key_for
from lineage_api.application.runtime_validation import runtime_window_manifest_errors
from lineage_api.application.workflows.deployment import DeploymentEvent
from lineage_api.domain.urns import LineageUrn


_CONTEXT_FIELDS = {
    "artifactDigest": ("artifactDigest", "digest"),
    "environment": ("environment", "env"),
    "platform": ("platform",),
    "repository": ("repository", "repo", "repoOrPath"),
    "system": ("system",),
}
_EVIDENCE_KEYS = frozenset({"level", "source", "class", "repositoryClass", "ref"})
_SKIPPED_BASELINE_FILES = frozenset(
    {"expected-lineage.json", "repository-evidence.json"}
)


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > 2_048:
        raise ValueError(f"invalid {name}")
    return value


def _bounded_context(document: Mapping[str, Any]) -> dict[str, Any]:
    inherited = document.get("context")
    sources = [inherited, document] if isinstance(inherited, Mapping) else [document]
    context: dict[str, Any] = {}
    for output_name, aliases in _CONTEXT_FIELDS.items():
        for source in sources:
            value = next((source.get(alias) for alias in aliases if alias in source), None)
            if value is not None:
                context[output_name] = _required_text(output_name, value)
                break
    for required in ("repository", "artifactDigest", "environment", "system"):
        if required not in context:
            raise ValueError(f"classification input is missing {required}")
    for source in sources:
        if "repositorySource" in source:
            context["repositorySource"] = validate_artifact_reference(
                source["repositorySource"]
            )
            break
    for source in sources:
        if "catalogSnapshotRef" in source:
            context["catalogSnapshotRef"] = validate_artifact_reference(
                source["catalogSnapshotRef"]
            )
            break
    for source in sources:
        if "repositoryInventory" in source:
            context["repositoryInventory"] = _paths(
                "repository inventory", source["repositoryInventory"], limit=10_000
            )
            break
    for name in (
        "acceptedAt",
        "activeBaseVersion",
        "catalogSnapshotId",
        "classificationPolicyVersion",
        "resolverVersion",
        "rulesetVersion",
    ):
        for source in sources:
            if name in source:
                context[name] = _required_text(name, source[name])
                break
    for source in sources:
        if "activeBaseFence" in source:
            fence = source["activeBaseFence"]
            if not isinstance(fence, int) or isinstance(fence, bool) or fence < 0:
                raise ValueError("active base fence is invalid")
            context["activeBaseFence"] = fence
            break
    for name in ("changedPaths", "removedPaths", "dependencyClosure"):
        for source in sources:
            if name in source:
                context[name] = _paths(name, source[name], limit=10_000)
                break
    for source in sources:
        if "runtimeManifestRefs" in source:
            raw_references = source["runtimeManifestRefs"]
            if not isinstance(raw_references, list) or len(raw_references) > 100:
                raise ValueError("runtime manifest references must be a bounded array")
            context["runtimeManifestRefs"] = [
                validate_artifact_reference(reference) for reference in raw_references
            ]
            break
    return context


class ClassificationStageUseCase:
    def __init__(self, *, policy_version: str) -> None:
        self._policy_version = _required_text("classification policy version", policy_version)

    def execute(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if not isinstance(input_document, Mapping):
            raise ValueError("classification input must be an object")
        if input_document.get("schemaVersion") != "1.0.0":
            raise ValueError("unsupported classification input schema")
        lineage_context = _bounded_context(input_document)
        raw_evidence = input_document.get("classificationEvidence", input_document.get("evidence"))
        if not isinstance(raw_evidence, list):
            raise ValueError("classification evidence must be an array")
        evidence: list[ClassificationEvidence] = []
        for item in raw_evidence:
            if not isinstance(item, Mapping) or set(item) - _EVIDENCE_KEYS:
                raise ValueError("invalid classification evidence item")
            if (
                "class" in item
                and "repositoryClass" in item
                and item["class"] != item["repositoryClass"]
            ):
                raise ValueError("classification evidence class aliases conflict")
            repository_class = item.get("repositoryClass", item.get("class"))
            evidence.append(
                ClassificationEvidence(
                    level=item.get("level"),  # type: ignore[arg-type]
                    source=item.get("source"),  # type: ignore[arg-type]
                    repository_class=repository_class,  # type: ignore[arg-type]
                    ref=item.get("ref"),  # type: ignore[arg-type]
                )
            )
        decision = evaluate_classification(
            lineage_context["repository"],
            evidence,
            self._policy_version,
            context.correlation_id,
        )
        decision_document = {
            "decisionId": decision.decision_id,
            "repoOrPath": decision.repo_or_path,
            "repositoryClass": decision.repository_class,
            "baselineTreatment": decision.baseline_treatment,
            "onChangeTreatment": decision.on_change_treatment,
            "evidenceLevelUsed": decision.evidence_level_used,
            "evidenceRefs": list(decision.evidence_refs),
            "policyVersion": decision.policy_version,
            "status": decision.status,
            "reason": decision.reason,
        }
        return StageExecutionResult(
            artifact_kind="classification-decision",
            schema_version="1.0.0",
            document={
                "schemaVersion": "1.0.0",
                "artifactType": "classification-decision",
                "workflowKind": context.workflow_kind,
                "workflowVersion": context.workflow_version,
                "stageId": context.stage_id,
                "stageName": context.stage_name,
                "commandId": context.command_id,
                "correlationId": context.correlation_id,
                "source": dict(context.input_reference),
                "context": lineage_context,
                "decision": decision_document,
            },
        )


def _path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value.encode()) > 1_024:
        raise ValueError("invalid repository path")
    parsed = PurePosixPath(value)
    if value.startswith("/") or "\\" in value or ".." in parsed.parts or "." == value:
        raise ValueError("repository paths must be normalized and relative")
    return parsed.as_posix()


def _paths(name: str, value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    if len(value) > limit:
        raise ValueError(f"{name} exceeds the scope limit")
    return sorted({_path(item) for item in value})


def _lineage_context(document: Mapping[str, Any]) -> Mapping[str, Any]:
    context = document.get("context")
    if not isinstance(context, Mapping):
        raise ValueError("stage input is missing lineage context")
    return context


def _common_context(context: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for name in ("artifactDigest", "environment", "repository", "system"):
        normalized[name] = _required_text(name, context.get(name))
    if "platform" in context:
        normalized["platform"] = _required_text("platform", context["platform"])
    source = context.get("repositorySource")
    if source is not None:
        normalized["repositorySource"] = validate_artifact_reference(source)
    catalog = context.get("catalogSnapshotRef")
    if catalog is not None:
        normalized["catalogSnapshotRef"] = validate_artifact_reference(catalog)
    for name in (
        "activeBaseVersion",
        "acceptedAt",
        "catalogSnapshotId",
        "classificationPolicyVersion",
        "resolverVersion",
        "rulesetVersion",
    ):
        if name in context:
            normalized[name] = _required_text(name, context[name])
    if "activeBaseFence" in context:
        fence = context["activeBaseFence"]
        if not isinstance(fence, int) or isinstance(fence, bool) or fence < 0:
            raise ValueError("active base fence is invalid")
        normalized["activeBaseFence"] = fence
    for name in ("changedPaths", "removedPaths", "dependencyClosure"):
        if name in context:
            normalized[name] = _paths(name, context[name], limit=10_000)
    if "repositoryInventory" in context:
        normalized["repositoryInventory"] = _paths(
            "repository inventory", context["repositoryInventory"], limit=10_000
        )
    if "runtimeManifestRefs" in context:
        raw_references = context["runtimeManifestRefs"]
        if not isinstance(raw_references, list) or len(raw_references) > 100:
            raise ValueError("runtime manifest references must be a bounded array")
        normalized["runtimeManifestRefs"] = [
            validate_artifact_reference(reference) for reference in raw_references
        ]
    return normalized


class CoverageStageUseCase:
    def __init__(
        self,
        artifacts: ArtifactStorePort,
        *,
        max_scope: int = 10_000,
        chunk_size: int = 100,
    ) -> None:
        if not 1 <= max_scope <= 10_000:
            raise ValueError("coverage max scope must be between 1 and 10000")
        if not 1 <= chunk_size <= min(max_scope, 1_000):
            raise ValueError("coverage chunk size is outside its bound")
        self._artifacts = artifacts
        self._max_scope = max_scope
        self._chunk_size = chunk_size

    def execute(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if not isinstance(input_document, Mapping):
            raise ValueError("coverage input must be an object")
        if input_document.get("schemaVersion") != "1.0.0":
            raise ValueError("unsupported coverage input schema")
        lineage_context = _lineage_context(input_document)
        common = _common_context(lineage_context)
        if context.workflow_kind == "BASELINE" and context.stage_id == "B4":
            return self._baseline(input_document, lineage_context, common, context)
        if context.workflow_kind == "INCREMENTAL" and context.stage_id == "I3":
            return self._incremental(lineage_context, common, context)
        raise ValueError("coverage use case received an unsupported stage")

    def _baseline(
        self,
        document: Mapping[str, Any],
        lineage_context: Mapping[str, Any],
        common: dict[str, Any],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        decision = document.get("decision")
        if not isinstance(decision, Mapping) or decision.get("status") != "EVALUATED":
            raise ValueError("baseline coverage requires an evaluated classification")
        if "repositorySource" not in common:
            raise ValueError("baseline coverage requires an immutable repository source")
        for name in (
            "platform",
            "catalogSnapshotRef",
            "catalogSnapshotId",
            "resolverVersion",
            "rulesetVersion",
            "activeBaseVersion",
            "activeBaseFence",
            "acceptedAt",
        ):
            if name not in common:
                raise ValueError(f"baseline coverage requires pinned {name}")
        inventory = _paths(
            "repository inventory",
            lineage_context.get("repositoryInventory"),
            limit=self._max_scope,
        )
        recomputed = [path for path in inventory if path.endswith(".py")]
        skipped = [
            path
            for path in inventory
            if path.endswith(".md") or path in _SKIPPED_BASELINE_FILES
        ]
        unsupported = sorted(set(inventory) - set(recomputed) - set(skipped))
        coverage = {
            "expectedScope": inventory,
            "recomputedScope": recomputed,
            "skippedScope": skipped,
            "unsupportedScope": unsupported,
        }
        plan = {
            "schemaVersion": "1.0.0",
            "artifactType": "coverage-plan",
            "workflowKind": context.workflow_kind,
            "workflowVersion": context.workflow_version,
            "stageId": context.stage_id,
            "commandId": context.command_id,
            "correlationId": context.correlation_id,
            "classificationDecision": dict(context.input_reference),
            "context": common,
            "coverage": coverage,
        }
        plan_reference = validate_artifact_reference(
            self._artifacts.put(
                "coverage-plan",
                f"commands/{context.command_id}/coverage/{context.stage_id}.json",
                plan,
                "1.0.0",
            )
        )
        inventory_references: list[dict[str, Any]] = []
        for index in range(0, len(recomputed), self._chunk_size):
            paths = recomputed[index : index + self._chunk_size]
            work_index = index // self._chunk_size
            work_unit = {
                "schemaVersion": "1.0.0",
                "artifactType": "sca-work-unit",
                "workUnitId": f"{context.command_id}-B5-{work_index:05d}",
                "repository": common["repository"],
                "artifactDigest": common["artifactDigest"],
                "environment": common["environment"],
                "platform": common["platform"],
                "system": common["system"],
                "pack": "python-ast",
                "paths": paths,
                "repositorySource": common["repositorySource"],
                "catalogSnapshotRef": common["catalogSnapshotRef"],
                "catalogSnapshotId": common["catalogSnapshotId"],
                "resolverVersion": common["resolverVersion"],
                "rulesetVersion": common["rulesetVersion"],
                "activeBaseVersion": common["activeBaseVersion"],
                "activeBaseFence": common["activeBaseFence"],
                "acceptedAt": common["acceptedAt"],
                "runtimeManifestRefs": common.get("runtimeManifestRefs", []),
                "coverage": {
                    "expectedScope": paths,
                    "completedScope": [],
                    "reusedScope": [],
                    "skippedScope": [],
                    "unsupportedScope": [],
                    "quarantinedScope": [],
                    "failedScope": [],
                },
                "coveragePlan": plan_reference,
                "correlationId": context.correlation_id,
            }
            inventory_references.append(
                validate_artifact_reference(
                    self._artifacts.put(
                        "sca-work-unit",
                        (
                            f"commands/{context.command_id}/work-units/"
                            f"{work_index:05d}.json"
                        ),
                        work_unit,
                        "1.0.0",
                    )
                )
            )
        return StageExecutionResult(
            artifact_kind="work-inventory",
            schema_version="1.0.0",
            document={
                "schemaVersion": "1.0.0",
                "artifactType": "work-inventory",
                "workflowKind": context.workflow_kind,
                "workflowVersion": context.workflow_version,
                "stageId": context.stage_id,
                "stageName": context.stage_name,
                "commandId": context.command_id,
                "correlationId": context.correlation_id,
                "source": dict(context.input_reference),
                "context": common,
                "coveragePlanRef": plan_reference,
                "workUnitRefs": inventory_references,
            },
        )

    def _incremental(
        self,
        lineage_context: Mapping[str, Any],
        common: dict[str, Any],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        changed = _paths(
            "changed paths", lineage_context.get("changedPaths"), limit=self._max_scope
        )
        removed = _paths(
            "removed paths", lineage_context.get("removedPaths", []), limit=self._max_scope
        )
        expected = sorted(set(changed) | set(removed))
        if len(expected) > self._max_scope:
            raise ValueError("differential coverage exceeds the scope limit")
        recomputed = [path for path in changed if path.endswith(".py")]
        unsupported = sorted(set(changed) - set(recomputed))
        return StageExecutionResult(
            artifact_kind="coverage-plan",
            schema_version="1.0.0",
            document={
                "schemaVersion": "1.0.0",
                "artifactType": "coverage-plan",
                "workflowKind": context.workflow_kind,
                "workflowVersion": context.workflow_version,
                "stageId": context.stage_id,
                "stageName": context.stage_name,
                "commandId": context.command_id,
                "correlationId": context.correlation_id,
                "source": dict(context.input_reference),
                "context": common,
                "coverage": {
                    "expectedScope": expected,
                    "recomputedScope": recomputed,
                    "removedScope": removed,
                    "reusedScope": [],
                    "unsupportedScope": unsupported,
                },
            },
        )


class ControlStageUseCase:
    def __init__(
        self, artifacts: ArtifactStorePort, control: PublicationControlPort
    ) -> None:
        self._artifacts = artifacts
        self._control = control

    def execute(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if context.workflow_kind == "BASELINE" and context.stage_id == "B1":
            return self._pin_intent(input_document, context, baseline=True)
        if context.workflow_kind == "BASELINE" and context.stage_id == "B2":
            return self._baseline_pins(input_document, context)
        if context.workflow_kind == "INCREMENTAL" and context.stage_id == "I1":
            return self._pin_intent(input_document, context, baseline=False)
        if context.workflow_kind == "INCREMENTAL" and context.stage_id == "I2":
            return self._changed_scope(input_document, context)
        if context.workflow_kind == "INCREMENTAL" and context.stage_id == "I4":
            return self._immutable_inputs(input_document, context)
        if context.workflow_kind == "INCREMENTAL" and context.stage_id == "I8":
            return self._recheck(input_document, context)
        raise ValueError("control use case received an unsupported stage")

    def _pin_intent(
        self,
        input_document: object,
        context: StageExecutionContext,
        *,
        baseline: bool,
    ) -> StageExecutionResult:
        if (
            not isinstance(input_document, Mapping)
            or input_document.get("schemaVersion") != "1.0.0"
            or input_document.get("artifactType") != "lineage-intent"
        ):
            raise ValueError("control intake requires a versioned lineage intent")
        common = _bounded_context(input_document)
        accepted_at = _accepted_at(common.get("acceptedAt"))
        pointer = self._control.active_pointer(common["environment"])
        graph_version = _required_text(
            "active base version", pointer.get("graphVersion")
        )
        fence = pointer.get("fence")
        if not isinstance(fence, int) or isinstance(fence, bool) or fence < 0:
            raise ValueError("active base fence is invalid")
        common.update(
            acceptedAt=accepted_at,
            activeBaseVersion=graph_version,
            activeBaseFence=fence,
        )
        evidence = input_document.get("classificationEvidence")
        if not isinstance(evidence, list) or len(evidence) > 100:
            raise ValueError("classification evidence must be a bounded array")
        kind = "baseline-intent" if baseline else "incremental-base"
        return self._result(
            context,
            kind,
            common,
            {
                "classificationEvidence": list(evidence),
                "pointer": pointer,
            },
        )

    def _baseline_pins(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        document, common = self._prior(input_document, "baseline-intent")
        pins = {
            name: _required_text(name, common.get(name))
            for name in (
                "catalogSnapshotId",
                "classificationPolicyVersion",
                "resolverVersion",
                "rulesetVersion",
            )
        }
        catalog_reference = common.get("catalogSnapshotRef")
        if catalog_reference is None:
            raise ValueError("baseline pins require an immutable catalog snapshot")
        pins["catalogSnapshotRef"] = catalog_reference
        if "repositorySource" not in common:
            raise ValueError("baseline pins require an immutable repository source")
        return self._result(
            context,
            "baseline-pins",
            common,
            {
                "classificationEvidence": document.get("classificationEvidence"),
                "pins": dict(sorted(pins.items())),
            },
        )

    def _changed_scope(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        document, common = self._prior(input_document, "incremental-base")
        changed = set(common.get("changedPaths", []))
        closure = set(common.get("dependencyClosure", []))
        removed = set(common.get("removedPaths", []))
        recomputed = sorted((changed | closure) - removed)
        if len(recomputed) + len(removed) > 10_000:
            raise ValueError("incremental closure exceeds the scope limit")
        common.update(
            changedPaths=recomputed,
            removedPaths=sorted(removed),
            dependencyClosure=sorted(closure),
        )
        return self._result(
            context,
            "incremental-scope",
            common,
            {"classificationEvidence": document.get("classificationEvidence")},
        )

    def _immutable_inputs(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if (
            not isinstance(input_document, Mapping)
            or input_document.get("schemaVersion") != "1.0.0"
            or input_document.get("artifactType") != "coverage-plan"
        ):
            raise ValueError("I4 requires the exact differential coverage plan")
        common = _common_context(_lineage_context(input_document))
        source = common.get("repositorySource")
        if source is None:
            raise ValueError("I4 requires an immutable repository source")
        for name in (
            "platform",
            "catalogSnapshotRef",
            "catalogSnapshotId",
            "resolverVersion",
            "rulesetVersion",
            "activeBaseVersion",
            "activeBaseFence",
            "acceptedAt",
        ):
            if name not in common:
                raise ValueError(f"I4 requires pinned {name}")
        coverage = input_document.get("coverage")
        if not isinstance(coverage, Mapping):
            raise ValueError("I4 requires coverage accounting")
        expected = _paths(
            "I4 expected scope", coverage.get("expectedScope"), limit=10_000
        )
        recomputed = _paths(
            "I4 recomputed scope", coverage.get("recomputedScope"), limit=10_000
        )
        removed = _paths(
            "I4 removed scope", coverage.get("removedScope", []), limit=10_000
        )
        reused = _paths(
            "I4 reused scope", coverage.get("reusedScope", []), limit=10_000
        )
        unsupported = _paths(
            "I4 unsupported scope",
            coverage.get("unsupportedScope", []),
            limit=10_000,
        )
        if expected != sorted(set(recomputed + removed + reused + unsupported)):
            raise ValueError("I4 coverage accounting is incomplete")
        work_coverage = {
            "expectedScope": expected,
            "completedScope": [],
            "reusedScope": reused,
            "skippedScope": removed,
            "unsupportedScope": unsupported,
            "quarantinedScope": [],
            "failedScope": [],
        }
        return StageExecutionResult(
            artifact_kind="sca-work-unit",
            schema_version="1.0.0",
            document={
                "schemaVersion": "1.0.0",
                "artifactType": "sca-work-unit",
                "workflowKind": context.workflow_kind,
                "workflowVersion": context.workflow_version,
                "stageId": context.stage_id,
                "stageName": context.stage_name,
                "commandId": context.command_id,
                "correlationId": context.correlation_id,
                "source": dict(context.input_reference),
                "context": common,
                "workUnitId": f"{context.command_id}-I5-00000",
                "repository": common["repository"],
                "artifactDigest": common["artifactDigest"],
                "environment": common["environment"],
                "platform": common["platform"],
                "system": common["system"],
                "pack": "python-ast",
                "paths": recomputed,
                "repositorySource": source,
                "catalogSnapshotRef": common["catalogSnapshotRef"],
                "catalogSnapshotId": common["catalogSnapshotId"],
                "resolverVersion": common["resolverVersion"],
                "rulesetVersion": common["rulesetVersion"],
                "activeBaseVersion": common["activeBaseVersion"],
                "activeBaseFence": common["activeBaseFence"],
                "acceptedAt": common["acceptedAt"],
                "runtimeManifestRefs": common.get("runtimeManifestRefs", []),
                "coverage": work_coverage,
                "coveragePlan": dict(context.input_reference),
                "immutableInputs": {
                    "repositorySource": source,
                    "catalogSnapshotRef": common["catalogSnapshotRef"],
                },
            },
        )

    def _recheck(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if (
            not isinstance(input_document, Mapping)
            or input_document.get("schemaVersion") != "1.0.0"
            or input_document.get("artifactType") != "consolidation-result"
        ):
            raise ValueError("I8 requires the exact consolidation result")
        common = _common_context(_lineage_context(input_document))
        pointer = self._control.active_pointer(common["environment"])
        expected_version = _required_text(
            "pinned active base", common.get("activeBaseVersion")
        )
        expected_fence = common.get("activeBaseFence")
        if (
            pointer.get("graphVersion") != expected_version
            or pointer.get("fence") != expected_fence
        ):
            raise ValueError("active base changed before proposal creation")
        coverage = input_document.get("coverage")
        if not isinstance(coverage, Mapping) or coverage.get("state") not in {
            "COMPLETE",
            "INCOMPLETE",
        }:
            raise ValueError("I8 coverage state is invalid")
        document = dict(input_document)
        document.update(
            workflowKind=context.workflow_kind,
            workflowVersion=context.workflow_version,
            stageId=context.stage_id,
            stageName=context.stage_name,
            commandId=context.command_id,
            correlationId=context.correlation_id,
            source=dict(context.input_reference),
            context=common,
            baseRecheck={
                "activeBaseVersion": expected_version,
                "activeBaseFence": expected_fence,
                "coverageState": coverage["state"],
                "status": "VERIFIED",
            },
        )
        return StageExecutionResult(
            artifact_kind="incremental-recheck",
            schema_version="1.0.0",
            document=document,
        )

    @staticmethod
    def _prior(
        input_document: object, artifact_type: str
    ) -> tuple[Mapping[str, Any], dict[str, Any]]:
        if (
            not isinstance(input_document, Mapping)
            or input_document.get("schemaVersion") != "1.0.0"
            or input_document.get("artifactType") != artifact_type
        ):
            raise ValueError(f"control stage requires {artifact_type}")
        return input_document, _common_context(_lineage_context(input_document))

    @staticmethod
    def _result(
        context: StageExecutionContext,
        artifact_type: str,
        common: dict[str, Any],
        additions: Mapping[str, Any],
    ) -> StageExecutionResult:
        return StageExecutionResult(
            artifact_kind=artifact_type,
            schema_version="1.0.0",
            document={
                "schemaVersion": "1.0.0",
                "artifactType": artifact_type,
                "workflowKind": context.workflow_kind,
                "workflowVersion": context.workflow_version,
                "stageId": context.stage_id,
                "stageName": context.stage_name,
                "commandId": context.command_id,
                "correlationId": context.correlation_id,
                "source": dict(context.input_reference),
                "context": common,
                **dict(additions),
            },
        )


class RuntimeValidationStageUseCase:
    def __init__(self, artifacts: ArtifactStorePort, *, max_manifests: int = 100) -> None:
        if not 1 <= max_manifests <= 1_000:
            raise ValueError("runtime manifest limit must be between 1 and 1000")
        self._artifacts = artifacts
        self._max_manifests = max_manifests

    def execute(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if not isinstance(input_document, Mapping):
            raise ValueError("runtime validation input must be an object")
        if input_document.get("schemaVersion") != "1.0.0":
            raise ValueError("unsupported runtime validation input schema")
        lineage_context = _lineage_context(input_document)
        common = _common_context(lineage_context)
        if "assertionRefs" in lineage_context:
            common["assertionRefs"] = _references(
                "assertionRefs", lineage_context["assertionRefs"], limit=1_000
            )
        if "residueRefs" in lineage_context:
            common["residueRefs"] = _references(
                "residueRefs", lineage_context["residueRefs"], limit=1_000
            )
        if "coverage" in lineage_context:
            common["coverage"] = _coverage(
                lineage_context["coverage"], max_scope=10_000
            )
        if "tombstoneEdgeIds" in lineage_context:
            common["tombstoneEdgeIds"] = _edge_ids(
                "tombstone edge IDs",
                lineage_context["tombstoneEdgeIds"],
                limit=10_000,
            )
        raw_references = lineage_context.get("runtimeManifestRefs", [])
        if not isinstance(raw_references, list):
            raise ValueError("runtime manifest references must be an array")
        if len(raw_references) > self._max_manifests:
            raise ValueError("runtime manifest references exceed the limit")
        references_by_identity: dict[tuple[object, ...], dict[str, Any]] = {}
        for raw_reference in raw_references:
            reference = validate_artifact_reference(raw_reference)
            identity = tuple(reference[name] for name in sorted(reference))
            references_by_identity[identity] = reference
        references = sorted(
            references_by_identity.values(), key=lambda item: (item["bucket"], item["key"], item["versionId"])
        )

        status = "NOT_PROVIDED"
        reasons: set[str] = {"NO_RUNTIME_MANIFESTS"} if not references else set()
        complete: list[tuple[dict[str, Any], Mapping[str, Any]]] = []
        incomplete = False
        quarantined = False
        for reference in references:
            manifest = self._artifacts.get(reference)
            errors = runtime_window_manifest_errors(manifest)
            if errors or not isinstance(manifest, Mapping):
                quarantined = True
                reasons.add("INVALID_RUNTIME_MANIFEST")
                continue
            mismatch = None
            for field, expected in (
                ("repo", common["repository"]),
                ("environment", common["environment"]),
                ("artifactDigest", common["artifactDigest"]),
            ):
                if manifest[field] != expected:
                    mismatch = f"{field.replace('artifactDigest', 'artifact_digest').upper()}_MISMATCH"
                    break
            if mismatch is not None:
                quarantined = True
                reasons.add(mismatch)
                continue
            if manifest["outcome"] != "COMPLETE":
                incomplete = True
                reasons.update(str(reason) for reason in manifest["reasons"])
                continue
            complete.append((reference, manifest))

        if quarantined:
            status = "QUARANTINED"
        elif incomplete:
            status = "INCOMPLETE"
        elif references:
            status = "VALIDATED"
        trusted = complete if status == "VALIDATED" else []
        coverage = {
            "status": status,
            "windowIds": sorted(str(manifest["windowId"]) for _, manifest in trusted),
            "mechanisms": sorted({str(manifest["mechanism"]) for _, manifest in trusted}),
            "manifestRefs": [reference for reference, _ in trusted],
            "observationChecksums": sorted(
                str(manifest["observationChecksum"]) for _, manifest in trusted
            ),
            "accepted": sum(int(manifest["accepted"]) for _, manifest in trusted),
            "drained": sum(int(manifest["drained"]) for _, manifest in trusted),
            "reasons": sorted(reasons),
        }
        return StageExecutionResult(
            artifact_kind="runtime-validation",
            schema_version="1.0.0",
            document={
                "schemaVersion": "1.0.0",
                "artifactType": "runtime-validation",
                "workflowKind": context.workflow_kind,
                "workflowVersion": context.workflow_version,
                "stageId": context.stage_id,
                "stageName": context.stage_name,
                "commandId": context.command_id,
                "correlationId": context.correlation_id,
                "source": dict(context.input_reference),
                "context": common,
                "runtimeCoverage": coverage,
            },
        )


_ASSERTION_REQUIRED = frozenset(
    {
        "provenanceId",
        "from",
        "to",
        "edgeType",
        "mechanism",
        "exact",
        "evidenceRef",
        "repo",
        "runId",
        "correlationId",
        "sessionComplete",
    }
)
_ASSERTION_OPTIONAL = frozenset({"transform", "citation", "runtimeScope"})
_COVERAGE_FIELDS = (
    "completedScope",
    "reusedScope",
    "skippedScope",
    "unsupportedScope",
    "quarantinedScope",
    "failedScope",
)


def _references(name: str, value: object, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{name} must be a bounded reference array")
    by_identity: dict[tuple[object, ...], dict[str, Any]] = {}
    for item in value:
        reference = validate_artifact_reference(item)
        identity = tuple(reference[key] for key in sorted(reference))
        by_identity[identity] = reference
    return sorted(
        by_identity.values(), key=lambda item: (item["bucket"], item["key"], item["versionId"])
    )


def _assertion_document(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("lineage assertion must be an object")
    if not _ASSERTION_REQUIRED <= set(value) or set(value) - (
        _ASSERTION_REQUIRED | _ASSERTION_OPTIONAL
    ):
        raise ValueError("lineage assertion has a drifted schema")
    provenance_id = _required_text("provenance ID", value["provenanceId"])
    raw_sources = value["from"]
    if not isinstance(raw_sources, list) or not 1 <= len(raw_sources) <= 64:
        raise ValueError("lineage assertion sources are invalid")
    sources = sorted({_required_text("source URN", item) for item in raw_sources})
    target = _required_text("target URN", value["to"])
    for urn in (*sources, target):
        LineageUrn.parse(urn)
    edge_type = _required_text("edge type", value["edgeType"])
    if edge_type not in {"DERIVES", "READS", "WRITES", "CONNECTS"}:
        raise ValueError("unsupported lineage edge type")
    mechanism = _required_text("mechanism", value["mechanism"])
    if mechanism not in {"SCA", "LLM", "RUNTIME"}:
        raise ValueError("unsupported lineage mechanism")
    exact = value["exact"]
    session_complete = value["sessionComplete"]
    if not isinstance(exact, bool) or not isinstance(session_complete, bool):
        raise ValueError("lineage assertion flags must be boolean")
    runtime_scope = value.get("runtimeScope")
    if mechanism == "RUNTIME" and runtime_scope not in {"DATASET", "ELEMENT"}:
        raise ValueError("runtime assertion scope is invalid")
    if mechanism != "RUNTIME" and runtime_scope is not None:
        raise ValueError("only runtime assertions may carry runtime scope")
    evidence_ref = validate_artifact_reference(value["evidenceRef"])
    transform = value.get("transform")
    if transform is not None:
        transform = _required_text("transform", transform)
    normalized = {
        "provenanceId": provenance_id,
        "from": sources,
        "to": target,
        "edgeType": edge_type,
        "mechanism": mechanism,
        "exact": exact,
        "evidenceRef": evidence_ref,
        "repo": _required_text("assertion repository", value["repo"]),
        "runId": _required_text("assertion run ID", value["runId"]),
        "correlationId": _required_text("assertion correlation ID", value["correlationId"]),
        "sessionComplete": session_complete,
    }
    if transform is not None:
        normalized["transform"] = transform
    if runtime_scope is not None:
        normalized["runtimeScope"] = runtime_scope
    if "citation" in value:
        citation = value["citation"]
        if not isinstance(citation, Mapping) or set(citation) != {
            "file",
            "line",
            "astPath",
        }:
            raise ValueError("assertion citation schema is invalid")
        line = citation["line"]
        if not isinstance(line, int) or isinstance(line, bool) or not 1 <= line <= 10_000_000:
            raise ValueError("assertion citation line is invalid")
        normalized["citation"] = {
            "file": _path(citation["file"]),
            "line": line,
            "astPath": _required_text("citation AST path", citation["astPath"]),
        }
    return normalized


def _coverage(value: object, *, max_scope: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("consolidation requires coverage accounting")
    expected = _paths("expected coverage", value.get("expectedScope"), limit=max_scope)
    accounted: list[str] = []
    result: dict[str, Any] = {"expectedScope": expected}
    for field in _COVERAGE_FIELDS:
        paths = _paths(field, value.get(field, []), limit=max_scope)
        result[field] = paths
        accounted.extend(paths)
    exact = Counter(expected) == Counter(accounted)
    complete = exact and not any(
        result[field] for field in ("unsupportedScope", "quarantinedScope", "failedScope")
    )
    result["state"] = "COMPLETE" if complete else "INCOMPLETE"
    if not exact:
        result["reason"] = "SCOPE_ACCOUNTING_MISMATCH"
    return result


class ConsolidationStageUseCase:
    def __init__(
        self,
        artifacts: ArtifactStorePort,
        *,
        max_assertion_refs: int = 1_000,
        max_assertions: int = 10_000,
        max_scope: int = 10_000,
    ) -> None:
        if not 1 <= max_assertion_refs <= 1_000:
            raise ValueError("assertion reference limit is invalid")
        if not 1 <= max_assertions <= 10_000:
            raise ValueError("assertion limit is invalid")
        if not 1 <= max_scope <= 10_000:
            raise ValueError("consolidation scope limit is invalid")
        self._artifacts = artifacts
        self._max_assertion_refs = max_assertion_refs
        self._max_assertions = max_assertions
        self._max_scope = max_scope

    def execute(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if not isinstance(input_document, Mapping):
            raise ValueError("consolidation input must be an object")
        if input_document.get("schemaVersion") != "1.0.0":
            raise ValueError("unsupported consolidation input schema")
        lineage_context = _lineage_context(input_document)
        common = _common_context(lineage_context)
        assertion_refs = _references(
            "assertionRefs",
            lineage_context.get("assertionRefs", []),
            limit=self._max_assertion_refs,
        )
        coverage = _coverage(
            lineage_context.get("coverage"), max_scope=self._max_scope
        )
        if context.workflow_kind == "BASELINE" and context.stage_id == "B7":
            residue_refs = _references(
                "residueRefs", lineage_context.get("residueRefs", []), limit=1_000
            )
            return StageExecutionResult(
                artifact_kind="residue-decision",
                schema_version="1.0.0",
                document={
                    "schemaVersion": "1.0.0",
                    "artifactType": "residue-decision",
                    "workflowKind": context.workflow_kind,
                    "workflowVersion": context.workflow_version,
                    "stageId": context.stage_id,
                    "stageName": context.stage_name,
                    "commandId": context.command_id,
                    "correlationId": context.correlation_id,
                    "source": dict(context.input_reference),
                    "context": {
                        **common,
                        "assertionRefs": assertion_refs,
                        "coverage": coverage,
                        "residueRefs": residue_refs,
                    },
                    "residue": {
                        "status": "SKIPPED_WITH_RECORD",
                        "count": len(residue_refs),
                        "reason": "LLM_NOT_CONFIGURED",
                    },
                },
            )

        assertions: list[dict[str, Any]] = []
        for reference in assertion_refs:
            body = self._artifacts.get(reference)
            if (
                not isinstance(body, Mapping)
                or body.get("schemaVersion") != "1.0.0"
                or not isinstance(body.get("assertions"), list)
            ):
                raise ValueError("invalid assertion-set artifact")
            if len(assertions) + len(body["assertions"]) > self._max_assertions:
                raise ValueError("assertions exceed the consolidation limit")
            assertions.extend(_assertion_document(item) for item in body["assertions"])

        for assertion in assertions:
            urns = [
                LineageUrn.parse(value)
                for value in (*assertion["from"], assertion["to"])
            ]
            target = urns[-1]
            if (
                assertion["repo"] != common["repository"]
                or target.system != common["system"]
                or any(urn.env != common["environment"] for urn in urns)
            ):
                raise ValueError("assertion scope mismatch")

        by_provenance: dict[str, str] = {}
        unique_assertions: list[dict[str, Any]] = []
        for assertion in assertions:
            canonical = json.dumps(assertion, sort_keys=True, separators=(",", ":"))
            provenance_id = assertion["provenanceId"]
            existing = by_provenance.get(provenance_id)
            if existing is not None and existing != canonical:
                raise ValueError(f"provenance identity conflict: {provenance_id}")
            if existing is None:
                by_provenance[provenance_id] = canonical
                unique_assertions.append(assertion)

        grouped: dict[str, list[dict[str, Any]]] = {}
        for assertion in unique_assertions:
            edge_id = edge_key_for(
                assertion["from"], assertion["to"], assertion["edgeType"]
            )
            grouped.setdefault(edge_id, []).append(assertion)
        edges: list[dict[str, Any]] = []
        for edge_id, provenance in sorted(grouped.items()):
            ordered = sorted(provenance, key=lambda item: item["provenanceId"])
            decision = derive_consolidation(ordered)
            first = ordered[0]
            edge = {
                "schemaVersion": "1.0.0",
                "edgeKey": edge_id,
                "version": 1,
                "from": first["from"],
                "to": first["to"],
                "edgeType": first["edgeType"],
                "band": decision.band,
                "corroboration": decision.corroboration,
                "status": decision.status,
                "provenance": ordered,
                "autoPublishable": decision.auto_publishable,
                "system": LineageUrn.parse(first["to"]).system,
            }
            if decision.transform is not None:
                edge["transform"] = decision.transform
            edges.append(edge)
        edge_set = {
            "schemaVersion": "1.0.0",
            "artifactType": "consolidated-edge-set",
            "commandId": context.command_id,
            "correlationId": context.correlation_id,
            "context": common,
            "edges": edges,
        }
        if len(
            json.dumps(edge_set, sort_keys=True, separators=(",", ":")).encode()
        ) > MAX_STAGE_DOCUMENT_BYTES:
            raise ValueError("consolidated edge set exceeds the size limit")
        edge_set_ref = validate_artifact_reference(
            self._artifacts.put(
                "consolidated-edge-set",
                f"commands/{context.command_id}/edge-sets/{context.stage_id}.json",
                edge_set,
                "1.0.0",
            )
        )
        tombstones = (
            _paths(
                "tombstone edge IDs",
                lineage_context.get("tombstoneEdgeIds", []),
                limit=self._max_assertions,
            )
            if context.workflow_kind == "INCREMENTAL" and context.stage_id == "I7"
            else []
        )
        bands = Counter(edge["band"] for edge in edges)
        return StageExecutionResult(
            artifact_kind="consolidation-result",
            schema_version="1.0.0",
            document={
                "schemaVersion": "1.0.0",
                "artifactType": "consolidation-result",
                "workflowKind": context.workflow_kind,
                "workflowVersion": context.workflow_version,
                "stageId": context.stage_id,
                "stageName": context.stage_name,
                "commandId": context.command_id,
                "correlationId": context.correlation_id,
                "source": dict(context.input_reference),
                "context": common,
                "edgeSetRef": edge_set_ref,
                "edgeIds": [edge["edgeKey"] for edge in edges],
                "edgeCount": len(edges),
                "bands": dict(sorted(bands.items())),
                "coverage": coverage,
                "tombstoneEdgeIds": tombstones,
            },
        )


_PROPOSAL_TYPES = {
    ("BASELINE", "B9"): "BASELINE",
    ("INCREMENTAL", "I9"): "DELTA",
    ("NIGHTLY", "N6"): "RECONCILIATION",
}
_MAX_PROPOSAL_DOCUMENT_BYTES = 350_000


def _edge_ids(name: str, value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{name} must be a bounded array")
    normalized = [_required_text(name, item) for item in value]
    if any(len(item.encode()) > 256 for item in normalized):
        raise ValueError(f"{name} contains an oversized identity")
    if normalized != sorted(set(normalized)):
        raise ValueError(f"{name} must contain sorted unique identities")
    return normalized


def _accepted_at(value: object) -> str:
    text = _required_text("acceptedAt", value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("acceptedAt must be an RFC3339 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("acceptedAt must include a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ProposalStageUseCase:
    def __init__(
        self,
        artifacts: ArtifactStorePort,
        proposals: ProposalStorePort,
        *,
        max_edge_ids: int = 5_000,
    ) -> None:
        if not 1 <= max_edge_ids <= 5_000:
            raise ValueError("proposal edge ID limit is invalid")
        self._artifacts = artifacts
        self._proposals = proposals
        self._max_edge_ids = max_edge_ids

    def execute(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if not isinstance(input_document, Mapping):
            raise ValueError("proposal input must be an object")
        if (
            input_document.get("schemaVersion") != "1.0.0"
            or input_document.get("artifactType") != "consolidation-result"
        ):
            raise ValueError("proposal input must be a versioned consolidation result")
        try:
            proposal_type = _PROPOSAL_TYPES[(context.workflow_kind, context.stage_id)]
        except KeyError as error:
            raise ValueError("proposal use case received an unsupported stage") from error

        lineage_context = _lineage_context(input_document)
        common = _common_context(lineage_context)
        coverage = input_document.get("coverage")
        if not isinstance(coverage, Mapping):
            raise ValueError("proposal input is missing coverage state")
        coverage_state = coverage.get("state")
        if coverage_state not in {"COMPLETE", "INCOMPLETE"}:
            raise ValueError("proposal coverage state is invalid")
        if coverage_state != "COMPLETE":
            return self._decision(context, common, "INCOMPLETE_COVERAGE", None)

        edge_count = input_document.get("edgeCount")
        if not isinstance(edge_count, int) or isinstance(edge_count, bool) or edge_count < 0:
            raise ValueError("proposal edge count is invalid")
        edge_ids = _edge_ids(
            "proposal edge IDs", input_document.get("edgeIds"), limit=self._max_edge_ids
        )
        if edge_count != len(edge_ids):
            raise ValueError("proposal edge count does not match its identities")
        if edge_count == 0:
            return self._decision(context, common, "NO_LINEAGE", None)

        edge_set_ref = validate_artifact_reference(input_document.get("edgeSetRef"))
        edge_set = self._artifacts.get(edge_set_ref)
        if (
            not isinstance(edge_set, Mapping)
            or edge_set.get("schemaVersion") != "1.0.0"
            or edge_set.get("artifactType") != "consolidated-edge-set"
            or not isinstance(edge_set.get("edges"), list)
            or len(edge_set["edges"]) > self._max_edge_ids
        ):
            raise ValueError("proposal edge set is invalid")
        stored_edge_ids: list[str] = []
        for edge in edge_set["edges"]:
            if not isinstance(edge, Mapping):
                raise ValueError("proposal edge set contains an invalid edge")
            edge_id = _required_text("stored edge ID", edge.get("edgeKey"))
            if len(edge_id.encode()) > 256:
                raise ValueError("stored edge ID is oversized")
            if edge.get("system") != common["system"]:
                raise ValueError("proposal edge set crosses system boundaries")
            stored_edge_ids.append(edge_id)
        if stored_edge_ids != sorted(set(stored_edge_ids)) or stored_edge_ids != edge_ids:
            raise ValueError("proposal edge set identities do not match consolidation")

        tombstones = _edge_ids(
            "proposal tombstone edge IDs",
            input_document.get("tombstoneEdgeIds", []),
            limit=self._max_edge_ids,
        )
        expected_base = _required_text(
            "activeBaseVersion", common.get("activeBaseVersion")
        )
        created_at = _accepted_at(common.get("acceptedAt"))
        identity = {
            "proposalType": proposal_type,
            "system": common["system"],
            "environment": common["environment"],
            "artifactDigest": common["artifactDigest"],
            "edgeSetRef": edge_set_ref,
            "addedEdgeIds": edge_ids,
            "removedEdgeIds": tombstones,
            "expectedBaseVersion": expected_base,
            "correlationId": context.correlation_id,
        }
        proposal_id = "proposal-" + hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        proposal = {
            "schemaVersion": "1.0.0",
            "proposalId": proposal_id,
            "version": 1,
            "proposalType": proposal_type,
            "system": common["system"],
            "environment": common["environment"],
            "state": "IN_REVIEW",
            "expectedBaseVersion": expected_base,
            "diff": {
                "edgeSetRef": edge_set_ref,
                "addedEdgeIds": edge_ids,
                "removedEdgeIds": tombstones,
                "bandChangedEdgeIds": [],
            },
            "correlationId": context.correlation_id,
            "createdAt": created_at,
            "lockVersion": 1,
        }
        if len(json.dumps(proposal, sort_keys=True, separators=(",", ":")).encode()) > (
            _MAX_PROPOSAL_DOCUMENT_BYTES
        ):
            raise ValueError("proposal document exceeds its DynamoDB safety bound")
        persisted = self._proposals.put_proposal(proposal)
        return self._decision(context, common, "PROPOSAL_CREATED", persisted)

    @staticmethod
    def _decision(
        context: StageExecutionContext,
        common: dict[str, Any],
        decision: str,
        proposal: dict[str, Any] | None,
    ) -> StageExecutionResult:
        return StageExecutionResult(
            artifact_kind="proposal-decision",
            schema_version="1.0.0",
            document={
                "schemaVersion": "1.0.0",
                "artifactType": "proposal-decision",
                "workflowKind": context.workflow_kind,
                "workflowVersion": context.workflow_version,
                "stageId": context.stage_id,
                "stageName": context.stage_name,
                "commandId": context.command_id,
                "correlationId": context.correlation_id,
                "source": dict(context.input_reference),
                "context": common,
                "decision": decision,
                "proposal": proposal,
            },
        )


_PROJECTION_ROW_FIELDS = frozenset({"edgeId", "source", "target", "type"})


def _projection_rows(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 100_000:
        raise ValueError("projection rows must be a bounded array")
    by_identity: dict[tuple[str, str, str], dict[str, str]] = {}
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != _PROJECTION_ROW_FIELDS:
            raise ValueError("projection row schema is invalid")
        row = {
            name: _required_text(f"projection {name}", raw[name])
            for name in sorted(_PROJECTION_ROW_FIELDS)
        }
        identity = (row["edgeId"], row["source"], row["target"])
        prior = by_identity.get(identity)
        if prior is not None and prior != row:
            raise ValueError("projection identity conflict")
        by_identity[identity] = row
    return [by_identity[key] for key in sorted(by_identity)]


def _projection_checksum(rows: list[dict[str, str]]) -> str:
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class PublicationStageUseCase:
    def __init__(
        self,
        artifacts: ArtifactStorePort,
        packages: ArtifactStorePort,
        control: PublicationControlPort,
        projection: StageProjectionPort,
        *,
        max_edge_ids: int = 5_000,
    ) -> None:
        if not 1 <= max_edge_ids <= 5_000:
            raise ValueError("publication edge ID limit is invalid")
        self._artifacts = artifacts
        self._packages = packages
        self._control = control
        self._projection = projection
        self._max_edge_ids = max_edge_ids

    def execute(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if context.workflow_kind == "NIGHTLY" and context.stage_id == "N3":
            return self._verify_projection(input_document, context)
        if (context.workflow_kind, context.stage_id) not in {
            ("BASELINE", "B10"),
            ("INCREMENTAL", "I10"),
        }:
            raise ValueError("publication use case received an unsupported stage")
        return self._publish(input_document, context)

    def _publish(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if (
            not isinstance(input_document, Mapping)
            or input_document.get("schemaVersion") != "1.0.0"
            or input_document.get("artifactType") != "proposal-decision"
        ):
            raise ValueError("publication input must be a versioned proposal decision")
        common = _common_context(_lineage_context(input_document))
        decision = input_document.get("decision")
        if decision == "NO_LINEAGE":
            outcome = (
                "NO_LINEAGE"
                if context.workflow_kind == "BASELINE"
                else "NO_LINEAGE_IMPACT"
            )
            return self._receipt(context, common, outcome)
        if decision == "INCOMPLETE_COVERAGE":
            return self._receipt(context, common, "QUARANTINED")
        if decision != "PROPOSAL_CREATED":
            raise ValueError("publication proposal decision is invalid")
        proposal = input_document.get("proposal")
        if not isinstance(proposal, Mapping):
            raise ValueError("publication input is missing its proposal")
        state = proposal.get("state")
        if state == "IN_REVIEW":
            return self._receipt(context, common, "AWAITING_APPROVAL")
        if state == "REJECTED":
            return self._receipt(context, common, "REJECTED")
        if state not in {"APPROVED", "FINALIZED"}:
            raise ValueError("proposal is not publishable")
        if (
            proposal.get("schemaVersion") != "1.0.0"
            or proposal.get("system") != common["system"]
            or proposal.get("environment") != common["environment"]
            or proposal.get("correlationId") != context.correlation_id
        ):
            raise ValueError("approved proposal scope does not match publication")

        proposal_id = _required_text("proposal ID", proposal.get("proposalId"))
        proposal_version = proposal.get("version")
        if (
            not isinstance(proposal_version, int)
            or isinstance(proposal_version, bool)
            or proposal_version < 1
        ):
            raise ValueError("proposal version is invalid")
        expected_prior = _required_text(
            "proposal expected base", proposal.get("expectedBaseVersion")
        )
        approval_ref = validate_artifact_reference(proposal.get("approvalRef"))
        approved_at = _accepted_at(proposal.get("approvedAt"))
        diff = proposal.get("diff")
        if not isinstance(diff, Mapping):
            raise ValueError("approved proposal diff is invalid")
        edge_set_ref = validate_artifact_reference(diff.get("edgeSetRef"))
        added = _edge_ids(
            "published edge IDs", diff.get("addedEdgeIds"), limit=self._max_edge_ids
        )
        changed = _edge_ids(
            "band-changed edge IDs",
            diff.get("bandChangedEdgeIds", []),
            limit=self._max_edge_ids,
        )
        removed = _edge_ids(
            "removed edge IDs", diff.get("removedEdgeIds", []), limit=self._max_edge_ids
        )
        write_ids = sorted(set(added) | set(changed))
        if set(write_ids) & set(removed):
            raise ValueError("publication diff both writes and removes an edge")

        edge_set = self._artifacts.get(edge_set_ref)
        if (
            not isinstance(edge_set, Mapping)
            or edge_set.get("schemaVersion") != "1.0.0"
            or edge_set.get("artifactType") != "consolidated-edge-set"
            or not isinstance(edge_set.get("edges"), list)
            or len(edge_set["edges"]) > self._max_edge_ids
        ):
            raise ValueError("publication edge set is invalid")
        rows: list[dict[str, str]] = []
        merge_rows: list[dict[str, str]] = []
        stored_ids: list[str] = []
        for edge in edge_set["edges"]:
            if not isinstance(edge, Mapping) or edge.get("system") != common["system"]:
                raise ValueError("publication edge set crosses system boundaries")
            edge_id = _required_text("publication edge ID", edge.get("edgeKey"))
            sources = edge.get("from")
            if not isinstance(sources, list) or not 1 <= len(sources) <= 64:
                raise ValueError("publication edge sources are invalid")
            target = _required_text("publication edge target", edge.get("to"))
            edge_type = _required_text("publication edge type", edge.get("edgeType"))
            band = _required_text("publication edge band", edge.get("band"))
            corroboration = _required_text(
                "publication edge corroboration", edge.get("corroboration")
            )
            if band not in {"LOWEST", "SINGLE", "MEDIUM", "HIGH", "HIGHEST"}:
                raise ValueError("publication edge band is invalid")
            if corroboration not in {"NONE", "DATASET", "ELEMENT"}:
                raise ValueError("publication edge corroboration is invalid")
            target_urn = LineageUrn.parse(target)
            if (
                target_urn.env != common["environment"]
                or target_urn.system != common["system"]
            ):
                raise ValueError("publication edge target is outside the pinned scope")
            normalized_sources = sorted(
                {_required_text("publication edge source", item) for item in sources}
            )
            for source in normalized_sources:
                if LineageUrn.parse(source).env != common["environment"]:
                    raise ValueError("publication edge source is outside the pinned environment")
                row = {
                    "edgeId": edge_id,
                    "source": source,
                    "target": target,
                    "type": edge_type,
                }
                rows.append(row)
                merge_rows.append(
                    {**row, "band": band, "corroboration": corroboration}
                )
            stored_ids.append(edge_id)
        if stored_ids != sorted(set(stored_ids)) or stored_ids != write_ids:
            raise ValueError("publication edge identities do not match the approved diff")
        write_rows = _projection_rows(rows)

        operation_identity = {
            "proposalId": proposal_id,
            "proposalVersion": proposal_version,
            "approvalRef": approval_ref,
            "expectedPrior": expected_prior,
            "diff": {
                "edgeSetRef": edge_set_ref,
                "writeIds": write_ids,
                "removedIds": removed,
            },
            "environment": common["environment"],
            "system": common["system"],
        }
        operation_digest = hashlib.sha256(
            json.dumps(operation_identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        graph_version = f"graph-{operation_digest[:32]}"
        pointer = self._control.active_pointer(common["environment"])
        active_version = pointer.get("graphVersion")
        active_fence = pointer.get("fence")
        if not isinstance(active_fence, int) or isinstance(active_fence, bool) or active_fence < 0:
            raise ValueError("active pointer fence is invalid")

        if active_version == graph_version:
            actual_rows = _projection_rows(
                self._projection.namespace_checksum(graph_version)
            )
            graph_checksum = _projection_checksum(actual_rows)
            if (
                pointer.get("graphChecksum") not in {None, graph_checksum}
                or pointer.get("correlationId") != context.correlation_id
            ):
                raise ValueError("active publication replay does not match its pointer")
            package_ref = self._write_package(
                common,
                proposal_id,
                proposal_version,
                approval_ref,
                edge_set_ref,
                expected_prior,
                graph_version,
                graph_checksum,
                active_fence,
                approved_at,
            )
            if pointer.get("package") != package_ref:
                raise ValueError("active publication replay package does not match its pointer")
            return self._published_receipt(
                context, common, pointer, package_ref, graph_version, graph_checksum
            )
        if active_version != expected_prior:
            raise ValueError("active pointer differs from the approved proposal base")

        prior_rows = (
            []
            if expected_prior == "NONE"
            else _projection_rows(self._projection.namespace_checksum(expected_prior))
        )
        remaining = [row for row in prior_rows if row["edgeId"] not in set(removed)]
        expected_rows = _projection_rows([*remaining, *write_rows])
        graph_checksum = _projection_checksum(expected_rows)
        next_fence = active_fence + 1
        self._projection.copy_namespace(expected_prior, graph_version, fence=next_fence)
        if removed:
            self._projection.delete_edges(graph_version, removed)
        if write_rows:
            self._projection.merge_edges(
                graph_version,
                sorted(merge_rows, key=lambda row: (row["edgeId"], row["source"])),
                fence=next_fence,
            )
        actual_rows = _projection_rows(self._projection.namespace_checksum(graph_version))
        if actual_rows != expected_rows or _projection_checksum(actual_rows) != graph_checksum:
            raise ValueError("staged projection verification failed")

        package_ref = self._write_package(
            common,
            proposal_id,
            proposal_version,
            approval_ref,
            edge_set_ref,
            expected_prior,
            graph_version,
            graph_checksum,
            next_fence,
            approved_at,
        )
        activated = self._control.activate_pointer(
            environment=common["environment"],
            graph_version=graph_version,
            graph_checksum=graph_checksum,
            package_reference=package_ref,
            system=common["system"],
            artifact_digest=common["artifactDigest"],
            expected_prior=expected_prior,
            expected_fence=active_fence,
            next_fence=next_fence,
            correlation_id=context.correlation_id,
            activated_at=datetime.fromisoformat(approved_at.replace("Z", "+00:00")),
        )
        return self._published_receipt(
            context, common, activated, package_ref, graph_version, graph_checksum
        )

    def _write_package(
        self,
        common: dict[str, Any],
        proposal_id: str,
        proposal_version: int,
        approval_ref: dict[str, Any],
        edge_set_ref: dict[str, Any],
        expected_prior: str,
        graph_version: str,
        graph_checksum: str,
        fence: int,
        approved_at: str,
    ) -> dict[str, Any]:
        content = {
            "schemaVersion": "1.0.0",
            "artifactType": "lineage-package",
            "system": common["system"],
            "environment": common["environment"],
            "artifactDigest": common["artifactDigest"],
            "proposalId": proposal_id,
            "proposalVersion": proposal_version,
            "approvalRef": approval_ref,
            "edgeSetRef": edge_set_ref,
            "expectedPrior": expected_prior,
            "graphVersion": graph_version,
            "graphChecksum": graph_checksum,
            "fence": fence,
            "approvedAt": approved_at,
        }
        package_id = "package-" + hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        package = {**content, "packageId": package_id}
        return validate_artifact_reference(
            self._packages.put(
                "lineage-package",
                f"packages/{common['environment']}/{package_id}.json",
                package,
                "1.0.0",
            )
        )

    def _verify_projection(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if not isinstance(input_document, Mapping):
            raise ValueError("projection verification input must be an object")
        if input_document.get("schemaVersion") != "1.0.0":
            raise ValueError("unsupported projection verification schema")
        common = _common_context(_lineage_context(input_document))
        graph_version = _required_text("graph version", input_document.get("graphVersion"))
        expected_checksum = _required_text(
            "graph checksum", input_document.get("graphChecksum")
        )
        pointer = self._control.active_pointer(common["environment"])
        actual_checksum = _projection_checksum(
            _projection_rows(self._projection.namespace_checksum(graph_version))
        )
        if (
            pointer.get("graphVersion") != graph_version
            or pointer.get("graphChecksum") not in {None, expected_checksum}
            or actual_checksum != expected_checksum
        ):
            raise ValueError("active projection checksum verification failed")
        return StageExecutionResult(
            artifact_kind="projection-verification",
            schema_version="1.0.0",
            document={
                "schemaVersion": "1.0.0",
                "artifactType": "projection-verification",
                "workflowKind": context.workflow_kind,
                "workflowVersion": context.workflow_version,
                "stageId": context.stage_id,
                "stageName": context.stage_name,
                "commandId": context.command_id,
                "correlationId": context.correlation_id,
                "source": dict(context.input_reference),
                "context": common,
                "status": "VERIFIED",
                "graphVersion": graph_version,
                "graphChecksum": actual_checksum,
                "pointerFence": pointer["fence"],
            },
        )

    @staticmethod
    def _receipt(
        context: StageExecutionContext, common: dict[str, Any], outcome: str
    ) -> StageExecutionResult:
        return StageExecutionResult(
            artifact_kind="publication-decision",
            schema_version="1.0.0",
            document={
                "schemaVersion": "1.0.0",
                "artifactType": "publication-decision",
                "workflowKind": context.workflow_kind,
                "workflowVersion": context.workflow_version,
                "stageId": context.stage_id,
                "stageName": context.stage_name,
                "commandId": context.command_id,
                "correlationId": context.correlation_id,
                "source": dict(context.input_reference),
                "context": common,
                "terminalOutcome": outcome,
            },
        )

    @staticmethod
    def _published_receipt(
        context: StageExecutionContext,
        common: dict[str, Any],
        pointer: dict[str, Any],
        package_ref: dict[str, Any],
        graph_version: str,
        graph_checksum: str,
    ) -> StageExecutionResult:
        return StageExecutionResult(
            artifact_kind="publication-receipt",
            schema_version="1.0.0",
            document={
                "schemaVersion": "1.0.0",
                "artifactType": "publication-receipt",
                "workflowKind": context.workflow_kind,
                "workflowVersion": context.workflow_version,
                "stageId": context.stage_id,
                "stageName": context.stage_name,
                "commandId": context.command_id,
                "correlationId": context.correlation_id,
                "source": dict(context.input_reference),
                "context": common,
                "terminalOutcome": "PUBLISHED",
                "graphVersion": graph_version,
                "graphChecksum": graph_checksum,
                "packageRef": package_ref,
                "pointer": pointer,
            },
        )


_DEPLOYMENT_TERMINALS = frozenset(
    {
        "FAILED_NO_CHANGE",
        "LINEAGE_OUT_OF_SYNC",
        "FAILED_TERMINAL",
        "PROMOTED",
        "ROLLED_BACK",
    }
)


def _deployment_event(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("deployment event must be an object")
    try:
        event = DeploymentEvent(
            event_id=_required_text("deployment event ID", value.get("eventId")),
            event_type=value.get("eventType"),  # type: ignore[arg-type]
            provider=_required_text("deployment provider", value.get("provider")),
            provider_sequence=value.get("providerSequence"),  # type: ignore[arg-type]
            attempt=value.get("attempt"),  # type: ignore[arg-type]
            system=_required_text("deployment system", value.get("system")),
            environment=_required_text(
                "deployment environment", value.get("environment")
            ),
            outcome=value.get("outcome"),  # type: ignore[arg-type]
            artifact_digest=value.get("artifactDigest"),  # type: ignore[arg-type]
            correlation_id=_required_text(
                "deployment correlation ID", value.get("correlationId")
            ),
            audit_ref=_required_text("deployment audit reference", value.get("auditRef")),
            occurred_at=_required_text(
                "deployment occurrence time", value.get("occurredAt")
            ),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("deployment event schema is invalid") from error
    if value.get("schemaVersion") != "1.0.0":
        raise ValueError("deployment event schema is invalid")
    return event.as_dict()


def _hex_sha256(name: str, value: object) -> str:
    text = _required_text(name, value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return text


class DeploymentStageUseCase:
    def __init__(
        self,
        evidence: ArtifactStorePort,
        packages: ArtifactStorePort,
        control: DeploymentControlPort,
        projection: StageProjectionPort,
    ) -> None:
        self._evidence = evidence
        self._packages = packages
        self._control = control
        self._projection = projection

    def execute(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if context.workflow_kind != "DEPLOYMENT":
            raise ValueError("deployment use case received another workflow")
        if context.stage_id == "D1":
            return self._authenticate(input_document, context)
        document, common, event = self._prior(input_document, context)
        terminal = document.get("terminalOutcome")
        if terminal in _DEPLOYMENT_TERMINALS:
            return self._result(context, common, event, self._carry(document))
        if context.stage_id == "D2":
            return self._order(document, common, event, context)
        if context.stage_id == "D3":
            return self._record_digest(document, common, event, context)
        if context.stage_id == "D4":
            return self._resolve_package(document, common, event, context)
        if context.stage_id == "D5":
            return self._promote(document, common, event, context)
        if context.stage_id == "D6":
            return self._read_back(document, common, event, context)
        raise ValueError("deployment use case received an unsupported stage")

    def _authenticate(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if (
            not isinstance(input_document, Mapping)
            or input_document.get("schemaVersion") != "1.0.0"
            or input_document.get("artifactType") != "deployment-event"
        ):
            raise ValueError("D1 requires a versioned deployment event")
        common = _common_context(_lineage_context(input_document))
        event = _deployment_event(input_document.get("event"))
        event_artifact = event.get("artifactDigest", "NONE")
        if (
            event["system"] != common["system"]
            or event["environment"] != common["environment"]
            or event_artifact != common["artifactDigest"]
            or event["correlationId"] != context.correlation_id
        ):
            raise ValueError("deployment event is outside the command scope")
        authentication_ref = validate_artifact_reference(
            input_document.get("authenticationRef")
        )
        receipt = self._evidence.get(authentication_ref)
        event_digest = hashlib.sha256(
            json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if (
            not isinstance(receipt, Mapping)
            or set(receipt)
            != {
                "schemaVersion",
                "artifactType",
                "decision",
                "eventDigest",
                "provider",
                "principal",
                "verifiedAt",
            }
            or receipt.get("schemaVersion") != "1.0.0"
            or receipt.get("artifactType") != "deployment-authentication-receipt"
            or receipt.get("decision") != "AUTHENTICATED"
            or receipt.get("eventDigest") != event_digest
            or receipt.get("provider") != event["provider"]
        ):
            raise ValueError("deployment authentication receipt is invalid or unbound")
        _required_text("authenticated principal", receipt.get("principal"))
        _accepted_at(receipt.get("verifiedAt"))
        if event["eventType"] == "MERGE":
            return self._result(
                context,
                common,
                event,
                {
                    "authenticationRef": authentication_ref,
                    "eventDigest": event_digest,
                    "terminalOutcome": "FAILED_NO_CHANGE",
                    "reason": "NON_DEPLOYMENT_EVENT",
                },
            )
        claim = self._control.claim_deployment_event(event, event_digest)
        if claim.get("disposition") not in {"CLAIMED", "DUPLICATE"}:
            raise ValueError("deployment event claim is invalid")
        return self._result(
            context,
            common,
            event,
            {
                "authenticationRef": authentication_ref,
                "eventDigest": event_digest,
                "claimDisposition": claim["disposition"],
            },
        )

    def _order(
        self,
        document: Mapping[str, Any],
        common: dict[str, Any],
        event: dict[str, Any],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        order = self._control.establish_deployment_order(event)
        disposition = order.get("disposition")
        if disposition not in {"AUTHORITATIVE", "STALE"}:
            raise ValueError("deployment ordering result is invalid")
        additions: dict[str, Any] = {
            "authenticationRef": document["authenticationRef"],
            "eventDigest": document["eventDigest"],
            "orderingDisposition": disposition,
        }
        if disposition == "STALE":
            additions.update(
                terminalOutcome="FAILED_NO_CHANGE", reason="STALE_DEPLOYMENT_EVENT"
            )
        return self._result(context, common, event, additions)

    def _record_digest(
        self,
        document: Mapping[str, Any],
        common: dict[str, Any],
        event: dict[str, Any],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        self._control.record_deployed_digest(event)
        additions = self._carry(document)
        additions["deployedArtifactDigest"] = event.get("artifactDigest")
        if event["outcome"] == "FAILED":
            additions.update(
                terminalOutcome="FAILED_NO_CHANGE", reason="DEPLOYMENT_FAILED"
            )
            additions = self._control.complete_deployment(event, additions)
        return self._result(context, common, event, additions)

    def _resolve_package(
        self,
        document: Mapping[str, Any],
        common: dict[str, Any],
        event: dict[str, Any],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        artifact_digest = _required_text(
            "deployed artifact digest", event.get("artifactDigest")
        )
        registry = self._control.package_for(
            common["system"], common["environment"], artifact_digest
        )
        if registry is None:
            outcome = {
                **self._carry(document),
                "terminalOutcome": "LINEAGE_OUT_OF_SYNC",
                "reason": "EXACT_PACKAGE_NOT_FOUND",
            }
            outcome = self._control.complete_deployment(event, outcome)
            return self._result(
                context,
                common,
                event,
                outcome,
            )
        package_ref = validate_artifact_reference(registry.get("packageReference"))
        package = self._packages.get(package_ref)
        if (
            not isinstance(package, Mapping)
            or package.get("schemaVersion") != "1.0.0"
            or package.get("artifactType") != "lineage-package"
            or package.get("packageId") != registry.get("packageId")
            or package.get("system") != common["system"]
            or package.get("environment") != common["environment"]
            or package.get("artifactDigest") != artifact_digest
            or package.get("graphVersion") != registry.get("graphVersion")
            or package.get("graphChecksum") != registry.get("graphChecksum")
        ):
            raise ValueError("registered lineage package does not match its artifact")
        validate_artifact_reference(package.get("approvalRef"))
        validate_artifact_reference(package.get("edgeSetRef"))
        graph_checksum = _hex_sha256(
            "package graph checksum", package.get("graphChecksum")
        )
        actual_checksum = _projection_checksum(
            _projection_rows(
                self._projection.namespace_checksum(str(package["graphVersion"]))
            )
        )
        if actual_checksum != graph_checksum:
            raise ValueError("lineage package projection checksum does not match")
        return self._result(
            context,
            common,
            event,
            {
                **self._carry(document),
                "lineagePackageId": package["packageId"],
                "packageRef": package_ref,
                "graphVersion": package["graphVersion"],
                "graphChecksum": graph_checksum,
            },
        )

    def _promote(
        self,
        document: Mapping[str, Any],
        common: dict[str, Any],
        event: dict[str, Any],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        graph_version = _required_text("deployment graph version", document.get("graphVersion"))
        graph_checksum = _hex_sha256(
            "deployment graph checksum", document.get("graphChecksum")
        )
        package_ref = validate_artifact_reference(document.get("packageRef"))
        pointer = self._control.active_pointer(common["environment"])
        if (
            pointer.get("graphVersion") == graph_version
            and pointer.get("correlationId") == context.correlation_id
        ):
            if (
                pointer.get("graphChecksum") not in {None, graph_checksum}
                or (
                    pointer.get("package") is not None
                    and pointer.get("package") != package_ref
                )
            ):
                raise ValueError("already active deployment package does not match")
            promoted = pointer
        else:
            fence = pointer.get("fence")
            if not isinstance(fence, int) or isinstance(fence, bool) or fence < 0:
                raise ValueError("deployment pointer fence is invalid")
            promoted = self._control.promote_deployment(
                environment=common["environment"],
                graph_version=graph_version,
                graph_checksum=graph_checksum,
                package_reference=package_ref,
                system=common["system"],
                artifact_digest=common["artifactDigest"],
                expected_prior=_required_text(
                    "deployment prior graph", pointer.get("graphVersion")
                ),
                expected_fence=fence,
                next_fence=fence + 1,
                correlation_id=context.correlation_id,
                activated_at=datetime.fromisoformat(
                    str(event["occurredAt"]).replace("Z", "+00:00")
                ),
                action=(
                    "DEPLOYMENT_ROLLED_BACK"
                    if event["eventType"] == "ROLLBACK"
                    else "DEPLOYMENT_PROMOTED"
                ),
            )
        return self._result(
            context,
            common,
            event,
            {**self._carry(document), "pointer": promoted},
        )

    def _read_back(
        self,
        document: Mapping[str, Any],
        common: dict[str, Any],
        event: dict[str, Any],
        context: StageExecutionContext,
    ) -> StageExecutionResult:
        pointer = self._control.active_pointer(common["environment"])
        graph_version = _required_text("deployment graph version", document.get("graphVersion"))
        graph_checksum = _hex_sha256(
            "deployment graph checksum", document.get("graphChecksum")
        )
        package_ref = validate_artifact_reference(document.get("packageRef"))
        actual_checksum = _projection_checksum(
            _projection_rows(self._projection.namespace_checksum(graph_version))
        )
        if (
            pointer.get("graphVersion") != graph_version
            or pointer.get("graphChecksum") not in {None, graph_checksum}
            or pointer.get("correlationId") != context.correlation_id
            or (
                pointer.get("package") is not None
                and pointer.get("package") != package_ref
            )
            or actual_checksum != graph_checksum
        ):
            raise ValueError("deployment read-back verification failed")
        terminal = "ROLLED_BACK" if event["eventType"] == "ROLLBACK" else "PROMOTED"
        result = {
            **self._carry(document),
            "terminalOutcome": terminal,
            "reason": None,
            "pointer": pointer,
        }
        persisted = self._control.complete_deployment(event, result)
        stored = self._control.deployment_state(common["system"], common["environment"])
        if (
            stored is None
            or stored.get("eventId") != event["eventId"]
            or stored.get("lineagePackageId") != document.get("lineagePackageId")
            or stored.get("graphVersion") != graph_version
        ):
            raise ValueError("deployment state read-back verification failed")
        return self._result(context, common, event, persisted)

    @staticmethod
    def _prior(
        input_document: object, context: StageExecutionContext
    ) -> tuple[Mapping[str, Any], dict[str, Any], dict[str, Any]]:
        if (
            not isinstance(input_document, Mapping)
            or input_document.get("schemaVersion") != "1.0.0"
            or input_document.get("artifactType")
            != f"deployment-{int(context.stage_id[1:]) - 1}-result"
        ):
            raise ValueError("deployment stage input is not its exact prior result")
        common = _common_context(_lineage_context(input_document))
        event = _deployment_event(input_document.get("event"))
        return input_document, common, event

    @staticmethod
    def _carry(document: Mapping[str, Any]) -> dict[str, Any]:
        excluded = {
            "schemaVersion",
            "artifactType",
            "workflowKind",
            "workflowVersion",
            "stageId",
            "stageName",
            "commandId",
            "correlationId",
            "source",
            "context",
            "event",
        }
        return {key: value for key, value in document.items() if key not in excluded}

    @staticmethod
    def _result(
        context: StageExecutionContext,
        common: dict[str, Any],
        event: dict[str, Any],
        additions: Mapping[str, Any],
    ) -> StageExecutionResult:
        return StageExecutionResult(
            artifact_kind=f"deployment-{context.stage_id.lower()}-result",
            schema_version="1.0.0",
            document={
                "schemaVersion": "1.0.0",
                "artifactType": f"deployment-{context.stage_id[1:]}-result",
                "workflowKind": context.workflow_kind,
                "workflowVersion": context.workflow_version,
                "stageId": context.stage_id,
                "stageName": context.stage_name,
                "commandId": context.command_id,
                "correlationId": context.correlation_id,
                "source": dict(context.input_reference),
                "context": common,
                "event": event,
                **dict(additions),
            },
        )

def production_stage_use_cases(
    artifacts: ArtifactStorePort | None = None,
    proposal_store: ProposalStorePort | None = None,
    *,
    packages: ArtifactStorePort | None = None,
    publication_control: PublicationControlPort | None = None,
    projection: StageProjectionPort | None = None,
    sources: SourceArchivePort | None = None,
    pr_gate_control: PrGateControlPort | None = None,
    impact_projection: ImpactProjectionPort | None = None,
) -> dict[tuple[str, str], StageUseCase]:
    use_cases: dict[tuple[str, str], StageUseCase] = {
        ("BASELINE", "B3"): ClassificationStageUseCase(policy_version="1.0.0")
    }
    if artifacts is not None:
        if publication_control is not None:
            control = ControlStageUseCase(artifacts, publication_control)
            for identity in (
                ("BASELINE", "B1"),
                ("BASELINE", "B2"),
                ("INCREMENTAL", "I1"),
                ("INCREMENTAL", "I2"),
                ("INCREMENTAL", "I4"),
                ("INCREMENTAL", "I8"),
            ):
                use_cases[identity] = control
        coverage = CoverageStageUseCase(artifacts)
        use_cases[("BASELINE", "B4")] = coverage
        use_cases[("INCREMENTAL", "I3")] = coverage
        if sources is not None:
            sca = ScaStageUseCase(artifacts, sources)
            use_cases[("BASELINE", "B5")] = sca
            use_cases[("INCREMENTAL", "I5")] = sca
            use_cases[("NIGHTLY", "N2")] = sca
        runtime_validation = RuntimeValidationStageUseCase(artifacts)
        use_cases[("BASELINE", "B6")] = runtime_validation
        use_cases[("INCREMENTAL", "I6")] = runtime_validation
        consolidation = ConsolidationStageUseCase(artifacts)
        use_cases[("BASELINE", "B7")] = consolidation
        use_cases[("BASELINE", "B8")] = consolidation
        use_cases[("INCREMENTAL", "I7")] = consolidation
        use_cases[("PR_GATE", "P4")] = consolidation
        pr_control = pr_gate_control or cast(
            PrGateControlPort | None, publication_control
        )
        pr_projection = impact_projection or cast(
            ImpactProjectionPort | None, projection
        )
        if pr_control is not None and pr_projection is not None:
            pr_gate = PrGateStageUseCase(artifacts, pr_control, pr_projection)
            for index in range(1, 9):
                use_cases[("PR_GATE", f"P{index}")] = pr_gate
        if proposal_store is not None:
            proposal = ProposalStageUseCase(artifacts, proposal_store)
            use_cases[("BASELINE", "B9")] = proposal
            use_cases[("INCREMENTAL", "I9")] = proposal
            use_cases[("NIGHTLY", "N6")] = proposal
        if (
            packages is not None
            and publication_control is not None
            and projection is not None
        ):
            publication = PublicationStageUseCase(
                artifacts, packages, publication_control, projection
            )
            use_cases[("BASELINE", "B10")] = publication
            use_cases[("INCREMENTAL", "I10")] = publication
            use_cases[("NIGHTLY", "N3")] = publication
            deployment = DeploymentStageUseCase(
                artifacts, packages, publication_control, projection
            )
            for index in range(1, 7):
                use_cases[("DEPLOYMENT", f"D{index}")] = deployment
            nightly = NightlyStageUseCase(
                artifacts,
                cast(NightlyControlPort, publication_control),
                projection,
            )
            for stage_id in ("N1", "N3", "N4", "N5", "N6"):
                use_cases[("NIGHTLY", stage_id)] = nightly
    return use_cases


__all__ = [
    "ClassificationStageUseCase",
    "CoverageStageUseCase",
    "ConsolidationStageUseCase",
    "ControlStageUseCase",
    "ProposalStageUseCase",
    "PublicationStageUseCase",
    "DeploymentStageUseCase",
    "RuntimeValidationStageUseCase",
    "production_stage_use_cases",
]
