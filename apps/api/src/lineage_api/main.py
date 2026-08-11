from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from lineage_api.api_models import (
    CollectionRequest,
    CorrectionRequest,
    DeploymentOutcomeRequest,
    ImpactRequest,
    PRGateEvaluationRequest,
    PushRequest,
    ReviewRequest,
)
from lineage_api.application.collections import CollectionError, CollectionService
from lineage_api.application.workflows.deployment import DeploymentEvent
from lineage_api.application.workflows.pr_gate import PRGateChange, PRGateRequest
from lineage_api.config import Settings
from lineage_api.dependencies import AppServices, build_services
from lineage_api.domain.errors import DomainError
from lineage_api.services.intake import PushDelivery


NOT_FOUND_CODES = {"RUN_NOT_FOUND", "PROPOSAL_NOT_FOUND", "EDGE_NOT_FOUND", "VERSION_NOT_FOUND"}
CONFLICT_CODES = {
    "CONCURRENT_DECISION",
    "INVALID_PROPOSAL_TRANSITION",
    "POINTER_CONFLICT",
    "FENCE_LOST",
    "STALE_BASE_VERSION",
}
UNPROCESSABLE_CODES = {"DEPTH_EXCEEDED", "INVALID_DIRECTION", "UNKNOWN_CHANGE_TYPE"}
_CORRELATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _status_for(error: DomainError) -> int:
    if error.code in NOT_FOUND_CODES:
        return 404
    if error.code in CONFLICT_CODES:
        return 409
    if error.code in UNPROCESSABLE_CODES:
        return 422
    return 400


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings.from_environment()
    services = build_services(configured)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        services.ensure_seeded()
        yield

    application = FastAPI(
        title="Lineage Collector Prototype",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.services = services
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @application.exception_handler(DomainError)
    async def domain_error_handler(_: Request, error: DomainError) -> JSONResponse:
        return JSONResponse(error.as_dict(), status_code=_status_for(error))

    @application.exception_handler(CollectionError)
    async def collection_error_handler(
        request: Request, error: CollectionError
    ) -> JSONResponse:
        return JSONResponse(
            {
                "code": error.code,
                "message": str(error),
                "correlationId": _correlation_id(request),
            },
            status_code=error.status_code,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        correlation_id = request.headers.get("x-correlation-id", "request-validation")
        serializable_errors = [
            {key: value for key, value in item.items() if key not in {"ctx", "url"}}
            for item in error.errors()
        ]
        return JSONResponse(
            {
                "code": "INVALID_REQUEST",
                "message": "Request did not match the API contract",
                "correlationId": correlation_id,
                "details": {"errors": serializable_errors},
            },
            status_code=422,
        )

    @application.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/api/demo/reset")
    def reset_demo_state(request: Request) -> dict[str, Any]:
        return _services(request).reset()

    @application.post("/api/collections", status_code=202)
    def submit_collection(
        body: CollectionRequest, request: Request, response: Response
    ) -> dict[str, Any]:
        submission = _collections(request).submit(
            body.as_submission(), correlation_id=_correlation_id(request)
        )
        response.status_code = submission.status_code
        response.headers["Location"] = submission.location
        return dict(submission.document)

    @application.get("/api/collections/{command_id}")
    def collection_status(command_id: str, request: Request) -> dict[str, Any]:
        return dict(_collections(request).status(command_id))

    @application.post("/api/events/push")
    def push_event(body: PushRequest, request: Request, response: Response) -> dict[str, Any]:
        result = _services(request).orchestration.process_push(
            PushDelivery(body.payload, body.signature)
        )
        response.status_code = 200 if result["outcome"] == "DUPLICATE" else 202
        return result

    @application.get("/api/overview")
    def overview(request: Request) -> dict[str, Any]:
        current = _services(request)
        pointer = current.publisher.pointer("staging")
        resilience = current.observability.snapshot()
        with current.database.connection() as connection:
            counts = {
                "runs": int(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]),
                "inReview": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM proposals WHERE state = 'IN_REVIEW'"
                    ).fetchone()[0]
                ),
                "quarantined": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM quarantines WHERE resolved_at IS NULL"
                    ).fetchone()[0]
                ),
            }
        return {
            "activeVersion": pointer.active_version,
            "fencingToken": pointer.fencing_token,
            "counts": counts,
            "recentRuns": current.orchestration.list_runs()[:5],
            "resilience": resilience,
        }

    @application.get("/api/operations/resilience")
    def resilience(request: Request) -> dict[str, Any]:
        return _services(request).observability.snapshot()

    @application.get("/api/runs")
    def list_runs(request: Request) -> list[dict[str, Any]]:
        return _services(request).orchestration.list_runs()

    @application.get("/api/runs/{run_id}")
    def run_detail(run_id: str, request: Request) -> dict[str, Any]:
        return _services(request).orchestration.get_run(run_id)

    @application.get("/api/proposals")
    def proposals(request: Request, state: str = "IN_REVIEW") -> list[dict[str, Any]]:
        return [proposal.as_dict() for proposal in _services(request).review.list_queue(state)]

    @application.get("/api/proposals/{proposal_id}")
    def proposal_detail(
        proposal_id: str, request: Request, version: int | None = None
    ) -> dict[str, Any]:
        return _services(request).review.get(proposal_id, version).as_dict()

    @application.post("/api/proposals/{proposal_id}/approve")
    def approve(
        proposal_id: str, body: ReviewRequest, request: Request
    ) -> dict[str, Any]:
        return _services(request).orchestration.approve(
            proposal_id,
            body.version,
            body.actor,
            body.rationale,
            body.expectedLockVersion,
        )

    @application.post("/api/proposals/{proposal_id}/reject")
    def reject(
        proposal_id: str, body: ReviewRequest, request: Request
    ) -> dict[str, Any]:
        return _services(request).orchestration.reject(
            proposal_id,
            body.version,
            body.actor,
            body.rationale,
            body.expectedLockVersion,
        )

    @application.post("/api/proposals/{proposal_id}/correct")
    def correct(
        proposal_id: str, body: CorrectionRequest, request: Request
    ) -> dict[str, Any]:
        return _services(request).orchestration.correct(
            proposal_id,
            body.version,
            body.actor,
            body.rationale,
            body.expectedLockVersion,
            body.correctedEdges,
        )

    @application.get("/api/lineage/{urn:path}")
    def lineage(
        urn: str,
        request: Request,
        direction: str = Query(default="down"),
        depth: int = Query(default=3),
        version: str | None = None,
    ) -> dict[str, Any]:
        return _services(request).query.lineage(urn, direction, depth, version)

    @application.post("/api/impact")
    def impact(body: ImpactRequest, request: Request) -> dict[str, Any]:
        return _services(request).query.impact(
            body.subject, body.changeType, body.depth, body.version
        )

    @application.post("/api/pr-gate/evaluate")
    def evaluate_pr_gate(
        body: PRGateEvaluationRequest,
        request: Request,
    ) -> dict[str, object]:
        return _services(request).pr_gate.evaluate(
            PRGateRequest(
                repo=body.repo,
                pr_number=body.prNumber,
                head_sha=body.headSha,
                target_environment=body.targetEnvironment,
                policy_version=body.policyVersion,
                candidate_artifact_digest=body.candidateArtifactDigest,
                coverage_complete=body.coverageComplete,
                changes=tuple(
                    PRGateChange(
                        change_type=change.changeType,
                        subject=change.subject,
                        evidence_mechanisms=tuple(change.evidenceMechanisms),
                    )
                    for change in body.changes
                ),
                depth=body.depth,
            )
        )

    @application.post("/api/deployments/outcomes")
    def deployment_outcome(
        body: DeploymentOutcomeRequest,
        request: Request,
    ) -> dict[str, object]:
        payload = body.payload
        return _services(request).deployment.handle(
            DeploymentEvent(
                event_id=payload.eventId,
                event_type=payload.eventType,
                provider=payload.provider,
                provider_sequence=payload.providerSequence,
                attempt=payload.attempt,
                system=payload.system,
                environment=payload.environment,
                outcome=payload.outcome,
                artifact_digest=payload.artifactDigest,
                correlation_id=payload.correlationId,
                audit_ref=payload.auditRef,
                occurred_at=payload.occurredAt,
            ),
            signature=body.signature,
        )

    @application.get("/api/edges/{edge_key}")
    def edge_detail(
        edge_key: str, request: Request, version: str | None = None
    ) -> dict[str, Any]:
        return _services(request).query.edge_detail(edge_key, version)

    @application.get("/api/quarantine")
    def quarantine(request: Request) -> list[dict[str, Any]]:
        current = _services(request)
        with current.database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM quarantines ORDER BY created_at DESC, quarantine_id"
            ).fetchall()
        return [
            {
                "quarantineId": row["quarantine_id"],
                "kind": row["kind"],
                "reason": row["reason"],
                "raw": json.loads(row["raw_json"]),
                "candidates": json.loads(row["candidates_json"]),
                "correlationId": row["correlation_id"],
                "createdAt": row["created_at"],
                "resolvedAt": row["resolved_at"],
            }
            for row in rows
        ]

    @application.get("/api/audit")
    def audit(request: Request) -> list[dict[str, Any]]:
        current = _services(request)
        with current.database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events ORDER BY created_at DESC, audit_id"
            ).fetchall()
        return [
            {
                "auditId": row["audit_id"],
                "actor": row["actor"],
                "action": row["action"],
                "resourceType": row["resource_type"],
                "resourceId": row["resource_id"],
                "correlationId": row["correlation_id"],
                "detail": json.loads(row["payload_json"]),
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    return application


def _services(request: Request) -> AppServices:
    return request.app.state.services


def _collections(request: Request) -> CollectionService:
    collections = _services(request).collections
    if collections is None:  # pragma: no cover - the composition root always wires this
        raise CollectionError(
            503, "COLLECTION_UNAVAILABLE", "collection submission is not available"
        )
    return collections


def _correlation_id(request: Request) -> str:
    value = request.headers.get("x-correlation-id")
    if isinstance(value, str) and _CORRELATION_ID.fullmatch(value):
        return value
    return "collection-request"


app = create_app()
