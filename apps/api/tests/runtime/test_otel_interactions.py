from lineage_api.runtime.adapters.otel_interactions import normalize_interaction_span


def _span(**attributes) -> dict:
    return {
        "name": "GET /customers/{id}",
        "traceId": "t1",
        "spanId": "s1",
        "attributes": attributes,
    }


def test_an_http_client_span_becomes_a_rest_interaction() -> None:
    observation, issue = normalize_interaction_span(
        _span(
            **{
                "http.request.method": "GET",
                "http.route": "/customers/{id}",
                "peer.service": "customer-service",
                "server.address": "customer-service",
            }
        ),
        service_name="orders-service",
        observed_at="2026-08-12T10:00:00Z",
    )

    assert issue is None
    assert observation["channel"] == "REST"
    assert observation["operation"] == "GET /customers/{id}"
    assert observation["fromService"] == "orders-service"
    assert observation["toService"] == "customer-service"
    assert observation["mechanism"] == "RUNTIME"
    assert observation["traceId"] == "t1"


def test_an_rpc_span_becomes_a_grpc_interaction() -> None:
    observation, issue = normalize_interaction_span(
        _span(
            **{
                "rpc.system": "grpc",
                "rpc.service": "inventory.Inventory",
                "rpc.method": "Reserve",
                "peer.service": "inventory-service",
            }
        ),
        service_name="orders-service",
        observed_at="2026-08-12T10:00:00Z",
    )

    assert issue is None
    assert observation["channel"] == "GRPC"
    assert observation["operation"] == "inventory.Inventory/Reserve"


def test_a_graphql_span_becomes_a_graphql_interaction() -> None:
    observation, issue = normalize_interaction_span(
        _span(
            **{
                "graphql.operation.type": "query",
                "graphql.operation.name": "customer",
                "peer.service": "customer-service",
            }
        ),
        service_name="crm",
        observed_at="2026-08-12T10:00:00Z",
    )

    assert issue is None
    assert observation["channel"] == "GRAPHQL"
    assert observation["operation"] == "query customer"


def test_a_messaging_span_becomes_an_async_event_interaction() -> None:
    observation, issue = normalize_interaction_span(
        _span(
            **{
                "messaging.system": "kafka",
                "messaging.destination.name": "payment.completed",
                "peer.service": "payments-service",
            }
        ),
        service_name="orders-service",
        observed_at="2026-08-12T10:00:00Z",
    )

    assert issue is None
    assert observation["channel"] == "ASYNC_EVENT"
    assert observation["operation"] == "payment.completed"


def test_a_span_without_a_target_service_is_not_mappable() -> None:
    observation, issue = normalize_interaction_span(
        _span(**{"http.request.method": "GET", "http.route": "/x"}),
        service_name="orders-service",
        observed_at="2026-08-12T10:00:00Z",
    )

    assert observation is None
    assert issue.code == "OTEL_INTERACTION_TARGET_UNKNOWN"


def test_a_span_with_no_recognised_channel_is_not_mappable() -> None:
    observation, issue = normalize_interaction_span(
        _span(**{"peer.service": "x"}),
        service_name="orders-service",
        observed_at="2026-08-12T10:00:00Z",
    )

    assert observation is None
    assert issue.code == "OTEL_INTERACTION_NOT_MAPPABLE"


def test_no_field_value_ever_crosses_the_boundary() -> None:
    """Span attributes may carry payloads; an interaction must never relay one."""
    observation, _ = normalize_interaction_span(
        _span(
            **{
                "http.request.method": "GET",
                "http.route": "/customers/{id}",
                "peer.service": "customer-service",
                "http.request.body": '{"email":"a@b.com"}',
                "db.query.text": "select * from customers",
            }
        ),
        service_name="orders-service",
        observed_at="2026-08-12T10:00:00Z",
    )

    serialised = repr(observation)
    assert "a@b.com" not in serialised
    assert "select" not in serialised
    assert observation["requestFields"] == []
    assert observation["responseFields"] == []
