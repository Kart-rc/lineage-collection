from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from lineage_api.application.models import parse_utc
from lineage_api.services.orchestration import OrchestrationService
from lineage_api.services.query import QueryService


def _now() -> datetime:
    return datetime.now(UTC)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class MetricsSnapshotPort(Protocol):
    def snapshot(self) -> dict[str, Any]: ...


class LocalMetricsSnapshot:
    """Derive honest local health signals while leaving production-only gates unclaimed."""

    def __init__(
        self,
        orchestration: OrchestrationService,
        query: QueryService,
        *,
        clock: Callable[[], datetime] = _now,
        queue_age_limit_seconds: int = 60,
        baseline_age_limit_seconds: int = 86_400,
        approval_age_limit_seconds: int = 300,
        publish_lag_limit_seconds: int = 60,
        watermark_age_limit_seconds: int = 300,
    ) -> None:
        self._orchestration = orchestration
        self._query = query
        self._clock = clock
        self._queue_age_limit_seconds = queue_age_limit_seconds
        self._baseline_age_limit_seconds = baseline_age_limit_seconds
        self._approval_age_limit_seconds = approval_age_limit_seconds
        self._publish_lag_limit_seconds = publish_lag_limit_seconds
        self._watermark_age_limit_seconds = watermark_age_limit_seconds

    def snapshot(self) -> dict[str, Any]:
        now = self._clock().astimezone(UTC)
        control = self._orchestration.operational_observations()
        projection = self._query.projection_observations()

        oldest_queue_age = self._age(now, control["oldestQueuedAt"])
        queue_degraded = (
            int(control["retryCount"]) > 0
            or int(control["deadLetterCount"]) > 0
            or int(control["leaseStealCount"]) > 0
            or (
                oldest_queue_age is not None
                and oldest_queue_age > self._queue_age_limit_seconds
            )
        )
        queue = {
            "status": "DEGRADED" if queue_degraded else "HEALTHY",
            "depth": int(control["queueDepth"]),
            "oldestAgeSeconds": oldest_queue_age,
            "saturation": {
                "status": "NOT_CONFIGURED",
                "observedDepth": int(control["queueDepth"]),
                "capacity": None,
            },
            "retryCount": int(control["retryCount"]),
            "deadLetterCount": int(control["deadLetterCount"]),
            "leaseStealCount": int(control["leaseStealCount"]),
        }

        coverage_rows = control["coverage"]
        incomplete_count = sum(row["state"] != "COMPLETE" for row in coverage_rows)
        eligible_runtime = len(coverage_rows)
        joined_runtime = sum(
            row["runtimeStatus"] == "VALIDATED" for row in coverage_rows
        )
        if eligible_runtime == 0:
            runtime_status, runtime_rate = "NOT_AVAILABLE", None
        else:
            runtime_rate = joined_runtime / eligible_runtime
            runtime_status = "COMPLETE" if joined_runtime == eligible_runtime else "INCOMPLETE"
        baseline_rows = [
            row for row in coverage_rows if row["workflowKind"] == "BASELINE"
        ]
        if not baseline_rows:
            baseline = {
                "status": "NOT_AVAILABLE",
                "ageSeconds": None,
                "maxAgeSeconds": self._baseline_age_limit_seconds,
            }
        else:
            latest_baseline = baseline_rows[-1]
            baseline_age = self._age(now, latest_baseline["updatedAt"])
            if latest_baseline["state"] != "COMPLETE":
                baseline_status = "INCOMPLETE"
            elif baseline_age is not None and baseline_age > self._baseline_age_limit_seconds:
                baseline_status = "STALE"
            else:
                baseline_status = "CURRENT"
            baseline = {
                "status": baseline_status,
                "ageSeconds": baseline_age,
                "maxAgeSeconds": self._baseline_age_limit_seconds,
            }
        coverage_status = (
            "NOT_AVAILABLE"
            if not coverage_rows
            else "INCOMPLETE" if incomplete_count else "COMPLETE"
        )
        coverage = {
            "status": coverage_status,
            "incompleteCount": incomplete_count,
            "runtimeJoin": {
                "status": runtime_status,
                "joined": joined_runtime,
                "eligible": eligible_runtime,
                "rate": runtime_rate,
            },
            "baseline": baseline,
        }

        approval_age = self._age(now, control["oldestApprovalAt"])
        review = {
            "status": (
                "DEGRADED"
                if approval_age is not None
                and approval_age > self._approval_age_limit_seconds
                else "HEALTHY"
            ),
            "oldestApprovalAgeSeconds": approval_age,
        }

        publish_lag = self._publication_lag(now, control)
        pointer_package_status = projection["pointerPackageStatus"]
        if pointer_package_status == "OUT_OF_SYNC":
            publication_status = "OUT_OF_SYNC"
        elif publish_lag is None:
            publication_status = "NOT_AVAILABLE"
        elif publish_lag > self._publish_lag_limit_seconds:
            publication_status = "DEGRADED"
        else:
            publication_status = "HEALTHY"
        watermark_age = self._age(now, projection["watermarkAt"])
        if watermark_age is None:
            watermark_status = "NOT_AVAILABLE"
        elif watermark_age > self._watermark_age_limit_seconds:
            watermark_status = "STALE"
        else:
            watermark_status = "CURRENT"
        publication = {
            "status": publication_status,
            "publishLagSeconds": publish_lag,
            "pointerPackage": {
                "status": pointer_package_status,
                "activeVersion": projection["activeVersion"],
                "packageVersion": projection["packageVersion"],
            },
            "watermark": {
                "status": watermark_status,
                "version": projection["activeVersion"],
                "updatedAt": projection["watermarkAt"],
                "ageSeconds": watermark_age,
            },
        }

        correlation_missing = int(control["correlationMissing"])
        correlation_tracked = int(control["correlationTracked"])
        correlation = {
            "status": (
                "NOT_AVAILABLE"
                if correlation_tracked == 0
                else "INCOMPLETE" if correlation_missing else "COMPLETE"
            ),
            "trackedCount": correlation_tracked,
            "missingCount": correlation_missing,
        }
        degraded_states = {
            queue["status"],
            coverage["status"],
            coverage["runtimeJoin"]["status"],
            coverage["baseline"]["status"],
            review["status"],
            publication["status"],
            publication["watermark"]["status"],
            correlation["status"],
        }
        if publication_status == "OUT_OF_SYNC":
            overall_status = "OUT_OF_SYNC"
        elif degraded_states & {"DEGRADED", "INCOMPLETE", "STALE"}:
            overall_status = "DEGRADED"
        else:
            overall_status = "HEALTHY"
        not_configured = {"status": "NOT_CONFIGURED", "value": None}
        return {
            "schemaVersion": "1.0.0",
            "capturedAt": _utc_text(now),
            "status": overall_status,
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
    def _age(now: datetime, timestamp: str | None) -> int | None:
        if timestamp is None:
            return None
        return max(0, int((now - parse_utc(timestamp)).total_seconds()))

    @classmethod
    def _publication_lag(cls, now: datetime, control: dict[str, Any]) -> int | None:
        if control["inProgressPublicationAt"] is not None:
            return cls._age(now, control["inProgressPublicationAt"])
        completed = control["completedPublication"]
        if completed is None or completed["terminalAt"] is None:
            return None
        return max(
            0,
            int(
                (
                    parse_utc(completed["terminalAt"])
                    - parse_utc(completed["createdAt"])
                ).total_seconds()
            ),
        )
