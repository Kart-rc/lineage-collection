from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest


CHANGE_TYPES = (
    "COLUMN_DROP",
    "COLUMN_TYPE_CHANGE",
    "DATASET_REMOVAL",
    "COLUMN_RENAME",
    "TRANSFORM_CHANGE",
    "FINGERPRINT_DRIFT",
)


@dataclass
class CheckWriter:
    checks: dict[str, dict[str, object]]

    def upsert(self, result: dict[str, object]) -> dict[str, object]:
        self.checks[str(result["checkId"])] = result
        return result


class SequenceReader:
    def __init__(self, *values: object) -> None:
        self.values = list(values)
        self.last = values[-1]

    def __call__(self, *_: object) -> object:
        return self.values.pop(0) if self.values else self.last


class Monotonic:
    def __init__(self, *values: float) -> None:
        self.values = list(values)
        self.last = values[-1]

    def __call__(self) -> float:
        return self.values.pop(0) if self.values else self.last


def workflow_types():
    try:
        from lineage_api.application.workflows.pr_gate import (
            EnvironmentPin,
            PRGateChange,
            PRGateRequest,
            PRGateWorkflow,
        )
    except ModuleNotFoundError:
        pytest.fail("PR Gate workflow is not implemented")
    return EnvironmentPin, PRGateChange, PRGateRequest, PRGateWorkflow


def projection_error_type():
    from lineage_api.application.workflows.pr_gate import ProjectionUnavailableError

    return ProjectionUnavailableError


def request(*, mechanisms: tuple[str, ...] = ("SCA",), coverage: bool = True):
    _, change_type, request_type, _ = workflow_types()
    return request_type(
        repo="payments-pipeline",
        pr_number=42,
        head_sha="head-abc",
        target_environment="staging",
        policy_version="1.0.0",
        candidate_artifact_digest="sha256:candidate",
        coverage_complete=coverage,
        changes=(
            change_type(
                change_type="COLUMN_DROP",
                subject="urn:ldp:staging:snowflake:payments:raw.transactions#amount",
                evidence_mechanisms=mechanisms,
            ),
        ),
        depth=5,
    )


def environment(version: str = "v1", *, status: str = "HEALTHY"):
    environment_type, _, _, _ = workflow_types()
    return environment_type(
        environment="staging",
        graph_version=version,
        fencing_token=7,
        deployed_artifact_digest="sha256:deployed",
        status=status,
    )


def build_workflow(
    *,
    head_reader=None,
    environment_reader=None,
    impact_reader=None,
    monotonic=None,
    checks: CheckWriter | None = None,
):
    _, _, _, workflow_type = workflow_types()
    return workflow_type(
        head_reader=head_reader or SequenceReader("head-abc", "head-abc"),
        environment_reader=environment_reader or SequenceReader(environment(), environment()),
        impact_reader=impact_reader
        or (
            lambda *_: {
                "namespaceVersion": "v1",
                "truncated": False,
                "summary": {"block": 0, "warn": 0, "info": 0},
                "affected": [],
            }
        ),
        check_writer=checks or CheckWriter({}),
        monotonic=monotonic or Monotonic(0.0, 0.1, 0.2),
        utc_now=lambda: datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
    )


@pytest.mark.parametrize("change_name", CHANGE_TYPES)
def test_pr_gate_passes_complete_current_static_evaluation_for_all_change_types(
    change_name: str,
) -> None:
    _, change_type, request_type, _ = workflow_types()
    gate = build_workflow()
    value = replace(
        request(),
        changes=(
            change_type(
                change_type=change_name,
                subject="urn:ldp:staging:snowflake:payments:raw.transactions#amount",
                evidence_mechanisms=("SCA",),
            ),
        ),
    )

    result = gate.evaluate(value)

    assert result["verdict"] == "PASS"
    assert result["reasons"] == []
    assert result["headSha"] == "head-abc"
    assert result["environmentVersion"] == "v1"
    assert result["environmentFence"] == 7
    assert result["deployedArtifactDigest"] == "sha256:deployed"
    assert result["candidateArtifactDigest"] == "sha256:candidate"
    assert result["evaluatedChangeTypes"] == [change_name]


def test_pr_gate_blocks_only_calibrated_non_llm_violation() -> None:
    impact = lambda *_: {
        "namespaceVersion": "v1",
        "truncated": False,
        "summary": {"block": 1, "warn": 0, "info": 0},
        "affected": [{"severity": "BLOCK"}],
    }

    assert build_workflow(impact_reader=impact).evaluate(request())["verdict"] == "BLOCK"
    llm_only = build_workflow(impact_reader=impact).evaluate(request(mechanisms=("LLM",)))
    assert llm_only["verdict"] == "WARN"
    assert llm_only["reasons"] == ["LLM_ONLY_BLOCK_EVIDENCE"]


def test_pr_gate_block_wins_over_warn_reasons() -> None:
    # Impact summary contains BOTH warn > 0 (adds IMPACT_WARNING reason)
    # and block > 0 with non-LLM evidence (calibrated block).
    # The verdict must be BLOCK, not WARN: a warning must never mask a block.
    impact = lambda *_: {
        "namespaceVersion": "v1",
        "truncated": False,
        "summary": {"block": 1, "warn": 1, "info": 0},
        "affected": [{"severity": "BLOCK"}, {"severity": "WARN"}],
    }

    result = build_workflow(impact_reader=impact).evaluate(request())

    assert result["verdict"] == "BLOCK"
    assert "IMPACT_WARNING" in result["reasons"]


@pytest.mark.parametrize(
    ("coverage", "environment_value", "impact_reader", "reason"),
    [
        (False, None, None, "MISSING_ENVIRONMENT"),
        (True, "OUT_OF_SYNC", None, "LINEAGE_OUT_OF_SYNC"),
        (
            True,
            "HEALTHY",
            lambda *_: {
                "namespaceVersion": "v1",
                "truncated": True,
                "summary": {"block": 0, "warn": 0, "info": 0},
                "affected": [],
            },
            "IMPACT_TRUNCATED",
        ),
        (
            True,
            "HEALTHY",
            lambda *_: (_ for _ in ()).throw(
                projection_error_type()("projection unavailable")
            ),
            "PROJECTION_UNAVAILABLE",
        ),
    ],
)
def test_pr_gate_warns_for_incomplete_or_degraded_inputs(
    coverage: bool,
    environment_value: str | None,
    impact_reader,
    reason: str,
) -> None:
    if environment_value is None:
        env_reader = SequenceReader(None, None)
    else:
        env_reader = SequenceReader(
            environment(status=environment_value), environment(status=environment_value)
        )
    result = build_workflow(
        environment_reader=env_reader,
        impact_reader=impact_reader,
    ).evaluate(request(coverage=coverage))

    assert result["verdict"] == "WARN"
    assert reason in result["reasons"]
    if not coverage:
        assert "INCOMPLETE_COVERAGE" in result["reasons"]


def test_pr_gate_rechecks_head_environment_and_enforces_hard_deadline() -> None:
    force_pushed = build_workflow(
        head_reader=SequenceReader("head-abc", "head-new")
    ).evaluate(request())
    environment_changed = build_workflow(
        environment_reader=SequenceReader(environment("v1"), environment("v2"))
    ).evaluate(request())
    timed_out = build_workflow(monotonic=Monotonic(0.0, 121.0, 121.0)).evaluate(request())

    assert force_pushed["verdict"] == "WARN"
    assert force_pushed["reasons"] == ["PR_HEAD_SUPERSEDED"]
    assert environment_changed["verdict"] == "WARN"
    assert environment_changed["reasons"] == ["ENVIRONMENT_CHANGED"]
    assert timed_out["verdict"] == "WARN"
    assert timed_out["reasons"] == ["DEADLINE_EXCEEDED"]
    assert timed_out["evaluatedChangeTypes"] == []


def test_pr_gate_does_not_hide_unexpected_programming_errors() -> None:
    gate = build_workflow(
        impact_reader=lambda *_: (_ for _ in ()).throw(ValueError("bad implementation"))
    )

    with pytest.raises(ValueError, match="bad implementation"):
        gate.evaluate(request())


def test_pr_gate_upserts_one_stable_check_for_the_head() -> None:
    writer = CheckWriter({})
    gate = build_workflow(checks=writer)

    first = gate.evaluate(request())
    second = gate.evaluate(request())

    assert first["checkId"] == second["checkId"]
    assert len(writer.checks) == 1
