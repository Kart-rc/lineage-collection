from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import unquote


MAX_IDENTIFIER_LENGTH = 512
MAX_TEXT_LENGTH = 4_096
MAX_CURSOR_LENGTH = 4_096
MAX_PAGE_SIZE = 100
MAX_GRAPH_DEPTH = 5
MAX_GRAPH_RESULTS = 1_000

_PROPOSAL_STATES = frozenset(
    {"IN_REVIEW", "APPROVED", "REJECTED", "FINALIZED"}
)
_LINEAGE_DIRECTIONS = frozenset({"up", "down", "both"})
_CHANGE_TYPES = frozenset(
    {
        "COLUMN_DROP",
        "COLUMN_TYPE_CHANGE",
        "DATASET_REMOVAL",
        "COLUMN_RENAME",
        "TRANSFORM_CHANGE",
        "FINGERPRINT_DRIFT",
    }
)
_KILL_SWITCH_SCOPES = frozenset({"GLOBAL", "ENVIRONMENT", "WORKLOAD"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+=,-]*$")
# Graph subjects additionally allow one '#' separator: element URNs
# (dataset#element) and analyzer service endpoints (Type#method).
_SUBJECT = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:@/+=,-]*(?:#[A-Za-z0-9._:@/+=,-]+)?$"
)


@dataclass(frozen=True)
class ProductApiResult:
    status_code: int
    document: Mapping[str, object]
    headers: Mapping[str, str] = field(default_factory=dict)


class ProductApiError(ValueError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class ProductApiPort(Protocol):
    """Application-facing production query and mutation boundary."""

    def __getattr__(self, name: str) -> Any: ...


class ProductApiService:
    """Closed, bounded HTTP contract independent of API Gateway and AWS SDKs."""

    def __init__(self, port: ProductApiPort) -> None:
        self._port = port

    def handle(
        self,
        *,
        method: str,
        path: str,
        query: Mapping[str, str],
        body: Mapping[str, object] | None,
        principal: str,
        correlation_id: str,
    ) -> ProductApiResult:
        method = method.upper()
        common = {
            "principal": _required_text("principal", principal, MAX_IDENTIFIER_LENGTH),
            "correlation_id": _required_text(
                "correlationId", correlation_id, MAX_IDENTIFIER_LENGTH
            ),
        }
        status_code = 200
        headers: dict[str, str] = {}

        if method == "GET" and path == "/api/overview":
            _keys(query, {"environment"}, "query")
            document = self._port.overview(
                environment=_optional_identifier(query, "environment"), **common
            )
        elif method == "GET" and path == "/api/operations/resilience":
            _keys(query, {"environment"}, "query")
            document = self._port.resilience(
                environment=_optional_identifier(query, "environment"), **common
            )
        elif method == "GET" and path == "/api/runs":
            _keys(query, {"limit", "cursor", "workflow", "status", "environment"}, "query")
            document = self._port.list_runs(
                limit=_integer(query.get("limit", "25"), "limit", 1, MAX_PAGE_SIZE),
                cursor=_optional_cursor(query),
                workflow=_optional_identifier(query, "workflow"),
                status=_optional_identifier(query, "status"),
                environment=_optional_identifier(query, "environment"),
                **common,
            )
        elif method == "GET" and (value := _route(path, r"/api/runs/([^/]+)")):
            _keys(query, set(), "query")
            document = self._port.get_run(
                run_id=_resource_identifier("runId", value), **common
            )
        elif method == "GET" and path == "/api/proposals":
            _keys(query, {"limit", "cursor", "state"}, "query")
            state = query.get("state", "IN_REVIEW")
            if state not in _PROPOSAL_STATES:
                raise _bad("state", "is not supported")
            document = self._port.list_proposals(
                limit=_integer(query.get("limit", "25"), "limit", 1, MAX_PAGE_SIZE),
                cursor=_optional_cursor(query),
                state=state,
                **common,
            )
        elif method == "GET" and (
            value := _route(path, r"/api/proposals/([^/]+)")
        ):
            _keys(query, {"version"}, "query")
            document = self._port.get_proposal(
                proposal_id=_resource_identifier("proposalId", value),
                version=_optional_integer(query, "version", 1, 2_147_483_647),
                **common,
            )
        elif method == "POST" and (
            match := re.fullmatch(
                r"/api/proposals/([^/]+)/(approve|reject|correct)", path
            )
        ):
            _keys(query, set(), "query")
            document = self._review(
                proposal_id=_resource_identifier(
                    "proposalId", unquote(match.group(1))
                ),
                action=match.group(2).upper(),
                body=_required_body(body),
                **common,
            )
        elif method == "GET" and (
            value := _route(path, r"/api/lineage/(.+)")
        ):
            _keys(query, {"direction", "depth", "limit", "version"}, "query")
            direction = query.get("direction", "both")
            if direction not in _LINEAGE_DIRECTIONS:
                raise _bad("direction", "is not supported")
            document = self._port.lineage(
                subject=_subject_identifier("subject", value),
                direction=direction,
                depth=_integer(query.get("depth", "3"), "depth", 1, MAX_GRAPH_DEPTH),
                limit=_integer(
                    query.get("limit", "250"), "limit", 1, MAX_GRAPH_RESULTS
                ),
                version=_optional_identifier(query, "version"),
                **common,
            )
        elif method == "GET" and path == "/api/interactions":
            # The second lineage plane — service-to-service interactions.
            _keys(query, {"system"}, "query")
            document = self._port.interactions(
                system=_optional_identifier(query, "system"), **common
            )
        elif method == "GET" and (value := _route(path, r"/api/edges/([^/]+)")):
            _keys(query, {"version"}, "query")
            document = self._port.edge_detail(
                edge_key=_resource_identifier("edgeKey", value),
                version=_optional_identifier(query, "version"),
                **common,
            )
        elif method == "POST" and path == "/api/impact":
            _keys(query, set(), "query")
            document = self._impact(_required_body(body), **common)
        elif method == "GET" and path == "/api/runtime/admin":
            _keys(query, {"limit", "cursor", "scopeType", "scopeValue"}, "query")
            scope_type = query.get("scopeType")
            if scope_type is not None and scope_type not in _KILL_SWITCH_SCOPES:
                raise _bad("scopeType", "is not supported")
            document = self._port.runtime_admin(
                limit=_integer(query.get("limit", "25"), "limit", 1, MAX_PAGE_SIZE),
                cursor=_optional_cursor(query),
                scope_type=scope_type,
                scope_value=_optional_identifier(query, "scopeValue"),
                **common,
            )
        elif method == "POST" and path == "/api/runtime/admin/kill-switch":
            _keys(query, set(), "query")
            document = self._kill_switch(_required_body(body), **common)
        elif method == "POST" and path == "/api/collections":
            _keys(query, set(), "query")
            document = self._port.submit_collection(
                body=_required_body(body), **common
            )
            # Accepted, duplicate, and reused submissions are indistinguishable to the
            # caller: all three return 202 with the same durable status resource.
            status_code = 202
            headers["location"] = _status_url(document)
        elif method == "GET" and (
            value := _route(path, r"/api/collections/([^/]+)")
        ):
            _keys(query, set(), "query")
            document = self._port.get_collection(
                command_id=_resource_identifier("commandId", value), **common
            )
        else:
            raise ProductApiError(404, "NOT_FOUND", "route is not available")

        if not isinstance(document, Mapping):
            raise RuntimeError("product API port returned a non-document")
        return ProductApiResult(status_code, document, headers)

    def _review(
        self,
        *,
        proposal_id: str,
        action: str,
        body: Mapping[str, object],
        principal: str,
        correlation_id: str,
    ) -> Mapping[str, object]:
        allowed = {"version", "expectedLockVersion", "actor", "rationale"}
        if action == "CORRECT":
            allowed.add("correctedEdges")
        _keys(body, allowed, "body")
        corrected_edges: list[object] | None = None
        if action == "CORRECT":
            raw_edges = body.get("correctedEdges")
            if not isinstance(raw_edges, list) or not raw_edges:
                raise _bad("correctedEdges", "must be a non-empty array")
            if len(raw_edges) > MAX_GRAPH_RESULTS:
                raise _bad("correctedEdges", "has too many entries")
            if any(not isinstance(edge, Mapping) for edge in raw_edges):
                raise _bad("correctedEdges", "must contain objects")
            corrected_edges = list(raw_edges)
        return self._port.review_proposal(
            proposal_id=proposal_id,
            action=action,
            version=_body_integer(body, "version", 1, 2_147_483_647),
            expected_lock_version=_body_integer(
                body, "expectedLockVersion", 1, 2_147_483_647
            ),
            actor=_body_text(body, "actor", MAX_IDENTIFIER_LENGTH),
            rationale=_body_text(body, "rationale", MAX_TEXT_LENGTH),
            corrected_edges=corrected_edges,
            principal=principal,
            correlation_id=correlation_id,
        )

    def _impact(
        self,
        body: Mapping[str, object],
        **common: str,
    ) -> Mapping[str, object]:
        _keys(body, {"subject", "changeType", "depth", "limit", "version"}, "body")
        change_type = _body_text(body, "changeType", 64)
        if change_type not in _CHANGE_TYPES:
            raise _bad("changeType", "is not supported")
        return self._port.impact(
            subject=_subject_identifier(
                "subject", _body_text(body, "subject", MAX_IDENTIFIER_LENGTH)
            ),
            change_type=change_type,
            depth=_body_integer(body, "depth", 1, MAX_GRAPH_DEPTH),
            limit=_optional_body_integer(body, "limit", 1, MAX_GRAPH_RESULTS) or 250,
            version=(
                None
                if "version" not in body
                else _identifier(
                    "version", _required_text("version", body["version"], 512)
                )
            ),
            **common,
        )

    def _kill_switch(
        self,
        body: Mapping[str, object],
        **common: str,
    ) -> Mapping[str, object]:
        _keys(
            body,
            {
                "scopeType",
                "scopeValue",
                "active",
                "expectedVersion",
                "actor",
                "rationale",
            },
            "body",
        )
        scope_type = _body_text(body, "scopeType", 32)
        if scope_type not in _KILL_SWITCH_SCOPES:
            raise _bad("scopeType", "is not supported")
        active = body.get("active")
        if not isinstance(active, bool):
            raise _bad("active", "must be a boolean")
        return self._port.set_runtime_kill_switch(
            scope_type=scope_type,
            scope_value=_identifier(
                "scopeValue", _body_text(body, "scopeValue", MAX_IDENTIFIER_LENGTH)
            ),
            active=active,
            expected_version=_body_integer(
                body, "expectedVersion", 0, 2_147_483_647
            ),
            actor=_body_text(body, "actor", MAX_IDENTIFIER_LENGTH),
            rationale=_body_text(body, "rationale", MAX_TEXT_LENGTH),
            **common,
        )


def _status_url(document: object) -> str:
    if not isinstance(document, Mapping):
        raise RuntimeError("product API port returned a non-document")
    status_url = document.get("statusUrl")
    if (
        not isinstance(status_url, str)
        or not status_url.startswith("/api/collections/")
        or len(status_url) > MAX_IDENTIFIER_LENGTH
    ):
        raise RuntimeError("collection submission returned no status resource")
    return status_url


def _required_body(body: Mapping[str, object] | None) -> Mapping[str, object]:
    if body is None:
        raise _bad("body", "is required")
    return body


def _keys(value: Mapping[str, object], allowed: set[str], location: str) -> None:
    unexpected = set(value) - allowed
    if unexpected:
        raise _bad(location, f"contains unexpected fields: {', '.join(sorted(unexpected))}")


def _bad(field: str, message: str) -> ProductApiError:
    return ProductApiError(400, "INVALID_REQUEST", f"{field} {message}")


def _required_text(field: str, value: object, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _bad(field, "must be a non-empty string")
    value = value.strip()
    if len(value) > maximum:
        raise _bad(field, f"must be at most {maximum} characters")
    return value


def _identifier(field: str, value: object) -> str:
    result = _required_text(field, value, MAX_IDENTIFIER_LENGTH)
    if not _IDENTIFIER.fullmatch(result):
        raise _bad(field, "contains unsupported characters")
    return result


def _subject_identifier(field: str, value: object) -> str:
    result = _required_text(field, value, MAX_IDENTIFIER_LENGTH)
    if not _SUBJECT.fullmatch(result):
        raise _bad(field, "contains unsupported characters")
    return result


def _resource_identifier(field: str, value: object) -> str:
    result = _identifier(field, value)
    if "/" in result:
        raise _bad(field, "contains unsupported characters")
    return result


def _optional_identifier(query: Mapping[str, str], field: str) -> str | None:
    value = query.get(field)
    return None if value is None else _identifier(field, value)


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise _bad(field, "must be an integer")
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise _bad(field, "must be an integer") from None
    if str(result) != str(value):
        raise _bad(field, "must be an integer")
    if result < minimum or result > maximum:
        raise _bad(field, f"must be between {minimum} and {maximum}")
    return result


def _optional_integer(
    query: Mapping[str, str], field: str, minimum: int, maximum: int
) -> int | None:
    value = query.get(field)
    return None if value is None else _integer(value, field, minimum, maximum)


def _body_integer(
    body: Mapping[str, object], field: str, minimum: int, maximum: int
) -> int:
    if field not in body:
        raise _bad(field, "is required")
    return _integer(body[field], field, minimum, maximum)


def _optional_body_integer(
    body: Mapping[str, object], field: str, minimum: int, maximum: int
) -> int | None:
    if field not in body:
        return None
    return _integer(body[field], field, minimum, maximum)


def _body_text(body: Mapping[str, object], field: str, maximum: int) -> str:
    if field not in body:
        raise _bad(field, "is required")
    return _required_text(field, body[field], maximum)


def _optional_cursor(query: Mapping[str, str]) -> str | None:
    cursor = query.get("cursor")
    if cursor is None:
        return None
    return _required_text("cursor", cursor, MAX_CURSOR_LENGTH)


def _route(path: str, pattern: str) -> str | None:
    match = re.fullmatch(pattern, path)
    return None if match is None else unquote(match.group(1))
