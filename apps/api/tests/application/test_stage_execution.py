from __future__ import annotations

from copy import deepcopy

import pytest

from lineage_api.application.stage_execution import (
    StageExecutionContext,
    StageExecutionResult,
    StageTargetMismatchError,
)
from lineage_api.application.stage_handlers import ClassificationStageUseCase


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
