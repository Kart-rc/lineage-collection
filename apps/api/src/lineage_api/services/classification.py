from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Callable

from lineage_api.db import Database


TREATMENTS = {
    "APPLICATION_RUNTIME": ("INCLUDE", "INCREMENTAL_LINEAGE"),
    "DATA_PIPELINE": ("INCLUDE", "INCREMENTAL_AND_NATIVE"),
    "CONTRACT_SCHEMA_SOURCE": ("METADATA_ONLY", "REEVALUATE_DEPENDENTS"),
    "SHARED_LIBRARY": ("EXCLUDE", "REANALYZE_CONSUMERS"),
    "INFRASTRUCTURE": ("EXCLUDE", "EVALUATE_BINDINGS"),
    "DOCUMENTATION": ("EXCLUDE", "NO_LINEAGE_IMPACT"),
    "TEST_AUTOMATION": ("EXCLUDE", "UPDATE_COVERAGE"),
    "MIXED_MONOREPO": ("PATH_LEVEL", "ROUTE_CHANGED_PATHS"),
    "UNKNOWN": ("BLOCK", "REVIEW"),
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ClassificationEvidence:
    level: int
    source: str
    repository_class: str
    ref: str

    def __post_init__(self) -> None:
        if self.level not in range(1, 8):
            raise ValueError("Classification evidence level must be between 1 and 7")
        if self.repository_class not in TREATMENTS:
            raise ValueError(f"Unknown repository class: {self.repository_class}")


@dataclass(frozen=True, slots=True)
class ClassificationDecision:
    decision_id: str
    repo_or_path: str
    repository_class: str
    baseline_treatment: str
    on_change_treatment: str
    evidence_level_used: int
    evidence_refs: tuple[str, ...]
    policy_version: str
    status: str
    correlation_id: str
    decided_at: str
    reason: str | None = None


class ClassificationService:
    def __init__(
        self,
        database: Database,
        policy_version: str,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self._database = database
        self._policy_version = policy_version
        self._clock = clock

    def classify(
        self,
        repo_or_path: str,
        evidence: list[ClassificationEvidence],
        correlation_id: str,
    ) -> ClassificationDecision:
        if not evidence:
            selected_level = 7
            selected = []
            repository_class = "UNKNOWN"
            reason = "NO_DECISIVE_EVIDENCE"
        else:
            selected_level = min(item.level for item in evidence)
            selected = sorted(
                (item for item in evidence if item.level == selected_level),
                key=lambda item: (item.repository_class, item.source, item.ref),
            )
            classes = {item.repository_class for item in selected}
            if len(classes) > 1:
                repository_class = "UNKNOWN"
                reason = "EVIDENCE_CONFLICT"
            else:
                repository_class = next(iter(classes))
                reason = None
                if selected_level == 7 and TREATMENTS[repository_class][0] in {
                    "EXCLUDE",
                    "METADATA_ONLY",
                }:
                    repository_class = "UNKNOWN"
                    reason = "HEURISTIC_EXCLUSION_FORBIDDEN"

        baseline_treatment, on_change_treatment = TREATMENTS[repository_class]
        status = "REVIEW_REQUIRED" if repository_class == "UNKNOWN" else "EVALUATED"
        evidence_refs = tuple(item.ref for item in selected)
        decision_identity = {
            "repoOrPath": repo_or_path,
            "evidence": [asdict(item) for item in selected],
            "policyVersion": self._policy_version,
            "correlationId": correlation_id,
        }
        digest = hashlib.sha256(
            json.dumps(decision_identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        decision_id = f"class-{digest[:20]}"

        with self._database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM classification_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            if existing is not None:
                return self._from_row(existing)
            decided_at = self._clock()
            connection.execute(
                """
                INSERT INTO classification_decisions(
                    decision_id, repo_or_path, repository_class, baseline_treatment,
                    on_change_treatment, evidence_level, evidence_refs_json,
                    policy_version, status, correlation_id, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    repo_or_path,
                    repository_class,
                    baseline_treatment,
                    on_change_treatment,
                    selected_level,
                    json.dumps(evidence_refs),
                    self._policy_version,
                    status,
                    correlation_id,
                    decided_at,
                ),
            )
        return ClassificationDecision(
            decision_id=decision_id,
            repo_or_path=repo_or_path,
            repository_class=repository_class,
            baseline_treatment=baseline_treatment,
            on_change_treatment=on_change_treatment,
            evidence_level_used=selected_level,
            evidence_refs=evidence_refs,
            policy_version=self._policy_version,
            status=status,
            correlation_id=correlation_id,
            decided_at=decided_at,
            reason=reason,
        )

    def decision_count(self, decision_id: str) -> int:
        with self._database.connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM classification_decisions WHERE decision_id = ?",
                    (decision_id,),
                ).fetchone()[0]
            )

    @staticmethod
    def _from_row(row) -> ClassificationDecision:
        repository_class = str(row["repository_class"])
        reason = None
        if repository_class == "UNKNOWN":
            refs = json.loads(row["evidence_refs_json"])
            reason = "EVIDENCE_CONFLICT" if len(refs) > 1 else "HEURISTIC_EXCLUSION_FORBIDDEN"
        return ClassificationDecision(
            decision_id=str(row["decision_id"]),
            repo_or_path=str(row["repo_or_path"]),
            repository_class=repository_class,
            baseline_treatment=str(row["baseline_treatment"]),
            on_change_treatment=str(row["on_change_treatment"]),
            evidence_level_used=int(row["evidence_level"]),
            evidence_refs=tuple(json.loads(row["evidence_refs_json"])),
            policy_version=str(row["policy_version"]),
            status=str(row["status"]),
            correlation_id=str(row["correlation_id"]),
            decided_at=str(row["decided_at"]),
            reason=reason,
        )
