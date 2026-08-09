from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from lineage_api.application.product_api import ProductApiError, ProductApiResult
from lineage_api.infrastructure.aws.errors import AwsRetryableError


MAX_BODY_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


@lru_cache(maxsize=1)
def service_factory() -> object:
    # Kept lazy so application-contract tests never initialize AWS clients.
    from lineage_api.infrastructure.aws.query_projection import build_product_api

    return build_product_api()


def handler(event: object, _context: object) -> dict[str, Any]:
    correlation_id = _correlation_id(event)
    try:
        request = _request(event, correlation_id)
        service = service_factory()
        result = service.handle(**request)
        if not isinstance(result, ProductApiResult):
            raise RuntimeError("invalid product API result")
        return _response(result.status_code, result.document, correlation_id)
    except ProductApiError as error:
        return _response(
            error.status_code,
            {
                "error": {"code": error.code, "message": str(error)},
                "correlationId": correlation_id,
            },
            correlation_id,
        )
    except AwsRetryableError:
        return _response(
            503,
            {
                "error": {
                    "code": "SERVICE_UNAVAILABLE",
                    "message": "service is temporarily unavailable",
                },
                "correlationId": correlation_id,
            },
            correlation_id,
        )
    except Exception:
        return _response(
            500,
            {
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "request could not be completed",
                },
                "correlationId": correlation_id,
            },
            correlation_id,
        )


def _request(event: object, correlation_id: str) -> dict[str, object]:
    if not isinstance(event, Mapping):
        raise ProductApiError(400, "INVALID_REQUEST", "event must be an object")

    request_context = event.get("requestContext")
    if not isinstance(request_context, Mapping):
        raise ProductApiError(403, "FORBIDDEN", "authenticated identity is required")
    principal = _principal(request_context)
    if principal is None:
        raise ProductApiError(403, "FORBIDDEN", "authenticated identity is required")

    method = event.get("httpMethod")
    path = event.get("path")
    if not isinstance(method, str) or not isinstance(path, str):
        raise ProductApiError(400, "INVALID_REQUEST", "method and path are required")
    if len(path) > 4_096:
        raise ProductApiError(414, "URI_TOO_LONG", "request path is too long")

    query_value = event.get("queryStringParameters")
    if query_value is None:
        query: dict[str, str] = {}
    elif isinstance(query_value, Mapping) and all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in query_value.items()
    ):
        query = dict(query_value)
    else:
        raise ProductApiError(400, "INVALID_REQUEST", "query parameters are invalid")
    if len(query) > 20 or sum(len(key) + len(value) for key, value in query.items()) > 16_384:
        raise ProductApiError(400, "INVALID_REQUEST", "query parameters are too large")

    body = _body(event)
    return {
        "method": method.upper(),
        "path": path,
        "query": query,
        "body": body,
        "principal": principal,
        "correlation_id": correlation_id,
    }


def _body(event: Mapping[object, object]) -> Mapping[str, object] | None:
    raw = event.get("body")
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise ProductApiError(400, "INVALID_REQUEST", "body must be encoded text")
    if event.get("isBase64Encoded") is True:
        try:
            encoded = raw.encode("ascii")
            if len(encoded) > ((MAX_BODY_BYTES + 2) // 3) * 4 + 4:
                raise ProductApiError(413, "PAYLOAD_TOO_LARGE", "request body is too large")
            raw_bytes = base64.b64decode(encoded, validate=True)
            raw = raw_bytes.decode("utf-8")
        except (UnicodeError, UnicodeEncodeError, binascii.Error):
            raise ProductApiError(400, "INVALID_JSON", "request body is invalid") from None
    if len(raw.encode("utf-8")) > MAX_BODY_BYTES:
        raise ProductApiError(413, "PAYLOAD_TOO_LARGE", "request body is too large")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        raise ProductApiError(400, "INVALID_JSON", "request body is invalid JSON") from None
    if not isinstance(document, Mapping):
        raise ProductApiError(400, "INVALID_REQUEST", "request body must be an object")
    return document


def _principal(request_context: Mapping[object, object]) -> str | None:
    identity = request_context.get("identity")
    if isinstance(identity, Mapping):
        user_arn = identity.get("userArn")
        if isinstance(user_arn, str) and 0 < len(user_arn) <= 512:
            return user_arn
    authorizer = request_context.get("authorizer")
    if isinstance(authorizer, Mapping):
        principal_id = authorizer.get("principalId")
        if isinstance(principal_id, str) and 0 < len(principal_id) <= 512:
            return principal_id
    return None


def _correlation_id(event: object) -> str:
    if isinstance(event, Mapping):
        headers = event.get("headers")
        if isinstance(headers, Mapping):
            for key, value in headers.items():
                if (
                    isinstance(key, str)
                    and key.lower() == "x-correlation-id"
                    and isinstance(value, str)
                    and _CORRELATION_ID.fullmatch(value)
                ):
                    return value
        request_context = event.get("requestContext")
        if isinstance(request_context, Mapping):
            request_id = request_context.get("requestId")
            if isinstance(request_id, str) and _CORRELATION_ID.fullmatch(request_id):
                return request_id
    return "unavailable"


def _response(
    status_code: int, document: Mapping[str, object], correlation_id: str
) -> dict[str, Any]:
    body = json.dumps(document, sort_keys=True, separators=(",", ":"), default=str)
    if len(body.encode("utf-8")) > MAX_RESPONSE_BYTES:
        status_code = 500
        body = json.dumps(
            {
                "error": {
                    "code": "RESPONSE_TOO_LARGE",
                    "message": "response exceeded the safe size limit",
                },
                "correlationId": correlation_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
            "x-correlation-id": correlation_id,
        },
        "body": body,
    }
