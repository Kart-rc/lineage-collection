from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from lineage_api.application.stage_execution import (
    StageExecutionContext,
    StageTargetMismatchError,
)
from lineage_api.application.stage_handlers import production_stage_use_cases
from lineage_api.application.workflows.definitions import WORKFLOWS


ROOT = Path(__file__).parents[3]
MATRIX_PATH = ROOT / "fixtures" / "aws" / "workflow-inputs" / "stage-artifact-matrix.json"
REFERENCE = {
    "bucket": "evidence",
    "key": "matrix/input.json",
    "versionId": "fixture-v1",
    "sha256": "a" * 64,
    "sizeBytes": 1,
}
_KIND = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


def _matrix() -> dict[str, object]:
    document = json.loads(MATRIX_PATH.read_text())
    assert document["schemaVersion"] == "1.0.0"
    assert document["evidenceState"] == "LOCAL_PASS"
    assert isinstance(document["checkpointProofs"], list)
    assert isinstance(document["stages"], dict)
    return document


def _assert_proof_exists(proof: object) -> None:
    assert isinstance(proof, str) and proof.count("::") == 1
    relative, test_name = proof.split("::", 1)
    source = ROOT / relative
    assert source.is_file(), proof
    assert f"def {test_name}(" in source.read_text(), proof


def test_every_workflow_state_has_one_real_local_artifact_proof() -> None:
    marker = object()
    registry = production_stage_use_cases(
        marker,
        marker,
        packages=marker,
        publication_control=marker,
        projection=marker,
        sources=marker,
    )
    expected = {
        f"{kind}/{stage.stage_id}"
        for kind, workflow in WORKFLOWS.items()
        for stage in workflow.stages
    }
    matrix = _matrix()
    stages = matrix["stages"]
    assert isinstance(stages, dict)

    assert len(expected) == 40
    assert set(stages) == expected
    assert set(registry) == {
        tuple(identity.split("/", 1)) for identity in expected
    }
    for identity, evidence in stages.items():
        assert isinstance(evidence, dict)
        assert set(evidence) == {"artifactKind", "proof"}
        artifact_kind = evidence["artifactKind"]
        assert isinstance(artifact_kind, str) and _KIND.fullmatch(artifact_kind)
        assert artifact_kind not in {"stage-result", "generic-result", "input-echo"}
        _assert_proof_exists(evidence["proof"])
    checkpoint_proofs = matrix["checkpointProofs"]
    assert isinstance(checkpoint_proofs, list) and len(checkpoint_proofs) == 4
    for proof in checkpoint_proofs:
        _assert_proof_exists(proof)


def test_all_40_states_reject_the_wrong_compute_target_before_execution() -> None:
    for workflow_kind, workflow in WORKFLOWS.items():
        for stage in workflow.stages:
            with pytest.raises(StageTargetMismatchError):
                StageExecutionContext(
                    target="intake",
                    workflow_kind=workflow_kind,
                    workflow_version=workflow.version,
                    stage_id=stage.stage_id,
                    stage_name=stage.name,
                    command_id="matrix-command",
                    correlation_id="matrix-correlation",
                    input_reference=REFERENCE,
                )
