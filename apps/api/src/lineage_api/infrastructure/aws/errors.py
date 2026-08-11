from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar


class AwsAdapterError(RuntimeError):
    """Base error for a translated AWS service failure."""


class AwsRetryableError(AwsAdapterError):
    pass


class AwsConflictError(AwsAdapterError):
    pass


class AwsStaleFenceError(AwsConflictError):
    pass


class AwsMissingObjectError(AwsAdapterError):
    pass


_RETRYABLE = {
    "InternalFailure",
    "InternalServerError",
    "KmsThrottlingException",
    "ProvisionedThroughputExceededException",
    "RequestLimitExceeded",
    "RequestTimeout",
    "ServiceUnavailable",
    "SlowDown",
    "Throttling",
    "ThrottlingException",
    "TooManyRequestsException",
}
_CONFLICT = {
    "ConditionalCheckFailedException",
    "ConflictException",
    "ExecutionAlreadyExists",
    "IdempotentParameterMismatch",
    "PreconditionFailed",
    "TransactionCanceledException",
}
_MISSING = {"NoSuchKey", "NotFound", "ResourceNotFoundException"}


def error_code(error: Exception) -> str:
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        details = response.get("Error")
        if isinstance(details, dict) and isinstance(details.get("Code"), str):
            return details["Code"]
    return type(error).__name__


T = TypeVar("T")


def aws_call(operation: str, call: Callable[..., T], **kwargs: Any) -> T:
    try:
        return call(**kwargs)
    except AwsAdapterError:
        raise
    except Exception as error:
        code = error_code(error)
        message = f"{operation} failed with {code}"
        if code in _RETRYABLE:
            raise AwsRetryableError(message) from error
        if code in _CONFLICT:
            raise AwsConflictError(message) from error
        if code in _MISSING:
            raise AwsMissingObjectError(message) from error
        raise AwsAdapterError(message) from error
