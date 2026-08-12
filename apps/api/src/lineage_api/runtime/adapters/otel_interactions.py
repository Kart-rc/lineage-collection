"""Map OpenTelemetry spans onto the service interactions plane.

The existing OTel path emits a host-level `CONNECTIVITY` edge — `service://name` to
`https://host` — which throws away the verb, the route, the RPC method and the topic
that the span already carries. Those are exactly what the interactions plane needs, so
this adapter reads them instead of discarding them.

The metadata-only boundary is absolute and is why this builds an allowlisted payload
rather than copying attributes through: a span may legitimately carry
`http.request.body` or `db.query.text`, and neither may ever reach an observation.
"""

from __future__ import annotations

from typing import Any, Mapping

from lineage_api.runtime.adapters import AdapterIssue

# Only these attributes are ever read. Anything else in the span — including payload and
# query text — is not copied, so no field value can cross the boundary by accident.
_READ_ATTRIBUTES = frozenset(
    {
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
        "server.address",
    }
)


def _target(safe: Mapping[str, str]) -> str | None:
    return safe.get("peer.service") or safe.get("server.address") or None


def _channel_and_operation(safe: Mapping[str, str]) -> tuple[str, str] | None:
    method = safe.get("http.request.method")
    route = safe.get("http.route")
    if method and route:
        return "REST", f"{method} {route}"

    rpc_service = safe.get("rpc.service")
    rpc_method = safe.get("rpc.method")
    if rpc_service and rpc_method:
        return "GRPC", f"{rpc_service}/{rpc_method}"

    graphql_type = safe.get("graphql.operation.type")
    graphql_name = safe.get("graphql.operation.name")
    if graphql_type and graphql_name:
        return "GRAPHQL", f"{graphql_type} {graphql_name}"

    destination = safe.get("messaging.destination.name")
    if destination:
        return "ASYNC_EVENT", destination
    return None


def normalize_interaction_span(
    span: Mapping[str, Any],
    *,
    service_name: str,
    observed_at: str,
) -> tuple[dict[str, Any] | None, AdapterIssue | None]:
    attributes = span.get("attributes")
    if not isinstance(attributes, Mapping):
        return None, AdapterIssue("OTEL_SHAPE_INVALID", "attributes")

    safe = {
        key: str(value)
        for key, value in attributes.items()
        if key in _READ_ATTRIBUTES and isinstance(value, (str, int, float))
    }

    channel = _channel_and_operation(safe)
    if channel is None:
        return None, AdapterIssue("OTEL_INTERACTION_NOT_MAPPABLE", "attributes")

    target = _target(safe)
    if target is None:
        return None, AdapterIssue("OTEL_INTERACTION_TARGET_UNKNOWN", "peer.service")

    channel_name, operation = channel
    return (
        {
            "schemaVersion": "1.0.0",
            "observationId": f"int-{span.get('spanId', '')}",
            "fromService": service_name,
            "toService": target,
            "channel": channel_name,
            "operation": operation,
            "mechanism": "RUNTIME",
            "exact": False,
            "observedAt": observed_at,
            # Runtime witnesses that a call happened; the field contract comes from the
            # declared API, not from the wire.
            "requestFields": [],
            "responseFields": [],
            "traceId": str(span.get("traceId", "")),
            "spanId": str(span.get("spanId", "")),
        },
        None,
    )
