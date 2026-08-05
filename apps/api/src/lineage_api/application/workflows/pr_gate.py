from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


CHANGE_TYPES = {
    "COLUMN_DROP",
    "COLUMN_TYPE_CHANGE",
    "DATASET_REMOVAL",
    "COLUMN_RENAME",
    "TRANSFORM_CHANGE",
    "FINGERPRINT_DRIFT",
}
EVIDENCE_MECHANISMS = {"SCA", "NATIVE", "MANIFEST", "CACHE", "LLM"}


class ProjectionUnavailableError(RuntimeError):
    """A typed, expected failure to read the pinned lineage projection."""


class CheckWriter(Protocol):
    def upsert(self, result: dict[str, object]) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class EnvironmentPin:
    environment: str
    graph_version: str
    fencing_token: int
    deployed_artifact_digest: str
    status: str


@dataclass(frozen=True, slots=True)
class PRGateChange:
    change_type: str
    subject: str
    evidence_mechanisms: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.change_type not in CHANGE_TYPES:
            raise ValueError(f"unknown PR Gate change type: {self.change_type}")
        if not self.subject:
            raise ValueError("PR Gate change subject must not be empty")
        if not self.evidence_mechanisms:
            raise ValueError("PR Gate change requires evidence mechanisms")
        unknown = set(self.evidence_mechanisms) - EVIDENCE_MECHANISMS
        if unknown:
            raise ValueError(f"unknown PR Gate evidence mechanisms: {sorted(unknown)}")


@dataclass(frozen=True, slots=True)
class PRGateRequest:
    repo: str
    pr_number: int
    head_sha: str
    target_environment: str
    policy_version: str
    candidate_artifact_digest: str
    coverage_complete: bool
    changes: tuple[PRGateChange, ...]
    depth: int = 5

    def __post_init__(self) -> None:
        if not self.repo or not self.head_sha or not self.target_environment:
            raise ValueError("PR Gate identity fields must not be empty")
        if self.pr_number < 1:
            raise ValueError("PR number must be positive")
        if not 1 <= self.depth <= 5:
            raise ValueError("PR Gate depth must be between 1 and 5")
        if not self.changes:
            raise ValueError("PR Gate requires at least one change")


class PRGateWorkflow:
    DEADLINE_SECONDS = 120.0

    def __init__(
        self,
        *,
        head_reader: Callable[[str, int, str], str | None],
        environment_reader: Callable[[str], EnvironmentPin | None],
        impact_reader: Callable[[PRGateChange, EnvironmentPin, int], dict[str, Any]],
        check_writer: CheckWriter,
        monotonic: Callable[[], float],
        utc_now: Callable[[], datetime],
    ) -> None:
        self._head_reader = head_reader
        self._environment_reader = environment_reader
        self._impact_reader = impact_reader
        self._check_writer = check_writer
        self._monotonic = monotonic
        self._utc_now = utc_now

    def evaluate(self, request: PRGateRequest) -> dict[str, object]:
        started = self._monotonic()
        reasons: list[str] = []
        initial_head = self._head_reader(request.repo, request.pr_number, request.head_sha)
        initial_environment = self._environment_reader(request.target_environment)
        if initial_head != request.head_sha:
            self._add_reason(reasons, "PR_HEAD_SUPERSEDED")
        if not request.coverage_complete:
            self._add_reason(reasons, "INCOMPLETE_COVERAGE")
        if initial_environment is None:
            self._add_reason(reasons, "MISSING_ENVIRONMENT")
        elif initial_environment.status != "HEALTHY":
            self._add_reason(reasons, "LINEAGE_OUT_OF_SYNC")

        truncated = False
        calibrated_block = False
        evaluated_types: list[str] = []
        for change in request.changes:
            if self._monotonic() - started >= self.DEADLINE_SECONDS:
                self._add_reason(reasons, "DEADLINE_EXCEEDED")
                break
            if initial_environment is None or initial_environment.status != "HEALTHY":
                continue
            if change.change_type not in evaluated_types:
                evaluated_types.append(change.change_type)
            try:
                impact = self._impact_reader(change, initial_environment, request.depth)
            except ProjectionUnavailableError:
                self._add_reason(reasons, "PROJECTION_UNAVAILABLE")
                continue
            if impact.get("namespaceVersion") != initial_environment.graph_version:
                self._add_reason(reasons, "ENVIRONMENT_VERSION_MISMATCH")
            if bool(impact.get("truncated")):
                truncated = True
                self._add_reason(reasons, "IMPACT_TRUNCATED")
            summary = impact.get("summary", {})
            if int(summary.get("warn", 0)) > 0:
                self._add_reason(reasons, "IMPACT_WARNING")
            if int(summary.get("block", 0)) > 0:
                if set(change.evidence_mechanisms) == {"LLM"}:
                    self._add_reason(reasons, "LLM_ONLY_BLOCK_EVIDENCE")
                else:
                    calibrated_block = True

        if self._monotonic() - started >= self.DEADLINE_SECONDS:
            self._add_reason(reasons, "DEADLINE_EXCEEDED")
        final_head = self._head_reader(request.repo, request.pr_number, request.head_sha)
        final_environment = self._environment_reader(request.target_environment)
        if final_head != request.head_sha:
            self._add_reason(reasons, "PR_HEAD_SUPERSEDED")
        if final_environment != initial_environment:
            self._add_reason(reasons, "ENVIRONMENT_CHANGED")

        verdict = "WARN" if reasons else ("BLOCK" if calibrated_block else "PASS")
        environment_version = (
            initial_environment.graph_version if initial_environment is not None else "MISSING"
        )
        environment_fence = (
            initial_environment.fencing_token if initial_environment is not None else 0
        )
        check_identity = {
            "repo": request.repo,
            "prNumber": request.pr_number,
            "headSha": request.head_sha,
        }
        check_id = (
            "pr-check-"
            + hashlib.sha256(self._canonical(check_identity).encode()).hexdigest()[:20]
        )
        result: dict[str, object] = {
            "schemaVersion": "1.0.0",
            "checkId": check_id,
            "repo": request.repo,
            "prNumber": request.pr_number,
            "headSha": request.head_sha,
            "environment": request.target_environment,
            "environmentVersion": environment_version,
            "environmentFence": environment_fence,
            "deployedArtifactDigest": (
                initial_environment.deployed_artifact_digest
                if initial_environment is not None
                else None
            ),
            "candidateArtifactDigest": request.candidate_artifact_digest,
            "policyVersion": request.policy_version,
            "verdict": verdict,
            "reasons": reasons,
            "truncated": truncated,
            "evaluatedChangeTypes": evaluated_types,
            "evaluatedAt": self._utc_now().isoformat().replace("+00:00", "Z"),
        }
        self._check_writer.upsert(result)
        return result

    @staticmethod
    def _add_reason(reasons: list[str], reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
