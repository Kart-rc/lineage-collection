from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Final, Mapping, Sequence


_TREATMENTS = {
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
TREATMENTS: Final[Mapping[str, tuple[str, str]]] = MappingProxyType(_TREATMENTS)
MAX_CLASSIFICATION_EVIDENCE = 256
MAX_CLASSIFICATION_TEXT = 2_048


def _bounded_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if len(value.encode()) > MAX_CLASSIFICATION_TEXT:
        raise ValueError(f"{name} exceeds the classification text limit")
    return value


@dataclass(frozen=True, slots=True)
class ClassificationEvidence:
    level: int
    source: str
    repository_class: str
    ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.level, int) or isinstance(self.level, bool) or self.level not in range(1, 8):
            raise ValueError("Classification evidence level must be between 1 and 7")
        _bounded_text("Classification evidence source", self.source)
        _bounded_text("Classification evidence reference", self.ref)
        if self.repository_class not in TREATMENTS:
            raise ValueError(f"Unknown repository class: {self.repository_class}")


@dataclass(frozen=True, slots=True)
class ClassificationEvaluation:
    decision_id: str
    repo_or_path: str
    repository_class: str
    baseline_treatment: str
    on_change_treatment: str
    evidence_level_used: int
    evidence_refs: tuple[str, ...]
    policy_version: str
    status: str
    reason: str | None


def evaluate_classification(
    repo_or_path: str,
    evidence: Sequence[ClassificationEvidence],
    policy_version: str,
    correlation_id: str,
) -> ClassificationEvaluation:
    repo_or_path = _bounded_text("Repository or path", repo_or_path)
    policy_version = _bounded_text("Classification policy version", policy_version)
    correlation_id = _bounded_text("Classification correlation ID", correlation_id)
    if len(evidence) > MAX_CLASSIFICATION_EVIDENCE:
        raise ValueError("classification evidence exceeds the item limit")
    if not all(isinstance(item, ClassificationEvidence) for item in evidence):
        raise TypeError("classification evidence contains an unsupported item")
    unique_evidence = tuple(
        sorted(
            set(evidence),
            key=lambda item: (item.level, item.repository_class, item.source, item.ref),
        )
    )

    if not unique_evidence:
        selected_level = 7
        selected: list[ClassificationEvidence] = []
        repository_class = "UNKNOWN"
        reason = "NO_DECISIVE_EVIDENCE"
    else:
        selected_level = min(item.level for item in unique_evidence)
        selected = sorted(
            (item for item in unique_evidence if item.level == selected_level),
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
    evidence_refs = tuple(item.ref for item in selected)
    identity = {
        "repoOrPath": repo_or_path,
        "evidence": [asdict(item) for item in selected],
        "policyVersion": policy_version,
        "correlationId": correlation_id,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ClassificationEvaluation(
        decision_id=f"class-{digest[:20]}",
        repo_or_path=repo_or_path,
        repository_class=repository_class,
        baseline_treatment=baseline_treatment,
        on_change_treatment=on_change_treatment,
        evidence_level_used=selected_level,
        evidence_refs=evidence_refs,
        policy_version=policy_version,
        status="REVIEW_REQUIRED" if repository_class == "UNKNOWN" else "EVALUATED",
        reason=reason,
    )


__all__ = [
    "ClassificationEvaluation",
    "ClassificationEvidence",
    "MAX_CLASSIFICATION_EVIDENCE",
    "TREATMENTS",
    "evaluate_classification",
]
