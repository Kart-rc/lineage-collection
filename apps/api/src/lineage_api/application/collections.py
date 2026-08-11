"""Durable collection submit and status contract shared by every product surface.

FastAPI, the environment-neutral ``ProductApiService``, and the AWS API Gateway entry
point all parse and validate submissions through this module, so no surface can accept a
request the others would reject.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from lineage_api.application.repository_acquisition import (
    GitRepositoryRequest,
    LocalCheckoutRequest,
    RepositoryAcquisitionError,
    RepositoryAcquisitionService,
    RepositorySourceRequest,
)
from lineage_api.application.repository_collection import RepositoryCollectionError
from lineage_api.services.analyzer_registry import AnalyzerSelectionError


SOURCE_TYPES = ("LOCAL_CHECKOUT", "GIT")
# The durable command vocabulary is QUEUED, RUNNING, RETRY_WAIT, COMPLETED,
# FAILED_REDRIVABLE and FAILED_TERMINAL. Only the last two of those are settled;
# a redrivable failure can still make progress, so it is not terminal.
TERMINAL_COMMAND_STATUSES = frozenset({"COMPLETED", "FAILED_TERMINAL"})

MAX_ORIGIN_LENGTH = 2_048
MAX_PATH_LENGTH = 4_096
MAX_DETERMINANT_LENGTH = 128

_EXACT_COMMIT = re.compile(r"[0-9a-f]{40}")
_COMMAND_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")

_REQUIRED_FIELDS = (
    "sourceType",
    "origin",
    "repository",
    "revision",
    "environment",
    "platform",
    "system",
    "analyzerPack",
    "ruleset",
    "schemaProfile",
)
_ALLOWED_FIELDS = frozenset((*_REQUIRED_FIELDS, "checkoutPath"))


class CollectionError(RuntimeError):
    """A bounded, surface-independent collection failure."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message[:160])
        self.status_code = status_code
        self.code = code


def _invalid(field: str, message: str) -> CollectionError:
    return CollectionError(400, "INVALID_REQUEST", f"{field} {message}")


def _text(body: Mapping[str, object], field: str, limit: int) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value or value != value.strip():
        raise _invalid(field, "must be a bounded non-empty string")
    if len(value.encode()) > limit or any(ord(character) < 32 for character in value):
        raise _invalid(field, "is outside its closed bound")
    return value


def parse_collection_submission(
    body: Mapping[str, object] | None,
    *,
    allow_local_sources: bool,
) -> RepositorySourceRequest:
    """Turn an untrusted submission document into a validated source request."""

    if not isinstance(body, Mapping):
        raise _invalid("body", "must be an object")
    unexpected = set(body) - _ALLOWED_FIELDS
    if unexpected:
        raise _invalid("body", f"contains unexpected fields: {', '.join(sorted(unexpected))}")
    missing = [field for field in _REQUIRED_FIELDS if field not in body]
    if missing:
        raise _invalid("body", f"is missing required fields: {', '.join(missing)}")

    source_type = _text(body, "sourceType", 32)
    if source_type not in SOURCE_TYPES:
        raise _invalid("sourceType", "is not a supported source mode")

    revision = _text(body, "revision", 64)
    if _EXACT_COMMIT.fullmatch(revision) is None:
        raise _invalid("revision", "must be an exact lowercase 40-hex commit")

    shared: dict[str, Any] = {
        "origin": _text(body, "origin", MAX_ORIGIN_LENGTH),
        "repository": _text(body, "repository", MAX_DETERMINANT_LENGTH),
        "revision": revision,
        "environment": _text(body, "environment", MAX_DETERMINANT_LENGTH),
        "platform": _text(body, "platform", MAX_DETERMINANT_LENGTH),
        "system": _text(body, "system", MAX_DETERMINANT_LENGTH),
        "analyzer_pack": _text(body, "analyzerPack", MAX_DETERMINANT_LENGTH),
        "ruleset": _text(body, "ruleset", MAX_DETERMINANT_LENGTH),
        "schema_profile": _text(body, "schemaProfile", MAX_DETERMINANT_LENGTH),
    }

    if source_type == "GIT":
        if "checkoutPath" in body:
            raise _invalid("checkoutPath", "is not allowed for GIT sources")
        try:
            return GitRepositoryRequest(**shared)
        except (TypeError, ValueError):
            raise _invalid("body", "is not a valid GIT collection request") from None

    if not allow_local_sources:
        raise CollectionError(
            403,
            "LOCAL_SOURCE_DISABLED",
            "local repository sources are disabled in this environment",
        )
    if "checkoutPath" not in body:
        raise _invalid("checkoutPath", "is required for LOCAL_CHECKOUT sources")
    checkout_path = _text(body, "checkoutPath", MAX_PATH_LENGTH)
    try:
        return LocalCheckoutRequest(**shared, checkout_root=Path(checkout_path))
    except (TypeError, ValueError):
        raise _invalid("body", "is not a valid LOCAL_CHECKOUT collection request") from None


class CollectionStore(Protocol):
    """Durable projection of submitted collections, keyed by durable command."""

    def record(self, document: Mapping[str, object]) -> None: ...

    def get(self, command_id: str) -> Mapping[str, object] | None: ...


@dataclass(frozen=True, slots=True)
class CollectionSubmission:
    status_code: int
    location: str
    document: Mapping[str, object]


def validate_command_id(command_id: object) -> str:
    if not isinstance(command_id, str) or _COMMAND_ID.fullmatch(command_id) is None:
        raise _invalid("commandId", "is not a valid durable command identifier")
    return command_id


class CollectionService:
    """Submits repository collections durably and projects their status."""

    def __init__(
        self,
        *,
        acquisition: RepositoryAcquisitionService,
        store: CollectionStore,
        allow_local_sources: bool,
    ) -> None:
        if not isinstance(allow_local_sources, bool):
            raise TypeError("local repository source policy must be boolean")
        self._acquisition = acquisition
        self._store = store
        self._allow_local = allow_local_sources

    @property
    def allow_local_sources(self) -> bool:
        return self._allow_local

    def submit(
        self,
        body: Mapping[str, object] | None,
        *,
        correlation_id: str,
    ) -> CollectionSubmission:
        request = parse_collection_submission(
            body, allow_local_sources=self._allow_local
        )
        try:
            summary = self._acquisition.collect(request)
        except RepositoryAcquisitionError as error:
            raise CollectionError(
                _STATUS_BY_CODE.get(error.code, 502),
                error.code,
                _MESSAGE_BY_CODE.get(error.code, "repository collection failed"),
            ) from None
        except AnalyzerSelectionError as error:
            raise CollectionError(422, error.code, "analyzer selection is not supported") from None
        except RepositoryCollectionError as error:
            raise CollectionError(502, error.code, "repository collection failed") from None
        except Exception:
            raise CollectionError(500, "PIPELINE_FAILED", "repository collection failed") from None

        document = _project(summary, request, correlation_id=correlation_id)
        command_id = document.get("commandId")
        if not isinstance(command_id, str) or not command_id:
            raise CollectionError(500, "PIPELINE_FAILED", "repository collection failed")

        # A settled collection is authoritative. Re-submitting an equivalent request
        # must return the recorded outcome unchanged rather than overwriting it with
        # this request's DUPLICATE verdict, so the status resource stays stable and a
        # duplicate produces no durable change at all.
        stored = self._store.get(command_id)
        if stored is not None and stored.get("terminal") is True:
            document = dict(stored)
        else:
            self._store.record(document)
        return CollectionSubmission(
            status_code=202,
            location=f"/api/collections/{command_id}",
            document=document,
        )

    def status(self, command_id: str) -> Mapping[str, object]:
        document = self._store.get(validate_command_id(command_id))
        if document is None:
            raise CollectionError(404, "COLLECTION_NOT_FOUND", "collection is not known")
        return document


_STATUS_BY_CODE = {
    "LOCAL_SOURCE_DISABLED": 403,
    "SOURCE_ACQUISITION_FAILED": 502,
}
_MESSAGE_BY_CODE = {
    "LOCAL_SOURCE_DISABLED": "local repository sources are disabled in this environment",
    "SOURCE_ACQUISITION_FAILED": "repository source acquisition failed",
}


def _project(
    summary: Mapping[str, object],
    request: RepositorySourceRequest,
    *,
    correlation_id: str,
) -> dict[str, Any]:
    """Build the stable status document.

    The checkout path, raw Git output, and any exception text are deliberately absent:
    only the canonical credential-free origin and bounded codes survive.
    """

    command_status = summary.get("commandStatus")
    document: dict[str, Any] = {
        key: summary.get(key)
        for key in (
            "collectionId",
            "commandId",
            "statusUrl",
            "outcome",
            "reasonCode",
            "commandStatus",
            "determinantDigest",
            "revision",
            "scopeDigest",
            "runId",
            "runStatus",
            "stages",
            "proposalId",
            "proposalStatus",
            "runtimeStatus",
            "analysisStatus",
            "statusReasons",
            "coverageManifest",
            "counts",
        )
    }
    document.update(
        {
            "sourceType": request.source_type,
            "origin": request.origin,
            "repository": request.repository,
            "environment": request.environment,
            "platform": request.platform,
            "system": request.system,
            "analyzerPack": request.analyzer_pack,
            "ruleset": request.ruleset,
            "schemaProfile": request.schema_profile,
            "correlationId": correlation_id,
            "terminal": command_status in TERMINAL_COMMAND_STATUSES,
        }
    )
    return document
