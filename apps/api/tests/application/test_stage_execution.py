from __future__ import annotations

from copy import deepcopy

import pytest

from lineage_api.application.stage_execution import (
    StageExecutionContext,
    StageExecutionResult,
    StageTargetMismatchError,
)
from lineage_api.application.stage_handlers import (
    ClassificationStageUseCase,
    CoverageStageUseCase,
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
            "repository": "payments-pipeline",
            "system": "payments",
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
    assert isinstance(result.document, list)
    assert len(result.document) == 2
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
