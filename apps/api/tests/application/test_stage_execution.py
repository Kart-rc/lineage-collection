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
