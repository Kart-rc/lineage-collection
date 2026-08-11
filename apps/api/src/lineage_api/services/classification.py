from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from lineage_api.db import Database
from lineage_api.application.classification import (
    ClassificationEvidence,
    TREATMENTS,
    evaluate_classification,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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
        evaluation = evaluate_classification(
            repo_or_path, evidence, self._policy_version, correlation_id
        )
        repository_class = evaluation.repository_class
        baseline_treatment = evaluation.baseline_treatment
        on_change_treatment = evaluation.on_change_treatment
        selected_level = evaluation.evidence_level_used
        evidence_refs = evaluation.evidence_refs
        status = evaluation.status
        reason = evaluation.reason
        decision_id = evaluation.decision_id

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
