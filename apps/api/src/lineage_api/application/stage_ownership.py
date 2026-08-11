from __future__ import annotations

from enum import StrEnum

from lineage_api.application.workflows.definitions import WORKFLOWS


class UnknownStageError(ValueError):
    """Raised before I/O when a workflow stage has no compatible target."""


class StageOwner(StrEnum):
    CONTROL = "control-stage"
    CLASSIFICATION = "classification"
    COVERAGE = "coverage"
    SCA = "sca"
    RUNTIME_VALIDATION = "runtime-validation"
    CONSOLIDATION = "consolidation"
    PROPOSAL = "proposal"
    PUBLICATION = "publication"
    DEPLOYMENT = "deployment"


_OWNERS: dict[tuple[str, str], StageOwner] = {
    ("BASELINE", "B1"): StageOwner.CONTROL,
    ("BASELINE", "B2"): StageOwner.CONTROL,
    ("BASELINE", "B3"): StageOwner.CLASSIFICATION,
    ("BASELINE", "B4"): StageOwner.COVERAGE,
    ("BASELINE", "B5"): StageOwner.SCA,
    ("BASELINE", "B6"): StageOwner.RUNTIME_VALIDATION,
    ("BASELINE", "B7"): StageOwner.CONSOLIDATION,
    ("BASELINE", "B8"): StageOwner.CONSOLIDATION,
    ("BASELINE", "B9"): StageOwner.PROPOSAL,
    ("BASELINE", "B10"): StageOwner.PUBLICATION,
    ("INCREMENTAL", "I1"): StageOwner.CONTROL,
    ("INCREMENTAL", "I2"): StageOwner.CONTROL,
    ("INCREMENTAL", "I3"): StageOwner.COVERAGE,
    ("INCREMENTAL", "I4"): StageOwner.CONTROL,
    ("INCREMENTAL", "I5"): StageOwner.SCA,
    ("INCREMENTAL", "I6"): StageOwner.RUNTIME_VALIDATION,
    ("INCREMENTAL", "I7"): StageOwner.CONSOLIDATION,
    ("INCREMENTAL", "I8"): StageOwner.CONTROL,
    ("INCREMENTAL", "I9"): StageOwner.PROPOSAL,
    ("INCREMENTAL", "I10"): StageOwner.PUBLICATION,
    ("PR_GATE", "P1"): StageOwner.CONTROL,
    ("PR_GATE", "P2"): StageOwner.CONTROL,
    ("PR_GATE", "P3"): StageOwner.CONTROL,
    ("PR_GATE", "P4"): StageOwner.CONSOLIDATION,
    ("PR_GATE", "P5"): StageOwner.COVERAGE,
    ("PR_GATE", "P6"): StageOwner.CONTROL,
    ("PR_GATE", "P7"): StageOwner.CONTROL,
    ("PR_GATE", "P8"): StageOwner.CONTROL,
    ("NIGHTLY", "N1"): StageOwner.CONTROL,
    ("NIGHTLY", "N2"): StageOwner.SCA,
    ("NIGHTLY", "N3"): StageOwner.PUBLICATION,
    ("NIGHTLY", "N4"): StageOwner.CONTROL,
    ("NIGHTLY", "N5"): StageOwner.CONTROL,
    ("NIGHTLY", "N6"): StageOwner.PROPOSAL,
    **{
        ("DEPLOYMENT", f"D{index}"): StageOwner.DEPLOYMENT
        for index in range(1, 7)
    },
}

_EXPECTED = {
    (kind, stage.stage_id)
    for kind, workflow in WORKFLOWS.items()
    for stage in workflow.stages
}
if set(_OWNERS) != _EXPECTED:
    missing = sorted(_EXPECTED - set(_OWNERS))
    extra = sorted(set(_OWNERS) - _EXPECTED)
    raise RuntimeError(f"stage ownership drift; missing={missing!r}; extra={extra!r}")


def owner_for_stage(workflow_kind: str, stage_id: str) -> StageOwner:
    try:
        return _OWNERS[(workflow_kind, stage_id)]
    except KeyError as error:
        raise UnknownStageError(
            f"unknown or workflow-mismatched stage: {workflow_kind}/{stage_id}"
        ) from error


def stages_for_owner(owner: StageOwner) -> frozenset[tuple[str, str]]:
    return frozenset(identity for identity, assigned in _OWNERS.items() if assigned is owner)


def validate_stage_identity(
    workflow_kind: str,
    workflow_version: str,
    stage_id: str,
    stage_name: str,
) -> StageOwner:
    workflow = WORKFLOWS.get(workflow_kind)
    if workflow is None or workflow.version != workflow_version:
        raise UnknownStageError(
            f"unknown workflow version: {workflow_kind}/{workflow_version}"
        )
    stage = next((item for item in workflow.stages if item.stage_id == stage_id), None)
    if stage is None or stage.name != stage_name:
        raise UnknownStageError(
            f"unknown or drifted stage identity: {workflow_kind}/{stage_id}/{stage_name}"
        )
    return owner_for_stage(workflow_kind, stage_id)


__all__ = [
    "StageOwner",
    "UnknownStageError",
    "owner_for_stage",
    "stages_for_owner",
    "validate_stage_identity",
]
