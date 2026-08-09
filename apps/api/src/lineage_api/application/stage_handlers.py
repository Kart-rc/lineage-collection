from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Mapping

from lineage_api.application.classification import (
    ClassificationEvidence,
    evaluate_classification,
)
from lineage_api.application.stage_execution import (
    StageExecutionContext,
    StageExecutionResult,
    StageUseCase,
    validate_artifact_reference,
)
from lineage_api.application.ports import ArtifactStorePort
from lineage_api.application.runtime_validation import runtime_window_manifest_errors


_CONTEXT_FIELDS = {
    "artifactDigest": ("artifactDigest", "digest"),
    "environment": ("environment", "env"),
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
        if "repositoryInventory" in source:
            context["repositoryInventory"] = _paths(
                "repository inventory", source["repositoryInventory"], limit=10_000
            )
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
    source = context.get("repositorySource")
    if source is not None:
        normalized["repositorySource"] = validate_artifact_reference(source)
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
                "system": common["system"],
                "pack": "python-ast",
                "paths": paths,
                "repositorySource": common["repositorySource"],
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
            document=inventory_references,
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


def production_stage_use_cases(
    artifacts: ArtifactStorePort | None = None,
) -> dict[tuple[str, str], StageUseCase]:
    use_cases: dict[tuple[str, str], StageUseCase] = {
        ("BASELINE", "B3"): ClassificationStageUseCase(policy_version="1.0.0")
    }
    if artifacts is not None:
        coverage = CoverageStageUseCase(artifacts)
        use_cases[("BASELINE", "B4")] = coverage
        use_cases[("INCREMENTAL", "I3")] = coverage
        runtime_validation = RuntimeValidationStageUseCase(artifacts)
        use_cases[("BASELINE", "B6")] = runtime_validation
        use_cases[("INCREMENTAL", "I6")] = runtime_validation
    return use_cases


__all__ = [
    "ClassificationStageUseCase",
    "CoverageStageUseCase",
    "RuntimeValidationStageUseCase",
    "production_stage_use_cases",
]
