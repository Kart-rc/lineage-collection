from __future__ import annotations

import pytest

from lineage_api.application.stage_ownership import (
    StageOwner,
    UnknownStageError,
    owner_for_stage,
    stages_for_owner,
    validate_stage_identity,
)
from lineage_api.application.workflows.definitions import WORKFLOWS


def test_every_versioned_workflow_stage_has_exactly_one_functional_owner() -> None:
    assigned = {
        (kind, stage.stage_id): owner_for_stage(kind, stage.stage_id)
        for kind, workflow in WORKFLOWS.items()
        for stage in workflow.stages
    }

    assert len(assigned) == sum(len(workflow.stages) for workflow in WORKFLOWS.values())
    assert all(isinstance(owner, StageOwner) for owner in assigned.values())
    assert set(assigned) == {
        (kind, stage.stage_id)
        for kind, workflow in WORKFLOWS.items()
        for stage in workflow.stages
    }


def test_classification_has_one_explicit_initial_stage_and_deployment_owns_d1_to_d6() -> None:
    assert owner_for_stage("BASELINE", "B3") is StageOwner.CLASSIFICATION
    assert stages_for_owner(StageOwner.CLASSIFICATION) == frozenset({("BASELINE", "B3")})
    assert stages_for_owner(StageOwner.DEPLOYMENT) == frozenset(
        {("DEPLOYMENT", f"D{index}") for index in range(1, 7)}
    )
    assert owner_for_stage("BASELINE", "B5") is StageOwner.SCA
    assert owner_for_stage("INCREMENTAL", "I5") is StageOwner.SCA
    assert owner_for_stage("NIGHTLY", "N2") is StageOwner.SCA


@pytest.mark.parametrize(
    ("workflow_kind", "stage_id"),
    [
        ("BASELINE", "B99"),
        ("INCREMENTAL", "B3"),
        ("UNKNOWN", "B3"),
        ("", ""),
    ],
)
def test_unknown_or_workflow_mismatched_stage_fails_closed(
    workflow_kind: str, stage_id: str
) -> None:
    with pytest.raises(UnknownStageError):
        owner_for_stage(workflow_kind, stage_id)


def test_stage_identity_binds_exact_workflow_version_and_normative_name() -> None:
    assert (
        validate_stage_identity(
            "BASELINE",
            "1.0.0",
            "B3",
            "CLASSIFY_REPOSITORY_AND_PATHS",
        )
        is StageOwner.CLASSIFICATION
    )

    with pytest.raises(UnknownStageError):
        validate_stage_identity(
            "BASELINE",
            "2.0.0",
            "B3",
            "CLASSIFY_REPOSITORY_AND_PATHS",
        )
    with pytest.raises(UnknownStageError):
        validate_stage_identity("BASELINE", "1.0.0", "B3", "CLASSIFY_ANYTHING")
