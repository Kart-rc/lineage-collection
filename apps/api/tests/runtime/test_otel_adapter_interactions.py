"""OTel span enrichment onto the service interactions plane (Task 4 of the
service-interactions-plane design).

A span carrying `http.route` / `rpc.method` / `graphql.operation.*` /
`messaging.destination.name` is an interaction witness. The adapter must surface it as
an interaction observation on its own plane — never mixed into dataset observations,
never downgraded to a host-only CONNECTIVITY guess — and only under a profile that
explicitly permits INTERACTION granularity. Unresolvable targets are typed residue.
"""

from __future__ import annotations

import json

from lineage_api.runtime.adapters.otel import OtelAdapter, OtelProfile


def _profile(granularity: frozenset[str]) -> OtelProfile:
    return OtelProfile(
        semantic_convention_version="1.30.0",
        permitted_granularity=granularity,
        allowed_attributes=frozenset(
            {
                "server.address",
                "http.request.method",
                "http.route",
                "rpc.system",
                "rpc.service",
                "rpc.method",
                "graphql.operation.type",
                "graphql.operation.name",
                "messaging.system",
                "messaging.destination.name",
                "peer.service",
            }
        ),
    )


def _interaction_profile() -> OtelProfile:
    return _profile(frozenset({"CONNECTIVITY", "DATASET", "INTERACTION"}))


def _legacy_profile() -> OtelProfile:
    return _profile(frozenset({"CONNECTIVITY", "DATASET"}))


def _payload(attributes: dict[str, str]) -> dict[str, object]:
    return {
        "resourceSpans": [
            {
                "schemaUrl": "https://opentelemetry.io/schemas/1.30.0",
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "api-gateway"},
                        }
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "http-client", "version": "1.0.0"},
                        "spans": [
                            {
                                "traceId": "0123456789abcdef0123456789abcdef",
                                "spanId": "0123456789abcdef",
                                "name": "client-call",
                                "kind": 3,
                                "startTimeUnixNano": "1786104000000000000",
                                "attributes": [
                                    {"key": key, "value": {"stringValue": value}}
                                    for key, value in attributes.items()
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }


def test_rest_client_span_becomes_an_interaction_not_a_connectivity_guess() -> None:
    payload = _payload(
        {
            "http.request.method": "GET",
            "http.route": "/owners/{ownerId}",
            "peer.service": "customers-service",
            "server.address": "customers-service.internal",
        }
    )

    result = OtelAdapter().normalize(payload, _interaction_profile())

    assert result.unsupported == ()
    assert result.observations == ()
    assert len(result.interactions) == 1
    interaction = result.interactions[0]
    assert interaction["channel"] == "REST"
    assert interaction["operation"] == "GET /owners/{ownerId}"
    assert interaction["fromService"] == "api-gateway"
    assert interaction["toService"] == "customers-service"
    assert interaction["mechanism"] == "RUNTIME"
    assert interaction["traceId"] == "0123456789abcdef0123456789abcdef"


def test_messaging_span_becomes_an_async_event_interaction() -> None:
    payload = _payload(
        {
            "messaging.system": "kafka",
            "messaging.destination.name": "visits.events",
            "peer.service": "visits-service",
        }
    )

    result = OtelAdapter().normalize(payload, _interaction_profile())

    assert result.unsupported == ()
    assert len(result.interactions) == 1
    assert result.interactions[0]["channel"] == "ASYNC_EVENT"
    assert result.interactions[0]["operation"] == "visits.events"


def test_interaction_span_without_permitted_granularity_is_typed_not_downgraded() -> None:
    payload = _payload(
        {
            "http.request.method": "GET",
            "http.route": "/owners/{ownerId}",
            "server.address": "customers-service.internal",
        }
    )

    result = OtelAdapter().normalize(payload, _legacy_profile())

    # Fail closed: the span witnesses an interaction the profile has not approved.
    # Producing a host-only CONNECTIVITY edge instead would silently discard the route.
    assert result.interactions == ()
    assert result.observations == ()
    assert [issue.code for issue in result.unsupported] == [
        "OTEL_GRANULARITY_UNSUPPORTED"
    ]


def test_interaction_span_without_a_target_is_typed_residue() -> None:
    payload = _payload(
        {
            "http.request.method": "GET",
            "http.route": "/owners/{ownerId}",
        }
    )

    result = OtelAdapter().normalize(payload, _interaction_profile())

    assert result.interactions == ()
    assert [issue.code for issue in result.unsupported] == [
        "OTEL_INTERACTION_TARGET_UNKNOWN"
    ]


def test_address_only_span_still_maps_to_connectivity() -> None:
    payload = _payload({"server.address": "ledger.internal"})

    result = OtelAdapter().normalize(payload, _interaction_profile())

    assert result.interactions == ()
    assert len(result.observations) == 1
    assert result.observations[0]["granularity"] == "CONNECTIVITY"


def test_interactions_never_carry_unapproved_attribute_values() -> None:
    payload = _payload(
        {
            "http.request.method": "POST",
            "http.route": "/owners",
            "peer.service": "customers-service",
            "http.request.body": '{"firstName":"Sensitive"}',
        }
    )

    result = OtelAdapter().normalize(payload, _interaction_profile())

    assert len(result.interactions) == 1
    rendered = json.dumps(result.interactions[0])
    assert "Sensitive" not in rendered
    assert "http.request.body" not in rendered


def test_dataset_only_payloads_keep_their_normalized_checksum_shape() -> None:
    payload = _payload({"server.address": "ledger.internal"})

    first = OtelAdapter().normalize(payload, _legacy_profile())
    replay = OtelAdapter().normalize(payload, _legacy_profile())

    assert first.interactions == ()
    assert replay.normalized_checksum == first.normalized_checksum
