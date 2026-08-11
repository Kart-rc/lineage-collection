from __future__ import annotations

from dataclasses import dataclass

import pytest


def _sdk_types():
    try:
        from lineage_api.runtime.models import EmitResult, FieldMapping
        from lineage_api.runtime.sdk import ConfigurableRuntimeSDK
    except ModuleNotFoundError:
        pytest.fail("configurable runtime SDK is not implemented")
    return EmitResult, FieldMapping, ConfigurableRuntimeSDK


class CapturingProducer:
    def __init__(self, *, fail: bool = False) -> None:
        self.payloads: list[dict[str, object]] = []
        self.fail = fail

    def enqueue(self, payload: dict[str, object]):
        if self.fail:
            raise RuntimeError("producer unavailable")
        self.payloads.append(payload)
        EmitResult, *_ = _sdk_types()
        return EmitResult(accepted=True, observation_id="runtime-observation-001", reason=None)


def test_sdk_exposes_explicit_read_write_derive_and_connect_metadata_apis() -> None:
    _, FieldMapping, SDK = _sdk_types()
    producer = CapturingProducer()
    sdk = SDK(producer=producer, enabled=True)
    source = "snowflake://payments/raw.transactions"
    target = "snowflake://payments/analytics.daily_revenue"
    mapping = FieldMapping(
        source_dataset=source,
        source_field="amount",
        target_dataset=target,
        target_field="gross_revenue",
    )

    results = [
        sdk.read(source, aliases={"input": "transactions"}),
        sdk.write(target),
        sdk.derive((source,), target, field_mappings=(mapping,)),
        sdk.connect(source, target),
    ]

    assert all(result.accepted for result in results)
    assert [payload["operation"] for payload in producer.payloads] == [
        "READ",
        "WRITE",
        "DERIVE",
        "CONNECT",
    ]
    assert producer.payloads[0] == {
        "operation": "READ",
        "sourceDatasets": [source],
        "targetDatasets": [],
        "aliases": {"input": "transactions"},
        "fieldMappings": [],
    }
    assert producer.payloads[2]["fieldMappings"] == [
        {
            "sourceDataset": source,
            "sourceField": "amount",
            "targetDataset": target,
            "targetField": "gross_revenue",
        }
    ]


def test_sdk_does_not_resolve_catalog_identity_or_assign_confidence() -> None:
    _, _, SDK = _sdk_types()
    producer = CapturingProducer()
    sdk = SDK(producer=producer)

    sdk.derive(
        ("warehouse.raw.orders",),
        "warehouse.analytics.orders",
    )

    encoded = repr(producer.payloads[0]).lower()
    assert "confidence" not in encoded
    assert "catalogurn" not in encoded
    assert "resolved" not in encoded


def test_sdk_can_be_turned_off_and_contains_internal_producer_failures() -> None:
    _, _, SDK = _sdk_types()
    disabled_producer = CapturingProducer()
    disabled = SDK(producer=disabled_producer, enabled=False)

    disabled_result = disabled.read("warehouse.raw.orders")

    assert (disabled_result.accepted, disabled_result.reason) == (False, "SDK_DISABLED")
    assert disabled_producer.payloads == []

    failing = SDK(producer=CapturingProducer(fail=True))
    result = failing.write("warehouse.analytics.orders")
    assert (result.accepted, result.reason) == (False, "PRODUCER_INTERNAL_ERROR")


def test_sdk_rejects_unbounded_metadata_before_sorting_or_deduplication() -> None:
    _, FieldMapping, SDK = _sdk_types()
    producer = CapturingProducer()
    sdk = SDK(
        producer=producer,
        max_datasets=2,
        max_aliases=1,
        max_field_mappings=1,
        max_string_bytes=16,
    )

    with pytest.raises(ValueError, match="dataset limit"):
        sdk.derive(("source-a", "source-b", "source-c"), "target")
    with pytest.raises(ValueError, match="alias limit"):
        sdk.read("source", aliases={"a": "one", "b": "two"})
    with pytest.raises(ValueError, match="string limit"):
        sdk.write("x" * 17)
    mapping = FieldMapping("source", "a", "target", "b")
    with pytest.raises(ValueError, match="field mapping limit"):
        sdk.derive(("source",), "target", field_mappings=(mapping, mapping))

    assert producer.payloads == []


@pytest.mark.parametrize(
    "call",
    [
        lambda sdk: sdk.read(""),
        lambda sdk: sdk.write(""),
        lambda sdk: sdk.derive((), "target"),
        lambda sdk: sdk.derive(("source",), ""),
        lambda sdk: sdk.connect("source", ""),
    ],
)
def test_sdk_rejects_empty_metadata_without_calling_producer(call) -> None:
    _, _, SDK = _sdk_types()
    producer = CapturingProducer()
    sdk = SDK(producer=producer)

    with pytest.raises(ValueError):
        call(sdk)

    assert producer.payloads == []
