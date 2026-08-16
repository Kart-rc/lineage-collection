"""Serve the production product API against the floci-emulated AWS state.

This is the same ``ProductApiService`` the AWS Lambda entrypoint wraps — list
runs, review queue, proposal detail + approve/reject, lineage, edge detail,
impact — composed from real floci-backed adapters (DynamoDB control/ledger/
proposal tables, versioned S3 evidence) plus the run's SQLite graph projection
(floci has no ``neptunedata`` API). The React control room (`apps/web`) talks to
it unmodified through the vite dev proxy.

One deliberate addition: in AWS a DynamoDB stream triggers the publication
Lambda when a proposal is approved. Locally nothing drains that outbox, so this
server plays the stream's role — after an APPROVE it executes the pending
``PROPOSAL_APPROVED`` envelope through the same stage executor the E2E used,
completing the fenced publish so the UI immediately reflects the new graph.

Run:  uv run --project apps/api uvicorn --app-dir scripts/floci \
          floci_product_api:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration" / "floci"))

ENV_FILE = Path(__file__).parent / "floci.env"
LOG = logging.getLogger("floci-product-api")
logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")


def _load_env() -> None:
    import os

    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line and "=" in line and not line.startswith("#"):
                name, _, value = line.partition("=")
                os.environ.setdefault(name.strip(), value.strip())


def _projection_path() -> Path:
    import os

    override = os.environ.get("LINEAGE_FLOCI_PROJECTION_DB")
    if override:
        return Path(override)
    from collection_runner import bootstrap_live_projection

    # One persistent graph store (the Neptune stand-in): every historical run DB
    # is merged in once, and all future publications land here too.
    return bootstrap_live_projection(ROOT / "data" / "floci" / "projection-live.db")


def build() -> tuple[Any, Any, Any, Any]:
    import os

    import boto3
    from botocore.config import Config

    from lineage_api.application.product_api import ProductApiService
    from lineage_api.application.stage_execution import StageDispatcher
    from lineage_api.application.stage_handlers import production_stage_use_cases
    from lineage_api.infrastructure.aws.composition import AwsStageExecutor
    from lineage_api.infrastructure.aws.config import AwsRuntimeConfig
    from lineage_api.infrastructure.aws.dynamodb_control import DynamoDbControlAdapter
    from lineage_api.infrastructure.aws.kinesis_runtime import KinesisRuntimeAdapter
    from lineage_api.infrastructure.aws.query_projection import AwsProductQueryProjection
    from lineage_api.infrastructure.aws.s3_artifacts import S3ArtifactStore
    from lineage_api.infrastructure.aws.s3_sources import S3SourceArchiveStore

    from java_stage_overrides import (
        JavaConsolidationStageUseCase,
        JavaPublicationStageUseCase,
    )
    from sqlite_projection import SqliteStageProjection

    _load_env()
    endpoint = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")

    def client(name: str) -> Any:
        return boto3.client(
            name,
            endpoint_url=endpoint,
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
            aws_access_key_id="test",
            aws_secret_access_key="test",
            config=Config(retries={"max_attempts": 3}, read_timeout=30),
        )

    config = AwsRuntimeConfig.from_env(os.environ)
    ddb, s3, kin = client("dynamodb"), client("s3"), client("kinesis")
    queue_urls = tuple(
        os.environ[name]
        for name in sorted(os.environ)
        if name.startswith("LINEAGE_")
        and name.endswith("_QUEUE_URL")
        and os.environ[name]
    )
    dead_letter_queue_urls = tuple(
        os.environ[name]
        for name in sorted(os.environ)
        if name.startswith("LINEAGE_") and name.endswith("_DLQ_URL") and os.environ[name]
    )
    sqs = client("sqs") if queue_urls else None
    control = DynamoDbControlAdapter(
        ddb,
        config.control_table,
        config.ledger_table,
        config.pointer_table,
        proposal_table=config.proposal_table,
    )
    artifacts = S3ArtifactStore(s3, config.evidence_bucket)
    packages = S3ArtifactStore(s3, config.package_bucket)
    projection_db = _projection_path()
    projection = SqliteStageProjection(projection_db)

    from collection_runner import CollectionRunner, CollectionRunnerError

    runner = CollectionRunner(
        environment_variables=dict(os.environ),
        endpoint=endpoint,
        projection_path=projection_db,
    )

    class FlociQueryProjection(AwsProductQueryProjection):
        """The AWS projection plus an honest local acquisition/execution stage.

        In AWS the Fargate acquisition stage would enqueue the durable command;
        here the runner's worker thread plays that role against the emulator, so
        the UI's submit → poll → review → approve loop is the real mechanism.
        """

        def submit_collection(
            self, *, body: object, principal: str, correlation_id: str
        ) -> dict[str, object]:
            from lineage_api.application.collections import CollectionError
            from lineage_api.application.product_api import ProductApiError

            try:
                return runner.submit_collection(
                    body if isinstance(body, dict) else None,
                    principal=principal,
                    correlation_id=correlation_id,
                )
            except CollectionError as error:
                raise ProductApiError(
                    error.status_code, error.code, str(error)
                ) from None
            except CollectionRunnerError as error:
                status = {
                    "ANALYZER_NOT_SUPPORTED": 422,
                    "SOURCE_ACQUISITION_FAILED": 502,
                    "NO_PUBLISHED_PACKAGE": 409,
                }.get(error.code, 500)
                raise ProductApiError(status, error.code, str(error)) from None

        def get_proposal(
            self, *, proposal_id: str, version: object = None, **audit: str
        ) -> dict[str, object]:
            from collection_runner import hydrate_proposal_edges

            document = super().get_proposal(
                proposal_id=proposal_id, version=version, **audit
            )
            # Review must be possible BEFORE publication: hydrate the diff with
            # full edge bodies from the proposal's consolidated edge set (a
            # first-time system has nothing in the active projection namespace).
            return hydrate_proposal_edges(document, artifacts)

        def interactions(
            self, *, system: object = None, **audit: str
        ) -> dict[str, object]:
            # The second lineage plane, collected per system by the runner's
            # SCA interactions step and projected into the local graph store.
            items = projection.interactions(system if isinstance(system, str) else None)
            return {"schemaVersion": "1.0.0", "items": items}

    service = ProductApiService(
        FlociQueryProjection(
            ddb,
            control,
            artifacts,
            projection,
            control_table=config.control_table,
            ledger_table=config.ledger_table,
            proposal_table=config.proposal_table,
            sqs=sqs,
            queue_urls=queue_urls,
            dead_letter_queue_urls=dead_letter_queue_urls,
        )
    )
    # Publication executor (plays the DynamoDB-stream → publication-Lambda role).
    registry = production_stage_use_cases(
        artifacts,
        control,
        packages=packages,
        publication_control=control,
        projection=projection,
        sources=S3SourceArchiveStore(s3),
        pr_gate_control=control,
        impact_projection=projection,
    )
    registry[("BASELINE", "B8")] = JavaConsolidationStageUseCase(artifacts)
    registry[("INCREMENTAL", "I7")] = JavaConsolidationStageUseCase(artifacts)
    java_publication = JavaPublicationStageUseCase(artifacts, packages, control, projection)
    registry[("BASELINE", "B10")] = java_publication
    registry[("INCREMENTAL", "I10")] = java_publication
    executor = AwsStageExecutor(
        config,
        control,
        artifacts,
        KinesisRuntimeAdapter(kin, config.runtime_stream),
        projection,
        packages=packages,
        dispatcher=StageDispatcher(registry),
    )
    return service, executor, (ddb, config.ledger_table), runner


def drain_publication_outbox(executor: Any, ddb: Any, ledger_table: str) -> list[str]:
    from collection_runner import STALE_BASE_MARKER, mark_publication_stale

    published: list[str] = []
    paginator = ddb.get_paginator("scan")
    for page in paginator.paginate(TableName=ledger_table):
        for item in page.get("Items", []):
            if (
                item.get("topic", {}).get("S") == "PROPOSAL_APPROVED"
                and item.get("status", {}).get("S") == "PENDING"
            ):
                envelope = json.loads(item["payload"]["S"])
                envelope["outboxId"] = item["pk"]["S"].removeprefix("OUTBOX#")
                try:
                    result = executor.execute("publication", envelope)
                except Exception as error:
                    if STALE_BASE_MARKER in str(error):
                        # Correct fencing refusal — settle it honestly instead of
                        # leaving a zombie PENDING row and a forever-awaiting
                        # collection. A resubmission rebases on the current graph.
                        mark_publication_stale(
                            ddb,
                            ledger_table,
                            outbox_pk=item["pk"]["S"],
                            outbox_sk=item["sk"]["S"],
                            command_id=envelope.get("commandId"),
                            message=str(error),
                        )
                        published.append(
                            f"{envelope.get('commandId')}:STALE_PROPOSAL_BASE"
                        )
                        continue
                    raise
                published.append(
                    f"{envelope['commandId']}:{result.get('terminalOutcome') or result['outcome']}"
                )
    return published


def create_app() -> Any:
    service, executor, (ddb, ledger_table), runner = build()
    app = FastAPI(title="lineage product API · floci-backed")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "backend": "floci"}

    @app.post("/api/deployments")
    async def submit_deployment(request: Request) -> Response:
        from collection_runner import CollectionRunnerError

        correlation_id = request.headers.get("x-correlation-id") or f"ui-{uuid.uuid4().hex[:12]}"
        raw = await request.body()
        body = json.loads(raw) if raw else {}
        system = body.get("system")
        environment = body.get("environment")
        digest = body.get("artifactDigest")
        if not isinstance(system, str) or not system or not isinstance(environment, str) or not environment:
            return Response(
                content=json.dumps(
                    {
                        "error": {
                            "code": "INVALID_REQUEST",
                            "message": "system and environment are required",
                        },
                        "correlationId": correlation_id,
                    }
                ),
                status_code=400,
                media_type="application/json",
            )
        try:
            document = runner.submit_deployment(
                system=system,
                environment=environment,
                artifact_digest=digest if isinstance(digest, str) and digest else None,
                correlation_id=correlation_id,
            )
        except CollectionRunnerError as error:
            return Response(
                content=json.dumps(
                    {
                        "error": {"code": error.code, "message": str(error)},
                        "correlationId": correlation_id,
                    }
                ),
                status_code=409 if error.code == "NO_PUBLISHED_PACKAGE" else 500,
                media_type="application/json",
            )
        return Response(
            content=json.dumps(document), status_code=202, media_type="application/json"
        )

    @app.api_route("/api/{rest:path}", methods=["GET", "POST"])
    async def api(rest: str, request: Request) -> Response:
        body = None
        if request.method == "POST":
            raw = await request.body()
            body = json.loads(raw) if raw else {}
        correlation_id = request.headers.get("x-correlation-id") or f"ui-{uuid.uuid4().hex[:12]}"
        from lineage_api.application.product_api import ProductApiError

        try:
            result = service.handle(
                method=request.method,
                path=f"/api/{rest}",
                query=dict(request.query_params),
                body=body,
                principal=request.headers.get("x-principal", "user:floci-ui"),
                correlation_id=correlation_id,
            )
        except ProductApiError as error:
            return Response(
                content=json.dumps(
                    {
                        "error": {"code": error.code, "message": str(error)},
                        "correlationId": correlation_id,
                    }
                ),
                status_code=error.status_code,
                media_type="application/json",
            )
        document = result.document
        if (
            request.method == "POST"
            and rest.startswith("proposals/")
            and rest.endswith("/approve")
            and result.status_code < 400
        ):
            try:
                published = drain_publication_outbox(executor, ddb, ledger_table)
                if published:
                    LOG.info("publication outbox drained: %s", published)
            except Exception:
                LOG.exception("publication outbox drain failed — approval is recorded; rerun later")
        return Response(
            content=json.dumps(document),
            status_code=result.status_code,
            media_type="application/json",
            headers=dict(result.headers or {}),
        )

    return app


app = create_app()
