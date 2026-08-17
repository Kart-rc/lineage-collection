"""UI-driven collection and deployment execution for the floci product API server.

Closes the gap that made the control room feel disjointed from the collection
mechanism: ``POST /api/collections`` used to fail closed (the Fargate acquisition
stage does not exist in the emulation), so every pipeline had to be driven from
pytest. This module gives the floci server an honest local acquisition stage and
a single-threaded background worker that drives the SAME production stage
dispatcher the E2E harness uses (Java seams registered over the python-only
defaults), against the SAME emulated data plane:

- results/state in DynamoDB (durable command, stage leases, run ledger,
  ``COLLECTION#…/STATUS`` projection, proposals, fenced pointer),
- evidence/edge-sets/packages in versioned immutable S3,
- the graph in the SQLite stand-in for Neptune (floci has no ``neptunedata``
  openCypher API), at ``data/floci/projection-live.db``.

The worker serializes jobs — one pipeline at a time, exactly like the
single-concurrency Step Functions map the emulation mirrors.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import queue
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger("floci-collection-runner")

JAVA_PACK = "java-spring-data-jpa-v1"
JAVA_RULESET = "spring-data-rules-v1"
KNOWN_SCHEMA_PROFILES = frozenset({"h2", "mysql", "postgres"})

_GIT_TIMEOUT_SECONDS = 180
_SAFE_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_ms() -> str:
    moment = datetime.now(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


class _StepTimer:
    """Wall-clock + monotonic timing for every real step of a collection run.

    The entries make the pipeline's honesty self-evident in the UI: the slow
    parts (git fetch, javac+java) dominate, and each stage envelope carries its
    own measured duration instead of the ledger's 1-second completedAt grain.
    """

    MAX_ENTRIES = 40

    def __init__(self, update: Any) -> None:
        self.entries: list[dict[str, Any]] = []
        self._update = update

    def step(self, name: str, detail: str | None = None):
        import contextlib

        timer = self

        @contextlib.contextmanager
        def _measure():
            entry: dict[str, Any] = {"step": name, "startedAt": _now_ms()}
            if detail:
                entry["detail"] = detail
            start = time.monotonic()
            try:
                yield entry
            finally:
                entry["completedAt"] = _now_ms()
                entry["durationMs"] = int((time.monotonic() - start) * 1000)
                if len(timer.entries) < timer.MAX_ENTRIES:
                    timer.entries.append(entry)
                    timer._update(timings=list(timer.entries))

        return _measure()


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


class CollectionRunnerError(RuntimeError):
    """Bounded failure with a stable reason code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message[:500])
        self.code = code


# The publication use case refuses to activate a package whose proposal was
# pinned to a base the pointer has since moved past. That refusal is correct
# fencing — but the approval outbox row must not stay PENDING forever, and the
# originating collection must settle honestly so a resubmission can rebase.
STALE_BASE_MARKER = "active pointer differs"
MAX_HYDRATED_EDGES = 500


def read_ledger_document(
    ddb: Any, table: str, pk: str, sk: str
) -> dict[str, Any] | None:
    response = ddb.get_item(
        TableName=table, Key={"pk": {"S": pk}, "sk": {"S": sk}}, ConsistentRead=True
    )
    item = response.get("Item")
    raw = item.get("document", {}).get("S") if item else None
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def write_ledger_document(
    ddb: Any, table: str, pk: str, sk: str, document: Mapping[str, Any]
) -> None:
    ddb.put_item(
        TableName=table,
        Item={
            "pk": {"S": pk},
            "sk": {"S": sk},
            "document": {"S": json.dumps(document)},
            "updatedAt": {"S": _now()},
        },
    )


def mark_publication_stale(
    ddb: Any,
    ledger_table: str,
    *,
    outbox_pk: str,
    outbox_sk: str,
    command_id: str | None,
    message: str,
) -> None:
    """Settle a stale-base publication honestly: fail the outbox row so it is
    never re-drained, and fail the originating collection so idempotency offers
    a fresh rebasing attempt."""
    bounded = message[:300]
    ddb.update_item(
        TableName=ledger_table,
        Key={"pk": {"S": outbox_pk}, "sk": {"S": outbox_sk}},
        UpdateExpression="SET #status = :failed, failureReason = :reason",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":failed": {"S": "FAILED"},
            ":reason": {"S": f"STALE_PROPOSAL_BASE: {bounded}"},
        },
    )
    if not command_id:
        return
    document = read_ledger_document(ddb, ledger_table, f"COLLECTION#{command_id}", "STATUS")
    if document is None:
        return
    document.update(
        {
            "outcome": "FAILED",
            "reasonCode": "STALE_PROPOSAL_BASE",
            "commandStatus": "FAILED_TERMINAL",
            "runStatus": "FAILED",
            "terminal": True,
            "statusReasons": [
                "another publication advanced the pointer after this proposal "
                "was created; resubmit the collection to rebase on the current "
                "graph",
                bounded,
            ],
        }
    )
    write_ledger_document(
        ddb, ledger_table, f"COLLECTION#{command_id}", "STATUS", document
    )


def hydrate_proposal_edges(document: dict[str, Any], artifacts: Any) -> dict[str, Any]:
    """Fill diff.added/removed/bandChanged with full edge bodies from the
    proposal's consolidated edge set, so review is possible before publication
    (first-time systems have no edges in the active projection namespace)."""
    diff = document.get("diff")
    if not isinstance(diff, dict):
        return document
    ref = diff.get("edgeSetRef")
    if not isinstance(ref, Mapping):
        return document
    pairs = (
        ("addedEdgeIds", "added"),
        ("removedEdgeIds", "removed"),
        ("bandChangedEdgeIds", "bandChanged"),
    )
    if not any(diff.get(ids) and not diff.get(bodies) for ids, bodies in pairs):
        return document
    try:
        edge_set = artifacts.get(dict(ref))
    except Exception:  # pragma: no cover - hydration is best-effort
        LOG.warning("proposal edge-set hydration failed for %s", document.get("proposalId"))
        return document
    by_key = {
        edge.get("edgeKey"): dict(edge)
        for edge in edge_set.get("edges", [])
        if isinstance(edge, Mapping) and edge.get("edgeKey")
    }
    for ids_key, bodies_key in pairs:
        ids = diff.get(ids_key) or []
        if diff.get(bodies_key) or not isinstance(ids, list):
            continue
        diff[bodies_key] = [
            by_key[edge_id] for edge_id in ids[:MAX_HYDRATED_EDGES] if edge_id in by_key
        ]
    return document


def bootstrap_live_projection(live_path: Path) -> Path:
    """Create the single live projection DB by merging every historical run DB.

    The E2E harness writes one ``projection-<runId>.db`` per run; the server used
    to read only the newest. The live DB carries every published namespace so
    approvals from the UI keep extending one persistent graph store.
    """
    live_path.parent.mkdir(parents=True, exist_ok=True)
    if live_path.exists():
        return live_path
    connection = sqlite3.connect(str(live_path))
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS edges (
            namespace TEXT NOT NULL,
            edge_id TEXT NOT NULL,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            type TEXT NOT NULL,
            band TEXT NOT NULL DEFAULT 'LOWEST',
            corroboration TEXT NOT NULL DEFAULT 'NONE',
            document TEXT,
            fence INTEGER NOT NULL,
            PRIMARY KEY (namespace, edge_id, source, target)
        )
        """
    )
    for candidate in sorted(live_path.parent.glob("projection-*.db")):
        if candidate == live_path:
            continue
        try:
            connection.execute("ATTACH DATABASE ? AS old", (str(candidate),))
            connection.execute(
                "INSERT OR IGNORE INTO edges SELECT * FROM old.edges"
            )
            connection.commit()
            connection.execute("DETACH DATABASE old")
        except sqlite3.Error as error:  # pragma: no cover - defensive
            LOG.warning("skipping projection merge of %s: %s", candidate, error)
    connection.commit()
    connection.close()
    LOG.info("live projection bootstrapped at %s", live_path)
    return live_path


class CollectionRunner:
    """Serialized background executor for UI-submitted collections/deployments."""

    def __init__(
        self,
        *,
        environment_variables: Mapping[str, str],
        endpoint: str,
        projection_path: Path,
    ) -> None:
        self._env = dict(environment_variables)
        self._endpoint = endpoint
        self._projection_path = projection_path
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        import boto3
        from botocore.config import Config

        from lineage_api.infrastructure.aws.config import AwsRuntimeConfig

        def client(name: str) -> Any:
            return boto3.client(
                name,
                endpoint_url=endpoint,
                region_name=self._env.get("AWS_REGION", "us-east-1"),
                aws_access_key_id="test",
                aws_secret_access_key="test",
                config=Config(retries={"max_attempts": 3}, read_timeout=60),
            )

        self.config = AwsRuntimeConfig.from_env(self._env)
        self.ddb = client("dynamodb")
        self.s3 = client("s3")
        self.sqs = client("sqs")
        self.kinesis_client = client("kinesis")
        self.sfn = client("stepfunctions")

    # ------------------------------------------------------------- submission

    def submit_collection(
        self, body: Mapping[str, object] | None, *, principal: str, correlation_id: str
    ) -> dict[str, Any]:
        from lineage_api.application.collections import parse_collection_submission

        request = parse_collection_submission(body, allow_local_sources=True)
        if request.analyzer_pack != JAVA_PACK or request.ruleset != JAVA_RULESET:
            raise CollectionRunnerError(
                "ANALYZER_NOT_SUPPORTED",
                f"the floci cell runs analyzer {JAVA_PACK} with ruleset {JAVA_RULESET}",
            )
        if request.schema_profile != request.platform:
            raise CollectionRunnerError(
                "ANALYZER_NOT_SUPPORTED", "platform must equal schemaProfile"
            )
        if request.schema_profile not in KNOWN_SCHEMA_PROFILES:
            raise CollectionRunnerError(
                "ANALYZER_NOT_SUPPORTED",
                f"schemaProfile must be one of {sorted(KNOWN_SCHEMA_PROFILES)}",
            )

        identity = "|".join(
            (
                request.origin,
                request.repository,
                request.revision,
                request.environment,
                request.system,
            )
        )
        digest = hashlib.sha256(identity.encode()).hexdigest()[:12]

        with self._lock:
            head = self._get_document(f"COLLECTION_KEY#{digest}", "HEAD")
            if head and isinstance(head.get("commandId"), str):
                existing = self._get_document(f"COLLECTION#{head['commandId']}", "STATUS")
                if existing is not None:
                    if existing.get("commandStatus") != "FAILED_TERMINAL":
                        return existing
                    attempt = int(existing.get("attempt", 1)) + 1
                    command_id = f"cmd-ui-{digest}-r{attempt}"
                else:
                    attempt = 1
                    command_id = f"cmd-ui-{digest}"
            else:
                attempt = 1
                command_id = f"cmd-ui-{digest}"

            document = self._initial_document(
                command_id, request, correlation_id=correlation_id, attempt=attempt
            )
            self._put_document(f"COLLECTION#{command_id}", "STATUS", document)
            self._put_document(
                f"COLLECTION_KEY#{digest}", "HEAD", {"commandId": command_id}
            )
        self._enqueue(
            {
                "kind": "collection",
                "commandId": command_id,
                "correlationId": correlation_id,
                "principal": principal,
                "request": {
                    "sourceType": request.source_type,
                    "origin": request.origin,
                    "repository": request.repository,
                    "revision": request.revision,
                    "environment": request.environment,
                    "platform": request.platform,
                    "system": request.system,
                    "schemaProfile": request.schema_profile,
                    "runtimeVerification": bool(request.runtime_verification),
                    "checkoutPath": (
                        str(request.checkout_root)
                        if request.source_type == "LOCAL_CHECKOUT"
                        else None
                    ),
                },
            }
        )
        return document

    def submit_deployment(
        self,
        *,
        system: str,
        environment: str,
        artifact_digest: str | None,
        correlation_id: str,
    ) -> dict[str, Any]:
        head = self._get_document(f"REPO#{system}#{environment}", "HEAD") or {}
        digest = artifact_digest or head.get("artifactDigest")
        if not isinstance(digest, str) or not digest:
            raise CollectionRunnerError(
                "NO_PUBLISHED_PACKAGE",
                f"no published package is known for {system}/{environment}",
            )
        package = self._package_for(system, environment, digest)
        if package is None:
            raise CollectionRunnerError(
                "NO_PUBLISHED_PACKAGE",
                f"artifact {digest[:24]}… has no published package for "
                f"{system}/{environment} — approve and publish a proposal first",
            )
        command_id = f"cmd-uideploy-{_stamp()}"
        self._enqueue(
            {
                "kind": "deployment",
                "commandId": command_id,
                "correlationId": correlation_id,
                "system": system,
                "environment": environment,
                "artifactDigest": digest,
                "repository": str(head.get("repository") or system),
                "platform": str(head.get("platform") or "postgres"),
            }
        )
        return {
            "commandId": command_id,
            "system": system,
            "environment": environment,
            "artifactDigest": digest,
            "graphVersion": package["graphVersion"],
            "status": "QUEUED",
        }

    # ------------------------------------------------------------ worker loop

    def _enqueue(self, job: dict[str, Any]) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._worker, name="floci-collection-runner", daemon=True
                )
                self._thread.start()
        self._queue.put(job)

    def _worker(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job["kind"] == "collection":
                    self._run_collection(job)
                elif job["kind"] == "deployment":
                    self._run_deployment(job)
            except Exception:  # pragma: no cover - last-resort guard
                LOG.exception("collection runner job crashed: %s", job.get("commandId"))
            finally:
                self._queue.task_done()

    # ----------------------------------------------------------- composition

    def _session(self, origin: str) -> dict[str, Any]:
        """Per-job stage composition (same adapters + Java seams as the E2E)."""
        from lineage_api.application.sca_aggregation import BaselineScaAggregationUseCase
        from lineage_api.application.stage_execution import StageDispatcher
        from lineage_api.application.stage_handlers import production_stage_use_cases
        from lineage_api.infrastructure.aws.composition import (
            AwsScaAggregationExecutor,
            AwsStageExecutor,
            StepFunctionsWorkflowStarter,
        )
        from lineage_api.infrastructure.aws.dynamodb_control import DynamoDbControlAdapter
        from lineage_api.infrastructure.aws.kinesis_runtime import KinesisRuntimeAdapter
        from lineage_api.infrastructure.aws.s3_artifacts import S3ArtifactStore
        from lineage_api.infrastructure.aws.s3_map_results import S3MapResultReader
        from lineage_api.infrastructure.aws.s3_sources import S3SourceArchiveStore
        from lineage_api.infrastructure.aws.sqs_broker import SqsLaneBroker

        from java_stage_overrides import (
            JavaConsolidationStageUseCase,
            JavaControlStageUseCase,
            JavaCoverageStageUseCase,
            JavaPublicationStageUseCase,
            JavaScaStageUseCase,
        )
        from sqlite_projection import SqliteStageProjection

        control = DynamoDbControlAdapter(
            self.ddb,
            self.config.control_table,
            self.config.ledger_table,
            self.config.pointer_table,
            proposal_table=self.config.proposal_table,
        )
        artifacts = S3ArtifactStore(self.s3, self.config.evidence_bucket)
        packages = S3ArtifactStore(self.s3, self.config.package_bucket)
        sources = S3SourceArchiveStore(self.s3)
        kinesis = KinesisRuntimeAdapter(self.kinesis_client, self.config.runtime_stream)
        projection = SqliteStageProjection(self._projection_path)
        broker = SqsLaneBroker(
            self.sqs,
            {
                "interactive": self._env["LINEAGE_INTERACTIVE_QUEUE_URL"],
                "events": self._env["LINEAGE_EVENTS_QUEUE_URL"],
                "batch": self._env["LINEAGE_BATCH_QUEUE_URL"],
            },
        )
        starter = StepFunctionsWorkflowStarter(
            self.sfn,
            {
                "BASELINE": self._env["LINEAGE_BASELINE_WORKFLOW_ALIAS_ARN"],
                "INCREMENTAL": self._env["LINEAGE_INCREMENTAL_WORKFLOW_ALIAS_ARN"],
                "PR_GATE": self._env["LINEAGE_PR_GATE_WORKFLOW_ALIAS_ARN"],
                "NIGHTLY": self._env["LINEAGE_NIGHTLY_WORKFLOW_ALIAS_ARN"],
            },
            baseline_map_concurrency=1,
        )
        registry = production_stage_use_cases(
            artifacts,
            control,
            packages=packages,
            publication_control=control,
            projection=projection,
            sources=sources,
            pr_gate_control=control,
            impact_projection=projection,
        )
        java_coverage = JavaCoverageStageUseCase(artifacts, chunk_size=1_000)
        registry[("BASELINE", "B4")] = java_coverage
        registry[("INCREMENTAL", "I3")] = java_coverage
        registry[("INCREMENTAL", "I4")] = JavaControlStageUseCase(artifacts, control)
        java_sca = JavaScaStageUseCase(artifacts, sources, origin=origin)
        registry[("BASELINE", "B5")] = java_sca
        registry[("INCREMENTAL", "I5")] = java_sca
        java_consolidation = JavaConsolidationStageUseCase(artifacts)
        registry[("BASELINE", "B8")] = java_consolidation
        registry[("INCREMENTAL", "I7")] = java_consolidation
        java_publication = JavaPublicationStageUseCase(
            artifacts, packages, control, projection
        )
        registry[("BASELINE", "B10")] = java_publication
        registry[("INCREMENTAL", "I10")] = java_publication
        executor = AwsStageExecutor(
            self.config,
            control,
            artifacts,
            kinesis,
            projection,
            packages=packages,
            broker=broker,
            workflow_starter=starter,
            sources=sources,
            dispatcher=StageDispatcher(registry),
        )
        aggregation = AwsScaAggregationExecutor(
            control,
            artifacts,
            BaselineScaAggregationUseCase(
                artifacts, S3MapResultReader(self.s3, self.config.evidence_bucket)
            ),
        )
        return {
            "control": control,
            "artifacts": artifacts,
            "packages": packages,
            "sources": sources,
            "kinesis": kinesis,
            "projection": projection,
            "broker": broker,
            "executor": executor,
            "aggregation": aggregation,
        }

    # ------------------------------------------------------------ collection

    def _run_collection(self, job: dict[str, Any]) -> None:
        command_id = job["commandId"]
        request = job["request"]
        correlation_id = job["correlationId"]
        state = self._get_document(f"COLLECTION#{command_id}", "STATUS") or {}

        def update(**changes: Any) -> None:
            state.update(changes)
            state["updatedAt"] = _now_ms()
            self._put_document(f"COLLECTION#{command_id}", "STATUS", state)

        timer = _StepTimer(update)
        try:
            update(commandStatus="RUNNING", runId=command_id, runStatus="RUNNING")
            session = self._session(request["origin"])
            revision12 = str(request["revision"])[:12]
            acquire_detail = (
                f"shallow fetch {request['origin']}@{revision12}"
                if request["sourceType"] == "GIT"
                else f"local archive {revision12}"
            )
            with timer.step("acquire", acquire_detail):
                zip_bytes = self._acquire_zip(request)
            with timer.step(
                "register-source", f"S3 put sources/{command_id} · {len(zip_bytes)} bytes"
            ):
                source = self._upload_source(
                    command_id, request["repository"], zip_bytes
                )
            inventory = sorted(
                name
                for name in zipfile.ZipFile(io.BytesIO(zip_bytes)).namelist()
                if not name.endswith("/")
            )
            workflow, changed, removed, closure = self._plan_workflow(
                request, source, inventory
            )
            update(workflowKind=workflow, artifactDigest=source["artifactDigest"])
            # The interactions plane rides every submission (duplicates included —
            # the revision is immutable, so re-recording is idempotent). A failure
            # here never fails the collection: it is an auxiliary plane.
            with timer.step("interactions", "SCA service-interactions plane"):
                self._collect_interactions(
                    session, request, source, command_id, correlation_id, update
                )
            if workflow == "NO_CHANGES":
                update(
                    outcome="DUPLICATE",
                    reasonCode="NO_CHANGES",
                    commandStatus="COMPLETED",
                    runStatus="NO_CHANGES",
                    terminal=True,
                    statusReasons=[
                        "the submitted revision is byte-identical to the recorded head"
                    ],
                )
                return
            if workflow == "BASELINE":
                self._drive_baseline(
                    session,
                    request,
                    source,
                    inventory,
                    command_id,
                    correlation_id,
                    update,
                    timer,
                )
            else:
                self._drive_incremental(
                    session,
                    request,
                    source,
                    inventory,
                    changed,
                    removed,
                    closure,
                    command_id,
                    correlation_id,
                    update,
                    timer,
                )
            self._put_document(
                f"REPO#{request['system']}#{request['environment']}",
                "HEAD",
                {
                    "origin": request["origin"],
                    "repository": request["repository"],
                    "revision": request["revision"],
                    "platform": request["platform"],
                    "schemaProfile": request["schemaProfile"],
                    "artifactDigest": source["artifactDigest"],
                    "sourceReference": source["reference"],
                    "commandId": command_id,
                    "updatedAt": _now(),
                },
            )
        except CollectionRunnerError as error:
            LOG.exception("collection %s failed: %s", command_id, error)
            update(
                outcome="FAILED",
                reasonCode=error.code,
                commandStatus="FAILED_TERMINAL",
                runStatus="FAILED",
                terminal=True,
                statusReasons=[str(error)[:300]],
            )
        except Exception as error:
            LOG.exception("collection %s failed", command_id)
            update(
                outcome="FAILED",
                reasonCode="PIPELINE_FAILED",
                commandStatus="FAILED_TERMINAL",
                runStatus="FAILED",
                terminal=True,
                statusReasons=[f"{type(error).__name__}: {error}"[:300]],
            )

    def _drive_baseline(
        self,
        session: dict[str, Any],
        request: Mapping[str, Any],
        source: dict[str, Any],
        inventory: list[str],
        command_id: str,
        correlation_id: str,
        update: Any,
        timer: _StepTimer,
    ) -> None:
        artifacts = session["artifacts"]
        context = self._intent_context(
            request, source, inventory, changed=[], removed=[], closure=[]
        )
        input_ref = self._put_intent(artifacts, command_id, context)
        with timer.step("intake", "SQS FIFO events lane"):
            self._run_intake(session, "BASELINE", command_id, correlation_id, input_ref)
        update(stages=["intake"])

        current = input_ref
        completed = ["intake"]
        for stage_id in ("B1", "B2", "B3", "B4"):
            with timer.step(stage_id):
                current = self._execute_stage(
                    session, "BASELINE", stage_id, command_id, correlation_id, current
                )["output"]
            completed.append(stage_id)
            update(stages=list(completed))
        inventory_doc = artifacts.get(current)
        update(
            coverageManifest=self._coverage_manifest(
                command_id, self._coverage_from(artifacts, inventory_doc)
            )
        )

        children = []
        with timer.step("B5") as sca_entry:
            for index, work_ref in enumerate(inventory_doc["workUnitRefs"]):
                child = self._execute_stage(
                    session,
                    "BASELINE",
                    "B5",
                    command_id,
                    correlation_id,
                    work_ref,
                    idempotency_key=f"{command_id}:B5:{index:05d}",
                )
                children.append({"outcome": "SUCCEEDED", "output": child["output"]})
            sca_entry["detail"] = (
                f"tree-sitter SCA · {len(children)} work unit"
                f"{'s' if len(children) != 1 else ''}"
            )
        completed.append("B5")
        update(stages=list(completed), analysisStatus="COMPLETE")

        map_token = f"map-{command_id}"
        prefix = f"workflow-results/{command_id}/B5/{map_token}/"
        manifest_ref = artifacts.put(
            "map-manifest", f"{prefix}manifest.json", {"MapRunArn": map_token}, "1.0.0"
        )
        artifacts.put("map-result-shard", f"{prefix}SUCCEEDED_0.json", children, "1.0.0")
        with timer.step("B5A", "map-result aggregation"):
            aggregate = self._aggregate_b5(
                session, command_id, correlation_id, current, manifest_ref, map_token
            )
        if aggregate["outcome"] != "SUCCEEDED":
            raise CollectionRunnerError("PIPELINE_FAILED", f"B5A failed: {aggregate}")
        aggregate_doc = artifacts.get(aggregate["output"])
        sca_result = artifacts.get(children[0]["output"])
        evidence_doc = artifacts.get(sca_result["evidenceRef"])

        current = self._inject_runtime(
            session,
            request,
            source,
            command_id,
            correlation_id,
            aggregate_doc,
            sca_result,
            evidence_doc,
            stage_dir="B5A",
            update=update,
            timer=timer,
        )

        for stage_id in ("B6", "B7", "B8", "B9", "B10"):
            with timer.step(stage_id):
                result = self._execute_stage(
                    session, "BASELINE", stage_id, command_id, correlation_id, current
                )
            current = result["output"]
            completed.append(stage_id)
            document = artifacts.get(current)
            if stage_id == "B8":
                update(
                    stages=list(completed),
                    counts=self._counts(artifacts, document, evidence_doc),
                )
            elif stage_id == "B9":
                proposal = document.get("proposal") or {}
                update(
                    stages=list(completed),
                    proposalId=proposal.get("proposalId"),
                    proposalStatus=proposal.get("state"),
                )
            elif stage_id == "B10":
                terminal = result.get("terminalOutcome") or "COMPLETED"
                update(
                    stages=list(completed),
                    runStatus=terminal,
                    outcome="ACCEPTED",
                    commandStatus="COMPLETED",
                    terminal=True,
                )
            else:
                update(stages=list(completed))

    def _drive_incremental(
        self,
        session: dict[str, Any],
        request: Mapping[str, Any],
        source: dict[str, Any],
        inventory: list[str],
        changed: list[str],
        removed: list[str],
        closure: list[str],
        command_id: str,
        correlation_id: str,
        update: Any,
        timer: _StepTimer,
    ) -> None:
        artifacts = session["artifacts"]
        context = self._intent_context(
            request, source, inventory, changed=changed, removed=removed, closure=closure
        )
        input_ref = self._put_intent(artifacts, command_id, context)
        with timer.step("intake", "SQS FIFO events lane"):
            self._run_intake(
                session, "INCREMENTAL", command_id, correlation_id, input_ref
            )
        completed = ["intake"]
        update(stages=list(completed))

        current = input_ref
        for stage_id in ("I1", "I2", "I3", "I4", "I5"):
            with timer.step(stage_id):
                current = self._execute_stage(
                    session, "INCREMENTAL", stage_id, command_id, correlation_id, current
                )["output"]
            completed.append(stage_id)
            update(stages=list(completed))
            if stage_id == "I3":
                update(
                    coverageManifest=self._coverage_manifest(
                        command_id,
                        self._coverage_from(artifacts, artifacts.get(current)),
                    )
                )
        update(analysisStatus="COMPLETE")

        sca_result = artifacts.get(current)
        evidence_ref = sca_result.get("evidenceRef")
        evidence_doc = artifacts.get(evidence_ref) if evidence_ref else {"edges": []}
        if evidence_doc.get("edges"):
            current = self._inject_runtime(
                session,
                request,
                source,
                command_id,
                correlation_id,
                sca_result,
                sca_result,
                evidence_doc,
                stage_dir="I5",
                update=update,
                timer=timer,
            )
        else:
            update(runtimeStatus="NOT_APPLICABLE")

        for stage_id in ("I6", "I7", "I8", "I9", "I10"):
            with timer.step(stage_id):
                result = self._execute_stage(
                    session, "INCREMENTAL", stage_id, command_id, correlation_id, current
                )
            current = result["output"]
            completed.append(stage_id)
            document = artifacts.get(current)
            if stage_id == "I7":
                update(
                    stages=list(completed),
                    counts=self._counts(artifacts, document, evidence_doc),
                )
            elif stage_id == "I9":
                proposal = document.get("proposal") or {}
                update(
                    stages=list(completed),
                    proposalId=proposal.get("proposalId"),
                    proposalStatus=proposal.get("state"),
                )
            elif stage_id == "I10":
                terminal = result.get("terminalOutcome") or "COMPLETED"
                update(
                    stages=list(completed),
                    runStatus=terminal,
                    outcome="ACCEPTED",
                    commandStatus="COMPLETED",
                    terminal=True,
                )
            else:
                update(stages=list(completed))

    def _aggregate_b5(
        self,
        session: dict[str, Any],
        command_id: str,
        correlation_id: str,
        work_inventory: dict[str, Any],
        manifest_ref: dict[str, Any],
        map_token: str,
    ) -> dict[str, Any]:
        return session["aggregation"].execute(
            {
                "schemaVersion": "1.0.0",
                "operation": "BASELINE_SCA_AGGREGATE",
                "workflowKind": "BASELINE",
                "workflowVersion": "1.0.0",
                "commandId": command_id,
                "correlationId": correlation_id,
                "causationId": f"{command_id}:B5A",
                "idempotencyKey": f"{command_id}:B5A",
                "determinantDigest": "sha256:" + "0" * 64,
                "workInventory": work_inventory,
                "mapResult": {
                    "MapRunArn": (
                        "arn:aws:states:us-east-1:000000000000:mapRun:"
                        f"lineage/Map:{map_token}"
                    ),
                    "ResultWriterDetails": {
                        "Bucket": self.config.evidence_bucket,
                        "Key": manifest_ref["key"],
                    },
                },
            }
        )

    # ------------------------------------------------------------ deployment

    def _run_deployment(self, job: dict[str, Any]) -> None:
        command_id = job["commandId"]
        correlation_id = job["correlationId"]
        session = self._session("https://github.com/spring-projects/spring-petclinic")
        artifacts = session["artifacts"]
        event = {
            "schemaVersion": "1.0.0",
            "eventId": f"deploy-ui-{_stamp()}",
            "eventType": "DEPLOYMENT",
            "provider": "control-room",
            "providerSequence": int(time.time()),
            "attempt": 1,
            "system": job["system"],
            "environment": job["environment"],
            "outcome": "SUCCEEDED",
            "artifactDigest": job["artifactDigest"],
            "correlationId": correlation_id,
            "auditRef": f"audit://control-room/{command_id}",
            "occurredAt": _now(),
        }
        event_digest = hashlib.sha256(_canonical(event)).hexdigest()
        receipt_ref = artifacts.put(
            "deployment-authentication-receipt",
            f"commands/{command_id}/authentication.json",
            {
                "schemaVersion": "1.0.0",
                "artifactType": "deployment-authentication-receipt",
                "decision": "AUTHENTICATED",
                "eventDigest": event_digest,
                "provider": "control-room",
                "principal": "user:floci-ui",
                "verifiedAt": _now(),
            },
            "1.0.0",
        )
        input_ref = artifacts.put(
            "deployment-event",
            f"commands/{command_id}/input.json",
            {
                "schemaVersion": "1.0.0",
                "artifactType": "deployment-event",
                "context": {
                    "repository": job["repository"],
                    "artifactDigest": job["artifactDigest"],
                    "environment": job["environment"],
                    "platform": job["platform"],
                    "system": job["system"],
                },
                "event": event,
                "authenticationRef": receipt_ref,
            },
            "1.0.0",
        )
        current = input_ref
        try:
            for stage_id in ("D1", "D2", "D3", "D4", "D5", "D6"):
                result = self._execute_stage(
                    session, "DEPLOYMENT", stage_id, command_id, correlation_id, current
                )
                current = result["output"]
            LOG.info(
                "deployment %s finished: %s",
                command_id,
                result.get("terminalOutcome") or result["outcome"],
            )
        except Exception:
            LOG.exception("deployment %s failed", command_id)

    # --------------------------------------------------------------- helpers

    def _acquire_zip(self, request: Mapping[str, Any]) -> bytes:
        revision = request["revision"]
        if request["sourceType"] == "GIT":
            workdir = Path(tempfile.mkdtemp(prefix="floci-git-"))
            try:
                def git(*args: str) -> None:
                    completed = subprocess.run(
                        ["git", *args],
                        cwd=workdir,
                        env={**_SAFE_GIT_ENV, "PATH": "/usr/bin:/bin:/usr/local/bin"},
                        capture_output=True,
                        text=True,
                        timeout=_GIT_TIMEOUT_SECONDS,
                    )
                    if completed.returncode != 0:
                        raise CollectionRunnerError(
                            "SOURCE_ACQUISITION_FAILED",
                            f"git {args[0]} failed for {request['origin']}@{revision[:12]}",
                        )

                git("init", "--quiet", "--template=")
                git("remote", "add", "origin", request["origin"])
                git(
                    "fetch",
                    "--quiet",
                    "--depth",
                    "1",
                    "--no-tags",
                    "--no-recurse-submodules",
                    "origin",
                    revision,
                )
                git("checkout", "--quiet", "--force", "--detach", revision)
                archive = subprocess.run(
                    ["git", "archive", "--format=zip", revision],
                    cwd=workdir,
                    capture_output=True,
                    timeout=_GIT_TIMEOUT_SECONDS,
                )
                if archive.returncode != 0:
                    raise CollectionRunnerError(
                        "SOURCE_ACQUISITION_FAILED", "git archive failed"
                    )
                return archive.stdout
            finally:
                shutil.rmtree(workdir, ignore_errors=True)

        checkout = Path(str(request["checkoutPath"]))
        probe = subprocess.run(
            ["git", "-C", str(checkout), "cat-file", "-e", f"{revision}^{{commit}}"],
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        if probe.returncode != 0:
            raise CollectionRunnerError(
                "SOURCE_ACQUISITION_FAILED",
                f"revision {revision[:12]} is not present in {checkout}",
            )
        archive = subprocess.run(
            ["git", "-C", str(checkout), "archive", "--format=zip", revision],
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        if archive.returncode != 0:
            raise CollectionRunnerError(
                "SOURCE_ACQUISITION_FAILED", "git archive failed for the local checkout"
            )
        return archive.stdout

    def _upload_source(
        self, command_id: str, repository: str, payload: bytes
    ) -> dict[str, Any]:
        digest = hashlib.sha256(payload).hexdigest()
        key = f"sources/{command_id}/{repository}.zip"
        response = self.s3.put_object(
            Bucket=self.config.evidence_bucket, Key=key, Body=payload
        )
        return {
            "reference": {
                "bucket": self.config.evidence_bucket,
                "key": key,
                "versionId": response["VersionId"],
                "sha256": digest,
                "sizeBytes": len(payload),
            },
            "artifactDigest": f"sha256:{digest}",
        }

    def _plan_workflow(
        self,
        request: Mapping[str, Any],
        source: dict[str, Any],
        inventory: list[str],
    ) -> tuple[str, list[str], list[str], list[str]]:
        from java_stage_overrides import java_scope_partition

        system, environment = request["system"], request["environment"]
        has_package = self._has_any_package(system, environment)
        if not has_package:
            return "BASELINE", [], [], []

        head = self._get_document(f"REPO#{system}#{environment}", "HEAD")
        selected, _, _ = java_scope_partition(inventory, request["platform"])
        if not head or not isinstance(head.get("sourceReference"), Mapping):
            # A package exists but we never recorded a head (e.g. harness-driven
            # runs): recompute the full selected scope as the incremental delta.
            return "INCREMENTAL", list(selected), [], list(selected)

        if head.get("artifactDigest") == source["artifactDigest"]:
            return "NO_CHANGES", [], [], []

        previous = self._zip_manifest(head["sourceReference"])
        current = {
            name: hashlib.sha256(data).hexdigest()
            for name, data in self._zip_entries(source["reference"]).items()
        }
        changed = sorted(
            name
            for name, sha in current.items()
            if name not in previous or previous[name] != sha
        )
        removed = sorted(name for name in previous if name not in current)
        lineage_relevant = set(selected)
        touches_lineage = any(
            path in lineage_relevant for path in (*changed, *removed)
        )
        closure = list(selected) if touches_lineage else []
        return "INCREMENTAL", changed, removed, closure

    def _zip_entries(self, reference: Mapping[str, Any]) -> dict[str, bytes]:
        body = self.s3.get_object(
            Bucket=reference["bucket"],
            Key=reference["key"],
            VersionId=reference["versionId"],
        )["Body"].read()
        archive = zipfile.ZipFile(io.BytesIO(body))
        return {
            info.filename: archive.read(info.filename)
            for info in archive.infolist()
            if not info.filename.endswith("/")
        }

    def _zip_manifest(self, reference: Mapping[str, Any]) -> dict[str, str]:
        return {
            name: hashlib.sha256(data).hexdigest()
            for name, data in self._zip_entries(reference).items()
        }

    def _intent_context(
        self,
        request: Mapping[str, Any],
        source: dict[str, Any],
        inventory: list[str],
        *,
        changed: list[str],
        removed: list[str],
        closure: list[str],
    ) -> dict[str, Any]:
        catalog_body = json.loads(
            (ROOT / "fixtures" / "catalog" / "catalog-snapshot-v1.json").read_text()
        )
        from lineage_api.infrastructure.aws.s3_artifacts import S3ArtifactStore

        artifacts = S3ArtifactStore(self.s3, self.config.evidence_bucket)
        catalog_ref = artifacts.put(
            "catalog-snapshot",
            f"catalog/{request['system']}-{_stamp()}/catalog-snapshot-v1.json",
            catalog_body,
            "1.0.0",
        )
        return {
            "repository": request["repository"],
            "artifactDigest": source["artifactDigest"],
            "environment": request["environment"],
            "platform": request["platform"],
            "system": request["system"],
            "acceptedAt": _now(),
            "repositorySource": source["reference"],
            "repositoryInventory": inventory,
            "catalogSnapshotId": str(
                catalog_body.get("snapshotId")
                or catalog_body.get("catalogSnapshotId")
                or "catalog-demo-v1"
            ),
            "catalogSnapshotRef": catalog_ref,
            "resolverVersion": str(catalog_body.get("resolverVersion") or "1.0.0"),
            "rulesetVersion": JAVA_RULESET,
            "classificationPolicyVersion": "1.0.0",
            "changedPaths": changed,
            "removedPaths": removed,
            "dependencyClosure": closure,
            "runtimeManifestRefs": [],
        }

    def _put_intent(
        self, artifacts: Any, command_id: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        intent = {
            "schemaVersion": "1.0.0",
            "artifactType": "lineage-intent",
            "context": context,
            "classificationEvidence": [
                {
                    "level": 4,
                    "source": "APPLICATION_RUNTIME",
                    "repositoryClass": "APPLICATION_RUNTIME",
                    "ref": f"source-scope://{context['artifactDigest']}",
                }
            ],
        }
        return artifacts.put(
            "workflow-input", f"commands/{command_id}/input.json", intent, "1.0.0"
        )

    def _run_intake(
        self,
        session: dict[str, Any],
        workflow_kind: str,
        command_id: str,
        correlation_id: str,
        input_ref: dict[str, Any],
    ) -> None:
        envelope = {
            "schemaVersion": "1.0.0",
            "commandId": command_id,
            "correlationId": correlation_id,
            "causationId": f"{command_id}:intake",
            "idempotencyKey": f"{command_id}:intake",
            "stageId": "intake",
            "workflowKind": workflow_kind,
            "workflowVersion": "1.0.0",
            "input": input_ref,
        }
        broker = session["broker"]
        broker.publish(
            "events",
            f"repo:{command_id}:{workflow_kind}",
            json.dumps(envelope, sort_keys=True, separators=(",", ":")),
            correlation_id,
            message_id=f"{command_id}-intake",
        )
        # Drain the lane until our intake lands; execute anything claimed so a
        # leftover envelope from an interrupted run cannot wedge the queue.
        for _ in range(10):
            message = broker.claim("events", "floci-ui-runner")
            if message is None:
                time.sleep(0.5)
                continue
            claimed = json.loads(message.payload_ref)
            result = session["executor"].execute("intake", claimed)
            broker.acknowledge(message)
            if claimed.get("commandId") == command_id:
                if result["outcome"] not in {"SUCCEEDED", "SKIPPED"}:
                    raise CollectionRunnerError(
                        "PIPELINE_FAILED", f"intake failed: {result}"
                    )
                return
        raise CollectionRunnerError(
            "PIPELINE_FAILED", "intake message did not arrive on the events lane"
        )

    def _execute_stage(
        self,
        session: dict[str, Any],
        workflow_kind: str,
        stage_id: str,
        command_id: str,
        correlation_id: str,
        input_ref: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        from lineage_api.application.stage_ownership import owner_for_stage
        from lineage_api.application.workflows.definitions import WORKFLOWS

        workflow = WORKFLOWS[workflow_kind]
        stage = next(item for item in workflow.stages if item.stage_id == stage_id)
        envelope = {
            "schemaVersion": "1.0.0",
            "commandId": command_id,
            "correlationId": correlation_id,
            "causationId": f"{command_id}:{stage_id}",
            "idempotencyKey": idempotency_key or f"{command_id}:{stage_id}",
            "determinantDigest": "sha256:" + "0" * 64,
            "workflowKind": workflow_kind,
            "workflowVersion": workflow.version,
            "stageId": stage_id,
            "stageName": stage.name,
            "input": input_ref,
        }
        result = session["executor"].execute(
            owner_for_stage(workflow_kind, stage_id).value, envelope
        )
        if result["outcome"] != "SUCCEEDED":
            raise CollectionRunnerError(
                "PIPELINE_FAILED", f"{stage_id} did not succeed: {result}"
            )
        return result

    def _inject_runtime(
        self,
        session: dict[str, Any],
        request: Mapping[str, Any],
        source: dict[str, Any],
        command_id: str,
        correlation_id: str,
        carrier_doc: dict[str, Any],
        sca_result: dict[str, Any],
        evidence_doc: dict[str, Any],
        *,
        stage_dir: str,
        update: Any,
        timer: _StepTimer,
    ) -> dict[str, Any]:
        """Run the javac/java recording harness and splice its artifacts into the
        stage context, exactly as the harness driver does between B5A and B6."""
        import os

        from java_stage_overrides import run_runtime_corroboration

        artifacts = session["artifacts"]
        static_edges = evidence_doc.get("edges") or []
        carrier_context = carrier_doc["context"]
        artifact_kind = (
            "sca-batch-result" if stage_dir == "B5A" else "sca-stage-result"
        )
        if not static_edges:
            update(runtimeStatus="NOT_APPLICABLE")
            return artifacts.put(
                artifact_kind,
                f"commands/{command_id}/stages/{stage_dir}/runtime-augmented.json",
                carrier_doc,
                "1.0.0",
            )
        try:
            sources_map = self._read_java_sources(session, source["reference"])
            with timer.step("runtime-harness") as harness_entry:
                runtime = run_runtime_corroboration(
                    artifacts=artifacts,
                    kinesis=session["kinesis"],
                    sources=sources_map,
                    static_edges=static_edges,
                    command_id=command_id,
                    work_unit_id=sca_result["workUnitId"],
                    correlation_id=correlation_id,
                    common=carrier_context,
                    observed_at=_now(),
                    java_home=os.environ.get(
                        "LINEAGE_JAVA_HOME", "/opt/homebrew/opt/openjdk@21"
                    ),
                )
                harness_entry["detail"] = (
                    f"javac+java · {runtime.get('corroborated', 0)} corroborated · "
                    f"{runtime.get('verdict', 'UNKNOWN')}"
                )
        except Exception as error:
            LOG.exception("runtime corroboration failed for %s", command_id)
            update(
                runtimeStatus="FAILED",
                statusReasons=[f"runtime harness: {type(error).__name__}: {error}"[:300]],
            )
            return artifacts.put(
                artifact_kind,
                f"commands/{command_id}/stages/{stage_dir}/runtime-augmented.json",
                carrier_doc,
                "1.0.0",
            )
        update(runtimeStatus=runtime["verdict"])
        augmented = dict(carrier_doc)
        augmented["context"] = {
            **carrier_context,
            "runtimeManifestRefs": [runtime["manifestRef"]],
            "assertionRefs": [
                *carrier_context["assertionRefs"],
                runtime["assertionRef"],
            ],
        }
        return artifacts.put(
            artifact_kind,
            f"commands/{command_id}/stages/{stage_dir}/runtime-augmented.json",
            augmented,
            "1.0.0",
        )

    def _collect_interactions(
        self,
        session: dict[str, Any],
        request: Mapping[str, Any],
        source: Mapping[str, Any],
        command_id: str,
        correlation_id: str,
        update: Any,
    ) -> None:
        """Derive and persist the service-interactions plane for this revision.

        Static-only (SCA mechanism), metadata-only, checksummed into the evidence
        bucket and projected per system — the second lineage plane the dataset
        walk cannot see. Failure is recorded honestly but never fails the run.
        """
        try:
            from lineage_api.services.java_interaction_sca import (
                analyze_java_interactions,
                interaction_analysis_document,
            )

            sources = self._read_java_sources(session, source["reference"])
            analysis = analyze_java_interactions(
                sources, service=str(request["repository"])
            )
            document = interaction_analysis_document(
                analysis,
                system=str(request["system"]),
                service=str(request["repository"]),
                revision=str(request["revision"]),
                command_id=command_id,
                correlation_id=correlation_id,
            )
            reference = session["artifacts"].put(
                "interactions",
                f"commands/{command_id}/interactions/{command_id}-interactions.json",
                document,
                "1.0.0",
            )
            document["evidenceRef"] = reference
            session["projection"].upsert_interactions(
                str(request["system"]),
                command_id=command_id,
                revision=str(request["revision"]),
                document=document,
                updated_at=_now(),
            )
            update(
                interactionCounts={
                    "inbound": len(document["inbound"]),
                    "outbound": len(document["outbound"]),
                    "residue": len(document["residue"]),
                }
            )
        except Exception as error:  # auxiliary plane — record, never fail the run
            LOG.exception("interactions plane collection failed for %s", command_id)
            update(interactionCounts={"error": f"{type(error).__name__}: {error}"[:200]})

    def _read_java_sources(
        self, session: dict[str, Any], source_ref: Mapping[str, Any]
    ) -> dict[str, str]:
        collected: dict[str, str] = {}
        with session["sources"].materialize(source_ref) as root:
            for path in sorted(Path(root).rglob("*.java")):
                collected[path.relative_to(root).as_posix()] = path.read_text()
        return collected

    def _counts(
        self, artifacts: Any, consolidation_doc: dict[str, Any], evidence_doc: dict[str, Any]
    ) -> dict[str, int]:
        edges: list[dict[str, Any]] = []
        edge_set_ref = consolidation_doc.get("edgeSetRef")
        if isinstance(edge_set_ref, Mapping):
            try:
                edges = artifacts.get(edge_set_ref).get("edges") or []
            except Exception:  # pragma: no cover - counts are advisory
                edges = []
        return {
            "edges": int(consolidation_doc.get("edgeCount") or len(edges)),
            "reads": sum(1 for edge in edges if edge.get("edgeType") == "READS"),
            "writes": sum(1 for edge in edges if edge.get("edgeType") == "WRITES"),
            "residue": len(evidence_doc.get("residue") or []),
            "unresolved": 0,
        }

    def _coverage_from(
        self, artifacts: Any, stage_doc: Mapping[str, Any]
    ) -> dict[str, Any]:
        coverage = stage_doc.get("coverage")
        if isinstance(coverage, Mapping) and coverage:
            return dict(coverage)
        plan_ref = stage_doc.get("coveragePlanRef")
        if isinstance(plan_ref, Mapping):
            try:
                plan = artifacts.get(plan_ref)
                nested = plan.get("coverage")
                if isinstance(nested, Mapping):
                    return dict(nested)
            except Exception:  # pragma: no cover - coverage is advisory
                pass
        return {}

    def _coverage_manifest(
        self, command_id: str, coverage: Mapping[str, Any]
    ) -> dict[str, Any]:
        expected = coverage.get("expectedScope") or []
        completed = coverage.get("recomputedScope") or coverage.get("completedScope") or []
        skipped = coverage.get("skippedScope") or []
        unsupported = coverage.get("unsupportedScope") or []
        return {
            "manifestId": f"coverage-{command_id}",
            "state": "COMPLETE",
            "counts": {
                "expected": len(expected),
                "completed": len(completed),
                "skipped": len(skipped),
                "unsupported": len(unsupported),
                "failed": 0,
            },
        }

    def _initial_document(
        self,
        command_id: str,
        request: Any,
        *,
        correlation_id: str,
        attempt: int,
    ) -> dict[str, Any]:
        return {
            "commandId": command_id,
            "collectionId": command_id,
            "statusUrl": f"/api/collections/{command_id}",
            "sourceType": request.source_type,
            "origin": request.origin,
            "repository": request.repository,
            "revision": request.revision,
            "environment": request.environment,
            "platform": request.platform,
            "system": request.system,
            "analyzerPack": request.analyzer_pack,
            "ruleset": request.ruleset,
            "schemaProfile": request.schema_profile,
            "runtimeVerification": bool(request.runtime_verification),
            "workflowKind": None,
            "outcome": "ACCEPTED",
            "reasonCode": None,
            "commandStatus": "QUEUED",
            "terminal": False,
            "runId": None,
            "runStatus": None,
            "proposalId": None,
            "proposalStatus": None,
            "runtimeStatus": "PENDING",
            "analysisStatus": None,
            "statusReasons": [],
            "stages": [],
            "counts": {"edges": 0, "reads": 0, "writes": 0, "residue": 0, "unresolved": 0},
            "coverageManifest": None,
            "correlationId": correlation_id,
            "attempt": attempt,
            "submittedAt": _now(),
            "createdAt": _now_ms(),
            "updatedAt": _now_ms(),
            "timings": [],
        }

    # ------------------------------------------------------------- ddb utils

    def _put_document(self, pk: str, sk: str, document: Mapping[str, Any]) -> None:
        self.ddb.put_item(
            TableName=self.config.ledger_table,
            Item={
                "pk": {"S": pk},
                "sk": {"S": sk},
                "document": {"S": json.dumps(document)},
                "updatedAt": {"S": _now()},
            },
        )

    def _get_document(self, pk: str, sk: str) -> dict[str, Any] | None:
        response = self.ddb.get_item(
            TableName=self.config.ledger_table,
            Key={"pk": {"S": pk}, "sk": {"S": sk}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        raw = item.get("document", {}).get("S")
        if not isinstance(raw, str):
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def _has_any_package(self, system: str, environment: str) -> bool:
        response = self.ddb.query(
            TableName=self.config.ledger_table,
            KeyConditionExpression="pk = :pk",
            ExpressionAttributeValues={
                ":pk": {"S": f"PACKAGE#{system}#{environment}"}
            },
            Limit=1,
        )
        return bool(response.get("Items"))

    def _package_for(
        self, system: str, environment: str, artifact_digest: str
    ) -> dict[str, Any] | None:
        response = self.ddb.get_item(
            TableName=self.config.ledger_table,
            Key={
                "pk": {"S": f"PACKAGE#{system}#{environment}"},
                "sk": {"S": f"ARTIFACT#{artifact_digest}"},
            },
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        return {
            "packageId": item["packageId"]["S"],
            "graphVersion": item["graphVersion"]["S"],
            "graphChecksum": item["graphChecksum"]["S"],
        }
