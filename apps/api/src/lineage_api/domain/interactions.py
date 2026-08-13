"""Service-to-service interactions — the second lineage plane.

Dataset lineage answers "where did this column come from". Interactions answer "what
crossed this API boundary", which is where PII actually travels in a service estate: an
email leaves a service over a REST GET or a GraphQL query, and no amount of datastore
lineage sees it.

This is deliberately a separate contract from `runtime-observation.schema.json`. That
contract is dataset-centric — sourceDatasets, targetDataset, transform — and cannot
express an operation, a channel, or a request/response schema. Forcing interactions
into it would corrupt the corroboration rules that govern dataset lineage.

The metadata-only boundary is absolute here: a field may carry its name, type, and
declared classification, never a value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

INTERACTION_CHANNELS = frozenset({"REST", "GRPC", "GRAPHQL", "ASYNC_EVENT"})
INTERACTION_CLASSIFICATIONS = frozenset({"NONE", "PII", "SECRET"})
INTERACTION_MECHANISMS = frozenset({"SCA", "RUNTIME"})


@dataclass(frozen=True, slots=True)
class InteractionField:
    name: str
    type: str
    classification: str = "NONE"

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("interaction field requires a name")
        if self.classification not in INTERACTION_CLASSIFICATIONS:
            raise ValueError(
                f"unknown interaction field classification: {self.classification!r}"
            )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InteractionField":
        if "value" in payload:
            raise ValueError(
                "interaction fields are metadata-only and must not carry a value"
            )
        return cls(
            name=str(payload["name"]),
            type=str(payload.get("type", "")),
            classification=str(payload.get("classification", "NONE")),
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "type": self.type,
            "classification": self.classification,
        }


@dataclass(frozen=True, slots=True)
class InteractionObservation:
    observation_id: str
    from_service: str
    to_service: str
    channel: str
    operation: str
    mechanism: str
    exact: bool
    observed_at: str
    request_fields: tuple[InteractionField, ...] = field(default=())
    response_fields: tuple[InteractionField, ...] = field(default=())
    latency_p99_ms: int | None = None
    trace_id: str | None = None
    span_id: str | None = None
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.channel not in INTERACTION_CHANNELS:
            raise ValueError(f"unknown interaction channel: {self.channel!r}")
        if self.mechanism not in INTERACTION_MECHANISMS:
            raise ValueError(f"unknown interaction mechanism: {self.mechanism!r}")
        if not self.operation:
            raise ValueError("interaction requires an operation")
        if not self.from_service or not self.to_service:
            raise ValueError("interaction requires both endpoints")

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schemaVersion": self.schema_version,
            "observationId": self.observation_id,
            "fromService": self.from_service,
            "toService": self.to_service,
            "channel": self.channel,
            "operation": self.operation,
            "mechanism": self.mechanism,
            "exact": self.exact,
            "observedAt": self.observed_at,
            "requestFields": [item.as_dict() for item in self.request_fields],
            "responseFields": [item.as_dict() for item in self.response_fields],
        }
        for key, value in (
            ("latencyP99Ms", self.latency_p99_ms),
            ("traceId", self.trace_id),
            ("spanId", self.span_id),
        ):
            if value is not None:
                payload[key] = value
        return payload
