from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from lineage_api.runtime.adapters import AdapterIssue, AdapterResult, canonical, checksum


_CONTROL_ATTRIBUTES = {
    "lineage.contract.schema_url",
    "lineage.profile.version",
}
_MAX_RESOURCE_SPANS = 64
_MAX_SCOPE_SPANS = 64
_MAX_SPANS = 1_024
_MAX_ATTRIBUTES = 256
_MAX_SOURCE_BYTES = 1_000_000
_PROHIBITED_PARTS = (
    "authorization",
    "credential",
    "db.query",
    "db.statement",
    "header",
    "parameter",
    "password",
    "payload",
    "secret",
    "token",
    "value",
)
_SECRET_VALUE_MARKERS = ("bearer ", "password=", "secret=", "sk-", "token=")


@dataclass(frozen=True, slots=True)
class OtelAttributeContract:
    schema_url: str
    profile_version: str

    def __post_init__(self) -> None:
        if not self.schema_url or not self.profile_version:
            raise ValueError("OTel attribute contract identity must be non-empty")


@dataclass(frozen=True, slots=True)
class OtelProfile:
    semantic_convention_version: str
    permitted_granularity: frozenset[str]
    allowed_attributes: frozenset[str]
    custom_contract: OtelAttributeContract | None = None

    def __post_init__(self) -> None:
        if not self.semantic_convention_version:
            raise ValueError("OTel semantic convention version must be non-empty")
        if not self.permitted_granularity <= {"CONNECTIVITY", "DATASET", "ELEMENT"}:
            raise ValueError("OTel permitted granularity is invalid")


class TelemetryForwarder(Protocol):
    def forward(self, payload: dict[str, object]) -> None: ...


class OtelAdapter:
    """Pure OTLP JSON lineage branch; ordinary telemetry forwarding is a separate port."""

    def __init__(self, *, forwarder: TelemetryForwarder | None = None) -> None:
        self._forwarder = forwarder

    def normalize(self, payload: dict[str, object], profile: OtelProfile) -> AdapterResult:
        if self._forwarder is not None:
            self._forwarder.forward(payload)
        observations: list[dict[str, object]] = []
        unsupported: list[AdapterIssue] = []
        quarantined: list[AdapterIssue] = []
        if len(canonical(payload).encode()) > _MAX_SOURCE_BYTES:
            quarantined.append(AdapterIssue("OTEL_SOURCE_LIMIT_EXCEEDED", "payload"))
            return AdapterResult.build(
                observations=(),
                unsupported=unsupported,
                quarantined=quarantined,
                source=payload,
            )
        resource_spans = payload.get("resourceSpans")
        if not isinstance(resource_spans, list):
            quarantined.append(AdapterIssue("OTEL_SHAPE_INVALID", "resourceSpans"))
            return AdapterResult.build(
                observations=(),
                unsupported=unsupported,
                quarantined=quarantined,
                source=payload,
            )
        if len(resource_spans) > _MAX_RESOURCE_SPANS:
            quarantined.append(AdapterIssue("OTEL_SOURCE_LIMIT_EXCEEDED", "resourceSpans"))
            return AdapterResult.build(
                observations=(),
                unsupported=unsupported,
                quarantined=quarantined,
                source=payload,
            )
        source_checksum = checksum(payload)
        for resource_index, resource_span in enumerate(resource_spans):
            location = f"resourceSpans[{resource_index}]"
            if not isinstance(resource_span, dict):
                quarantined.append(AdapterIssue("OTEL_SHAPE_INVALID", location))
                continue
            schema_url = resource_span.get("schemaUrl")
            actual_version = str(schema_url).rstrip("/").rsplit("/", 1)[-1]
            if actual_version != profile.semantic_convention_version:
                unsupported.append(
                    AdapterIssue("OTEL_SEMANTIC_CONVENTION_UNSUPPORTED", location)
                )
                continue
            resource = resource_span.get("resource")
            raw_resource_attributes = (
                resource.get("attributes") if isinstance(resource, dict) else None
            )
            if isinstance(raw_resource_attributes, list) and len(
                raw_resource_attributes
            ) > _MAX_ATTRIBUTES:
                quarantined.append(
                    AdapterIssue("OTEL_SOURCE_LIMIT_EXCEEDED", f"{location}.resource.attributes")
                )
                continue
            resource_attributes = self._attributes(raw_resource_attributes)
            service_name = str(resource_attributes.get("service.name", "unknown-service"))
            scope_spans = resource_span.get("scopeSpans")
            if not isinstance(scope_spans, list):
                quarantined.append(AdapterIssue("OTEL_SHAPE_INVALID", f"{location}.scopeSpans"))
                continue
            if len(scope_spans) > _MAX_SCOPE_SPANS:
                quarantined.append(
                    AdapterIssue("OTEL_SOURCE_LIMIT_EXCEEDED", f"{location}.scopeSpans")
                )
                continue
            for scope_index, scope_span in enumerate(scope_spans):
                scope_location = f"{location}.scopeSpans[{scope_index}]"
                if not isinstance(scope_span, dict):
                    quarantined.append(AdapterIssue("OTEL_SHAPE_INVALID", scope_location))
                    continue
                spans = scope_span.get("spans")
                if not isinstance(spans, list):
                    quarantined.append(
                        AdapterIssue("OTEL_SHAPE_INVALID", f"{scope_location}.spans")
                    )
                    continue
                if len(spans) > _MAX_SPANS:
                    quarantined.append(
                        AdapterIssue("OTEL_SOURCE_LIMIT_EXCEEDED", f"{scope_location}.spans")
                    )
                    continue
                for span_index, span in enumerate(spans):
                    span_location = f"{scope_location}.spans[{span_index}]"
                    if not isinstance(span, dict):
                        quarantined.append(AdapterIssue("OTEL_SHAPE_INVALID", span_location))
                        continue
                    raw_span_attributes = span.get("attributes")
                    if isinstance(raw_span_attributes, list) and len(
                        raw_span_attributes
                    ) > _MAX_ATTRIBUTES:
                        quarantined.append(
                            AdapterIssue(
                                "OTEL_SOURCE_LIMIT_EXCEEDED",
                                f"{span_location}.attributes",
                            )
                        )
                        continue
                    normalized, issue = self._normalize_span(
                        span,
                        service_name=service_name,
                        profile=profile,
                        source_checksum=source_checksum,
                        location=span_location,
                    )
                    if issue is not None:
                        unsupported.append(issue)
                    elif normalized is not None:
                        observations.append(normalized)
        return AdapterResult.build(
            observations=() if quarantined else observations,
            unsupported=unsupported,
            quarantined=quarantined,
            source=payload,
        )

    def _normalize_span(
        self,
        span: dict[str, object],
        *,
        service_name: str,
        profile: OtelProfile,
        source_checksum: str,
        location: str,
    ) -> tuple[dict[str, object] | None, AdapterIssue | None]:
        attributes = self._attributes(span.get("attributes"))
        safe = {
            key: value
            for key, value in attributes.items()
            if key in profile.allowed_attributes or key in _CONTROL_ATTRIBUTES
            if not self._prohibited(key)
            if not self._prohibited_value(value)
        }
        custom_keys = {
            "lineage.source.dataset",
            "lineage.target.dataset",
            "lineage.source.field",
            "lineage.target.field",
        }
        if custom_keys & set(safe):
            contract = profile.custom_contract
            if contract is None or safe.get("lineage.contract.schema_url") != contract.schema_url:
                return None, AdapterIssue("OTEL_ATTRIBUTE_CONTRACT_UNAPPROVED", location)
            if safe.get("lineage.profile.version") != contract.profile_version:
                return None, AdapterIssue("OTEL_PROFILE_VERSION_UNSUPPORTED", location)
            required = custom_keys
            if not required <= set(safe) or "ELEMENT" not in profile.permitted_granularity:
                return None, AdapterIssue("OTEL_ATTRIBUTE_CONTRACT_UNAPPROVED", location)
            body: dict[str, object] = {
                "granularity": "ELEMENT",
                "sourceDatasets": [str(safe["lineage.source.dataset"])],
                "targetDataset": str(safe["lineage.target.dataset"]),
                "sourceFields": [str(safe["lineage.source.field"])],
                "targetField": str(safe["lineage.target.field"]),
                "edgeType": "DERIVES",
                "exact": True,
                "attributeContract": contract.schema_url,
            }
        elif {
            "db.system.name",
            "db.namespace",
            "db.collection.name",
        } <= set(safe):
            if "DATASET" not in profile.permitted_granularity:
                return None, AdapterIssue("OTEL_GRANULARITY_UNSUPPORTED", location)
            dataset = (
                f"{safe['db.system.name']}://{safe['db.namespace']}/"
                f"{safe['db.collection.name']}"
            )
            service = f"service://{service_name}"
            operation = str(safe.get("db.operation.name", "")).upper()
            if operation in {"SELECT", "READ", "GET"}:
                source_datasets, target_dataset, edge_type = [dataset], service, "READS"
            elif operation in {"INSERT", "UPDATE", "DELETE", "MERGE", "WRITE", "PUT"}:
                source_datasets, target_dataset, edge_type = [service], dataset, "WRITES"
            else:
                source_datasets, target_dataset, edge_type = [service], dataset, "CONNECTS"
            body = {
                "granularity": "DATASET",
                "sourceDatasets": source_datasets,
                "targetDataset": target_dataset,
                "edgeType": edge_type,
                "exact": False,
            }
        elif "server.address" in safe:
            if "CONNECTIVITY" not in profile.permitted_granularity:
                return None, AdapterIssue("OTEL_GRANULARITY_UNSUPPORTED", location)
            body = {
                "granularity": "CONNECTIVITY",
                "sourceDatasets": [f"service://{service_name}"],
                "targetDataset": f"https://{safe['server.address']}",
                "edgeType": "CONNECTS",
                "exact": False,
            }
        else:
            return None, AdapterIssue("OTEL_LINEAGE_NOT_MAPPABLE", location)
        trace_id = str(span.get("traceId", ""))
        span_id = str(span.get("spanId", ""))
        identity = {
            "sourceChecksum": source_checksum,
            "traceId": trace_id,
            "spanId": span_id,
            "body": body,
        }
        return (
            {
                "schemaVersion": "1.0.0",
                "observationId": "otel-" + hashlib.sha256(canonical(identity).encode()).hexdigest(),
                "mechanism": "OTEL",
                "serviceName": service_name,
                "traceId": trace_id,
                "spanId": span_id,
                "observedAt": self._timestamp(span.get("startTimeUnixNano")),
                "sourceChecksum": source_checksum,
                **body,
            },
            None,
        )

    @staticmethod
    def _attributes(value: object) -> dict[str, object]:
        if not isinstance(value, list):
            return {}
        result: dict[str, object] = {}
        for item in value:
            if not isinstance(item, dict) or not isinstance(item.get("key"), str):
                continue
            wrapped = item.get("value")
            if not isinstance(wrapped, dict):
                continue
            for kind in ("stringValue", "intValue", "doubleValue", "boolValue"):
                if kind in wrapped:
                    result[str(item["key"])] = wrapped[kind]
                    break
        return result

    @staticmethod
    def _prohibited(key: str) -> bool:
        normalized = key.casefold()
        return any(part in normalized for part in _PROHIBITED_PARTS)

    @staticmethod
    def _prohibited_value(value: object) -> bool:
        if not isinstance(value, str):
            return False
        normalized = value.casefold()
        return any(marker in normalized for marker in _SECRET_VALUE_MARKERS)

    @staticmethod
    def _timestamp(value: object) -> str:
        try:
            seconds = int(str(value)) / 1_000_000_000
            return datetime.fromtimestamp(seconds, tz=UTC).isoformat().replace("+00:00", "Z")
        except (OverflowError, TypeError, ValueError):
            return "1970-01-01T00:00:00Z"


def normalize_legacy_span(
    payload: dict[str, object],
    *,
    approved_parser_contracts: frozenset[str],
) -> tuple[dict[str, object] | None, AdapterIssue | None]:
    """Compatibility seam for the durable local session API's closed legacy envelope."""
    attributes = payload.get("attributes")
    if not isinstance(attributes, dict):
        return None, AdapterIssue("OTEL_SHAPE_INVALID", "attributes")
    body: dict[str, object] = {
        "granularity": "CONNECTIVITY",
        "sourceDatasets": [str(attributes.get("lineage.source.dataset", ""))],
        "targetDataset": str(attributes.get("lineage.target.dataset", "")),
        "edgeType": "CONNECTS",
        "exact": False,
        "traceId": str(payload.get("traceId", "")),
        "spanId": str(payload.get("spanId", "")),
        "observedAt": str(payload.get("observedAt", "")),
    }
    parser_contract = payload.get("parserContract")
    if parser_contract is not None:
        if str(parser_contract) not in approved_parser_contracts:
            return None, AdapterIssue("OTEL_PARSER_NOT_APPROVED", "parserContract")
        mapping = payload.get("fieldMapping")
        if not isinstance(mapping, dict):
            return None, AdapterIssue("OTEL_SHAPE_INVALID", "fieldMapping")
        body.update(
            {
                "granularity": "ELEMENT",
                "sourceFields": [str(mapping.get("sourceField", ""))],
                "targetField": str(mapping.get("targetField", "")),
                "edgeType": "DERIVES",
                "exact": True,
                "parserContract": str(parser_contract),
            }
        )
    return body, None
