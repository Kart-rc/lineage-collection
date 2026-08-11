from __future__ import annotations

import json
from typing import Any

import pytest

from lineage_api.application.product_api import ProductApiResult
from lineage_api.entrypoints.aws import product_api
from lineage_api.infrastructure.aws.errors import AwsRetryableError


def _event(
    method: str = "GET",
    path: str = "/api/overview",
    *,
    body: object | None = None,
) -> dict[str, Any]:
    return {
        "httpMethod": method,
        "path": path,
        "headers": {"X-Correlation-Id": "corr-api-1"},
        "queryStringParameters": {"environment": "staging"},
        "body": None if body is None else json.dumps(body),
        "isBase64Encoded": False,
        "requestContext": {
            "requestId": "request-1",
            "identity": {
                "userArn": "arn:aws:iam::111111111111:role/lineage-reader"
            },
        },
    }


def test_product_lambda_passes_only_bounded_normalized_request_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class Service:
        def handle(self, **kwargs: Any) -> ProductApiResult:
            calls.append(kwargs)
            return ProductApiResult(200, {"activeVersion": "graph-v1"})

    monkeypatch.setattr(product_api, "service_factory", lambda: Service())
    response = product_api.handler(_event(), object())

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"activeVersion": "graph-v1"}
    assert response["headers"]["x-correlation-id"] == "corr-api-1"
    assert response["headers"]["cache-control"] == "no-store"
    assert calls == [
        {
            "method": "GET",
            "path": "/api/overview",
            "query": {"environment": "staging"},
            "body": None,
            "principal": "arn:aws:iam::111111111111:role/lineage-reader",
            "correlation_id": "corr-api-1",
        }
    ]


def test_product_lambda_rejects_missing_identity_and_oversized_or_invalid_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        product_api,
        "service_factory",
        lambda: (_ for _ in ()).throw(AssertionError("service must not be built")),
    )
    missing_identity = _event()
    missing_identity["requestContext"]["identity"] = {}
    assert product_api.handler(missing_identity, object())["statusCode"] == 403

    oversized = _event("POST", "/api/impact")
    oversized["body"] = "x" * (product_api.MAX_BODY_BYTES + 1)
    assert product_api.handler(oversized, object())["statusCode"] == 413

    invalid = _event("POST", "/api/impact")
    invalid["body"] = "not-json"
    assert product_api.handler(invalid, object())["statusCode"] == 400


def test_product_lambda_redacts_unexpected_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Service:
        def handle(self, **_kwargs: Any) -> ProductApiResult:
            raise RuntimeError("secret-token-do-not-return")

    monkeypatch.setattr(product_api, "service_factory", lambda: Service())
    response = product_api.handler(_event(), object())

    assert response["statusCode"] == 500
    assert "secret-token" not in response["body"]
    assert json.loads(response["body"])["correlationId"] == "corr-api-1"


def test_product_lambda_marks_retryable_aws_failures_as_temporarily_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Service:
        def handle(self, **_kwargs: Any) -> ProductApiResult:
            raise AwsRetryableError("dynamodb secret detail")

    monkeypatch.setattr(product_api, "service_factory", lambda: Service())
    response = product_api.handler(_event(), object())

    assert response["statusCode"] == 503
    assert "secret detail" not in response["body"]
    assert json.loads(response["body"])["error"]["code"] == "SERVICE_UNAVAILABLE"
