from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from lineage_api.application.product_api import (
    ProductApiError,
    ProductApiService,
)
from lineage_api.application.consolidation import edge_key_for
from lineage_api.application.models import parse_utc
from lineage_api.domain.urns import LineageUrn
from lineage_api.infrastructure.aws.config import AwsRuntimeConfig
from lineage_api.infrastructure.aws.dynamodb_control import DynamoDbControlAdapter
from lineage_api.infrastructure.aws.errors import AwsConflictError, aws_call
from lineage_api.infrastructure.aws.neptune_projection import NeptuneProjectionAdapter
from lineage_api.infrastructure.aws.s3_artifacts import S3ArtifactStore


RUN_INDEX = "RunsByUpdatedAt"
PROPOSAL_INDEX = "ProposalsByState"
MAX_RUN_STAGES = 64
_REFERENCE_KEYS = frozenset({"bucket", "key", "versionId", "sha256", "sizeBytes"})

_SNAPSHOT_PAGE_LIMIT = 25
_BASELINE_SEARCH_LIMIT = 100
_MAX_COVERAGE_RUNS = 8
_QUEUE_AGE_LIMIT_SECONDS = 60
_BASELINE_AGE_LIMIT_SECONDS = 86_400
_APPROVAL_AGE_LIMIT_SECONDS = 300
_RUNTIME_VALIDATION_STAGES = frozenset({"B6", "I6"})
_CONSOLIDATION_STAGES = frozenset({"B7", "B8", "I7"})


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("product API clock must include a timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _text(name: str, value: object, maximum: int = 2_048) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > maximum:
        raise ValueError(f"invalid {name}")
    return value.strip()


def _document(item: Mapping[str, Any], name: str) -> dict[str, Any]:
    raw = item.get("document", {}).get("S")
    if not isinstance(raw, str) or len(raw.encode()) > 350_000:
        raise RuntimeError(f"stored {name} document is invalid")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError(f"stored {name} document is invalid")
    return value


def _string(item: Mapping[str, Any], name: str, default: str | None = None) -> str | None:
    value = item.get(name)
    if isinstance(value, Mapping) and isinstance(value.get("S"), str):
        return value["S"]
    return default


def _json_string(item: Mapping[str, Any], name: str) -> object | None:
    raw = _string(item, name)
    if raw is None:
        return None
    return json.loads(raw)


def _encode_cursor(key: Mapping[str, Any] | None) -> str | None:
    if not key:
        return None
    encoded = base64.urlsafe_b64encode(_canonical(dict(key)).encode()).decode()
    return encoded.rstrip("=")


def _decode_cursor(
    value: str | None,
    *,
    fields: frozenset[str],
    partition_field: str,
    partition_value: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.b64decode(value + padding, altchars=b"-_", validate=True)
        if len(raw) > 16_384:
            raise ValueError
        decoded = json.loads(raw)
    except (ValueError, UnicodeError, json.JSONDecodeError):
        raise ProductApiError(400, "INVALID_CURSOR", "cursor is invalid") from None
    if not isinstance(decoded, dict) or set(decoded) != fields:
        raise ProductApiError(400, "INVALID_CURSOR", "cursor is invalid")
    for field, typed in decoded.items():
        if (
            not isinstance(field, str)
            or not isinstance(typed, dict)
            or set(typed) != {"S"}
            or not isinstance(typed["S"], str)
            or not typed["S"]
            or len(typed["S"]) > 4_096
        ):
            raise ProductApiError(400, "INVALID_CURSOR", "cursor is invalid")
    if decoded[partition_field]["S"] != partition_value:
        raise ProductApiError(400, "INVALID_CURSOR", "cursor is invalid")
    return decoded


def _run(item: Mapping[str, Any]) -> dict[str, object]:
    command_id = _text("run command ID", _string(item, "commandId"), 512)
    result: dict[str, object] = {
        "runId": command_id,
        "workflowKind": _string(item, "workflowKind", "UNKNOWN"),
        "workflowVersion": _string(item, "workflowVersion", "UNKNOWN"),
        "currentStageId": _string(item, "currentStageId", "UNKNOWN"),
        "currentStageName": _string(item, "currentStageName", "UNKNOWN"),
        "status": _string(item, "status", "UNKNOWN"),
        "correlationId": _string(item, "correlationId"),
        "environment": _string(item, "environment"),
        "system": _string(item, "system"),
        "createdAt": _string(item, "createdAt"),
        "updatedAt": _string(item, "updatedAt"),
        "output": _json_string(item, "output"),
    }
    terminal = _string(item, "terminalOutcome")
    if terminal is not None:
        result["terminalOutcome"] = terminal
    return result


def _stage(item: Mapping[str, Any]) -> dict[str, object]:
    return {
        "stageId": _string(item, "stageId", "UNKNOWN"),
        "stageName": _string(item, "stageName", "UNKNOWN"),
        "status": _string(item, "status", "UNKNOWN"),
        "completedAt": _string(item, "completedAt"),
        "output": _json_string(item, "output"),
    }


class AwsProductQueryProjection:
    """Bounded AWS read model and conditional product mutation adapter."""

    def __init__(
        self,
        client: Any,
        control: DynamoDbControlAdapter,
        artifacts: S3ArtifactStore,
        projection: NeptuneProjectionAdapter,
        *,
        control_table: str,
        ledger_table: str,
        proposal_table: str,
        default_environment: str = "staging",
        clock: Callable[[], datetime] | None = None,
        sqs: Any | None = None,
        queue_urls: Sequence[str] = (),
        dead_letter_queue_urls: Sequence[str] = (),
    ) -> None:
        self._client = client
        self._control = control
        self._artifacts = artifacts
        self._projection = projection
        self._control_table = control_table
        self._ledger_table = ledger_table
        self._proposal_table = proposal_table
        self._default_environment = _text(
            "default environment", default_environment, 256
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sqs = sqs
        self._queue_urls = tuple(queue_urls)
        self._dead_letter_queue_urls = tuple(dead_letter_queue_urls)

    def list_runs(
        self,
        *,
        limit: int,
        cursor: str | None,
        workflow: str | None,
        status: str | None,
        environment: str | None,
        **_audit: str,
    ) -> dict[str, object]:
        start = _decode_cursor(
            cursor,
            fields=frozenset({"pk", "sk", "queryPk", "querySk"}),
            partition_field="queryPk",
            partition_value="RUN",
        )
        request: dict[str, Any] = {
            "TableName": self._ledger_table,
            "IndexName": RUN_INDEX,
            "KeyConditionExpression": "queryPk = :run",
            "ExpressionAttributeValues": {":run": {"S": "RUN"}},
            "ScanIndexForward": False,
            "Limit": limit,
        }
        filters: list[str] = []
        names: dict[str, str] = {}
        for field, value in (
            ("workflowKind", workflow),
            ("status", status),
            ("environment", environment),
        ):
            if value is None:
                continue
            token = f":filter{len(filters)}"
            if field == "status":
                names["#status"] = "status"
                filters.append(f"#status = {token}")
            else:
                filters.append(f"{field} = {token}")
            request["ExpressionAttributeValues"][token] = {"S": value}
        if filters:
            request["FilterExpression"] = " AND ".join(filters)
            if names:
                request["ExpressionAttributeNames"] = names
        if start is not None:
            request["ExclusiveStartKey"] = start
        response = aws_call("dynamodb.list_runs", self._client.query, **request)
        items = response.get("Items", [])
        if not isinstance(items, list) or len(items) > limit:
            raise RuntimeError("run query exceeded its bound")
        return {
            "items": [_run(item) for item in items],
            "nextCursor": _encode_cursor(response.get("LastEvaluatedKey")),
        }

    def get_run(self, *, run_id: str, **_audit: str) -> dict[str, object]:
        response = aws_call(
            "dynamodb.get_run_summary",
            self._client.get_item,
            TableName=self._ledger_table,
            Key={"pk": {"S": f"RUN#{run_id}"}, "sk": {"S": "SUMMARY"}},
            ConsistentRead=True,
        )
        summary = response.get("Item")
        if not isinstance(summary, Mapping):
            raise ProductApiError(404, "RUN_NOT_FOUND", "run does not exist")
        timeline = aws_call(
            "dynamodb.get_run_timeline",
            self._client.query,
            TableName=self._ledger_table,
            KeyConditionExpression="pk = :pk AND begins_with(sk, :stage)",
            ExpressionAttributeValues={
                ":pk": {"S": f"COMMAND#{run_id}"},
                ":stage": {"S": "STAGE#"},
            },
            ScanIndexForward=True,
            Limit=MAX_RUN_STAGES,
        )
        if timeline.get("LastEvaluatedKey"):
            raise RuntimeError("run timeline exceeded the workflow stage bound")
        items = timeline.get("Items", [])
        if not isinstance(items, list) or len(items) > MAX_RUN_STAGES:
            raise RuntimeError("run timeline exceeded the workflow stage bound")
        return {"run": _run(summary), "stages": [_stage(item) for item in items]}

    def get_collection(self, *, command_id: str, **_audit: str) -> dict[str, object]:
        response = aws_call(
            "dynamodb.get_collection_status",
            self._client.get_item,
            TableName=self._ledger_table,
            Key={
                "pk": {"S": f"COLLECTION#{command_id}"},
                "sk": {"S": "STATUS"},
            },
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not isinstance(item, Mapping):
            raise ProductApiError(
                404, "COLLECTION_NOT_FOUND", "collection is not known"
            )
        return _document(item, "collection")

    def submit_collection(self, **_request: object) -> dict[str, object]:
        """Refuse rather than fork the semantics.

        In AWS, repository acquisition runs as a Fargate stage, so an honest submit
        enqueues a durable command and returns a non-terminal QUEUED status. That
        acquisition stage does not exist yet, and synthesizing a local-shaped
        synchronous submit here would introduce exactly the AWS-only semantic fork the
        architecture forbids. Fail closed with a stable code until the stage lands.
        """
        raise ProductApiError(
            501,
            "COLLECTION_SUBMIT_NOT_CONFIGURED",
            "collection submission is not configured in this environment",
        )

    def list_proposals(
        self,
        *,
        limit: int,
        cursor: str | None,
        state: str | None,
        **_audit: str,
    ) -> dict[str, object]:
        selected_state = state or "IN_REVIEW"
        partition = f"PROPOSAL_STATE#{selected_state}"
        start = _decode_cursor(
            cursor,
            fields=frozenset({"pk", "sk", "queryPk", "querySk"}),
            partition_field="queryPk",
            partition_value=partition,
        )
        request: dict[str, Any] = {
            "TableName": self._proposal_table,
            "IndexName": PROPOSAL_INDEX,
            "KeyConditionExpression": "queryPk = :state",
            "ExpressionAttributeValues": {":state": {"S": partition}},
            "ScanIndexForward": True,
            "Limit": limit,
        }
        if start is not None:
            request["ExclusiveStartKey"] = start
        response = aws_call("dynamodb.list_proposals", self._client.query, **request)
        items = response.get("Items", [])
        if not isinstance(items, list) or len(items) > limit:
            raise RuntimeError("proposal query exceeded its bound")
        return {
            "items": [_document(item, "proposal") for item in items],
            "nextCursor": _encode_cursor(response.get("LastEvaluatedKey")),
        }

    def get_proposal(
        self,
        *,
        proposal_id: str,
        version: int | None,
        **_audit: str,
    ) -> dict[str, Any]:
        if version is not None:
            response = aws_call(
                "dynamodb.get_product_proposal",
                self._client.get_item,
                TableName=self._proposal_table,
                Key={
                    "pk": {"S": f"PROPOSAL#{proposal_id}"},
                    "sk": {"S": f"VERSION#{version:010d}"},
                },
                ConsistentRead=True,
            )
            item = response.get("Item")
        else:
            response = aws_call(
                "dynamodb.get_latest_product_proposal",
                self._client.query,
                TableName=self._proposal_table,
                KeyConditionExpression="pk = :pk AND begins_with(sk, :version)",
                ExpressionAttributeValues={
                    ":pk": {"S": f"PROPOSAL#{proposal_id}"},
                    ":version": {"S": "VERSION#"},
                },
                ScanIndexForward=False,
                Limit=1,
                ConsistentRead=True,
            )
            items = response.get("Items", [])
            item = items[0] if isinstance(items, list) and items else None
        if not isinstance(item, Mapping):
            raise ProductApiError(404, "PROPOSAL_NOT_FOUND", "proposal does not exist")
        return _document(item, "proposal")

    def interactions(
        self, *, system: str | None = None, **_audit: str
    ) -> dict[str, Any]:
        # The interactions plane is projected by deployments that collect it
        # (the floci cell overrides this); claiming an empty plane would be a lie.
        raise ProductApiError(
            501,
            "NOT_IMPLEMENTED",
            "the interactions projection is not available on this deployment",
        )

    def lineage(
        self,
        *,
        subject: str,
        direction: str,
        depth: int,
        limit: int,
        version: str | None,
        **_audit: str,
    ) -> dict[str, Any]:
        namespace = version or self._active_namespace(_environment(subject))
        return self._projection.lineage(
            namespace, subject, direction=direction, depth=depth, limit=limit
        )

    def edge_detail(
        self,
        *,
        edge_key: str,
        version: str | None,
        **_audit: str,
    ) -> dict[str, Any]:
        namespace = version or self._active_namespace(self._default_environment)
        result = self._projection.edge_detail(namespace, edge_key)
        if result is None:
            raise ProductApiError(404, "EDGE_NOT_FOUND", "edge does not exist")
        return result

    def impact(
        self,
        *,
        subject: str,
        change_type: str,
        depth: int,
        limit: int,
        version: str | None,
        **_audit: str,
    ) -> dict[str, Any]:
        namespace = version or self._active_namespace(_environment(subject))
        return self._projection.impact(
            namespace, subject, change_type, depth=depth, limit=limit
        )

    def overview(
        self, *, environment: str | None, principal: str, correlation_id: str
    ) -> dict[str, object]:
        selected = environment or self._default_environment
        pointer = self._control.active_pointer(selected)
        runs = self.list_runs(
            limit=5,
            cursor=None,
            workflow=None,
            status=None,
            environment=selected,
            principal=principal,
            correlation_id=correlation_id,
        )
        proposals = self.list_proposals(
            limit=5,
            cursor=None,
            state="IN_REVIEW",
            principal=principal,
            correlation_id=correlation_id,
        )
        return {
            "environment": selected,
            "activeVersion": pointer.get("graphVersion"),
            "fencingToken": pointer.get("fence"),
            "recentRuns": runs["items"],
            "inReviewSample": proposals["items"],
            "countsAreComplete": False,
            "resilience": self.resilience(
                environment=selected,
                principal=principal,
                correlation_id=correlation_id,
            ),
        }

    def resilience(
        self, *, environment: str | None, principal: str, correlation_id: str
    ) -> dict[str, object]:
        selected = environment or self._default_environment
        now = self._clock().astimezone(UTC)
        pointer = self._control.active_pointer(selected)
        runs = self.list_runs(
            limit=_SNAPSHOT_PAGE_LIMIT,
            cursor=None,
            workflow=None,
            status=None,
            environment=selected,
            principal=principal,
            correlation_id=correlation_id,
        )["items"]
        proposals = self.list_proposals(
            limit=_SNAPSHOT_PAGE_LIMIT,
            cursor=None,
            state="IN_REVIEW",
            principal=principal,
            correlation_id=correlation_id,
        )["items"]

        correlation = self._correlation_signal(runs, proposals)
        coverage = self._coverage_signal(now, selected, runs)
        review = self._review_signal(now, proposals)
        publication = self._publication_signal(now, pointer)
        queue = self._queue_signal()

        degraded = {
            queue["status"],
            coverage["status"],
            coverage["runtimeJoin"]["status"],
            coverage["baseline"]["status"],
            review["status"],
            publication["status"],
            publication["watermark"]["status"],
            correlation["status"],
        }
        observed = (
            queue["status"] != "NOT_AVAILABLE"
            or correlation["status"] != "NOT_AVAILABLE"
            or coverage["status"] != "NOT_AVAILABLE"
            or publication["status"] != "NOT_AVAILABLE"
            or review["oldestApprovalAgeSeconds"] is not None
        )
        if publication["status"] == "OUT_OF_SYNC":
            overall = "OUT_OF_SYNC"
        elif degraded & {"DEGRADED", "INCOMPLETE", "STALE"}:
            overall = "DEGRADED"
        elif not observed:
            # A plane with no observable signal must not claim health.
            overall = "NOT_AVAILABLE"
        else:
            overall = "HEALTHY"
        not_configured = {"status": "NOT_CONFIGURED", "value": None}
        return {
            "schemaVersion": "1.0.0",
            "capturedAt": _timestamp(now),
            "status": overall,
            "correlation": correlation,
            "queue": queue,
            "coverage": coverage,
            "review": review,
            "publication": publication,
            "productionSignals": {
                "replication": dict(not_configured),
                "errorBudgetBurn": dict(not_configured),
                "unitCost": dict(not_configured),
            },
        }

    @staticmethod
    def _age(now: datetime, timestamp: object) -> int | None:
        if not isinstance(timestamp, str) or not timestamp:
            return None
        return max(0, int((now - parse_utc(timestamp)).total_seconds()))

    @staticmethod
    def _correlation_signal(
        runs: list[dict[str, object]], proposals: list[dict[str, Any]]
    ) -> dict[str, object]:
        values = [item.get("correlationId") for item in (*runs, *proposals)]
        tracked = sum(1 for value in values if isinstance(value, str) and value)
        missing = len(values) - tracked
        return {
            "status": (
                "NOT_AVAILABLE"
                if tracked == 0
                else "INCOMPLETE" if missing else "COMPLETE"
            ),
            "trackedCount": tracked,
            "missingCount": missing,
        }

    def _coverage_signal(
        self, now: datetime, environment: str, runs: list[dict[str, object]]
    ) -> dict[str, object]:
        baseline = self._baseline_signal(now, environment)
        rows = []
        lineage_runs = [
            run for run in runs if run.get("workflowKind") in {"BASELINE", "INCREMENTAL"}
        ]
        for run in lineage_runs[:_MAX_COVERAGE_RUNS]:
            row = self._coverage_row(str(run["runId"]))
            if row is not None:
                rows.append(row)
        incomplete = sum(1 for row in rows if row["state"] != "COMPLETE")
        joined = sum(1 for row in rows if row["runtimeStatus"] == "VALIDATED")
        if not rows:
            runtime_join: dict[str, object] = {
                "status": "NOT_AVAILABLE",
                "joined": 0,
                "eligible": 0,
                "rate": None,
            }
        else:
            runtime_join = {
                "status": "COMPLETE" if joined == len(rows) else "INCOMPLETE",
                "joined": joined,
                "eligible": len(rows),
                "rate": joined / len(rows),
            }
        return {
            "status": (
                "NOT_AVAILABLE"
                if not rows
                else "INCOMPLETE" if incomplete else "COMPLETE"
            ),
            "incompleteCount": incomplete,
            "runtimeJoin": runtime_join,
            "baseline": baseline,
        }

    def _coverage_row(self, run_id: str) -> dict[str, object] | None:
        # Coverage facts live only in the run's stage artifacts; a run whose
        # evidence is missing or unreadable yields no row rather than failing
        # the whole health snapshot.
        try:
            timeline = aws_call(
                "dynamodb.coverage_timeline",
                self._client.query,
                TableName=self._ledger_table,
                KeyConditionExpression="pk = :pk AND begins_with(sk, :stage)",
                ExpressionAttributeValues={
                    ":pk": {"S": f"COMMAND#{run_id}"},
                    ":stage": {"S": "STAGE#"},
                },
                ScanIndexForward=True,
                Limit=MAX_RUN_STAGES,
            )
            runtime_ref = consolidation_ref = None
            for item in timeline.get("Items", []):
                stage_id = _string(item, "stageId")
                reference = _json_string(item, "output")
                if reference is None:
                    continue
                if stage_id in _RUNTIME_VALIDATION_STAGES:
                    runtime_ref = reference
                elif stage_id in _CONSOLIDATION_STAGES:
                    consolidation_ref = reference
            runtime_status = None
            if runtime_ref is not None:
                runtime_document = self._artifacts.get(runtime_ref)
                if isinstance(runtime_document, Mapping):
                    runtime_coverage = runtime_document.get("runtimeCoverage")
                    if isinstance(runtime_coverage, Mapping):
                        runtime_status = runtime_coverage.get("status")
            if consolidation_ref is None:
                return None
            document = self._artifacts.get(consolidation_ref)
            if not isinstance(document, Mapping):
                return None
            coverage = document.get("coverage")
            if not isinstance(coverage, Mapping) or coverage.get("state") not in {
                "COMPLETE",
                "INCOMPLETE",
            }:
                return None
            return {"state": coverage["state"], "runtimeStatus": runtime_status}
        except Exception:
            return None

    def _baseline_signal(self, now: datetime, environment: str) -> dict[str, object]:
        response = aws_call(
            "dynamodb.latest_baseline_run",
            self._client.query,
            TableName=self._ledger_table,
            IndexName=RUN_INDEX,
            KeyConditionExpression="queryPk = :run",
            FilterExpression="workflowKind = :baseline AND environment = :environment",
            ExpressionAttributeValues={
                ":run": {"S": "RUN"},
                ":baseline": {"S": "BASELINE"},
                ":environment": {"S": environment},
            },
            ScanIndexForward=False,
            Limit=_BASELINE_SEARCH_LIMIT,
        )
        items = response.get("Items", [])
        if not isinstance(items, list) or not items:
            return {
                "status": "NOT_AVAILABLE",
                "ageSeconds": None,
                "maxAgeSeconds": _BASELINE_AGE_LIMIT_SECONDS,
            }
        latest = items[0]
        age = self._age(now, _string(latest, "updatedAt"))
        if _string(latest, "terminalOutcome") != "PUBLISHED":
            status = "INCOMPLETE"
        elif age is not None and age > _BASELINE_AGE_LIMIT_SECONDS:
            status = "STALE"
        else:
            status = "CURRENT"
        return {
            "status": status,
            "ageSeconds": age,
            "maxAgeSeconds": _BASELINE_AGE_LIMIT_SECONDS,
        }

    def _review_signal(
        self, now: datetime, proposals: list[dict[str, Any]]
    ) -> dict[str, object]:
        # The IN_REVIEW page is queried ascending by createdAt, so the first
        # item is the oldest waiting approval.
        age = self._age(now, proposals[0].get("createdAt")) if proposals else None
        return {
            "status": (
                "DEGRADED"
                if age is not None and age > _APPROVAL_AGE_LIMIT_SECONDS
                else "HEALTHY"
            ),
            "oldestApprovalAgeSeconds": age,
        }

    def _publication_signal(
        self, now: datetime, pointer: Mapping[str, Any]
    ) -> dict[str, object]:
        version = pointer.get("graphVersion")
        active_version = version if isinstance(version, str) and version != "NONE" else None
        package = pointer.get("package")
        if active_version is None:
            pointer_package_status = "NOT_AVAILABLE"
        elif package:
            pointer_package_status = "IN_SYNC"
        else:
            pointer_package_status = "OUT_OF_SYNC"
        activated_at = pointer.get("activatedAt")
        watermark_age = self._age(now, activated_at)
        # The pointer's activation IS the watermark on this path: the graph
        # projection is swapped in the same fenced transaction, so an existing
        # activation timestamp means the projection is current with the pointer.
        watermark = {
            "status": "CURRENT" if watermark_age is not None else "NOT_AVAILABLE",
            "version": active_version if watermark_age is not None else None,
            "updatedAt": activated_at if watermark_age is not None else None,
            "ageSeconds": watermark_age,
        }
        if pointer_package_status == "OUT_OF_SYNC":
            status = "OUT_OF_SYNC"
        elif active_version is None:
            status = "NOT_AVAILABLE"
        else:
            status = "HEALTHY"
        return {
            "status": status,
            # There is no publication-operations ledger on this path, so the
            # lag is honestly unobserved rather than derived from run spans.
            "publishLagSeconds": None,
            "pointerPackage": {
                "status": pointer_package_status,
                "activeVersion": active_version,
                "packageVersion": active_version if package else None,
            },
            "watermark": watermark,
        }

    def _queue_signal(self) -> dict[str, object]:
        if self._sqs is None or not self._queue_urls:
            return {
                "status": "NOT_AVAILABLE",
                "depth": 0,
                "oldestAgeSeconds": None,
                "saturation": {
                    "status": "NOT_CONFIGURED",
                    "observedDepth": 0,
                    "capacity": None,
                },
                "retryCount": 0,
                "deadLetterCount": 0,
                "leaseStealCount": 0,
            }
        depth = 0
        oldest: int | None = None
        for url in self._queue_urls:
            attributes = self._queue_attributes(url)
            messages = int(attributes.get("ApproximateNumberOfMessages", "0"))
            depth += messages
            age = attributes.get("ApproximateAgeOfOldestMessage")
            if messages > 0 and age is not None:
                oldest = max(oldest or 0, int(age))
        dead_letters = sum(
            int(self._queue_attributes(url).get("ApproximateNumberOfMessages", "0"))
            for url in self._dead_letter_queue_urls
        )
        degraded = dead_letters > 0 or (
            oldest is not None and oldest > _QUEUE_AGE_LIMIT_SECONDS
        )
        return {
            "status": "DEGRADED" if degraded else "HEALTHY",
            "depth": depth,
            "oldestAgeSeconds": oldest,
            "saturation": {
                "status": "NOT_CONFIGURED",
                "observedDepth": depth,
                "capacity": None,
            },
            # Retries and lease steals are per-command attributes with no
            # index on this path; the dead-letter queues are the durable
            # failure signal.
            "retryCount": 0,
            "deadLetterCount": dead_letters,
            "leaseStealCount": 0,
        }

    def _queue_attributes(self, url: str) -> Mapping[str, str]:
        response = aws_call(
            "sqs.queue_attributes",
            self._sqs.get_queue_attributes,
            QueueUrl=url,
            AttributeNames=[
                "ApproximateNumberOfMessages",
                "ApproximateAgeOfOldestMessage",
            ],
        )
        attributes = response.get("Attributes", {})
        return attributes if isinstance(attributes, Mapping) else {}

    def runtime_admin(
        self,
        *,
        limit: int,
        cursor: str | None,
        scope_type: str | None,
        scope_value: str | None,
        **_audit: str,
    ) -> dict[str, object]:
        start = _decode_cursor(
            cursor,
            fields=frozenset({"pk", "sk"}),
            partition_field="pk",
            partition_value="RUNTIME_ADMIN",
        )
        request: dict[str, Any] = {
            "TableName": self._control_table,
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :kill)",
            "ExpressionAttributeValues": {
                ":pk": {"S": "RUNTIME_ADMIN"},
                ":kill": {"S": "KILL#"},
            },
            "ScanIndexForward": True,
            "Limit": limit,
            "ConsistentRead": True,
        }
        filters: list[str] = []
        for field, value in (("scopeType", scope_type), ("scopeValue", scope_value)):
            if value is not None:
                token = f":filter{len(filters)}"
                filters.append(f"{field} = {token}")
                request["ExpressionAttributeValues"][token] = {"S": value}
        if filters:
            request["FilterExpression"] = " AND ".join(filters)
        if start is not None:
            request["ExclusiveStartKey"] = start
        response = aws_call("dynamodb.runtime_admin", self._client.query, **request)
        items = response.get("Items", [])
        if not isinstance(items, list) or len(items) > limit:
            raise RuntimeError("runtime admin query exceeded its bound")
        return {
            "items": [
                {
                    "scopeType": _string(item, "scopeType"),
                    "scopeValue": _string(item, "scopeValue"),
                    "active": item.get("active", {}).get("BOOL", False),
                    "version": int(item.get("version", {"N": "0"})["N"]),
                    "updatedAt": _string(item, "updatedAt"),
                    "updatedBy": _string(item, "updatedBy"),
                }
                for item in items
            ],
            "nextCursor": _encode_cursor(response.get("LastEvaluatedKey")),
        }

    def set_runtime_kill_switch(
        self,
        *,
        scope_type: str,
        scope_value: str,
        active: bool,
        expected_version: int,
        actor: str,
        rationale: str,
        principal: str,
        correlation_id: str,
    ) -> dict[str, object]:
        now = _timestamp(self._clock())
        next_version = expected_version + 1
        identity = _canonical(
            {
                "scopeType": scope_type,
                "scopeValue": scope_value,
                "active": active,
                "version": next_version,
                "principal": principal,
                "correlationId": correlation_id,
            }
        )
        audit_id = "audit-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        try:
            aws_call(
                "dynamodb.set_runtime_kill_switch",
                self._client.transact_write_items,
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self._control_table,
                            "Key": {
                                "pk": {"S": "RUNTIME_ADMIN"},
                                "sk": {"S": f"KILL#{scope_type}#{scope_value}"},
                            },
                            "UpdateExpression": (
                                "SET scopeType = :scopeType, scopeValue = :scopeValue, "
                                "active = :active, version = :nextVersion, updatedAt = :now, "
                                "updatedBy = :principal"
                            ),
                            "ConditionExpression": (
                                "(attribute_not_exists(version) AND :expectedVersion = :zero) "
                                "OR version = :expectedVersion"
                            ),
                            "ExpressionAttributeValues": {
                                ":scopeType": {"S": scope_type},
                                ":scopeValue": {"S": scope_value},
                                ":active": {"BOOL": active},
                                ":expectedVersion": {"N": str(expected_version)},
                                ":nextVersion": {"N": str(next_version)},
                                ":zero": {"N": "0"},
                                ":now": {"S": now},
                                ":principal": {"S": principal},
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._ledger_table,
                            "Item": {
                                "pk": {"S": f"AUDIT#{audit_id}"},
                                "sk": {"S": "EVENT"},
                                "action": {"S": "RUNTIME_KILL_SWITCH_CHANGED"},
                                "actor": {"S": actor},
                                "principal": {"S": principal},
                                "resourceId": {"S": f"{scope_type}:{scope_value}"},
                                "correlationId": {"S": correlation_id},
                                "rationale": {"S": rationale},
                                "createdAt": {"S": now},
                            },
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                ],
                ClientRequestToken=hashlib.sha256(identity.encode()).hexdigest()[:36],
            )
        except AwsConflictError as error:
            raise ProductApiError(
                409, "CONCURRENT_CONTROL_CHANGE", "runtime control changed concurrently"
            ) from error
        return {
            "scopeType": scope_type,
            "scopeValue": scope_value,
            "active": active,
            "version": next_version,
            "updatedAt": now,
            "updatedBy": principal,
        }

    def review_proposal(
        self,
        *,
        proposal_id: str,
        action: str,
        version: int,
        expected_lock_version: int,
        actor: str,
        rationale: str,
        corrected_edges: list[object] | None,
        principal: str,
        correlation_id: str,
    ) -> dict[str, object]:
        proposal = self.get_proposal(
            proposal_id=proposal_id,
            version=version,
            principal=principal,
            correlation_id=correlation_id,
        )
        replay_state = {"APPROVE": "APPROVED", "REJECT": "REJECTED"}.get(action)
        replay_decision = proposal.get("decision")
        if (
            replay_state is not None
            and proposal.get("state") == replay_state
            and proposal.get("lockVersion") == expected_lock_version + 1
            and isinstance(replay_decision, Mapping)
            and replay_decision.get("decision") == replay_state
            and replay_decision.get("actor") == actor
            and replay_decision.get("principal") == principal
            and replay_decision.get("rationale") == rationale
            and isinstance(proposal.get("approvalRef"), Mapping)
        ):
            return {
                "proposal": proposal,
                "approvalRef": dict(proposal["approvalRef"]),
                "publication": (
                    "OUTBOX_PENDING" if replay_state == "APPROVED" else "NOT_REQUESTED"
                ),
            }
        if proposal.get("state") != "IN_REVIEW":
            raise ProductApiError(409, "INVALID_PROPOSAL_TRANSITION", "proposal is not in review")
        if proposal.get("lockVersion") != expected_lock_version:
            raise ProductApiError(409, "CONCURRENT_DECISION", "proposal changed concurrently")
        if action == "CORRECT":
            return self._correct(
                proposal,
                corrected_edges,
                actor=actor,
                rationale=rationale,
                principal=principal,
                correlation_id=correlation_id,
            )
        if action not in {"APPROVE", "REJECT"}:
            raise ValueError("invalid proposal review action")
        return self._decide(
            proposal,
            action,
            actor=actor,
            rationale=rationale,
            principal=principal,
            correlation_id=correlation_id,
        )

    def _decide(
        self,
        proposal: dict[str, Any],
        action: str,
        *,
        actor: str,
        rationale: str,
        principal: str,
        correlation_id: str,
    ) -> dict[str, object]:
        now = _timestamp(self._clock())
        proposal_id = _text("proposal ID", proposal.get("proposalId"), 512)
        version = proposal.get("version")
        lock_version = proposal.get("lockVersion")
        if not isinstance(version, int) or not isinstance(lock_version, int):
            raise RuntimeError("stored proposal version is invalid")
        decision = "APPROVED" if action == "APPROVE" else "REJECTED"
        identity = _canonical(
            {
                "proposalId": proposal_id,
                "version": version,
                "decision": decision,
                "principal": principal,
                "actor": actor,
                "rationale": rationale,
            }
        )
        digest = hashlib.sha256(identity.encode()).hexdigest()
        approval_id = f"approval-{digest[:24]}"
        approval = {
            "schemaVersion": "1.0.0",
            "approvalId": approval_id,
            "proposalId": proposal_id,
            "proposalVersion": version,
            "decision": decision,
            "actor": actor,
            "principal": principal,
            "rationale": rationale,
            "correlationId": proposal.get("correlationId"),
            "requestCorrelationId": correlation_id,
            "decidedAt": now,
        }
        approval_ref = self._artifacts.put(
            "approval",
            f"approvals/{proposal_id}/v{version}/{approval_id}.json",
            approval,
            "1.0.0",
        )
        updated = {
            **proposal,
            "state": decision,
            "lockVersion": lock_version + 1,
            "updatedAt": now,
            "decision": approval,
            "approvalRef": approval_ref,
        }
        if decision == "APPROVED":
            updated["approvedAt"] = now

        transaction: list[dict[str, Any]] = [
            self._proposal_update(updated, expected_lock_version=lock_version),
            self._audit_put(
                action=f"PROPOSAL_{decision}",
                proposal=proposal,
                actor=actor,
                principal=principal,
                rationale=rationale,
                correlation_id=correlation_id,
                created_at=now,
                identity=digest,
            ),
        ]
        publication = "NOT_REQUESTED"
        if decision == "APPROVED":
            envelope, decision_ref = self._publication_envelope(updated, approval_ref)
            outbox_id = "proposal-approval-" + hashlib.sha256(
                digest.encode()
            ).hexdigest()[:24]
            envelope["outboxId"] = outbox_id
            transaction.append(
                self._outbox_put(envelope, decision_ref, now, outbox_id)
            )
            publication = "OUTBOX_PENDING"
        try:
            aws_call(
                "dynamodb.review_proposal",
                self._client.transact_write_items,
                TransactItems=transaction,
                ClientRequestToken=digest[:36],
            )
        except AwsConflictError as error:
            raise ProductApiError(409, "CONCURRENT_DECISION", "proposal changed concurrently") from error
        return {
            "proposal": updated,
            "approvalRef": approval_ref,
            "publication": publication,
        }

    def _correct(
        self,
        proposal: dict[str, Any],
        corrected_edges: list[object] | None,
        *,
        actor: str,
        rationale: str,
        principal: str,
        correlation_id: str,
    ) -> dict[str, object]:
        if not corrected_edges:
            raise ProductApiError(400, "INVALID_CORRECTION", "corrected edges are required")
        system = _text("proposal system", proposal.get("system"), 512)
        environment = _text(
            "proposal environment", proposal.get("environment"), 512
        )
        normalized: list[dict[str, Any]] = []
        for edge in corrected_edges:
            if not isinstance(edge, Mapping):
                raise ProductApiError(400, "INVALID_CORRECTION", "corrected edge is invalid")
            value = dict(edge)
            allowed = {
                "schemaVersion",
                "edgeKey",
                "version",
                "from",
                "to",
                "edgeType",
                "band",
                "corroboration",
                "status",
                "provenance",
                "autoPublishable",
                "system",
                "transform",
                "updatedAt",
            }
            if set(value) - allowed or value.get("schemaVersion") != "1.0.0":
                raise ProductApiError(400, "INVALID_CORRECTION", "corrected edge schema is invalid")
            version = value.get("version")
            sources = value.get("from")
            target = value.get("to")
            edge_type = value.get("edgeType")
            provenance = value.get("provenance")
            if (
                not isinstance(version, int)
                or isinstance(version, bool)
                or version < 1
                or not isinstance(sources, list)
                or not 1 <= len(sources) <= 64
                or any(not isinstance(source, str) for source in sources)
                or sources != sorted(set(sources))
                or not isinstance(provenance, list)
                or not 1 <= len(provenance) <= 256
                or any(not isinstance(item, Mapping) for item in provenance)
                or not isinstance(value.get("autoPublishable"), bool)
            ):
                raise ProductApiError(400, "INVALID_CORRECTION", "corrected edge schema is invalid")
            normalized_sources = [
                _text("corrected edge source", source, 2_048) for source in sources
            ]
            normalized_target = _text("corrected edge target", target, 2_048)
            normalized_type = _text("corrected edge type", edge_type, 128)
            try:
                parsed = [
                    LineageUrn.parse(source)
                    for source in [*normalized_sources, normalized_target]
                ]
            except ValueError as error:
                raise ProductApiError(
                    400, "INVALID_CORRECTION", "corrected edge scope is invalid"
                ) from error
            if any(item.env != environment or item.system != system for item in parsed):
                raise ProductApiError(400, "INVALID_CORRECTION", "corrected edge scope is invalid")
            edge_key = _text("corrected edge key", value.get("edgeKey"), 512)
            if edge_key != edge_key_for(
                normalized_sources, normalized_target, normalized_type
            ):
                raise ProductApiError(400, "INVALID_CORRECTION", "corrected edge identity is invalid")
            if value.get("system") != system:
                raise ProductApiError(400, "INVALID_CORRECTION", "corrected edge crosses systems")
            if value.get("band") not in {"LOWEST", "SINGLE", "MEDIUM", "HIGH", "HIGHEST"}:
                raise ProductApiError(400, "INVALID_CORRECTION", "corrected edge band is invalid")
            if value.get("corroboration") not in {"NONE", "DATASET", "ELEMENT"}:
                raise ProductApiError(400, "INVALID_CORRECTION", "corrected edge corroboration is invalid")
            if value.get("status") != "PROPOSED":
                raise ProductApiError(400, "INVALID_CORRECTION", "corrected edge status is invalid")
            for optional_text in ("transform", "updatedAt"):
                if value.get(optional_text) is not None:
                    _text(f"corrected edge {optional_text}", value[optional_text], 4_096)
            value["from"] = normalized_sources
            value["to"] = normalized_target
            value["edgeType"] = normalized_type
            normalized.append(value)
        normalized.sort(key=lambda edge: edge["edgeKey"])
        edge_ids = [edge["edgeKey"] for edge in normalized]
        if edge_ids != sorted(set(edge_ids)):
            raise ProductApiError(400, "INVALID_CORRECTION", "corrected edge keys must be unique")

        now = _timestamp(self._clock())
        proposal_id = _text("proposal ID", proposal.get("proposalId"), 512)
        version = int(proposal["version"])
        lock_version = int(proposal["lockVersion"])
        edge_set = {
            "schemaVersion": "1.0.0",
            "artifactType": "consolidated-edge-set",
            "commandId": proposal.get("commandId"),
            "correlationId": proposal.get("correlationId"),
            "context": {
                key: proposal.get(key)
                for key in ("repository", "artifactDigest", "system", "environment")
            },
            "edges": normalized,
        }
        edge_ref = self._artifacts.put(
            "consolidated-edge-set",
            f"corrections/{proposal_id}/v{version + 1}/edges.json",
            edge_set,
            "1.0.0",
        )
        identity = hashlib.sha256(
            _canonical(
                {
                    "proposalId": proposal_id,
                    "version": version,
                    "principal": principal,
                    "edgeRef": edge_ref,
                }
            ).encode()
        ).hexdigest()
        superseded = {
            **proposal,
            "state": "SUPERSEDED",
            "lockVersion": lock_version + 1,
            "updatedAt": now,
            "supersededBy": f"{proposal_id}:v{version + 1}",
        }
        current_diff = proposal.get("diff")
        removed = (
            current_diff.get("removedEdgeIds", [])
            if isinstance(current_diff, Mapping)
            else []
        )
        successor = {
            **proposal,
            "version": version + 1,
            "state": "IN_REVIEW",
            "lockVersion": 1,
            "createdAt": now,
            "updatedAt": now,
            "supersedes": f"{proposal_id}:v{version}",
            "diff": {
                "edgeSetRef": edge_ref,
                "addedEdgeIds": edge_ids,
                "removedEdgeIds": removed,
                "bandChangedEdgeIds": [],
            },
        }
        try:
            aws_call(
                "dynamodb.correct_proposal",
                self._client.transact_write_items,
                TransactItems=[
                    self._proposal_update(
                        superseded, expected_lock_version=lock_version
                    ),
                    self._proposal_put(successor),
                    self._audit_put(
                        action="PROPOSAL_CORRECTED",
                        proposal=proposal,
                        actor=actor,
                        principal=principal,
                        rationale=rationale,
                        correlation_id=correlation_id,
                        created_at=now,
                        identity=identity,
                    ),
                ],
                ClientRequestToken=identity[:36],
            )
        except AwsConflictError as error:
            raise ProductApiError(409, "CONCURRENT_DECISION", "proposal changed concurrently") from error
        return {"proposal": successor, "publication": "NOT_REQUESTED"}

    def _publication_envelope(
        self, proposal: dict[str, Any], approval_ref: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        proposal_type = proposal.get("proposalType")
        if proposal_type == "BASELINE":
            workflow_kind, proposal_stage_id, stage_id, stage_name = (
                "BASELINE",
                "B9",
                "B10",
                "STAGE_VERIFY_FENCE_AND_ACTIVATE",
            )
        elif proposal_type == "DELTA":
            workflow_kind, proposal_stage_id, stage_id, stage_name = (
                "INCREMENTAL",
                "I9",
                "I10",
                "PUBLISH_WITH_FENCED_PROTOCOL",
            )
        elif proposal_type == "RECONCILIATION":
            workflow_kind, proposal_stage_id, stage_id, stage_name = (
                "INCREMENTAL",
                "I9",
                "I10",
                "PUBLISH_WITH_FENCED_PROTOCOL",
            )
        else:
            raise ProductApiError(
                409,
                "PUBLICATION_NOT_SUPPORTED",
                "proposal type does not support direct publication",
            )
        source_command_id = _text(
            "proposal command ID", proposal.get("commandId"), 512
        )
        if proposal_type == "RECONCILIATION":
            command_id = "publication-" + hashlib.sha256(
                f"{proposal['proposalId']}:{proposal['version']}".encode()
            ).hexdigest()[:24]
        else:
            command_id = source_command_id
        correlation_id = _text(
            "proposal correlation ID", proposal.get("correlationId"), 512
        )
        context = {
            key: proposal.get(key)
            for key in (
                "artifactDigest",
                "environment",
                "repository",
                "system",
            )
        }
        context["activeBaseVersion"] = proposal.get("expectedBaseVersion")
        context["acceptedAt"] = proposal.get("createdAt")
        decision = {
            "schemaVersion": "1.0.0",
            "artifactType": "proposal-decision",
            "workflowKind": workflow_kind,
            "workflowVersion": "1.0.0",
            "stageId": proposal_stage_id,
            "stageName": "APPROVED_PROPOSAL_RESUME",
            "commandId": command_id,
            "sourceCommandId": source_command_id,
            "correlationId": correlation_id,
            "context": context,
            "decision": "PROPOSAL_CREATED",
            "proposal": {**proposal, "approvalRef": approval_ref},
        }
        version = int(proposal["version"])
        decision_ref = self._artifacts.put(
            "proposal-decision",
            (
                f"approvals/{proposal['proposalId']}/v{version}/"
                f"publication-{approval_ref['sha256']}.json"
            ),
            decision,
            "1.0.0",
        )
        determinant = hashlib.sha256(_canonical(decision).encode()).hexdigest()
        envelope = {
            "schemaVersion": "1.0.0",
            "commandId": command_id,
            "correlationId": correlation_id,
            "causationId": f"{proposal['proposalId']}:v{version}",
            "determinantDigest": f"sha256:{determinant}",
            "idempotencyKey": f"approval:{proposal['proposalId']}:v{version}:{stage_id}",
            "input": decision_ref,
            "workflowKind": workflow_kind,
            "workflowVersion": "1.0.0",
            "stageId": stage_id,
            "stageName": stage_name,
        }
        return envelope, decision_ref

    def _proposal_update(
        self, proposal: dict[str, Any], *, expected_lock_version: int
    ) -> dict[str, Any]:
        state = _text("proposal state", proposal.get("state"), 64)
        return {
            "Update": {
                "TableName": self._proposal_table,
                "Key": {
                    "pk": {"S": f"PROPOSAL#{proposal['proposalId']}"},
                    "sk": {"S": f"VERSION#{int(proposal['version']):010d}"},
                },
                "UpdateExpression": (
                    "SET document = :document, #state = :state, queryPk = :queryPk, "
                    "lockVersion = :nextLockVersion, updatedAt = :updatedAt"
                ),
                "ConditionExpression": (
                    "#state = :inReview AND lockVersion = :expectedLockVersion"
                ),
                "ExpressionAttributeNames": {"#state": "state"},
                "ExpressionAttributeValues": {
                    ":document": {"S": _canonical(proposal)},
                    ":state": {"S": state},
                    ":queryPk": {"S": f"PROPOSAL_STATE#{state}"},
                    ":inReview": {"S": "IN_REVIEW"},
                    ":expectedLockVersion": {"N": str(expected_lock_version)},
                    ":nextLockVersion": {"N": str(proposal["lockVersion"])},
                    ":updatedAt": {"S": str(proposal.get("updatedAt", ""))},
                },
            }
        }

    def _proposal_put(self, proposal: dict[str, Any]) -> dict[str, Any]:
        created_at = _text("proposal createdAt", proposal.get("createdAt"), 128)
        proposal_id = _text("proposal ID", proposal.get("proposalId"), 512)
        version = int(proposal["version"])
        state = _text("proposal state", proposal.get("state"), 64)
        return {
            "Put": {
                "TableName": self._proposal_table,
                "Item": {
                    "pk": {"S": f"PROPOSAL#{proposal_id}"},
                    "sk": {"S": f"VERSION#{version:010d}"},
                    "document": {"S": _canonical(proposal)},
                    "state": {"S": state},
                    "lockVersion": {"N": str(proposal["lockVersion"])},
                    "queryPk": {"S": f"PROPOSAL_STATE#{state}"},
                    "querySk": {"S": f"{created_at}#{proposal_id}#{version:010d}"},
                    "createdAt": {"S": created_at},
                },
                "ConditionExpression": "attribute_not_exists(pk)",
            }
        }

    def _audit_put(
        self,
        *,
        action: str,
        proposal: dict[str, Any],
        actor: str,
        principal: str,
        rationale: str,
        correlation_id: str,
        created_at: str,
        identity: str,
    ) -> dict[str, Any]:
        audit_id = "audit-" + hashlib.sha256(
            f"{identity}:{action}".encode()
        ).hexdigest()[:24]
        return {
            "Put": {
                "TableName": self._ledger_table,
                "Item": {
                    "pk": {"S": f"AUDIT#{audit_id}"},
                    "sk": {"S": "EVENT"},
                    "action": {"S": action},
                    "actor": {"S": actor},
                    "principal": {"S": principal},
                    "resourceType": {"S": "PROPOSAL"},
                    "resourceId": {"S": str(proposal["proposalId"])},
                    "proposalVersion": {"N": str(proposal["version"])},
                    "correlationId": {"S": correlation_id},
                    "rationale": {"S": rationale},
                    "createdAt": {"S": created_at},
                },
                "ConditionExpression": "attribute_not_exists(pk)",
            }
        }

    def _outbox_put(
        self,
        envelope: dict[str, Any],
        decision_ref: dict[str, Any],
        created_at: str,
        outbox_id: str,
    ) -> dict[str, Any]:
        if set(decision_ref) != _REFERENCE_KEYS:
            raise RuntimeError("publication decision reference is invalid")
        return {
            "Put": {
                "TableName": self._ledger_table,
                "Item": {
                    "pk": {"S": f"OUTBOX#{outbox_id}"},
                    "sk": {"S": "EVENT"},
                    "topic": {"S": "PROPOSAL_APPROVED"},
                    "correlationId": {"S": str(envelope["correlationId"])},
                    "payload": {"S": _canonical(envelope)},
                    "status": {"S": "PENDING"},
                    "createdAt": {"S": created_at},
                },
                "ConditionExpression": "attribute_not_exists(pk)",
            }
        }

    def _active_namespace(self, environment: str) -> str:
        pointer = self._control.active_pointer(environment)
        namespace = pointer.get("graphVersion")
        if not isinstance(namespace, str) or namespace == "NONE":
            raise ProductApiError(404, "VERSION_NOT_FOUND", "active graph does not exist")
        return namespace


def _environment(subject: str) -> str:
    parts = subject.split(":", 4)
    if len(parts) < 4 or parts[:2] != ["urn", "ldp"] or not parts[2]:
        raise ProductApiError(400, "INVALID_URN", "lineage subject is invalid")
    return parts[2]


def build_product_api(
    *,
    env: Mapping[str, str] | None = None,
    clients: Mapping[str, Any] | None = None,
) -> ProductApiService:
    values = os.environ if env is None else env
    config = AwsRuntimeConfig.from_env(values)
    queue_urls = tuple(
        values[name]
        for name in sorted(values)
        if name.startswith("LINEAGE_") and name.endswith("_QUEUE_URL") and values[name]
    )
    dead_letter_queue_urls = tuple(
        values[name]
        for name in sorted(values)
        if name.startswith("LINEAGE_") and name.endswith("_DLQ_URL") and values[name]
    )
    if clients is None:
        try:
            import boto3
        except ModuleNotFoundError as error:
            raise RuntimeError("boto3 AWS runtime extra is required") from error
        session = boto3.session.Session(region_name=config.region)
        sdk = {
            "dynamodb": session.client("dynamodb"),
            "s3": session.client("s3"),
            "sqs": session.client("sqs"),
            "neptunedata": session.client(
                "neptunedata", endpoint_url=f"https://{config.neptune_endpoint}:8182"
            ),
        }
    else:
        sdk = dict(clients)
    control = DynamoDbControlAdapter(
        sdk["dynamodb"],
        config.control_table,
        config.ledger_table,
        config.pointer_table,
        proposal_table=config.proposal_table,
    )
    return ProductApiService(
        AwsProductQueryProjection(
            sdk["dynamodb"],
            control,
            S3ArtifactStore(sdk["s3"], config.evidence_bucket),
            NeptuneProjectionAdapter(sdk["neptunedata"]),
            control_table=config.control_table,
            ledger_table=config.ledger_table,
            proposal_table=config.proposal_table,
            default_environment=values.get("LINEAGE_ENVIRONMENT", "staging"),
            sqs=sdk.get("sqs") if queue_urls else None,
            queue_urls=queue_urls,
            dead_letter_queue_urls=dead_letter_queue_urls,
        )
    )
