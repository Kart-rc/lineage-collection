from __future__ import annotations

from pathlib import Path

import pytest

from lineage_api.db import Database


def _classification_types():
    try:
        from lineage_api.services.classification import (
            ClassificationEvidence,
            ClassificationService,
        )
    except ModuleNotFoundError:
        pytest.fail("Classification service is not implemented")
    return ClassificationEvidence, ClassificationService


@pytest.fixture
def classifier(tmp_path: Path):
    _, classification_service = _classification_types()
    database = Database(tmp_path / "lineage.db")
    database.initialize()
    return classification_service(database, policy_version="1.0.0")


def _evidence(level: int, repository_class: str, source: str = "fixture"):
    classification_evidence, _ = _classification_types()
    return classification_evidence(
        level=level,
        source=source,
        repository_class=repository_class,
        ref=f"fixture://{source}/{repository_class.lower()}",
    )


def test_first_decisive_evidence_level_wins(classifier) -> None:
    decision = classifier.classify(
        repo_or_path="payments-pipeline",
        evidence=[
            _evidence(4, "APPLICATION_RUNTIME", "manifest"),
            _evidence(1, "DATA_PIPELINE", "catalog"),
            _evidence(7, "DOCUMENTATION", "heuristic"),
        ],
        correlation_id="corr-001",
    )

    assert decision.repository_class == "DATA_PIPELINE"
    assert decision.evidence_level_used == 1
    assert decision.baseline_treatment == "INCLUDE"
    assert decision.on_change_treatment == "INCREMENTAL_AND_NATIVE"
    assert decision.status == "EVALUATED"


def test_same_precedence_conflict_becomes_unknown(classifier) -> None:
    decision = classifier.classify(
        repo_or_path="payments-pipeline",
        evidence=[
            _evidence(4, "DATA_PIPELINE", "pyproject"),
            _evidence(4, "APPLICATION_RUNTIME", "package-json"),
        ],
        correlation_id="corr-002",
    )

    assert decision.repository_class == "UNKNOWN"
    assert decision.status == "REVIEW_REQUIRED"
    assert decision.reason == "EVIDENCE_CONFLICT"


def test_heuristics_can_propose_but_never_exclude(classifier) -> None:
    decision = classifier.classify(
        repo_or_path="docs-only",
        evidence=[_evidence(7, "DOCUMENTATION", "heuristic")],
        correlation_id="corr-003",
    )

    assert decision.repository_class == "UNKNOWN"
    assert decision.reason == "HEURISTIC_EXCLUSION_FORBIDDEN"
    assert decision.status == "REVIEW_REQUIRED"


def test_classification_replay_is_deterministic_and_history_is_immutable(classifier) -> None:
    evidence = [_evidence(1, "DATA_PIPELINE", "catalog")]

    first = classifier.classify("payments-pipeline", evidence, "corr-004")
    second = classifier.classify("payments-pipeline", evidence, "corr-004")

    assert first == second
    assert classifier.decision_count(first.decision_id) == 1
