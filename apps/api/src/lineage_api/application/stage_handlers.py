from __future__ import annotations

from pathlib import PurePosixPath
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
from typing import Any, Mapping

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
from lineage_api.application.ports import ArtifactStorePort, ProposalStorePort
from lineage_api.application.consolidation import derive_consolidation, edge_key_for
from lineage_api.application.runtime_validation import runtime_window_manifest_errors
from lineage_api.domain.urns import LineageUrn


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
    for name in ("activeBaseVersion", "acceptedAt"):
        if name in context:
            normalized[name] = _required_text(name, context[name])
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


def production_stage_use_cases(
    artifacts: ArtifactStorePort | None = None,
    proposal_store: ProposalStorePort | None = None,
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
        consolidation = ConsolidationStageUseCase(artifacts)
        use_cases[("BASELINE", "B7")] = consolidation
        use_cases[("BASELINE", "B8")] = consolidation
        use_cases[("INCREMENTAL", "I7")] = consolidation
        use_cases[("PR_GATE", "P4")] = consolidation
        if proposal_store is not None:
            proposal = ProposalStageUseCase(artifacts, proposal_store)
            use_cases[("BASELINE", "B9")] = proposal
            use_cases[("INCREMENTAL", "I9")] = proposal
            use_cases[("NIGHTLY", "N6")] = proposal
    return use_cases


__all__ = [
    "ClassificationStageUseCase",
    "CoverageStageUseCase",
    "ConsolidationStageUseCase",
    "ProposalStageUseCase",
    "RuntimeValidationStageUseCase",
    "production_stage_use_cases",
]
