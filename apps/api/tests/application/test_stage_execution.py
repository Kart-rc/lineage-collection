from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from lineage_api.application.stage_execution import (
    StageExecutionContext,
    StageExecutionResult,
    StageTargetMismatchError,
)
from lineage_api.application.stage_handlers import (
    ClassificationStageUseCase,
    ConsolidationStageUseCase,
    ControlStageUseCase,
    CoverageStageUseCase,
    DeploymentStageUseCase,
    PublicationStageUseCase,
    ProposalStageUseCase,
    RuntimeValidationStageUseCase,
)


def _context() -> StageExecutionContext:
    return StageExecutionContext(
        target="classification",
        workflow_kind="BASELINE",
        workflow_version="1.0.0",
        stage_id="B3",
        stage_name="CLASSIFY_REPOSITORY_AND_PATHS",
        command_id="cmd-001",
        correlation_id="corr-001",
        input_reference={
            "bucket": "evidence",
            "key": "intents/cmd-001.json",
            "versionId": "v1",
            "sha256": "a" * 64,
            "sizeBytes": 512,
        },
    )


def _input() -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "repo": "payments-pipeline",
        "digest": "sha256:source-v1",
        "env": "staging",
        "system": "payments",
        "evidence": [
            {
                "level": 4,
                "source": "manifest_structure",
                "class": "DATA_PIPELINE",
                "ref": "s3://evidence/repositories/payments-pipeline/manifest.json?versionId=v3",
            },
            {
                "level": 1,
                "source": "governed_catalog_metadata",
                "class": "DATA_PIPELINE",
                "ref": "catalog://repositories/payments-pipeline@42",
            },
        ],
    }


def _coverage_input() -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "artifactType": "classification-decision",
        "context": {
            "artifactDigest": "sha256:source-v1",
            "environment": "staging",
            "platform": "snowflake",
            "repository": "payments-pipeline",
            "system": "payments",
            "acceptedAt": "2026-08-08T15:00:00Z",
            "activeBaseVersion": "graph-v1",
            "activeBaseFence": 7,
            "catalogSnapshotRef": {
                "bucket": "evidence",
                "key": "catalog/catalog-v1.json",
                "versionId": "catalog-v1",
                "sha256": "c" * 64,
                "sizeBytes": 4096,
            },
            "catalogSnapshotId": "catalog-demo-v1",
            "resolverVersion": "1.0.0",
            "rulesetVersion": "python-demo-v1",
            "classificationPolicyVersion": "1.0.0",
            "runtimeManifestRefs": [],
            "repositorySource": {
                "bucket": "evidence",
                "key": "source/payments-pipeline.zip",
                "versionId": "source-v1",
                "sha256": "d" * 64,
                "sizeBytes": 4096,
            },
            "repositoryInventory": [
                "README.md",
                "job.scala",
                "pipeline.py",
                "src/helpers.py",
            ],
        },
        "decision": {
            "decisionId": "class-001",
            "repositoryClass": "DATA_PIPELINE",
            "policyVersion": "1.0.0",
            "status": "EVALUATED",
        },
    }


class RecordingArtifacts:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str, object, str]] = []

    def put(self, kind: str, key: str, body: object, version: str) -> dict[str, object]:
        self.writes.append((kind, key, body, version))
        return {
            "bucket": "evidence",
            "key": key,
            "versionId": f"v{len(self.writes)}",
            "sha256": f"{len(self.writes):064x}",
            "sizeBytes": 512,
        }

    def get(self, _reference: object) -> object:
        raise AssertionError("coverage planning must not read an unpinned secondary object")


def _runtime_reference(index: int = 1) -> dict[str, object]:
    return {
        "bucket": "evidence",
        "key": f"runtime/windows/window-{index}.json",
        "versionId": f"runtime-v{index}",
        "sha256": f"{index + 20:064x}",
        "sizeBytes": 2048,
    }


RUNTIME_ARTIFACT = "sha256:" + "a" * 64


def _complete_runtime_manifest(index: int = 1) -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "manifestId": f"runtime-window-manifest-{index}",
        "windowId": f"runtime-window-{index}",
        "leaseId": f"runtime-lease-{index}",
        "profileId": "payments-spark-openlineage",
        "profileVersion": "1.0.0",
        "workloadId": "payments-pipeline",
        "repo": "payments-pipeline",
        "environment": "staging",
        "artifactDigest": RUNTIME_ARTIFACT,
        "mechanism": "OPENLINEAGE",
        "outcome": "COMPLETE",
        "attempted": 2,
        "accepted": 2,
        "rejected": 0,
        "duplicates": 0,
        "retried": 0,
        "buffered": 0,
        "dropped": 0,
        "quarantined": 0,
        "drained": 2,
        "sourceChecksum": "sha256:" + "e" * 64,
        "observationChecksum": "sha256:" + "f" * 64,
        "reasons": [],
        "reasonCounts": {},
        "emitterCounts": {
            "spark-openlineage-v1": {
                "attempted": 2,
                "accepted": 2,
                "rejected": 0,
                "duplicates": 0,
                "retried": 0,
                "buffered": 0,
                "dropped": 0,
                "quarantined": 0,
                "drained": 2,
            }
        },
        "closedAt": "2026-08-08T12:05:00Z",
    }


class RuntimeArtifacts:
    def __init__(self, manifests: dict[str, object]) -> None:
        self.manifests = manifests
        self.reads: list[object] = []

    def get(self, reference: object) -> object:
        self.reads.append(reference)
        return deepcopy(self.manifests[reference["key"]])

    def put(self, *_args: object) -> object:
        raise AssertionError("runtime use case result is persisted by the executor")


FROM_URN = "urn:ldp:staging:snowflake:payments:raw.transactions#amount"
TO_URN = "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue"


def _assertion(mechanism: str, provenance_id: str) -> dict[str, object]:
    runtime = mechanism == "RUNTIME"
    return {
        "provenanceId": provenance_id,
        "from": [FROM_URN],
        "to": TO_URN,
        "edgeType": "DERIVES",
        "transform": None if runtime else "SUM(amount)",
        "mechanism": mechanism,
        "exact": not runtime,
        "evidenceRef": {
            "bucket": "evidence",
            "key": f"assertions/{provenance_id}.json",
            "versionId": "v1",
            "sha256": "7" * 64,
            "sizeBytes": 200,
        },
        "repo": "payments-pipeline",
        "runId": "run-001",
        "correlationId": "corr-consolidate",
        "runtimeScope": "ELEMENT" if runtime else None,
        "sessionComplete": True,
    }


class ConsolidationArtifacts:
    def __init__(self, documents: dict[str, object]) -> None:
        self.documents = documents
        self.writes: list[tuple[str, str, object, str]] = []

    def get(self, reference: object) -> object:
        return deepcopy(self.documents[reference["key"]])

    def put(self, kind: str, key: str, body: object, version: str) -> dict[str, object]:
        self.writes.append((kind, key, body, version))
        return {
            "bucket": "evidence",
            "key": key,
            "versionId": f"edge-v{len(self.writes)}",
            "sha256": f"{len(self.writes) + 40:064x}",
            "sizeBytes": 1024,
        }


class ProposalStore:
    def __init__(self) -> None:
        self.proposals: dict[str, dict[str, object]] = {}
        self.calls = 0

    def put_proposal(self, proposal: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        proposal_id = proposal["proposalId"]
        existing = self.proposals.setdefault(proposal_id, deepcopy(proposal))
        if existing != proposal:
            raise ValueError("proposal identity conflict")
        return deepcopy(existing)

    def get_proposal(self, proposal_id: str, version: int) -> dict[str, object] | None:
        proposal = self.proposals.get(proposal_id)
        return deepcopy(proposal) if proposal and proposal["version"] == version else None


class PublicationPointer:
    def __init__(self) -> None:
        self.pointer = {
            "environment": "staging",
            "graphVersion": "graph-v1",
            "fence": 7,
            "correlationId": "corr-prior",
        }
        self.activations = 0

    def active_pointer(self, environment: str) -> dict[str, object]:
        assert environment == "staging"
        return deepcopy(self.pointer)

    def activate_pointer(self, **request: object) -> dict[str, object]:
        if self.pointer["graphVersion"] == request["graph_version"]:
            return deepcopy(self.pointer)
        assert self.pointer["graphVersion"] == request["expected_prior"]
        assert self.pointer["fence"] == request["expected_fence"]
        self.activations += 1
        self.pointer = {
            "environment": request["environment"],
            "graphVersion": request["graph_version"],
            "fence": request["next_fence"],
            "correlationId": request["correlation_id"],
            "package": deepcopy(request["package_reference"]),
            "graphChecksum": request["graph_checksum"],
        }
        return deepcopy(self.pointer)


class PublicationProjection:
    def __init__(self, *, corrupt_readback: bool = False) -> None:
        self.namespaces: dict[str, list[dict[str, object]]] = {
            "graph-v1": [
                {
                    "edgeId": "edge-old-001",
                    "source": FROM_URN,
                    "target": TO_URN,
                    "type": "DERIVES",
                }
            ]
        }
        self.corrupt_readback = corrupt_readback

    def copy_namespace(self, source: str, target: str, *, fence: int) -> int:
        self.namespaces[target] = deepcopy(self.namespaces.get(source, []))
        return len(self.namespaces[target])

    def delete_edges(self, namespace: str, edge_ids: list[str]) -> int:
        before = len(self.namespaces[namespace])
        self.namespaces[namespace] = [
            row for row in self.namespaces[namespace] if row["edgeId"] not in edge_ids
        ]
        return before - len(self.namespaces[namespace])

    def merge_edges(
        self, namespace: str, edges: list[dict[str, object]], *, fence: int
    ) -> int:
        by_identity = {
            (row["edgeId"], row["source"], row["target"]): row
            for row in self.namespaces.setdefault(namespace, [])
        }
        for row in edges:
            by_identity[(row["edgeId"], row["source"], row["target"])] = deepcopy(row)
        self.namespaces[namespace] = list(by_identity.values())
        return len(edges)

    def namespace_checksum(self, namespace: str) -> list[dict[str, object]]:
        if self.corrupt_readback and namespace != "graph-v1":
            return []
        return [
            {
                key: value
                for key, value in row.items()
                if key in {"edgeId", "source", "target", "type"}
            }
            for row in deepcopy(self.namespaces.get(namespace, []))
        ]


class PackageArtifacts:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str, object, str]] = []
        self.references: dict[str, dict[str, object]] = {}

    def put(self, kind: str, key: str, body: object, version: str) -> dict[str, object]:
        self.writes.append((kind, key, deepcopy(body), version))
        return deepcopy(
            self.references.setdefault(
                key,
                {
                    "bucket": "packages",
                    "key": key,
                    "versionId": "package-v1",
                    "sha256": "8" * 64,
                    "sizeBytes": 2048,
                },
            )
        )

    def get(self, reference: object) -> object:
        raise AssertionError(f"unexpected package read: {reference}")


class DeploymentControl(PublicationPointer):
    def __init__(self, package: dict[str, object]) -> None:
        super().__init__()
        self.package = package
        self.event_digest: str | None = None
        self.state: dict[str, object] | None = None
        self.order_disposition = "AUTHORITATIVE"
        self.complete_calls = 0

    def claim_deployment_event(
        self, event: dict[str, object], event_digest: str
    ) -> dict[str, object]:
        if self.event_digest not in {None, event_digest}:
            raise ValueError("deployment event identity conflict")
        disposition = "CLAIMED" if self.event_digest is None else "DUPLICATE"
        self.event_digest = event_digest
        return {"disposition": disposition}

    def establish_deployment_order(
        self, event: dict[str, object]
    ) -> dict[str, object]:
        return {"disposition": self.order_disposition}

    def record_deployed_digest(self, event: dict[str, object]) -> None:
        self.state = {"eventId": event["eventId"], "status": "RECORDED"}

    def package_for(
        self, system: str, environment: str, artifact_digest: str
    ) -> dict[str, object] | None:
        if (
            system,
            environment,
            artifact_digest,
        ) == ("payments", "staging", "sha256:artifact-v2"):
            return deepcopy(self.package)
        return None

    def promote_deployment(self, **request: object) -> dict[str, object]:
        return self.activate_pointer(**request)

    def complete_deployment(
        self, event: dict[str, object], result: dict[str, object]
    ) -> dict[str, object]:
        self.complete_calls += 1
        self.state = {**deepcopy(result), "eventId": event["eventId"]}
        return deepcopy(self.state)

    def deployment_state(
        self, system: str, environment: str
    ) -> dict[str, object] | None:
        return deepcopy(self.state)


def _assertion_reference() -> dict[str, object]:
    return {
        "bucket": "evidence",
        "key": "assertion-sets/run-001.json",
        "versionId": "assertions-v1",
        "sha256": "6" * 64,
        "sizeBytes": 4096,
    }


def _consolidation_document() -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "context": {
            "repository": "payments-pipeline",
            "artifactDigest": RUNTIME_ARTIFACT,
            "environment": "staging",
            "system": "payments",
            "assertionRefs": [_assertion_reference()],
            "coverage": {
                "expectedScope": ["pipeline.py"],
                "completedScope": ["pipeline.py"],
                "reusedScope": [],
                "skippedScope": [],
                "unsupportedScope": [],
                "quarantinedScope": [],
                "failedScope": [],
            },
            "tombstoneEdgeIds": ["edge-removed-1"],
        },
    }


def _mark_runtime_incomplete(manifest: dict[str, object]) -> None:
    emitter = deepcopy(manifest["emitterCounts"])
    emitter["spark-openlineage-v1"]["dropped"] = 1
    manifest.update(
        outcome="INCOMPLETE",
        reasons=["DROPPED_OBSERVATION"],
        reasonCounts={"DROPPED_OBSERVATION": 1},
        emitterCounts=emitter,
        dropped=1,
    )


def test_classification_stage_emits_an_immutable_domain_decision_not_an_input_echo() -> None:
    result = ClassificationStageUseCase(policy_version="1.0.0").execute(
        _input(), _context()
    )

    assert result.artifact_kind == "classification-decision"
    assert result.schema_version == "1.0.0"
    assert result.document["artifactType"] == "classification-decision"
    assert result.document["decision"] == {
        "decisionId": result.document["decision"]["decisionId"],
        "repoOrPath": "payments-pipeline",
        "repositoryClass": "DATA_PIPELINE",
        "baselineTreatment": "INCLUDE",
        "onChangeTreatment": "INCREMENTAL_AND_NATIVE",
        "evidenceLevelUsed": 1,
        "evidenceRefs": ["catalog://repositories/payments-pipeline@42"],
        "policyVersion": "1.0.0",
        "status": "EVALUATED",
        "reason": None,
    }
    assert result.document["source"] == _context().input_reference
    assert result.document["context"] == {
        "artifactDigest": "sha256:source-v1",
        "environment": "staging",
        "repository": "payments-pipeline",
        "system": "payments",
    }
    assert "target" not in result.document
    assert "inputDocument" not in result.document
    assert "evidence" not in result.document


def test_classification_is_deterministic_across_evidence_order_and_records_unknown() -> None:
    use_case = ClassificationStageUseCase(policy_version="1.0.0")
    first = _input()
    second = deepcopy(first)
    second["evidence"] = list(reversed(second["evidence"]))  # type: ignore[arg-type]

    assert use_case.execute(first, _context()) == use_case.execute(second, _context())
    duplicated = deepcopy(first)
    duplicated["evidence"] = [*duplicated["evidence"], duplicated["evidence"][1]]  # type: ignore[index]
    assert use_case.execute(first, _context()) == use_case.execute(duplicated, _context())

    unknown = deepcopy(first)
    unknown["evidence"] = []
    result = use_case.execute(unknown, _context()).document["decision"]
    assert result["repositoryClass"] == "UNKNOWN"
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["reason"] == "NO_DECISIVE_EVIDENCE"


def test_classification_carries_only_the_bounded_repository_inputs_needed_by_coverage() -> None:
    body = _input()
    body["repositorySource"] = {
        "bucket": "evidence",
        "key": "source/payments-pipeline.zip",
        "versionId": "source-v1",
        "sha256": "d" * 64,
        "sizeBytes": 4096,
    }
    body["repositoryInventory"] = ["src/helpers.py", "pipeline.py", "pipeline.py"]

    result = ClassificationStageUseCase(policy_version="1.0.0").execute(body, _context())

    assert result.document["context"]["repositorySource"] == body["repositorySource"]
    assert result.document["context"]["repositoryInventory"] == [
        "pipeline.py",
        "src/helpers.py",
    ]


@pytest.mark.parametrize(
    "change",
    (
        lambda body: body.update(repo=""),
        lambda body: body.update(evidence="not-a-list"),
        lambda body: body["evidence"][0].update(level=8),
        lambda body: body["evidence"][0].__setitem__("class", "NOT_A_CLASS"),
        lambda body: body["evidence"][0].update(repositoryClass="APPLICATION_RUNTIME"),
    ),
)
def test_classification_rejects_malformed_or_unbounded_input(change) -> None:
    body = _input()
    change(body)

    with pytest.raises(ValueError):
        ClassificationStageUseCase(policy_version="1.0.0").execute(body, _context())


def test_stage_result_detaches_nested_mutable_input() -> None:
    nested = {"artifactType": "test-result", "payload": {"items": ["one"]}}
    result = StageExecutionResult("test-result", "1.0.0", nested)

    nested["payload"]["items"].append("two")

    assert result.document["payload"] == {"items": ["one"]}


def test_baseline_coverage_writes_a_bounded_plan_and_immutable_sca_work_units() -> None:
    artifacts = RecordingArtifacts()
    context = StageExecutionContext(
        target="coverage",
        workflow_kind="BASELINE",
        workflow_version="1.0.0",
        stage_id="B4",
        stage_name="BUILD_COVERAGE_PLAN",
        command_id="cmd-001",
        correlation_id="corr-001",
        input_reference=_context().input_reference,
    )

    result = CoverageStageUseCase(artifacts, max_scope=100, chunk_size=1).execute(
        _coverage_input(), context
    )

    assert result.artifact_kind == "work-inventory"
    assert result.document["artifactType"] == "work-inventory"
    assert result.document["coveragePlanRef"]["versionId"] == "v1"
    assert len(result.document["workUnitRefs"]) == 2
    assert [write[0] for write in artifacts.writes] == [
        "coverage-plan",
        "sca-work-unit",
        "sca-work-unit",
    ]
    plan = artifacts.writes[0][2]
    assert plan["coverage"] == {
        "expectedScope": ["README.md", "job.scala", "pipeline.py", "src/helpers.py"],
        "recomputedScope": ["pipeline.py", "src/helpers.py"],
        "skippedScope": ["README.md"],
        "unsupportedScope": ["job.scala"],
    }
    work_units = [write[2] for write in artifacts.writes[1:]]
    assert [unit["paths"] for unit in work_units] == [["pipeline.py"], ["src/helpers.py"]]
    assert all(unit["pack"] == "python-ast" for unit in work_units)
    assert all(unit["platform"] == "snowflake" for unit in work_units)
    assert all(unit["catalogSnapshotRef"]["versionId"] == "catalog-v1" for unit in work_units)
    assert all(unit["resolverVersion"] == "1.0.0" for unit in work_units)
    assert all(unit["activeBaseFence"] == 7 for unit in work_units)
    assert all(unit["coverage"]["expectedScope"] == unit["paths"] for unit in work_units)
    plan_reference = {
        "bucket": "evidence",
        "key": artifacts.writes[0][1],
        "versionId": "v1",
        "sha256": f"{1:064x}",
        "sizeBytes": 512,
    }
    assert all(unit["coveragePlan"] == plan_reference for unit in work_units)
    assert all("inputDocument" not in unit and "target" not in unit for unit in work_units)


def test_incremental_coverage_emits_a_typed_differential_plan_without_work_unit_writes() -> None:
    artifacts = RecordingArtifacts()
    context = StageExecutionContext(
        target="coverage",
        workflow_kind="INCREMENTAL",
        workflow_version="1.0.0",
        stage_id="I3",
        stage_name="BUILD_DIFFERENTIAL_COVERAGE_PLAN",
        command_id="cmd-002",
        correlation_id="corr-002",
        input_reference=_context().input_reference,
    )
    document = {
        "schemaVersion": "1.0.0",
        "context": {
            **_coverage_input()["context"],
            "changedPaths": ["src/helpers.py", "pipeline.py", "pipeline.py"],
            "removedPaths": ["old.py"],
        },
    }

    result = CoverageStageUseCase(artifacts, max_scope=100, chunk_size=20).execute(
        document, context
    )

    assert result.artifact_kind == "coverage-plan"
    assert result.document["artifactType"] == "coverage-plan"
    assert result.document["coverage"] == {
        "expectedScope": ["old.py", "pipeline.py", "src/helpers.py"],
        "recomputedScope": ["pipeline.py", "src/helpers.py"],
        "removedScope": ["old.py"],
        "reusedScope": [],
        "unsupportedScope": [],
    }
    assert artifacts.writes == []


def test_coverage_rejects_scope_overflow_before_writing_any_artifact() -> None:
    artifacts = RecordingArtifacts()
    document = _coverage_input()
    document["context"]["repositoryInventory"] = [f"src/{index}.py" for index in range(4)]
    context = StageExecutionContext(
        target="coverage",
        workflow_kind="BASELINE",
        workflow_version="1.0.0",
        stage_id="B4",
        stage_name="BUILD_COVERAGE_PLAN",
        command_id="cmd-003",
        correlation_id="corr-003",
        input_reference=_context().input_reference,
    )

    with pytest.raises(ValueError, match="scope limit"):
        CoverageStageUseCase(artifacts, max_scope=3, chunk_size=1).execute(document, context)

    assert artifacts.writes == []


@pytest.mark.parametrize(
    ("workflow_kind", "stage_id", "stage_name"),
    (
        ("BASELINE", "B6", "VALIDATE_OPTIONAL_RUNTIME_EVIDENCE"),
        ("INCREMENTAL", "I6", "VALIDATE_OPTIONAL_RUNTIME_EVIDENCE"),
    ),
)
def test_runtime_stage_validates_only_complete_artifact_bound_closing_manifests(
    workflow_kind: str, stage_id: str, stage_name: str
) -> None:
    reference = _runtime_reference()
    artifacts = RuntimeArtifacts({reference["key"]: _complete_runtime_manifest()})
    context = StageExecutionContext(
        target="runtime-validation",
        workflow_kind=workflow_kind,
        workflow_version="1.0.0",
        stage_id=stage_id,
        stage_name=stage_name,
        command_id="cmd-runtime",
        correlation_id="corr-runtime",
        input_reference=_context().input_reference,
    )
    document = {
        "schemaVersion": "1.0.0",
        "context": {
            "repository": "payments-pipeline",
            "artifactDigest": RUNTIME_ARTIFACT,
            "environment": "staging",
            "system": "payments",
            "runtimeManifestRefs": [reference],
        },
    }

    result = RuntimeValidationStageUseCase(artifacts).execute(document, context)

    assert result.artifact_kind == "runtime-validation"
    assert result.document["artifactType"] == "runtime-validation"
    assert result.document["runtimeCoverage"] == {
        "status": "VALIDATED",
        "windowIds": ["runtime-window-1"],
        "mechanisms": ["OPENLINEAGE"],
        "manifestRefs": [reference],
        "observationChecksums": ["sha256:" + "f" * 64],
        "accepted": 2,
        "drained": 2,
        "reasons": [],
    }
    assert "emitterCounts" not in result.document
    assert artifacts.reads == [reference]


def test_runtime_stage_records_optional_absence_without_reading_or_inventing_evidence() -> None:
    artifacts = RuntimeArtifacts({})
    context = StageExecutionContext(
        target="runtime-validation",
        workflow_kind="BASELINE",
        workflow_version="1.0.0",
        stage_id="B6",
        stage_name="VALIDATE_OPTIONAL_RUNTIME_EVIDENCE",
        command_id="cmd-runtime-none",
        correlation_id="corr-runtime-none",
        input_reference=_context().input_reference,
    )
    document = {
        "schemaVersion": "1.0.0",
        "context": {
            "repository": "payments-pipeline",
            "artifactDigest": RUNTIME_ARTIFACT,
            "environment": "staging",
            "system": "payments",
            "runtimeManifestRefs": [],
        },
    }

    result = RuntimeValidationStageUseCase(artifacts).execute(document, context)

    assert result.document["runtimeCoverage"] == {
        "status": "NOT_PROVIDED",
        "windowIds": [],
        "mechanisms": [],
        "manifestRefs": [],
        "observationChecksums": [],
        "accepted": 0,
        "drained": 0,
        "reasons": ["NO_RUNTIME_MANIFESTS"],
    }
    assert artifacts.reads == []


@pytest.mark.parametrize(
    ("workflow_kind", "stage_id", "stage_name"),
    (
        ("BASELINE", "B6", "VALIDATE_OPTIONAL_RUNTIME_EVIDENCE"),
        ("INCREMENTAL", "I6", "VALIDATE_OPTIONAL_RUNTIME_EVIDENCE"),
    ),
)
def test_runtime_stage_preserves_static_evidence_and_coverage_for_consolidation(
    workflow_kind: str, stage_id: str, stage_name: str
) -> None:
    artifacts = RuntimeArtifacts({})
    document = {
        "schemaVersion": "1.0.0",
        "context": {
            "repository": "payments-pipeline",
            "artifactDigest": RUNTIME_ARTIFACT,
            "environment": "staging",
            "system": "payments",
            "runtimeManifestRefs": [],
            "assertionRefs": [_assertion_reference()],
            "residueRefs": [_runtime_reference(7)],
            "coverage": {
                "expectedScope": ["pipeline.py"],
                "completedScope": ["pipeline.py"],
                "reusedScope": [],
                "skippedScope": [],
                "unsupportedScope": [],
                "quarantinedScope": [],
                "failedScope": [],
            },
            "tombstoneEdgeIds": ["edge-old-001"],
        },
    }
    context = StageExecutionContext(
        target="runtime-validation",
        workflow_kind=workflow_kind,
        workflow_version="1.0.0",
        stage_id=stage_id,
        stage_name=stage_name,
        command_id="cmd-runtime-carry",
        correlation_id="corr-runtime-carry",
        input_reference=_context().input_reference,
    )

    result = RuntimeValidationStageUseCase(artifacts).execute(document, context)

    assert result.document["context"]["assertionRefs"] == [_assertion_reference()]
    assert result.document["context"]["residueRefs"] == [_runtime_reference(7)]
    assert result.document["context"]["coverage"]["state"] == "COMPLETE"
    assert result.document["context"]["tombstoneEdgeIds"] == ["edge-old-001"]


@pytest.mark.parametrize(
    ("mutation", "status", "reason"),
    (
        (
            _mark_runtime_incomplete,
            "INCOMPLETE",
            "DROPPED_OBSERVATION",
        ),
        (
            lambda manifest: manifest.update(artifactDigest="sha256:" + "9" * 64),
            "QUARANTINED",
            "ARTIFACT_DIGEST_MISMATCH",
        ),
        (
            lambda manifest: manifest.update(dropped=1),
            "QUARANTINED",
            "INVALID_RUNTIME_MANIFEST",
        ),
    ),
)
def test_runtime_stage_never_promotes_incomplete_mismatched_or_invalid_evidence(
    mutation, status: str, reason: str
) -> None:
    reference = _runtime_reference()
    manifest = _complete_runtime_manifest()
    mutation(manifest)
    artifacts = RuntimeArtifacts({reference["key"]: manifest})
    context = StageExecutionContext(
        target="runtime-validation",
        workflow_kind="INCREMENTAL",
        workflow_version="1.0.0",
        stage_id="I6",
        stage_name="VALIDATE_OPTIONAL_RUNTIME_EVIDENCE",
        command_id="cmd-runtime-bad",
        correlation_id="corr-runtime-bad",
        input_reference=_context().input_reference,
    )
    document = {
        "schemaVersion": "1.0.0",
        "context": {
            "repository": "payments-pipeline",
            "artifactDigest": RUNTIME_ARTIFACT,
            "environment": "staging",
            "system": "payments",
            "runtimeManifestRefs": [reference],
        },
    }

    coverage = RuntimeValidationStageUseCase(artifacts).execute(document, context).document[
        "runtimeCoverage"
    ]

    assert coverage["status"] == status
    assert reason in coverage["reasons"]
    assert coverage["manifestRefs"] == []
    assert coverage["observationChecksums"] == []


def test_baseline_consolidation_persists_one_deterministic_edge_set_and_complete_coverage() -> None:
    reference = _assertion_reference()
    artifacts = ConsolidationArtifacts(
        {
            reference["key"]: {
                "schemaVersion": "1.0.0",
                "assertions": [
                    _assertion("RUNTIME", "runtime-001"),
                    _assertion("SCA", "sca-001"),
                    _assertion("SCA", "sca-001"),
                ],
            }
        }
    )
    context = StageExecutionContext(
        target="consolidation",
        workflow_kind="BASELINE",
        workflow_version="1.0.0",
        stage_id="B8",
        stage_name="CONSOLIDATE_AND_VERIFY_COVERAGE",
        command_id="cmd-consolidate",
        correlation_id="corr-consolidate",
        input_reference=_context().input_reference,
    )

    result = ConsolidationStageUseCase(artifacts).execute(
        _consolidation_document(), context
    )

    assert result.artifact_kind == "consolidation-result"
    assert result.document["artifactType"] == "consolidation-result"
    assert result.document["edgeCount"] == 1
    assert result.document["coverage"]["state"] == "COMPLETE"
    assert result.document["bands"] == {"HIGH": 1}
    assert result.document["tombstoneEdgeIds"] == []
    assert len(result.document["edgeIds"]) == 1
    assert [write[0] for write in artifacts.writes] == ["consolidated-edge-set"]
    edges = artifacts.writes[0][2]["edges"]
    assert len(edges) == 1
    assert edges[0]["band"] == "HIGH"
    assert edges[0]["corroboration"] == "ELEMENT"
    assert [item["provenanceId"] for item in edges[0]["provenance"]] == [
        "runtime-001",
        "sca-001",
    ]


@pytest.mark.parametrize(
    ("workflow_kind", "stage_id", "stage_name", "expected_tombstones"),
    (
        (
            "INCREMENTAL",
            "I7",
            "CONSOLIDATE_DELTAS_AND_TOMBSTONES",
            ["edge-removed-1"],
        ),
        ("PR_GATE", "P4", "ANALYZE_RELEVANT_CHANGED_PATHS", []),
    ),
)
def test_incremental_and_pr_gate_consolidation_use_the_same_derivation(
    workflow_kind: str,
    stage_id: str,
    stage_name: str,
    expected_tombstones: list[str],
) -> None:
    reference = _assertion_reference()
    artifacts = ConsolidationArtifacts(
        {
            reference["key"]: {
                "schemaVersion": "1.0.0",
                "assertions": [_assertion("SCA", "sca-001")],
            }
        }
    )
    context = StageExecutionContext(
        target="consolidation",
        workflow_kind=workflow_kind,
        workflow_version="1.0.0",
        stage_id=stage_id,
        stage_name=stage_name,
        command_id=f"cmd-{stage_id.lower()}",
        correlation_id="corr-consolidate",
        input_reference=_context().input_reference,
    )

    result = ConsolidationStageUseCase(artifacts).execute(
        _consolidation_document(), context
    )

    assert result.document["edgeCount"] == 1
    assert result.document["bands"] == {"SINGLE": 1}
    assert result.document["tombstoneEdgeIds"] == expected_tombstones


def test_baseline_residue_stage_records_disabled_policy_and_carries_only_refs() -> None:
    artifacts = ConsolidationArtifacts({})
    context = StageExecutionContext(
        target="consolidation",
        workflow_kind="BASELINE",
        workflow_version="1.0.0",
        stage_id="B7",
        stage_name="ANALYZE_RESIDUE_WITH_POLICY",
        command_id="cmd-b7",
        correlation_id="corr-consolidate",
        input_reference=_context().input_reference,
    )
    document = _consolidation_document()
    document["context"]["residueRefs"] = [_runtime_reference(7)]

    result = ConsolidationStageUseCase(artifacts).execute(document, context)

    assert result.artifact_kind == "residue-decision"
    assert result.document["residue"] == {
        "status": "SKIPPED_WITH_RECORD",
        "count": 1,
        "reason": "LLM_NOT_CONFIGURED",
    }
    assert result.document["context"]["assertionRefs"] == [_assertion_reference()]
    assert result.document["context"]["residueRefs"] == [_runtime_reference(7)]
    assert artifacts.writes == []


def test_consolidation_rejects_conflicting_reuse_of_one_provenance_identity() -> None:
    reference = _assertion_reference()
    first = _assertion("SCA", "shared-provenance")
    second = {**first, "transform": "amount * 2"}
    artifacts = ConsolidationArtifacts(
        {
            reference["key"]: {
                "schemaVersion": "1.0.0",
                "assertions": [first, second],
            }
        }
    )
    context = StageExecutionContext(
        target="consolidation",
        workflow_kind="INCREMENTAL",
        workflow_version="1.0.0",
        stage_id="I7",
        stage_name="CONSOLIDATE_DELTAS_AND_TOMBSTONES",
        command_id="cmd-conflict",
        correlation_id="corr-consolidate",
        input_reference=_context().input_reference,
    )

    with pytest.raises(ValueError, match="provenance identity conflict"):
        ConsolidationStageUseCase(artifacts).execute(_consolidation_document(), context)

    assert artifacts.writes == []


@pytest.mark.parametrize(
    "mutation",
    (
        lambda assertion: assertion.update(repo="another-repository"),
        lambda assertion: assertion.update(
            to="urn:ldp:staging:snowflake:another_system:analytics.daily_revenue#gross_revenue"
        ),
        lambda assertion: assertion.update(
            to="urn:ldp:production:snowflake:payments:analytics.daily_revenue#gross_revenue"
        ),
    ),
)
def test_consolidation_rejects_assertions_outside_the_pinned_scope(mutation) -> None:
    reference = _assertion_reference()
    assertion = _assertion("SCA", "sca-out-of-scope")
    mutation(assertion)
    artifacts = ConsolidationArtifacts(
        {
            reference["key"]: {
                "schemaVersion": "1.0.0",
                "assertions": [assertion],
            }
        }
    )
    context = StageExecutionContext(
        target="consolidation",
        workflow_kind="INCREMENTAL",
        workflow_version="1.0.0",
        stage_id="I7",
        stage_name="CONSOLIDATE_DELTAS_AND_TOMBSTONES",
        command_id="cmd-out-of-scope",
        correlation_id="corr-consolidate",
        input_reference=_context().input_reference,
    )

    with pytest.raises(ValueError, match="assertion scope mismatch"):
        ConsolidationStageUseCase(artifacts).execute(_consolidation_document(), context)

    assert artifacts.writes == []


def test_consolidation_rejects_unbounded_or_secret_bearing_citation_metadata() -> None:
    reference = _assertion_reference()
    assertion = _assertion("SCA", "sca-secret-citation")
    assertion["citation"] = {
        "file": "pipeline.py",
        "line": 10,
        "astPath": "Module.Assign",
        "secret": "must-not-cross-the-boundary",
    }
    artifacts = ConsolidationArtifacts(
        {
            reference["key"]: {
                "schemaVersion": "1.0.0",
                "assertions": [assertion],
            }
        }
    )
    context = StageExecutionContext(
        target="consolidation",
        workflow_kind="BASELINE",
        workflow_version="1.0.0",
        stage_id="B8",
        stage_name="CONSOLIDATE_AND_VERIFY_COVERAGE",
        command_id="cmd-secret-citation",
        correlation_id="corr-consolidate",
        input_reference=_context().input_reference,
    )

    with pytest.raises(ValueError, match="citation schema"):
        ConsolidationStageUseCase(artifacts).execute(_consolidation_document(), context)

    assert artifacts.writes == []


def _proposal_input(*, edge_count: int = 1, coverage_state: str = "COMPLETE") -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "artifactType": "consolidation-result",
        "context": {
            "repository": "payments-pipeline",
            "artifactDigest": RUNTIME_ARTIFACT,
            "environment": "staging",
            "system": "payments",
            "activeBaseVersion": "graph-v1",
            "acceptedAt": "2026-08-08T12:00:00Z",
        },
        "edgeSetRef": {
            "bucket": "evidence",
            "key": "commands/cmd-proposal/edge-sets/I7.json",
            "versionId": "edges-v1",
            "sha256": "5" * 64,
            "sizeBytes": 2048,
        },
        "edgeIds": ["edge-001"] if edge_count else [],
        "edgeCount": edge_count,
        "bands": {"HIGH": edge_count} if edge_count else {},
        "coverage": {
            "state": coverage_state,
            "expectedScope": ["pipeline.py"],
            "completedScope": ["pipeline.py"] if coverage_state == "COMPLETE" else [],
            "reusedScope": [],
            "skippedScope": [],
            "unsupportedScope": [] if coverage_state == "COMPLETE" else ["pipeline.py"],
            "quarantinedScope": [],
            "failedScope": [],
        },
        "tombstoneEdgeIds": ["edge-old-001"],
    }


def _edge_set() -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "artifactType": "consolidated-edge-set",
        "edges": [
            {
                "schemaVersion": "1.0.0",
                "edgeKey": "edge-001",
                "version": 1,
                "from": [FROM_URN],
                "to": TO_URN,
                "edgeType": "DERIVES",
                "band": "HIGH",
                "corroboration": "ELEMENT",
                "status": "PROPOSED",
                "provenance": [_assertion("SCA", "sca-001")],
                "autoPublishable": True,
                "system": "payments",
                "transform": "SUM(amount)",
            }
        ],
    }


@pytest.mark.parametrize(
    ("workflow_kind", "stage_id", "stage_name", "proposal_type"),
    (
        ("BASELINE", "B9", "CREATE_PROPOSAL_OR_AUTOPUBLISH_DECISION", "BASELINE"),
        ("INCREMENTAL", "I9", "CREATE_DELTA_PROPOSAL", "DELTA"),
        ("NIGHTLY", "N6", "EMIT_PROPOSALS_ALERTS_AND_EVIDENCE", "RECONCILIATION"),
    ),
)
def test_proposal_stages_conditionally_persist_one_reference_based_proposal(
    workflow_kind: str, stage_id: str, stage_name: str, proposal_type: str
) -> None:
    document = _proposal_input()
    edge_reference = document["edgeSetRef"]
    artifacts = ConsolidationArtifacts({edge_reference["key"]: _edge_set()})
    store = ProposalStore()
    context = StageExecutionContext(
        target="proposal",
        workflow_kind=workflow_kind,
        workflow_version="1.0.0",
        stage_id=stage_id,
        stage_name=stage_name,
        command_id=f"cmd-{stage_id.lower()}",
        correlation_id="corr-proposal",
        input_reference=_context().input_reference,
    )
    use_case = ProposalStageUseCase(artifacts, store)

    first = use_case.execute(document, context)
    replay = use_case.execute(document, context)

    assert replay == first
    assert first.artifact_kind == "proposal-decision"
    assert first.document["decision"] == "PROPOSAL_CREATED"
    proposal = first.document["proposal"]
    assert proposal["proposalType"] == proposal_type
    assert proposal["state"] == "IN_REVIEW"
    assert proposal["expectedBaseVersion"] == "graph-v1"
    assert proposal["diff"] == {
        "edgeSetRef": edge_reference,
        "addedEdgeIds": ["edge-001"],
        "removedEdgeIds": ["edge-old-001"],
        "bandChangedEdgeIds": [],
    }
    assert "edges" not in proposal
    assert store.calls == 2
    assert len(store.proposals) == 1


@pytest.mark.parametrize(
    ("document", "decision"),
    (
        (_proposal_input(edge_count=0), "NO_LINEAGE"),
        (_proposal_input(coverage_state="INCOMPLETE"), "INCOMPLETE_COVERAGE"),
    ),
)
def test_proposal_stage_records_no_proposal_decisions_without_mutating_dynamodb(
    document: dict[str, object], decision: str
) -> None:
    artifacts = ConsolidationArtifacts({})
    store = ProposalStore()
    context = StageExecutionContext(
        target="proposal",
        workflow_kind="BASELINE",
        workflow_version="1.0.0",
        stage_id="B9",
        stage_name="CREATE_PROPOSAL_OR_AUTOPUBLISH_DECISION",
        command_id="cmd-no-proposal",
        correlation_id="corr-proposal",
        input_reference=_context().input_reference,
    )

    result = ProposalStageUseCase(artifacts, store).execute(document, context)

    assert result.document["decision"] == decision
    assert result.document["proposal"] is None
    assert store.calls == 0
    assert artifacts.writes == []


@pytest.mark.parametrize(
    "mutation",
    (
        lambda document: document.update(edgeCount=2),
        lambda document: document["context"].update(acceptedAt="not-a-timestamp"),
    ),
)
def test_proposal_stage_rejects_drift_before_persisting(
    mutation: object,
) -> None:
    document = _proposal_input()
    mutation(document)
    edge_reference = document["edgeSetRef"]
    artifacts = ConsolidationArtifacts({edge_reference["key"]: _edge_set()})
    store = ProposalStore()
    context = StageExecutionContext(
        target="proposal",
        workflow_kind="BASELINE",
        workflow_version="1.0.0",
        stage_id="B9",
        stage_name="CREATE_PROPOSAL_OR_AUTOPUBLISH_DECISION",
        command_id="cmd-drifted-proposal",
        correlation_id="corr-proposal",
        input_reference=_context().input_reference,
    )

    with pytest.raises(ValueError):
        ProposalStageUseCase(artifacts, store).execute(document, context)

    assert store.calls == 0


def test_proposal_stage_rejects_an_edge_set_from_another_system() -> None:
    document = _proposal_input()
    edge_reference = document["edgeSetRef"]
    edge_set = _edge_set()
    edge_set["edges"][0]["system"] = "orders"
    artifacts = ConsolidationArtifacts({edge_reference["key"]: edge_set})
    store = ProposalStore()
    context = StageExecutionContext(
        target="proposal",
        workflow_kind="BASELINE",
        workflow_version="1.0.0",
        stage_id="B9",
        stage_name="CREATE_PROPOSAL_OR_AUTOPUBLISH_DECISION",
        command_id="cmd-cross-system-proposal",
        correlation_id="corr-proposal",
        input_reference=_context().input_reference,
    )

    with pytest.raises(ValueError, match="system boundaries"):
        ProposalStageUseCase(artifacts, store).execute(document, context)

    assert store.calls == 0


def _approved_proposal_decision() -> dict[str, object]:
    document = _proposal_input()
    return {
        "schemaVersion": "1.0.0",
        "artifactType": "proposal-decision",
        "context": document["context"],
        "decision": "PROPOSAL_CREATED",
        "proposal": {
            "schemaVersion": "1.0.0",
            "proposalId": "proposal-approved",
            "version": 2,
            "proposalType": "DELTA",
            "system": "payments",
            "environment": "staging",
            "state": "APPROVED",
            "expectedBaseVersion": "graph-v1",
            "diff": {
                "edgeSetRef": document["edgeSetRef"],
                "addedEdgeIds": ["edge-001"],
                "removedEdgeIds": ["edge-old-001"],
                "bandChangedEdgeIds": [],
            },
            "approvalRef": {
                "bucket": "evidence",
                "key": "approvals/proposal-approved.json",
                "versionId": "approval-v1",
                "sha256": "9" * 64,
                "sizeBytes": 800,
            },
            "approvedAt": "2026-08-08T13:00:00Z",
            "correlationId": "corr-publish",
            "createdAt": "2026-08-08T12:00:00Z",
            "lockVersion": 2,
        },
    }


@pytest.mark.parametrize(
    ("workflow_kind", "stage_id", "stage_name"),
    (
        ("BASELINE", "B10", "STAGE_VERIFY_FENCE_AND_ACTIVATE"),
        ("INCREMENTAL", "I10", "PUBLISH_WITH_FENCED_PROTOCOL"),
    ),
)
def test_publication_stages_build_verify_and_atomically_activate_a_reference_package(
    workflow_kind: str, stage_id: str, stage_name: str
) -> None:
    document = _approved_proposal_decision()
    edge_reference = document["proposal"]["diff"]["edgeSetRef"]
    artifacts = ConsolidationArtifacts({edge_reference["key"]: _edge_set()})
    packages = PackageArtifacts()
    pointer = PublicationPointer()
    projection = PublicationProjection()
    context = StageExecutionContext(
        target="publication",
        workflow_kind=workflow_kind,
        workflow_version="1.0.0",
        stage_id=stage_id,
        stage_name=stage_name,
        command_id=f"cmd-{stage_id.lower()}",
        correlation_id="corr-publish",
        input_reference=_context().input_reference,
    )
    use_case = PublicationStageUseCase(artifacts, packages, pointer, projection)

    first = use_case.execute(document, context)
    replay = use_case.execute(document, context)

    assert replay == first
    assert first.artifact_kind == "publication-receipt"
    assert first.document["terminalOutcome"] == "PUBLISHED"
    assert first.document["pointer"]["fence"] == 8
    assert first.document["pointer"]["graphVersion"].startswith("graph-")
    assert first.document["graphChecksum"]
    assert first.document["packageRef"]["bucket"] == "packages"
    assert pointer.activations == 1
    assert len(packages.writes) == 2
    package = packages.writes[0][2]
    assert package["edgeSetRef"] == edge_reference
    assert "edges" not in package


def test_publication_does_not_mutate_an_unapproved_proposal() -> None:
    document = _approved_proposal_decision()
    document["proposal"]["state"] = "IN_REVIEW"
    artifacts = ConsolidationArtifacts({})
    packages = PackageArtifacts()
    pointer = PublicationPointer()
    projection = PublicationProjection()
    context = StageExecutionContext(
        target="publication",
        workflow_kind="BASELINE",
        workflow_version="1.0.0",
        stage_id="B10",
        stage_name="STAGE_VERIFY_FENCE_AND_ACTIVATE",
        command_id="cmd-awaiting-approval",
        correlation_id="corr-publish",
        input_reference=_context().input_reference,
    )

    result = PublicationStageUseCase(
        artifacts, packages, pointer, projection
    ).execute(document, context)

    assert result.document["terminalOutcome"] == "AWAITING_APPROVAL"
    assert pointer.activations == 0
    assert packages.writes == []


def test_publication_verification_mismatch_never_advances_the_pointer() -> None:
    document = _approved_proposal_decision()
    edge_reference = document["proposal"]["diff"]["edgeSetRef"]
    artifacts = ConsolidationArtifacts({edge_reference["key"]: _edge_set()})
    packages = PackageArtifacts()
    pointer = PublicationPointer()
    projection = PublicationProjection(corrupt_readback=True)
    context = StageExecutionContext(
        target="publication",
        workflow_kind="INCREMENTAL",
        workflow_version="1.0.0",
        stage_id="I10",
        stage_name="PUBLISH_WITH_FENCED_PROTOCOL",
        command_id="cmd-corrupt-publication",
        correlation_id="corr-publish",
        input_reference=_context().input_reference,
    )

    with pytest.raises(ValueError, match="staged projection verification failed"):
        PublicationStageUseCase(artifacts, packages, pointer, projection).execute(
            document, context
        )

    assert pointer.activations == 0
    assert packages.writes == []


def test_publication_rejects_a_stale_expected_base_before_staging() -> None:
    document = _approved_proposal_decision()
    edge_reference = document["proposal"]["diff"]["edgeSetRef"]
    artifacts = ConsolidationArtifacts({edge_reference["key"]: _edge_set()})
    packages = PackageArtifacts()
    pointer = PublicationPointer()
    pointer.pointer["graphVersion"] = "graph-newer"
    projection = PublicationProjection()
    before = deepcopy(projection.namespaces)
    context = StageExecutionContext(
        target="publication",
        workflow_kind="BASELINE",
        workflow_version="1.0.0",
        stage_id="B10",
        stage_name="STAGE_VERIFY_FENCE_AND_ACTIVATE",
        command_id="cmd-stale-publication",
        correlation_id="corr-publish",
        input_reference=_context().input_reference,
    )

    with pytest.raises(ValueError, match="differs from the approved proposal base"):
        PublicationStageUseCase(artifacts, packages, pointer, projection).execute(
            document, context
        )

    assert projection.namespaces == before
    assert packages.writes == []
    assert pointer.activations == 0


def test_nightly_publication_stage_verifies_the_exact_projection_checksum() -> None:
    document = _approved_proposal_decision()
    edge_reference = document["proposal"]["diff"]["edgeSetRef"]
    artifacts = ConsolidationArtifacts({edge_reference["key"]: _edge_set()})
    packages = PackageArtifacts()
    pointer = PublicationPointer()
    projection = PublicationProjection()
    publish_context = StageExecutionContext(
        target="publication",
        workflow_kind="BASELINE",
        workflow_version="1.0.0",
        stage_id="B10",
        stage_name="STAGE_VERIFY_FENCE_AND_ACTIVATE",
        command_id="cmd-publish-for-nightly",
        correlation_id="corr-publish",
        input_reference=_context().input_reference,
    )
    use_case = PublicationStageUseCase(artifacts, packages, pointer, projection)
    published = use_case.execute(document, publish_context).document
    nightly_context = StageExecutionContext(
        target="publication",
        workflow_kind="NIGHTLY",
        workflow_version="1.0.0",
        stage_id="N3",
        stage_name="VERIFY_PROJECTION_CHECKSUMS",
        command_id="cmd-nightly-verify",
        correlation_id="corr-nightly",
        input_reference=_context().input_reference,
    )

    verified = use_case.execute(published, nightly_context)

    assert verified.artifact_kind == "projection-verification"
    assert verified.document["status"] == "VERIFIED"
    assert verified.document["graphVersion"] == published["graphVersion"]


_DEPLOYMENT_STAGES = (
    ("D1", "AUTHENTICATE_AND_DEDUPLICATE_OUTCOME"),
    ("D2", "ESTABLISH_AUTHORITATIVE_ORDERING"),
    ("D3", "RECORD_ACTUAL_DEPLOYED_DIGEST"),
    ("D4", "RESOLVE_EXACT_APPROVED_LINEAGE_PACKAGE"),
    ("D5", "RESERVE_FENCE_AND_PROMOTE"),
    ("D6", "READ_BACK_AND_VERIFY_CORRELATION"),
)


def _deployment_context(stage_id: str, stage_name: str) -> StageExecutionContext:
    return StageExecutionContext(
        target="deployment",
        workflow_kind="DEPLOYMENT",
        workflow_version="1.0.0",
        stage_id=stage_id,
        stage_name=stage_name,
        command_id="cmd-deployment",
        correlation_id="corr-deployment",
        input_reference=_context().input_reference,
    )


def _deployment_fixture() -> tuple[
    dict[str, object],
    ConsolidationArtifacts,
    ConsolidationArtifacts,
    DeploymentControl,
    PublicationProjection,
]:
    event = {
        "schemaVersion": "1.0.0",
        "eventId": "deploy-002",
        "eventType": "DEPLOYMENT",
        "provider": "github-actions",
        "providerSequence": 2,
        "attempt": 1,
        "system": "payments",
        "environment": "staging",
        "outcome": "SUCCEEDED",
        "artifactDigest": "sha256:artifact-v2",
        "correlationId": "corr-deployment",
        "auditRef": "provider-audit://deploy-002",
        "occurredAt": "2026-08-08T14:00:00Z",
    }
    event_digest = hashlib.sha256(
        json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    authentication_ref = {
        "bucket": "evidence",
        "key": "deployment-auth/deploy-002.json",
        "versionId": "auth-v1",
        "sha256": "3" * 64,
        "sizeBytes": 600,
    }
    input_document = {
        "schemaVersion": "1.0.0",
        "artifactType": "deployment-event",
        "context": {
            "repository": "payments-pipeline",
            "artifactDigest": "sha256:artifact-v2",
            "environment": "staging",
            "system": "payments",
            "acceptedAt": "2026-08-08T14:00:01Z",
        },
        "event": event,
        "authenticationRef": authentication_ref,
    }
    rows = [
        {
            "edgeId": "edge-001",
            "source": FROM_URN,
            "target": TO_URN,
            "type": "DERIVES",
        }
    ]
    graph_checksum = hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    package_ref = {
        "bucket": "packages",
        "key": "packages/staging/package-deploy-v2.json",
        "versionId": "package-v2",
        "sha256": "4" * 64,
        "sizeBytes": 1800,
    }
    package = {
        "schemaVersion": "1.0.0",
        "artifactType": "lineage-package",
        "packageId": "package-deploy-v2",
        "system": "payments",
        "environment": "staging",
        "artifactDigest": "sha256:artifact-v2",
        "graphVersion": "graph-v2",
        "graphChecksum": graph_checksum,
        "approvalRef": _approved_proposal_decision()["proposal"]["approvalRef"],
        "edgeSetRef": _proposal_input()["edgeSetRef"],
    }
    evidence = ConsolidationArtifacts(
        {
            authentication_ref["key"]: {
                "schemaVersion": "1.0.0",
                "artifactType": "deployment-authentication-receipt",
                "decision": "AUTHENTICATED",
                "eventDigest": event_digest,
                "provider": "github-actions",
                "principal": "repo:payments/actions/deploy",
                "verifiedAt": "2026-08-08T14:00:00Z",
            }
        }
    )
    packages = ConsolidationArtifacts({package_ref["key"]: package})
    registry = {
        "packageReference": package_ref,
        "packageId": "package-deploy-v2",
        "graphVersion": "graph-v2",
        "graphChecksum": graph_checksum,
    }
    control = DeploymentControl(registry)
    projection = PublicationProjection()
    projection.namespaces["graph-v2"] = rows
    return input_document, evidence, packages, control, projection


def test_deployment_d1_through_d6_promotes_only_the_authenticated_exact_package() -> None:
    document, evidence, packages, control, projection = _deployment_fixture()
    use_case = DeploymentStageUseCase(evidence, packages, control, projection)

    for stage_id, stage_name in _DEPLOYMENT_STAGES:
        result = use_case.execute(
            document, _deployment_context(stage_id, stage_name)
        )
        assert result.artifact_kind == f"deployment-{stage_id.lower()}-result"
        document = result.document

    assert document["terminalOutcome"] == "PROMOTED"
    assert document["deployedArtifactDigest"] == "sha256:artifact-v2"
    assert document["lineagePackageId"] == "package-deploy-v2"
    assert document["graphVersion"] == "graph-v2"
    assert document["pointer"]["fence"] == 8
    assert control.pointer["graphVersion"] == "graph-v2"
    assert control.state["eventId"] == "deploy-002"


def test_deployment_d1_rejects_an_unbound_authentication_receipt_before_claim() -> None:
    document, evidence, packages, control, projection = _deployment_fixture()
    evidence.documents[document["authenticationRef"]["key"]]["eventDigest"] = "0" * 64

    with pytest.raises(ValueError, match="authentication receipt"):
        DeploymentStageUseCase(evidence, packages, control, projection).execute(
            document, _deployment_context(*_DEPLOYMENT_STAGES[0])
        )

    assert control.event_digest is None
    assert control.activations == 0


@pytest.mark.parametrize(
    ("event_type", "outcome", "reason"),
    (
        ("DEPLOYMENT", "FAILED", "DEPLOYMENT_FAILED"),
        ("MERGE", "SUCCEEDED", "NON_DEPLOYMENT_EVENT"),
    ),
)
def test_non_successful_deployment_paths_never_move_the_pointer(
    event_type: str, outcome: str, reason: str
) -> None:
    document, evidence, packages, control, projection = _deployment_fixture()
    event = document["event"]
    event["eventType"] = event_type
    event["outcome"] = outcome
    event.pop("artifactDigest")
    document["context"]["artifactDigest"] = "NONE"
    event_digest = hashlib.sha256(
        json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    auth_key = document["authenticationRef"]["key"]
    evidence.documents[auth_key]["eventDigest"] = event_digest
    use_case = DeploymentStageUseCase(evidence, packages, control, projection)

    for stage_id, stage_name in _DEPLOYMENT_STAGES:
        result = use_case.execute(
            document, _deployment_context(stage_id, stage_name)
        )
        document = result.document

    assert document["terminalOutcome"] == "FAILED_NO_CHANGE"
    assert document["reason"] == reason
    assert control.activations == 0
    assert control.pointer["graphVersion"] == "graph-v1"
    if event_type == "MERGE":
        assert control.event_digest is None


def test_stale_deployment_order_does_not_overwrite_the_authoritative_state() -> None:
    document, evidence, packages, control, projection = _deployment_fixture()
    control.order_disposition = "STALE"
    use_case = DeploymentStageUseCase(evidence, packages, control, projection)
    for stage_id, stage_name in _DEPLOYMENT_STAGES[:2]:
        document = use_case.execute(
            document, _deployment_context(stage_id, stage_name)
        ).document

    assert document["terminalOutcome"] == "FAILED_NO_CHANGE"
    assert document["reason"] == "STALE_DEPLOYMENT_EVENT"
    assert control.complete_calls == 0
    assert control.activations == 0


_CONTROL_STAGES = {
    "B1": ("BASELINE", "ACCEPT_AND_DEDUPLICATE_INTENT"),
    "B2": ("BASELINE", "PIN_REPOSITORY_AND_DETERMINANTS"),
    "I1": ("INCREMENTAL", "DEDUPLICATE_AND_PIN_ACTIVE_BASE"),
    "I2": ("INCREMENTAL", "COMPUTE_CHANGED_PATHS_AND_CLOSURE"),
    "I4": ("INCREMENTAL", "FETCH_REQUIRED_IMMUTABLE_ARTIFACTS"),
    "I8": ("INCREMENTAL", "RECHECK_BASE_AND_COVERAGE"),
}


def _control_context(stage_id: str) -> StageExecutionContext:
    workflow_kind, stage_name = _CONTROL_STAGES[stage_id]
    return StageExecutionContext(
        target="control-stage",
        workflow_kind=workflow_kind,
        workflow_version="1.0.0",
        stage_id=stage_id,
        stage_name=stage_name,
        command_id=f"cmd-{stage_id.lower()}",
        correlation_id="corr-control",
        input_reference=_context().input_reference,
    )


def _control_intent() -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "artifactType": "lineage-intent",
        "context": {
            "repository": "payments-pipeline",
            "artifactDigest": RUNTIME_ARTIFACT,
            "environment": "staging",
            "platform": "snowflake",
            "system": "payments",
            "acceptedAt": "2026-08-08T15:00:00Z",
            "repositorySource": {
                "bucket": "evidence",
                "key": "sources/payments-v2.zip",
                "versionId": "source-v2",
                "sha256": "d" * 64,
                "sizeBytes": 4096,
            },
            "repositoryInventory": ["pipeline.py", "README.md"],
            "catalogSnapshotId": "catalog-2026-08-08",
            "catalogSnapshotRef": {
                "bucket": "evidence",
                "key": "catalog/catalog-2026-08-08.json",
                "versionId": "catalog-2026-08-08",
                "sha256": "c" * 64,
                "sizeBytes": 4096,
            },
            "resolverVersion": "resolver-2",
            "rulesetVersion": "rules-3",
            "classificationPolicyVersion": "1.0.0",
            "changedPaths": ["pipeline.py"],
            "removedPaths": ["legacy.py"],
            "dependencyClosure": ["shared.py"],
            "runtimeManifestRefs": [_runtime_reference(1)],
        },
        "classificationEvidence": [
            {
                "level": 4,
                "source": "APPLICATION_RUNTIME",
                "repositoryClass": "DATA_PIPELINE",
                "ref": "package-json",
            }
        ],
    }


def test_baseline_control_stages_pin_pointer_snapshots_and_feed_classification() -> None:
    artifacts = ConsolidationArtifacts({})
    pointer = PublicationPointer()
    use_case = ControlStageUseCase(artifacts, pointer)

    b1 = use_case.execute(_control_intent(), _control_context("B1"))
    b2 = use_case.execute(b1.document, _control_context("B2"))
    classified = ClassificationStageUseCase(policy_version="1.0.0").execute(
        b2.document,
        StageExecutionContext(
            target="classification",
            workflow_kind="BASELINE",
            workflow_version="1.0.0",
            stage_id="B3",
            stage_name="CLASSIFY_REPOSITORY_AND_PATHS",
            command_id="cmd-b3",
            correlation_id="corr-control",
            input_reference=_context().input_reference,
        ),
    )

    assert b1.artifact_kind == "baseline-intent"
    assert b1.document["context"]["activeBaseVersion"] == "graph-v1"
    assert b1.document["context"]["activeBaseFence"] == 7
    assert b2.artifact_kind == "baseline-pins"
    assert b2.document["pins"] == {
        "catalogSnapshotRef": _control_intent()["context"]["catalogSnapshotRef"],
        "catalogSnapshotId": "catalog-2026-08-08",
        "classificationPolicyVersion": "1.0.0",
        "resolverVersion": "resolver-2",
        "rulesetVersion": "rules-3",
    }
    assert classified.document["context"]["activeBaseVersion"] == "graph-v1"
    assert classified.document["context"]["repositoryInventory"] == [
        "README.md",
        "pipeline.py",
    ]
    assert classified.document["context"]["runtimeManifestRefs"][0]["versionId"] == (
        "runtime-v1"
    )


def test_incremental_control_stages_close_scope_and_recheck_the_exact_base() -> None:
    artifacts = ConsolidationArtifacts({})
    pointer = PublicationPointer()
    use_case = ControlStageUseCase(artifacts, pointer)

    i1 = use_case.execute(_control_intent(), _control_context("I1"))
    i2 = use_case.execute(i1.document, _control_context("I2"))
    coverage = {
        "schemaVersion": "1.0.0",
        "artifactType": "coverage-plan",
        "context": i2.document["context"],
        "coverage": {
            "expectedScope": ["legacy.py", "pipeline.py", "shared.py"],
            "recomputedScope": ["pipeline.py", "shared.py"],
            "removedScope": ["legacy.py"],
            "reusedScope": [],
            "unsupportedScope": [],
        },
    }
    i4 = use_case.execute(coverage, _control_context("I4"))
    consolidation = _proposal_input()
    consolidation["context"] = i4.document["context"]
    i8 = use_case.execute(consolidation, _control_context("I8"))

    assert i2.document["context"]["changedPaths"] == ["pipeline.py", "shared.py"]
    assert i2.document["context"]["removedPaths"] == ["legacy.py"]
    assert i4.artifact_kind == "sca-work-unit"
    assert i4.document["artifactType"] == "sca-work-unit"
    assert i4.document["paths"] == ["pipeline.py", "shared.py"]
    assert i4.document["catalogSnapshotRef"]["versionId"] == "catalog-2026-08-08"
    assert i4.document["coverage"] == {
        "expectedScope": ["legacy.py", "pipeline.py", "shared.py"],
        "completedScope": [],
        "reusedScope": [],
        "skippedScope": ["legacy.py"],
        "unsupportedScope": [],
        "quarantinedScope": [],
        "failedScope": [],
    }
    assert i4.document["immutableInputs"]["repositorySource"]["versionId"] == "source-v2"
    assert i8.document["artifactType"] == "consolidation-result"
    assert i8.document["baseRecheck"] == {
        "activeBaseVersion": "graph-v1",
        "activeBaseFence": 7,
        "coverageState": "COMPLETE",
        "status": "VERIFIED",
    }


def test_incremental_base_recheck_fails_before_proposal_when_pointer_moved() -> None:
    artifacts = ConsolidationArtifacts({})
    pointer = PublicationPointer()
    pointer.pointer["graphVersion"] = "graph-newer"
    document = _proposal_input()
    document["context"]["activeBaseFence"] = 7

    with pytest.raises(ValueError, match="active base changed"):
        ControlStageUseCase(artifacts, pointer).execute(
            document, _control_context("I8")
        )


def test_context_fails_closed_when_target_does_not_own_the_exact_stage() -> None:
    with pytest.raises(StageTargetMismatchError):
        StageExecutionContext(
            target="control-stage",
            workflow_kind="BASELINE",
            workflow_version="1.0.0",
            stage_id="B3",
            stage_name="CLASSIFY_REPOSITORY_AND_PATHS",
            command_id="cmd-001",
            correlation_id="corr-001",
            input_reference=_context().input_reference,
        )
