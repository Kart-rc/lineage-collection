from __future__ import annotations

import json
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parents[4] / "fixtures" / "runtime" / "openlineage"


def _types():
    try:
        from lineage_api.runtime.adapters.openlineage import (
            OpenLineageAdapter,
            OpenLineageControl,
            OpenLineageProfile,
        )
    except ModuleNotFoundError:
        pytest.fail("OpenLineage runtime adapter is not implemented")
    return OpenLineageAdapter, OpenLineageControl, OpenLineageProfile


def _fixture() -> dict[str, object]:
    return json.loads((FIXTURES / "multi-output.json").read_text(encoding="utf-8"))


def _profile():
    _, Control, Profile = _types()
    return Profile(
        supported_schema_urls=frozenset(
            {"https://openlineage.io/spec/2-0-2/OpenLineage.json"}
        ),
        supported_facet_schema_urls=frozenset(
            {
                "https://openlineage.io/spec/facets/1-0-1/ParentRunFacet.json",
                "https://openlineage.io/spec/facets/1-2-0/ColumnLineageDatasetFacet.json",
            }
        ),
        permitted_granularity=frozenset({"DATASET", "ELEMENT"}),
        control=Control(
            schema_url="https://lineage.local/openlineage-control/1.0.0",
            lease_id="runtime-lease-001",
            profile_id="payments-spark-openlineage",
            profile_version="1.0.0",
            artifact_digest="sha256:runtime-artifact-v1",
        ),
        supported_producer_prefixes=frozenset(
            {
                "https://github.com/OpenLineage/OpenLineage/tree/1.39.0/integration/spark"
            }
        ),
    )


@pytest.mark.parametrize("event_type", ["START", "RUNNING", "COMPLETE", "FAIL"])
def test_run_state_parent_and_multi_output_lineage_are_preserved(event_type: str) -> None:
    Adapter, *_ = _types()
    payload = {**_fixture(), "eventType": event_type}

    result = Adapter().normalize(payload, _profile())

    assert result.unsupported == ()
    assert result.quarantined == ()
    assert len(result.observations) == 2
    element, dataset = result.observations
    assert element["granularity"] == "ELEMENT"
    assert element["sourceDatasets"] == [
        "snowflake://payments/raw.refunds",
        "snowflake://payments/raw.transactions",
    ]
    assert element["sourceFields"] == ["amount", "amount"]
    assert dataset["granularity"] == "DATASET"
    assert all(observation["runState"] == event_type for observation in result.observations)
    assert all(observation["runId"] == "runtime-run-multi-1" for observation in result.observations)
    assert all(observation["parentRunId"] == "parent-run-1" for observation in result.observations)


def test_absent_column_facet_remains_dataset_level() -> None:
    Adapter, *_ = _types()
    payload = _fixture()
    payload["outputs"] = [payload["outputs"][1]]

    result = Adapter().normalize(payload, _profile())

    assert len(result.observations) == 1
    assert result.observations[0]["granularity"] == "DATASET"
    assert "sourceFields" not in result.observations[0]


def test_duplicate_outputs_normalize_to_one_observation_identity() -> None:
    Adapter, *_ = _types()
    payload = _fixture()
    payload["outputs"] = [payload["outputs"][1], payload["outputs"][1]]

    result = Adapter().normalize(payload, _profile())

    assert len(result.observations) == 1
    assert len({item["observationId"] for item in result.observations}) == 1


def test_empty_column_input_mapping_is_quarantined_not_inflated_to_exact() -> None:
    Adapter, *_ = _types()
    payload = _fixture()
    payload["outputs"][0]["facets"]["columnLineage"]["fields"][
        "gross_revenue"
    ]["inputFields"] = []

    result = Adapter().normalize(payload, _profile())

    assert result.observations == ()
    assert result.quarantined[0].code == "OPENLINEAGE_SHAPE_INVALID"


def test_unknown_facet_version_is_unsupported_not_guessed() -> None:
    Adapter, *_ = _types()
    payload = _fixture()
    payload["outputs"][0]["facets"]["columnLineage"]["_schemaURL"] = (
        "https://openlineage.io/spec/facets/9-9-9/ColumnLineageDatasetFacet.json"
    )

    result = Adapter().normalize(payload, _profile())

    assert all(item["granularity"] != "ELEMENT" for item in result.observations)
    assert result.unsupported[0].code == "OPENLINEAGE_FACET_UNSUPPORTED"


def test_unknown_connector_version_is_unsupported_not_guessed() -> None:
    Adapter, *_ = _types()
    payload = _fixture()
    payload["producer"] = (
        "https://github.com/OpenLineage/OpenLineage/tree/9.0.0/integration/spark"
    )

    result = Adapter().normalize(payload, _profile())

    assert result.observations == ()
    assert result.unsupported[0].code == "OPENLINEAGE_CONNECTOR_UNSUPPORTED"


def test_empty_outputs_are_quarantined_instead_of_returning_empty_success() -> None:
    Adapter, *_ = _types()
    payload = _fixture()
    payload["outputs"] = []

    result = Adapter().normalize(payload, _profile())

    assert result.observations == ()
    assert result.quarantined[0].code == "OPENLINEAGE_SHAPE_INVALID"


def test_output_count_is_bounded_before_normalization() -> None:
    Adapter, *_ = _types()
    payload = _fixture()
    payload["outputs"] = [payload["outputs"][1]] * 257

    result = Adapter().normalize(payload, _profile())

    assert result.observations == ()
    assert result.quarantined[0].code == "OPENLINEAGE_SOURCE_LIMIT_EXCEEDED"


def test_column_mapping_count_is_bounded_before_normalization() -> None:
    Adapter, *_ = _types()
    payload = _fixture()
    mapping = payload["outputs"][0]["facets"]["columnLineage"]["fields"][
        "gross_revenue"
    ]
    payload["outputs"][0]["facets"]["columnLineage"]["fields"] = {
        f"target_{index}": mapping for index in range(1025)
    }

    result = Adapter().normalize(payload, _profile())

    assert result.observations == ()
    assert result.quarantined[0].code == "OPENLINEAGE_SOURCE_LIMIT_EXCEEDED"


def test_oversized_metadata_is_quarantined_before_lineage_normalization() -> None:
    Adapter, *_ = _types()
    payload = _fixture()
    payload["outputs"][0]["name"] = "x" * 1_000_001

    result = Adapter().normalize(payload, _profile())

    assert result.observations == ()
    assert result.quarantined[0].code == "OPENLINEAGE_SOURCE_LIMIT_EXCEEDED"


def test_control_facet_mismatch_is_quarantined_without_normalized_evidence() -> None:
    Adapter, *_ = _types()
    payload = _fixture()
    payload["run"]["facets"]["lineageControl"]["artifactDigest"] = "sha256:other"

    result = Adapter().normalize(payload, _profile())

    assert result.observations == ()
    assert result.quarantined[0].code == "OPENLINEAGE_CONTROL_MISMATCH"
    assert "sha256:other" not in repr(result.quarantined[0])


def test_repeated_event_normalizes_byte_identically_without_duplicate_edges() -> None:
    Adapter, *_ = _types()
    payload = _fixture()

    first = Adapter().normalize(payload, _profile())
    replay = Adapter().normalize(payload, _profile())

    assert replay == first
    assert len({item["observationId"] for item in first.observations}) == len(
        first.observations
    )
    assert replay.normalized_checksum == first.normalized_checksum
