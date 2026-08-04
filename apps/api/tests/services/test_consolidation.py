from __future__ import annotations

from pathlib import Path

import pytest

from lineage_api.db import Database


FROM_URN = "urn:ldp:staging:snowflake:payments:raw.transactions#amount"
TO_URN = "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue"


def _consolidation_types():
    try:
        from lineage_api.services.consolidation import (
            ConsolidationService,
            MechanismAssertion,
        )
    except ModuleNotFoundError:
        pytest.fail("Consolidation is not implemented")
    return ConsolidationService, MechanismAssertion


def _service(tmp_path: Path, name: str = "lineage.db"):
    consolidation_service, _ = _consolidation_types()
    database = Database(tmp_path / name)
    database.initialize()
    return consolidation_service(database, clock=lambda: "2026-08-04T16:00:00Z")


def _assertion(
    mechanism: str,
    provenance_id: str,
    *,
    transform: str | None = "SUM(amount)",
    exact: bool = True,
    runtime_scope: str | None = None,
    session_complete: bool = True,
):
    _, mechanism_assertion = _consolidation_types()
    return mechanism_assertion(
        provenance_id=provenance_id,
        from_urns=(FROM_URN,),
        to_urn=TO_URN,
        edge_type="DERIVES",
        transform=transform,
        mechanism=mechanism,
        exact=exact,
        evidence_ref={
            "schemaVersion": "1.0.0",
            "kind": mechanism.lower(),
            "key": provenance_id,
            "checksum": "a" * 64,
        },
        repo="payments-pipeline",
        run_id="run-001",
        correlation_id="corr-001",
        runtime_scope=runtime_scope,
        session_complete=session_complete,
    )


def test_merge_is_idempotent_by_provenance_id(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assertion = _assertion("SCA", "prov-sca")

    first = service.merge(assertion)
    replay = service.merge(assertion)

    assert replay == first
    assert first.version == 1
    assert first.band == "SINGLE"
    assert len(first.provenance) == 1
    assert service.version_count(first.edge_key) == 1


def test_engine_arrival_order_is_commutative(tmp_path: Path) -> None:
    sca = _assertion("SCA", "prov-sca")
    llm = _assertion("LLM", "prov-llm", exact=False)
    runtime = _assertion(
        "RUNTIME",
        "prov-runtime",
        transform=None,
        exact=False,
        runtime_scope="ELEMENT",
    )
    forward = _service(tmp_path, "forward.db")
    reverse = _service(tmp_path, "reverse.db")

    forward_result = forward.merge_many([sca, llm, runtime])
    reverse_result = reverse.merge_many([runtime, llm, sca])

    assert forward_result.as_dict() == reverse_result.as_dict()
    assert forward_result.band == "HIGHEST"
    assert forward_result.corroboration == "ELEMENT"
    assert [item.provenance_id for item in forward_result.provenance] == [
        "prov-llm",
        "prov-runtime",
        "prov-sca",
    ]


def test_dataset_runtime_corroboration_never_changes_the_band(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.merge_many(
        [
            _assertion("SCA", "prov-sca"),
            _assertion(
                "RUNTIME",
                "prov-runtime-dataset",
                transform=None,
                exact=False,
                runtime_scope="DATASET",
            ),
        ]
    )

    assert result.band == "SINGLE"
    assert result.corroboration == "DATASET"


def test_incomplete_runtime_session_is_retained_but_never_promotes(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.merge_many(
        [
            _assertion("SCA", "prov-sca"),
            _assertion(
                "RUNTIME",
                "prov-runtime-incomplete",
                transform=None,
                exact=False,
                runtime_scope="ELEMENT",
                session_complete=False,
            ),
        ]
    )

    assert result.band == "SINGLE"
    assert result.corroboration == "NONE"
    assert len(result.provenance) == 2


def test_different_exact_or_probable_transforms_conflict_without_averaging(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.merge_many(
        [
            _assertion("SCA", "prov-sca", transform="SUM(amount)"),
            _assertion("LLM", "prov-llm", transform="amount * 2", exact=False),
        ]
    )

    assert result.status == "CONFLICTING"
    assert result.band == "MEDIUM"
    assert result.transform is None
    assert {item.transform for item in result.provenance} == {"SUM(amount)", "amount * 2"}
    assert result.auto_publishable is False


def test_equivalent_normalized_transforms_do_not_conflict(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.merge_many(
        [
            _assertion("SCA", "prov-sca", transform="SUM( amount )"),
            _assertion("LLM", "prov-llm", transform="sum(amount)", exact=False),
        ]
    )

    assert result.status == "PROPOSED"
    assert result.transform == "SUM( amount )"
    assert result.auto_publishable is True
