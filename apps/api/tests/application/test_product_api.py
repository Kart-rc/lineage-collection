from __future__ import annotations

from typing import Any

import pytest

from lineage_api.application.product_api import (
    ProductApiError,
    ProductApiService,
)


class Port:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str):
        def call(**kwargs: Any) -> dict[str, object]:
            self.calls.append((name, kwargs))
            if name.startswith("list_"):
                return {"items": [], "nextCursor": None}
            return {"operation": name}

        return call


def _call(
    api: ProductApiService,
    method: str,
    path: str,
    *,
    query: dict[str, str] | None = None,
    body: dict[str, object] | None = None,
):
    return api.handle(
        method=method,
        path=path,
        query=query or {},
        body=body,
        principal="arn:aws:iam::111111111111:role/lineage-reviewer",
        correlation_id="corr-product-1",
    )


def test_product_api_has_closed_route_parity_without_demo_mutations() -> None:
    port = Port()
    api = ProductApiService(port)
    routes = [
        ("GET", "/api/overview", {"environment": "staging"}, None, "overview"),
        ("GET", "/api/operations/resilience", {}, None, "resilience"),
        ("GET", "/api/runs", {"limit": "25"}, None, "list_runs"),
        ("GET", "/api/runs/run-1", {}, None, "get_run"),
        ("GET", "/api/proposals", {"state": "IN_REVIEW"}, None, "list_proposals"),
        ("GET", "/api/proposals/proposal-1", {"version": "1"}, None, "get_proposal"),
        (
            "POST",
            "/api/proposals/proposal-1/approve",
            {},
            {
                "version": 1,
                "expectedLockVersion": 2,
                "actor": "reviewer@example.com",
                "rationale": "validated",
            },
            "review_proposal",
        ),
        (
            "POST",
            "/api/proposals/proposal-1/reject",
            {},
            {
                "version": 1,
                "expectedLockVersion": 2,
                "actor": "reviewer@example.com",
                "rationale": "invalid evidence",
            },
            "review_proposal",
        ),
        (
            "POST",
            "/api/proposals/proposal-1/correct",
            {},
            {
                "version": 1,
                "expectedLockVersion": 2,
                "actor": "reviewer@example.com",
                "rationale": "corrected target",
                "correctedEdges": [{"edgeKey": "edge-1"}],
            },
            "review_proposal",
        ),
        (
            "GET",
            "/api/lineage/urn%3Aldp%3Astaging%3Asnowflake%3Apayments%3Araw.transactions",
            {"direction": "down", "depth": "3", "version": "graph-v1"},
            None,
            "lineage",
        ),
        ("GET", "/api/edges/edge-1", {"version": "graph-v1"}, None, "edge_detail"),
        (
            "POST",
            "/api/impact",
            {},
            {
                "subject": "urn:ldp:staging:snowflake:payments:raw.transactions",
                "changeType": "COLUMN_DROP",
                "depth": 5,
                "version": "graph-v1",
            },
            "impact",
        ),
        ("GET", "/api/runtime/admin", {}, None, "runtime_admin"),
        (
            "POST",
            "/api/runtime/admin/kill-switch",
            {},
            {
                "scopeType": "ENVIRONMENT",
                "scopeValue": "production",
                "active": True,
                "expectedVersion": 3,
                "actor": "operator@example.com",
                "rationale": "incident containment",
            },
            "set_runtime_kill_switch",
        ),
    ]

    for method, path, query, body, operation in routes:
        result = _call(api, method, path, query=query, body=body)
        assert result.status_code in {200, 202}
        assert port.calls[-1][0] == operation

    with pytest.raises(ProductApiError) as error:
        _call(api, "POST", "/api/demo/reset", body={})
    assert error.value.status_code == 404
    assert all(call[0] not in {"reset", "seed"} for call in port.calls)


def test_product_api_bounds_queries_and_closes_privileged_mutation_bodies() -> None:
    port = Port()
    api = ProductApiService(port)

    for query in ({"limit": "101"}, {"limit": "0"}, {"limit": "NaN"}):
        with pytest.raises(ProductApiError, match="limit"):
            _call(api, "GET", "/api/runs", query=query)
    with pytest.raises(ProductApiError, match="depth"):
        _call(
            api,
            "GET",
            "/api/lineage/urn%3Aldp%3Astaging%3Asnowflake%3Apayments%3Araw.transactions",
            query={"depth": "6"},
        )
    with pytest.raises(ProductApiError, match="unexpected"):
        _call(
            api,
            "POST",
            "/api/proposals/proposal-1/approve",
            body={
                "version": 1,
                "expectedLockVersion": 1,
                "actor": "reviewer",
                "rationale": "ok",
                "admin": True,
            },
        )
    with pytest.raises(ProductApiError, match="expectedLockVersion"):
        _call(
            api,
            "POST",
            "/api/proposals/proposal-1/reject",
            body={
                "version": 1,
                "expectedLockVersion": 0,
                "actor": "reviewer",
                "rationale": "reject",
            },
        )
    assert port.calls == []


@pytest.mark.parametrize(
    "change_type",
    [
        "COLUMN_DROP",
        "COLUMN_TYPE_CHANGE",
        "DATASET_REMOVAL",
        "COLUMN_RENAME",
        "TRANSFORM_CHANGE",
        "FINGERPRINT_DRIFT",
    ],
)
def test_product_api_uses_the_existing_impact_change_vocabulary(
    change_type: str,
) -> None:
    port = Port()
    result = _call(
        ProductApiService(port),
        "POST",
        "/api/impact",
        body={
            "subject": "urn:ldp:staging:snowflake:payments:raw.transactions",
            "changeType": change_type,
            "depth": 3,
            "version": "graph-v7",
        },
    )

    assert result.status_code == 200
    assert port.calls[-1][1]["version"] == "graph-v7"


@pytest.mark.parametrize(
    "path",
    [
        "/api/runs/run-1%2Fhidden",
        "/api/proposals/proposal-1%2Fapprove",
        "/api/edges/edge-1%2Fhidden",
    ],
)
def test_product_api_rejects_encoded_path_separators(path: str) -> None:
    with pytest.raises(ProductApiError, match="unsupported characters"):
        _call(ProductApiService(Port()), "GET", path)


def test_product_api_passes_principal_correlation_and_optimistic_version_to_review() -> None:
    port = Port()
    api = ProductApiService(port)
    result = _call(
        api,
        "POST",
        "/api/proposals/proposal-1/approve",
        body={
            "version": 2,
            "expectedLockVersion": 7,
            "actor": "reviewer@example.com",
            "rationale": "evidence verified",
        },
    )

    assert result.status_code == 200
    assert port.calls == [
        (
            "review_proposal",
            {
                "proposal_id": "proposal-1",
                "action": "APPROVE",
                "version": 2,
                "expected_lock_version": 7,
                "actor": "reviewer@example.com",
                "rationale": "evidence verified",
                "corrected_edges": None,
                "principal": "arn:aws:iam::111111111111:role/lineage-reviewer",
                "correlation_id": "corr-product-1",
            },
        )
    ]
