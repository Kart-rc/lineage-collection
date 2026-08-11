from __future__ import annotations

import json
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parents[4] / "fixtures" / "runtime" / "otel"


def _types():
    try:
        from lineage_api.runtime.adapters.otel import (
            OtelAdapter,
            OtelAttributeContract,
            OtelProfile,
        )
    except ModuleNotFoundError:
        pytest.fail("OTel runtime adapter is not implemented")
    return OtelAdapter, OtelAttributeContract, OtelProfile


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _profile(*, semantic_version: str = "1.30.0", custom: bool = True):
    _, Contract, Profile = _types()
    return Profile(
        semantic_convention_version=semantic_version,
        permitted_granularity=frozenset({"CONNECTIVITY", "DATASET", "ELEMENT"}),
        allowed_attributes=frozenset(
            {
                "db.system.name",
                "db.namespace",
                "db.collection.name",
                "db.operation.name",
                "server.address",
                "http.request.method",
                "lineage.source.dataset",
                "lineage.target.dataset",
                "lineage.source.field",
                "lineage.target.field",
            }
        ),
        custom_contract=(
            Contract(
                schema_url="https://lineage.local/otel-attributes/1.0.0",
                profile_version="1.0.0",
            )
            if custom
            else None
        ),
    )


class CapturingForwarder:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def forward(self, payload: dict[str, object]) -> None:
        self.payloads.append(payload)


def test_otlp_json_preserves_service_trace_span_and_never_inflates_generic_span() -> None:
    Adapter, *_ = _types()
    result = Adapter().normalize(_fixture("otlp-http.json"), _profile())

    assert result.unsupported == ()
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert (
        observation["serviceName"],
        observation["traceId"],
        observation["spanId"],
    ) == (
        "payments-service",
        "0123456789abcdef0123456789abcdef",
        "0123456789abcdef",
    )
    assert observation["granularity"] == "DATASET"
    assert observation["granularity"] != "ELEMENT"
    assert observation["exact"] is False


def test_database_write_direction_is_not_reversed_and_unknown_operation_is_connectivity() -> None:
    Adapter, *_ = _types()
    write_payload = _fixture("otlp-http.json")
    write_attributes = write_payload["resourceSpans"][0]["scopeSpans"][0]["spans"][
        0
    ]["attributes"]
    write_attributes[3]["value"]["stringValue"] = "INSERT"

    write_result = Adapter().normalize(write_payload, _profile())

    assert write_result.observations[0]["sourceDatasets"] == [
        "service://payments-service"
    ]
    assert write_result.observations[0]["targetDataset"] == (
        "snowflake://payments/raw.transactions"
    )
    assert write_result.observations[0]["edgeType"] == "WRITES"

    unknown_payload = _fixture("otlp-http.json")
    unknown_attributes = unknown_payload["resourceSpans"][0]["scopeSpans"][0][
        "spans"
    ][0]["attributes"]
    unknown_attributes[3]["value"]["stringValue"] = "EXECUTE"

    unknown_result = Adapter().normalize(unknown_payload, _profile())

    assert unknown_result.observations[0]["edgeType"] == "CONNECTS"
    assert unknown_result.observations[0]["exact"] is False


def test_sensitive_telemetry_is_forwarded_but_cannot_enter_lineage_result_or_errors() -> None:
    Adapter, *_ = _types()
    forwarder = CapturingForwarder()
    payload = _fixture("otlp-sensitive.json")

    result = Adapter(forwarder=forwarder).normalize(payload, _profile())

    assert forwarder.payloads == [payload]
    assert len(result.observations) == 1
    encoded = repr(result).casefold()
    assert "card_number" not in encoded
    assert "private-token" not in encoded
    assert "authorization" not in encoded
    assert result.observations[0]["granularity"] == "CONNECTIVITY"


def test_only_exact_approved_custom_contract_can_emit_element_mapping() -> None:
    Adapter, *_ = _types()
    payload = _fixture("otlp-custom-field.json")

    approved = Adapter().normalize(payload, _profile())
    unapproved = Adapter().normalize(payload, _profile(custom=False))
    mismatch_payload = json.loads(json.dumps(payload))
    mismatch_payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"][1][
        "value"
    ]["stringValue"] = "2.0.0"
    mismatched = Adapter().normalize(mismatch_payload, _profile())

    assert approved.observations[0]["granularity"] == "ELEMENT"
    assert approved.observations[0]["sourceFields"] == ["amount"]
    assert unapproved.observations == ()
    assert unapproved.unsupported[0].code == "OTEL_ATTRIBUTE_CONTRACT_UNAPPROVED"
    assert mismatched.observations == ()
    assert mismatched.unsupported[0].code == "OTEL_PROFILE_VERSION_UNSUPPORTED"


def test_semantic_convention_mismatch_is_unsupported_and_forwarding_still_occurs() -> None:
    Adapter, *_ = _types()
    forwarder = CapturingForwarder()
    payload = _fixture("otlp-http.json")

    result = Adapter(forwarder=forwarder).normalize(
        payload, _profile(semantic_version="1.29.0")
    )

    assert result.observations == ()
    assert result.unsupported[0].code == "OTEL_SEMANTIC_CONVENTION_UNSUPPORTED"
    assert forwarder.payloads == [payload]


def test_resource_span_count_is_bounded_without_interrupting_telemetry_forwarding() -> None:
    Adapter, *_ = _types()
    forwarder = CapturingForwarder()
    payload = _fixture("otlp-http.json")
    payload["resourceSpans"] = payload["resourceSpans"] * 65

    result = Adapter(forwarder=forwarder).normalize(payload, _profile())

    assert forwarder.payloads == [payload]
    assert result.observations == ()
    assert result.quarantined[0].code == "OTEL_SOURCE_LIMIT_EXCEEDED"


def test_span_attribute_count_is_bounded_before_mapping() -> None:
    Adapter, *_ = _types()
    payload = _fixture("otlp-http.json")
    span = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    span["attributes"] = [
        {
            "key": f"safe.attribute.{index}",
            "value": {"stringValue": "metadata"},
        }
        for index in range(257)
    ]

    result = Adapter().normalize(payload, _profile())

    assert result.observations == ()
    assert result.quarantined[0].code == "OTEL_SOURCE_LIMIT_EXCEEDED"


def test_allowed_attribute_with_secret_shaped_value_cannot_enter_lineage() -> None:
    Adapter, *_ = _types()
    payload = _fixture("otlp-sensitive.json")
    attributes = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0][
        "attributes"
    ]
    attributes[0]["value"]["stringValue"] = "Bearer private-token"

    result = Adapter().normalize(payload, _profile())

    assert result.observations == ()
    assert "private-token" not in repr(result).casefold()


def test_oversized_metadata_is_quarantined_before_lineage_normalization() -> None:
    Adapter, *_ = _types()
    payload = _fixture("otlp-http.json")
    payload["resourceSpans"][0]["resource"]["attributes"][0]["value"][
        "stringValue"
    ] = "s" * 1_000_001

    result = Adapter().normalize(payload, _profile())

    assert result.observations == ()
    assert result.quarantined[0].code == "OTEL_SOURCE_LIMIT_EXCEEDED"


def test_nested_limit_suppresses_all_partial_lineage_output() -> None:
    Adapter, *_ = _types()
    payload = _fixture("otlp-http.json")
    valid_span = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    oversized_span = json.loads(json.dumps(valid_span))
    oversized_span["spanId"] = "ffffffffffffffff"
    oversized_span["attributes"] = [
        {"key": f"safe.attribute.{index}", "value": {"stringValue": "metadata"}}
        for index in range(257)
    ]
    payload["resourceSpans"][0]["scopeSpans"][0]["spans"] = [
        valid_span,
        oversized_span,
    ]

    result = Adapter().normalize(payload, _profile())

    assert result.observations == ()
    assert result.quarantined[0].code == "OTEL_SOURCE_LIMIT_EXCEEDED"


def test_otel_normalization_is_byte_deterministic() -> None:
    Adapter, *_ = _types()
    payload = _fixture("otlp-http.json")

    first = Adapter().normalize(payload, _profile())
    replay = Adapter().normalize(payload, _profile())

    assert replay == first
    assert replay.normalized_checksum == first.normalized_checksum
