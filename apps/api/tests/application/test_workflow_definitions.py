from __future__ import annotations

from dataclasses import replace

from lineage_api.application.workflows.definitions import (
    RETRY_CLASSES,
    SIDE_EFFECT_MODES,
    WORKFLOWS,
)


EXPECTED_STAGES = {
    "BASELINE": tuple(f"B{index}" for index in range(1, 11)),
    "INCREMENTAL": tuple(f"I{index}" for index in range(1, 11)),
    "PR_GATE": tuple(f"P{index}" for index in range(1, 9)),
    "DEPLOYMENT": tuple(f"D{index}" for index in range(1, 7)),
    "NIGHTLY": tuple(f"N{index}" for index in range(1, 7)),
}


def test_every_workflow_is_versioned_complete_reachable_and_bounded() -> None:
    assert set(WORKFLOWS) == set(EXPECTED_STAGES)

    for kind, expected in EXPECTED_STAGES.items():
        workflow = WORKFLOWS[kind]
        assert workflow.version == "1.0.0"
        assert workflow.entry_stage == expected[0]
        assert workflow.stage_ids == expected
        assert workflow.validation_errors() == ()
        assert workflow.reachable_stage_ids() == set(expected)
        assert workflow.workflow_timeout_seconds > 0
        assert sum(stage.timeout_seconds for stage in workflow.stages) <= (
            workflow.workflow_timeout_seconds
        )
        for stage in workflow.stages:
            assert 0 < stage.timeout_seconds <= workflow.workflow_timeout_seconds
            assert 1 <= stage.max_attempts <= 5
            assert stage.input_contracts
            assert stage.output_contracts
            assert stage.idempotency_determinants
            assert stage.retry_class in RETRY_CLASSES
            assert stage.side_effect_mode in SIDE_EFFECT_MODES
            assert stage.on_success
            assert stage.on_error
            assert workflow.route_reaches_terminal(stage.stage_id, "success")
            assert workflow.route_reaches_terminal(stage.stage_id, "error")


def test_pr_gate_is_read_only_except_for_stable_check_and_audit_output() -> None:
    workflow = WORKFLOWS["PR_GATE"]

    assert workflow.workflow_timeout_seconds == 120
    assert {stage.side_effect_mode for stage in workflow.stages[:-1]} <= {
        "PURE",
        "READ_ONLY",
    }
    assert workflow.stages[-1].side_effect_mode == "CHECK_AND_AUDIT_WRITE"
    assert workflow.terminal_states == ("PASS", "WARN", "BLOCK")


def test_definition_validation_detects_orphans_unbounded_work_and_dead_ends() -> None:
    workflow = WORKFLOWS["INCREMENTAL"]
    broken_stage = replace(
        workflow.stages[1],
        timeout_seconds=0,
        on_success=(),
        on_error=(),
    )
    broken = replace(workflow, stages=(workflow.stages[0], broken_stage, *workflow.stages[2:]))

    assert set(broken.validation_errors()) >= {
        "I2 timeout must be positive and workflow-bounded",
        "I2 has no success route",
        "I2 has no error route",
        "unreachable stages: I3,I4,I5,I6,I7,I8,I9,I10",
    }
