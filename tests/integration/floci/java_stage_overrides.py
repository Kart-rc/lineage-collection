"""Java-aware stage use cases for the floci-backed workflow E2E.

The production workflow-path stage handlers are python-ast/ldp-URN-only today
(B5/I5 reject packs other than ``python-ast``; consolidation/publication reject the
Java cell's ``service://repo/Type#method`` endpoint URNs). These overrides are the
additive seam the plan calls for: they mirror the production artifact contracts
byte-for-byte, delegate every consolidation/publication decision to the same
production domain functions (``edge_key_for``, ``derive_consolidation``, fenced
pointer activation), and relax only the URN grammar checks so a Java
service-anchored edge is admitted on exactly one side of an edge.

Nothing in production code is modified; the driver registers these instances over
the production registry for B4/B5/B8/B10 and I3/I4/I5/I7/I10 only.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from lineage_api.application.consolidation import derive_consolidation, edge_key_for
from lineage_api.application.pr_gate_execution import PrGateStageUseCase
from lineage_api.application.java_runtime_stage import run_java_runtime_stage
from lineage_api.application.stage_execution import (
    MAX_STAGE_DOCUMENT_BYTES,
    StageExecutionContext,
    StageExecutionResult,
    validate_artifact_reference,
)
from lineage_api.application.stage_handlers import (
    ConsolidationStageUseCase,
    ControlStageUseCase,
    CoverageStageUseCase,
    PublicationStageUseCase,
    _accepted_at,
    _common_context,
    _coverage,
    _edge_ids,
    _lineage_context,
    _paths,
    _projection_checksum,
    _projection_rows,
    _references,
    _required_text,
)
from lineage_api.domain.urns import LineageUrn
from lineage_api.services.analyzer_registry import AnalyzerRegistry, AnalyzerSelection

JAVA_PACK = "java-spring-data-jpa-v1"
JAVA_RULESET = "spring-data-rules-v1"
JAVA_SOURCE_KIND = "git-checkout"
JAVA_FRAMEWORK = "spring-data-jpa"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _is_service_urn(value: str) -> bool:
    return value.startswith("service://") and "#" in value


def _ldp_side(sources: list[str], target: str) -> LineageUrn:
    """The single ldp URN that scopes a Java service-anchored edge."""
    candidates = [urn for urn in (*sources, target) if not _is_service_urn(urn)]
    if len(candidates) != 1:
        raise ValueError("Java edge must carry exactly one ldp URN side")
    return LineageUrn.parse(candidates[0])


class _CheckoutSnapshot:
    """AnalyzerSnapshot over a materialized immutable source checkout."""

    def __init__(
        self,
        root: Path,
        *,
        origin: str,
        repository: str,
        revision: str,
        scope_digest: str,
        environment: str,
        platform: str,
        system: str,
        paths: tuple[str, ...],
    ) -> None:
        self._root = root
        self.origin = origin
        self.repository = repository
        self.revision = revision
        self.scope_digest = scope_digest
        self.environment = environment
        self.platform = platform
        self.system = system
        self.analyzer_pack = JAVA_PACK
        self.ruleset = JAVA_RULESET
        self.paths = paths

    def read_bytes(self, relative_path: str) -> bytes:
        return (self._root / relative_path).read_bytes()


class _ScopeStub:
    """Path-only snapshot stub for AnalyzerRegistry.source_scope (no file reads)."""

    def __init__(self, paths: tuple[str, ...]) -> None:
        self.paths = paths
        self.analyzer_pack = JAVA_PACK
        self.ruleset = JAVA_RULESET


def java_selection(schema_profile: str) -> AnalyzerSelection:
    return AnalyzerSelection(
        analyzer_pack=JAVA_PACK,
        ruleset=JAVA_RULESET,
        source_kind=JAVA_SOURCE_KIND,
        framework=JAVA_FRAMEWORK,
        schema_profile=schema_profile,
    )


def java_scope_partition(
    paths: list[str], schema_profile: str
) -> tuple[list[str], list[str], list[str]]:
    scope = AnalyzerRegistry.default().source_scope(
        _ScopeStub(tuple(sorted(paths))), java_selection(schema_profile)
    )
    return (
        sorted(scope.selected_scope),
        sorted(scope.skipped_scope),
        sorted(scope.unsupported_scope),
    )


class JavaCoverageStageUseCase(CoverageStageUseCase):
    """B4/I3 with the Java cell's own source disposition instead of `.py` scoping."""

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
        recomputed, skipped, unsupported = java_scope_partition(
            inventory, common["platform"]
        )
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
        # The Java cell analyzes a module closure, not per-file chunks: one work unit
        # carries the whole selected scope so entity/repository resolution stays whole.
        work_unit = {
            "schemaVersion": "1.0.0",
            "artifactType": "sca-work-unit",
            "workUnitId": f"{context.command_id}-B5-00000",
            "repository": common["repository"],
            "artifactDigest": common["artifactDigest"],
            "environment": common["environment"],
            "platform": common["platform"],
            "system": common["system"],
            "pack": JAVA_PACK,
            "paths": recomputed,
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
                "expectedScope": recomputed,
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
        work_reference = validate_artifact_reference(
            self._artifacts.put(
                "sca-work-unit",
                f"commands/{context.command_id}/work-units/00000.json",
                work_unit,
                "1.0.0",
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
                "workUnitRefs": [work_reference],
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
        recomputed, skipped, unsupported = java_scope_partition(
            changed, common["platform"]
        )
        # Files the Java cell deliberately skips (docs, tests, generated dirs) are
        # reusable prior dispositions, not unsupported territory: a docs-only change
        # honestly proves no lineage impact instead of quarantining the run.
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
                    "reusedScope": skipped,
                    "unsupportedScope": unsupported,
                },
            },
        )


class JavaControlStageUseCase(ControlStageUseCase):
    """I4 emitting a Java-pack work unit; every other control stage stays production."""

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
        reused = _paths("I4 reused scope", coverage.get("reusedScope", []), limit=10_000)
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
                "pack": JAVA_PACK,
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


class JavaScaStageUseCase:
    """B5/I5 for the java-spring-data-jpa-v1 cell, mirroring ScaStageUseCase's
    artifact contract (sca-evidence, assertion-set, sca-residue, sca-stage-result)."""

    def __init__(self, artifacts: Any, sources: Any, *, origin: str) -> None:
        self._artifacts = artifacts
        self._sources = sources
        self._origin = origin
        self._registry = AnalyzerRegistry.default()

    def execute(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if (context.workflow_kind, context.stage_id) not in {
            ("BASELINE", "B5"),
            ("INCREMENTAL", "I5"),
        }:
            raise ValueError("Java SCA use case received an unsupported stage")
        if (
            not isinstance(input_document, Mapping)
            or input_document.get("schemaVersion") != "1.0.0"
            or input_document.get("artifactType") != "sca-work-unit"
        ):
            raise ValueError("Java SCA requires a versioned work unit")
        if input_document.get("pack") != JAVA_PACK:
            raise ValueError("unsupported SCA analyzer pack")
        if input_document.get("correlationId") != context.correlation_id:
            raise ValueError("SCA work unit correlation does not match its command")
        work_unit_id = _required_text("SCA work unit ID", input_document.get("workUnitId"))
        repository = _required_text("SCA repository", input_document.get("repository"))
        artifact_digest = _required_text(
            "SCA artifact digest", input_document.get("artifactDigest")
        )
        environment = _required_text("SCA environment", input_document.get("environment"))
        platform = _required_text("SCA platform", input_document.get("platform"))
        system = _required_text("SCA system", input_document.get("system"))
        paths = _paths("SCA paths", input_document.get("paths"), limit=10_000)
        source_ref = validate_artifact_reference(input_document.get("repositorySource"))
        catalog_ref = validate_artifact_reference(input_document.get("catalogSnapshotRef"))
        coverage = dict(input_document.get("coverage", {}))
        expected_scope = coverage.get("expectedScope", [])
        if not set(paths) <= set(expected_scope):
            raise ValueError("SCA work paths must be contained in expected coverage")
        coverage["completedScope"] = paths
        for name in (
            "reusedScope",
            "skippedScope",
            "unsupportedScope",
            "quarantinedScope",
            "failedScope",
        ):
            coverage.setdefault(name, [])

        if paths:
            with self._sources.materialize(source_ref) as repository_root:
                snapshot = _CheckoutSnapshot(
                    repository_root,
                    origin=self._origin,
                    repository=repository,
                    revision=artifact_digest.removeprefix("sha256:"),
                    scope_digest=artifact_digest,
                    environment=environment,
                    platform=platform,
                    system=system,
                    paths=tuple(paths),
                )
                result = self._registry.analyze(
                    snapshot,
                    java_selection(platform),
                    work_unit_id,
                    context.correlation_id,
                )
            if result.status != "COMPLETE":
                raise ValueError(
                    f"Java SCA did not complete: {result.status} {result.status_reasons}"
                )
            evidence_document = result.document
        else:
            evidence_document = {
                "schemaVersion": "1.0.0",
                "status": "COMPLETE",
                "statusReasons": [],
                "edges": [],
                "residue": [],
            }

        evidence_ref = validate_artifact_reference(
            self._artifacts.put(
                "sca-evidence",
                f"commands/{context.command_id}/sca/{work_unit_id}/evidence.json",
                evidence_document,
                "1.0.0",
            )
        )
        assertions = []
        for edge in evidence_document["edges"]:
            citation_source = edge.get("evidence", {})
            assertions.append(
                {
                    "provenanceId": edge["provenanceId"],
                    "from": edge["from"],
                    "to": edge["to"],
                    "edgeType": edge["edgeType"],
                    "transform": edge["transform"],
                    "mechanism": "SCA",
                    "exact": bool(edge["exact"]),
                    "evidenceRef": evidence_ref,
                    "repo": edge["repo"],
                    "runId": work_unit_id,
                    "correlationId": context.correlation_id,
                    "sessionComplete": True,
                    "citation": {
                        "file": citation_source["file"],
                        "line": citation_source["line"],
                        "astPath": citation_source["astPath"],
                    },
                }
            )
        assertion_document = {
            "schemaVersion": "1.0.0",
            "artifactType": "assertion-set",
            "workUnitId": work_unit_id,
            "assertions": assertions,
        }
        if len(_canonical(assertion_document)) > MAX_STAGE_DOCUMENT_BYTES:
            raise ValueError("SCA assertion set exceeds its bounded output contract")
        assertion_ref = validate_artifact_reference(
            self._artifacts.put(
                "assertion-set",
                f"commands/{context.command_id}/sca/{work_unit_id}/assertions.json",
                assertion_document,
                "1.0.0",
            )
        )
        residue_ref = validate_artifact_reference(
            self._artifacts.put(
                "sca-residue",
                f"commands/{context.command_id}/sca/{work_unit_id}/residue.json",
                {
                    "schemaVersion": "1.0.0",
                    "artifactType": "sca-residue",
                    "workUnitId": work_unit_id,
                    "residue": evidence_document["residue"],
                },
                "1.0.0",
            )
        )
        common: dict[str, Any] = {
            "repository": repository,
            "artifactDigest": artifact_digest,
            "environment": environment,
            "platform": platform,
            "system": system,
            "repositorySource": source_ref,
            "catalogSnapshotRef": catalog_ref,
            "catalogSnapshotId": _required_text(
                "SCA catalog snapshot", input_document.get("catalogSnapshotId")
            ),
            "resolverVersion": _required_text(
                "SCA resolver version", input_document.get("resolverVersion")
            ),
            "rulesetVersion": _required_text(
                "SCA ruleset version", input_document.get("rulesetVersion")
            ),
            "activeBaseVersion": _required_text(
                "SCA active base", input_document.get("activeBaseVersion")
            ),
            "activeBaseFence": input_document.get("activeBaseFence"),
            "acceptedAt": _required_text(
                "SCA accepted time", input_document.get("acceptedAt")
            ),
            "runtimeManifestRefs": [
                validate_artifact_reference(reference)
                for reference in input_document.get("runtimeManifestRefs", [])
            ],
            "assertionRefs": [assertion_ref],
            "residueRefs": [residue_ref],
            "coverage": coverage,
        }
        return StageExecutionResult(
            artifact_kind="sca-stage-result",
            schema_version="1.0.0",
            document={
                "schemaVersion": "1.0.0",
                "artifactType": "sca-stage-result",
                "workflowKind": context.workflow_kind,
                "workflowVersion": context.workflow_version,
                "stageId": context.stage_id,
                "stageName": context.stage_name,
                "commandId": context.command_id,
                "correlationId": context.correlation_id,
                "source": dict(context.input_reference),
                "context": common,
                "workUnitId": work_unit_id,
                "evidenceRef": evidence_ref,
                "assertionRef": assertion_ref,
                "residueRef": residue_ref,
                "stats": {
                    "edges": len(evidence_document["edges"]),
                    "residue": len(evidence_document["residue"]),
                },
            },
        )


def _java_assertion_document(value: object) -> dict[str, Any]:
    """`stage_handlers._assertion_document` with the URN grammar relaxed so a
    `service://repo/Type#method` endpoint URN is admitted; every ldp URN still
    parses strictly, and each edge must carry exactly one ldp side."""
    required = frozenset(
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
    optional = frozenset({"transform", "citation", "runtimeScope"})
    if not isinstance(value, Mapping):
        raise ValueError("lineage assertion must be an object")
    if not required <= set(value) or set(value) - (required | optional):
        raise ValueError("lineage assertion has a drifted schema")
    provenance_id = _required_text("provenance ID", value["provenanceId"])
    raw_sources = value["from"]
    if not isinstance(raw_sources, list) or not 1 <= len(raw_sources) <= 64:
        raise ValueError("lineage assertion sources are invalid")
    sources = sorted({_required_text("source URN", item) for item in raw_sources})
    target = _required_text("target URN", value["to"])
    for urn in (*sources, target):
        if not _is_service_urn(urn):
            LineageUrn.parse(urn)
    _ldp_side(sources, target)
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
            "file": str(citation["file"]),
            "line": line,
            "astPath": _required_text("citation AST path", citation["astPath"]),
        }
    return normalized


class JavaConsolidationStageUseCase(ConsolidationStageUseCase):
    """B8/I7 consolidation admitting service-anchored Java edges; identical grouping,
    dedup, band math (`edge_key_for` + `derive_consolidation`) and output contract."""

    def execute(
        self, input_document: object, context: StageExecutionContext
    ) -> StageExecutionResult:
        if (context.workflow_kind, context.stage_id) not in {
            ("BASELINE", "B8"),
            ("INCREMENTAL", "I7"),
        }:
            raise ValueError("Java consolidation received an unsupported stage")
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
        coverage = _coverage(lineage_context.get("coverage"), max_scope=self._max_scope)

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
            assertions.extend(
                _java_assertion_document(item) for item in body["assertions"]
            )

        for assertion in assertions:
            ldp = _ldp_side(assertion["from"], assertion["to"])
            if (
                assertion["repo"] != common["repository"]
                or ldp.system != common["system"]
                or ldp.env != common["environment"]
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
                "system": _ldp_side(first["from"], first["to"]).system,
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
        if len(_canonical(edge_set)) > MAX_STAGE_DOCUMENT_BYTES:
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


class JavaPublicationStageUseCase(PublicationStageUseCase):
    """B10/I10 fenced publication with the ldp scope checks applied only to ldp URNs;
    packages, checksums, fenced pointer activation all delegate to production code."""

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
            normalized_sources = sorted(
                {_required_text("publication edge source", item) for item in sources}
            )
            ldp = _ldp_side(normalized_sources, target)
            if ldp.env != common["environment"] or ldp.system != common["system"]:
                raise ValueError("publication edge is outside the pinned scope")
            for source in normalized_sources:
                row = {
                    "edgeId": edge_id,
                    "source": source,
                    "target": target,
                    "type": edge_type,
                }
                rows.append(row)
                merge_rows.append(
                    {
                        **row,
                        "band": band,
                        "corroboration": corroboration,
                        "document": json.dumps(
                            dict(edge),
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        ),
                    }
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
                raise ValueError(
                    "active publication replay package does not match its pointer"
                )
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


class JavaPrGateStageUseCase(PrGateStageUseCase):
    """P1-P8 with the affected-node URN grammar relaxed: a blast radius over the
    Java graph legitimately lands on `service://repo/Type#method` endpoints."""

    @staticmethod
    def _affected_item(
        value: object, change_type: str, maximum_depth: int
    ) -> dict[str, Any]:
        from lineage_api.application.pr_gate_execution import (
            _integer as _pr_integer,
            _text as _pr_text,
        )
        from lineage_api.domain.impact import BAND_ORDER, severity_for

        required = {"urn", "severity", "band", "pathLength", "viaEdges"}
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("PR impact affected result schema is invalid")
        urn = _pr_text("PR affected URN", value["urn"])
        if not _is_service_urn(urn):
            LineageUrn.parse(urn)
        band = _pr_text("PR affected evidence band", value["band"], limit=32)
        if band not in BAND_ORDER:
            raise ValueError("PR impact affected evidence band is invalid")
        severity = _pr_text("PR affected severity", value["severity"], limit=16)
        if severity != severity_for(change_type, band):
            raise ValueError("PR impact affected severity is not calibrated")
        path_length = _pr_integer(
            "PR affected path length",
            value["pathLength"],
            minimum=1,
            maximum=maximum_depth,
        )
        via_edges = value["viaEdges"]
        if not isinstance(via_edges, list) or len(via_edges) != path_length:
            raise ValueError("PR impact affected path evidence is invalid")
        normalized_edges = [
            _pr_text("PR impact edge ID", item, limit=512) for item in via_edges
        ]
        if len(set(normalized_edges)) != len(normalized_edges):
            raise ValueError("PR impact path repeats an edge")
        return {
            "urn": urn,
            "severity": severity,
            "band": band,
            "pathLength": path_length,
            "viaEdges": normalized_edges,
        }


def run_runtime_corroboration(
    *,
    artifacts: Any,
    kinesis: Any,
    sources: dict[str, str],
    static_edges: list[dict[str, Any]],
    command_id: str,
    work_unit_id: str,
    correlation_id: str,
    common: Mapping[str, Any],
    observed_at: str,
    java_home: str,
) -> dict[str, Any]:
    """Run the real javac/java harness against the checkout, stream observations to
    the (floci) Kinesis runtime stream, and materialize the two artifacts the
    UNCHANGED production B6/I6 validator and consolidation math consume: a
    runtime-window manifest and a RUNTIME/ELEMENT assertion set."""
    result = run_java_runtime_stage(
        sources=sources,
        static_edges=static_edges,
        observed_at=observed_at,
        java_home=java_home,
    )
    observations = [dict(observation) for observation in result.observations]
    for observation in observations:
        partition = str(
            observation["to"]
            if not _is_service_urn(str(observation["to"]))
            else observation["from"][0]
        )
        kinesis.put_observation(partition, observation)

    runtime_evidence = {
        "schemaVersion": "1.0.0",
        "artifactType": "runtime-evidence",
        "verdict": result.verdict,
        "corroborated": result.corroborated,
        "staticOnly": result.static_only,
        "executedMethods": list(result.executed),
        "observations": observations,
    }
    evidence_ref = validate_artifact_reference(
        artifacts.put(
            "runtime-evidence",
            f"commands/{command_id}/runtime/{work_unit_id}/evidence.json",
            runtime_evidence,
            "1.0.0",
        )
    )

    stable_by_identity = {
        (tuple(edge["from"]), edge["to"], edge["edgeType"]): edge["provenanceId"]
        for edge in static_edges
    }
    runtime_assertions = []
    for observation in observations:
        identity = (
            tuple(observation["from"]),
            observation["to"],
            observation["edgeType"],
        )
        stable_id = stable_by_identity[identity]
        runtime_assertions.append(
            {
                "provenanceId": f"{stable_id}:runtime",
                "from": list(observation["from"]),
                "to": observation["to"],
                "edgeType": observation["edgeType"],
                "mechanism": "RUNTIME",
                "exact": bool(observation["exact"]),
                "evidenceRef": evidence_ref,
                "repo": common["repository"],
                "runId": work_unit_id,
                "correlationId": correlation_id,
                "sessionComplete": bool(observation["sessionComplete"]),
                "runtimeScope": observation["runtimeScope"],
            }
        )
    assertion_ref = validate_artifact_reference(
        artifacts.put(
            "assertion-set",
            f"commands/{command_id}/runtime/{work_unit_id}/assertions.json",
            {
                "schemaVersion": "1.0.0",
                "artifactType": "assertion-set",
                "workUnitId": f"{work_unit_id}-runtime",
                "assertions": runtime_assertions,
            },
            "1.0.0",
        )
    )

    count = len(observations)
    counters = {
        "attempted": count,
        "accepted": count,
        "rejected": 0,
        "duplicates": 0,
        "retried": 0,
        "buffered": 0,
        "dropped": 0,
        "quarantined": 0,
        "drained": count,
    }
    observation_checksum = (
        "sha256:" + hashlib.sha256(_canonical(observations)).hexdigest()
    )
    manifest = {
        "schemaVersion": "1.0.0",
        "manifestId": f"manifest-{command_id}",
        "windowId": f"window-{command_id}",
        "leaseId": f"lease-{command_id}",
        "profileId": "java-harness-recording-proxy",
        "profileVersion": "1.0.0",
        "workloadId": work_unit_id,
        "repo": common["repository"],
        "environment": common["environment"],
        "artifactDigest": common["artifactDigest"],
        "mechanism": "SDK",
        "outcome": "COMPLETE",
        **counters,
        "sourceChecksum": common["artifactDigest"],
        "observationChecksum": observation_checksum,
        "reasons": [],
        "reasonCounts": {},
        "emitterCounts": {"java-harness": counters},
        "closedAt": observed_at,
    }
    manifest_ref = validate_artifact_reference(
        artifacts.put(
            "runtime-window-manifest",
            f"commands/{command_id}/runtime/{work_unit_id}/window-manifest.json",
            manifest,
            "1.0.0",
        )
    )
    return {
        "verdict": result.verdict,
        "corroborated": result.corroborated,
        "staticOnly": result.static_only,
        "observations": observations,
        "manifestRef": manifest_ref,
        "assertionRef": assertion_ref,
        "evidenceRef": evidence_ref,
    }


__all__ = [
    "JAVA_PACK",
    "JAVA_RULESET",
    "JavaControlStageUseCase",
    "JavaConsolidationStageUseCase",
    "JavaCoverageStageUseCase",
    "JavaPublicationStageUseCase",
    "JavaScaStageUseCase",
    "java_scope_partition",
    "java_selection",
    "run_runtime_corroboration",
]
