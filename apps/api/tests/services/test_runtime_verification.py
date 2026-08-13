from __future__ import annotations

import json
from pathlib import Path

import pytest

from lineage_api.services.runtime_verification import (
    ObservedEdge,
    RecordingInstrumentation,
    MAX_TEST_CASES,
    RuntimeTestPlan,
    RuntimeVerificationError,
    StaticEdge,
    generate_test_plan,
    run_test_plan,
    verify_static_lineage,
)


ROOT = Path(__file__).resolve().parents[4]
FIXTURE = ROOT / "fixtures" / "repositories" / "payments-pipeline"
NAMESPACE = "urn:ldp:staging:snowflake:payments"


def _fixture_source() -> str:
    return (FIXTURE / "pipeline.py").read_text(encoding="utf-8")


def _fixture_edges() -> tuple[StaticEdge, ...]:
    payload = json.loads((FIXTURE / "expected-lineage.json").read_text(encoding="utf-8"))
    return tuple(
        StaticEdge(
            from_urn=edge["from"],
            to_urn=edge["to"],
            edge_type=edge["type"],
            transform=edge["transform"],
        )
        for edge in payload["edges"]
    )


def _resolver(dataset: str, element: str) -> str | None:
    """Stand-in for the pinned catalog resolver: no guessing, unknown names are None."""
    known = {
        "snowflake://payments/raw.transactions": "raw.transactions",
        "analytics.daily_revenue": "analytics.daily_revenue",
    }
    resolved = known.get(dataset)
    return None if resolved is None else f"{NAMESPACE}:{resolved}#{element}"


# --- 1. test generation ---------------------------------------------------------------


def test_plan_is_generated_from_the_analyzed_module() -> None:
    plan = generate_test_plan("pipeline.py", _fixture_source(), _fixture_edges())

    entry_points = [case.entry_point for case in plan.cases]
    assert "build_daily_revenue" in entry_points
    # The dynamic-source function takes an annotated str, so it is callable too.
    assert "unresolved_dynamic_source" in entry_points
    dynamic = next(c for c in plan.cases if c.entry_point == "unresolved_dynamic_source")
    assert dynamic.arguments == ("generated",)
    assert plan.targeted_edges == _fixture_edges()


def test_plan_generation_is_deterministic() -> None:
    source = _fixture_source()
    first = generate_test_plan("pipeline.py", source, _fixture_edges())
    second = generate_test_plan("pipeline.py", source, _fixture_edges())

    assert first == second


def test_unsynthesizable_signatures_are_skipped_rather_than_guessed() -> None:
    source = (
        "def ok(a: str) -> None:\n    pass\n\n"
        "def unannotated(a) -> None:\n    pass\n\n"
        "def variadic(*args: str) -> None:\n    pass\n\n"
        "def _private(a: str) -> None:\n    pass\n"
    )

    plan = generate_test_plan("m.py", source, ())

    assert [case.entry_point for case in plan.cases] == ["ok"]


def test_unparseable_source_fails_closed() -> None:
    with pytest.raises(RuntimeVerificationError):
        generate_test_plan("m.py", "def broken(:\n", ())


# --- 2 & 3. instrumentation and execution ---------------------------------------------


def test_execution_is_refused_unless_explicitly_enabled() -> None:
    plan = generate_test_plan("pipeline.py", _fixture_source(), _fixture_edges())

    with pytest.raises(RuntimeVerificationError, match="explicitly enabled"):
        run_test_plan(plan, _fixture_source())


def test_instrumentation_records_only_referenced_source_elements() -> None:
    instrumentation = RecordingInstrumentation()
    handle = instrumentation.read_dataset("in", elements=["a", "b"])
    instrumentation.write_dataset(
        "out", sources=[handle], mappings={"total": "SUM(a)"}
    )

    # `b` is never referenced by the transform, so no edge may claim it.
    assert [(o.from_element, o.to_element) for o in instrumentation.observations] == [
        ("a", "total")
    ]


def test_running_the_plan_observes_the_real_fixture_execution() -> None:
    source = _fixture_source()
    plan = generate_test_plan("pipeline.py", source, _fixture_edges())

    result = run_test_plan(plan, source, allow_execution=True)

    assert "runtime_verifies_build_daily_revenue" in result.executed
    observed = {(o.from_element, o.to_element) for o in result.observations}
    assert observed == {
        ("customer_id", "customer_id"),
        ("amount", "gross_revenue"),
        ("occurred_at", "revenue_date"),
    }


def test_a_failing_case_is_recorded_by_class_without_leaking_values() -> None:
    source = "def boom(secret: str) -> None:\n    raise ValueError(secret + '-token')\n"
    plan = generate_test_plan("m.py", source, ())

    result = run_test_plan(plan, source, allow_execution=True)

    assert result.failures == (("runtime_verifies_boom", "ValueError"),)
    assert all("token" not in reason for _name, reason in result.failures)


# --- 4. verification ------------------------------------------------------------------


def test_the_full_loop_corroborates_the_static_lineage() -> None:
    source = _fixture_source()
    edges = _fixture_edges()
    plan = generate_test_plan("pipeline.py", source, edges)

    result = run_test_plan(plan, source, allow_execution=True)
    verification = verify_static_lineage(edges, result.observations, _resolver)

    assert verification.verdict == "CORROBORATED"
    assert len(verification.corroborated) == len(edges)
    assert verification.static_only == ()
    assert verification.runtime_only == ()


def test_an_unobserved_static_edge_is_reported_not_dropped() -> None:
    edges = (
        *_fixture_edges(),
        StaticEdge(
            from_urn=f"{NAMESPACE}:raw.transactions#customer_id",
            to_urn=f"{NAMESPACE}:analytics.never_run#value",
            edge_type="DERIVES",
            transform="value",
        ),
    )
    source = _fixture_source()
    plan = generate_test_plan("pipeline.py", source, edges)
    result = run_test_plan(plan, source, allow_execution=True)

    verification = verify_static_lineage(edges, result.observations, _resolver)

    assert verification.verdict == "PARTIALLY_CORROBORATED"
    assert [edge.to_urn for edge in verification.static_only] == [
        f"{NAMESPACE}:analytics.never_run#value"
    ]


def test_runtime_evidence_never_invents_an_edge() -> None:
    observation = ObservedEdge(
        from_dataset="snowflake://payments/raw.transactions",
        from_element="amount",
        to_dataset="analytics.daily_revenue",
        to_element="unclaimed_column",
        transform="amount",
        entry_point="build_daily_revenue",
    )

    verification = verify_static_lineage((), (observation,), _resolver)

    assert verification.corroborated == ()
    assert len(verification.runtime_only) == 1
    assert verification.verdict == "NOT_PROVIDED"


def test_unresolvable_datasets_are_runtime_only_never_guessed() -> None:
    observation = ObservedEdge(
        from_dataset="tenant_generated.transactions",
        from_element="amount",
        to_dataset="analytics.daily_revenue",
        to_element="gross_revenue",
        transform="SUM(amount)",
        entry_point="unresolved_dynamic_source",
    )

    verification = verify_static_lineage((), (observation,), _resolver)

    assert verification.runtime_only == (observation,)
    assert verification.corroborated == ()


def test_verification_summary_is_bounded_and_serializable() -> None:
    edges = _fixture_edges()
    source = _fixture_source()
    plan = generate_test_plan("pipeline.py", source, edges)
    result = run_test_plan(plan, source, allow_execution=True)

    summary = verify_static_lineage(edges, result.observations, _resolver).as_dict()

    assert summary["verdict"] == "CORROBORATED"
    assert summary["corroboratedCount"] == len(edges)
    assert json.loads(json.dumps(summary)) == summary


def test_plan_case_bound_is_enforced() -> None:
    single = generate_test_plan("m.py", "def f(a: str) -> None:\n    pass\n", ()).cases

    with pytest.raises(RuntimeVerificationError, match="case bound"):
        RuntimeTestPlan(
            module_path="m.py",
            cases=single * (MAX_TEST_CASES + 1),
            targeted_edges=(),
        )
