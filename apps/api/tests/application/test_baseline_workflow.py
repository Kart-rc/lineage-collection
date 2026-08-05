from __future__ import annotations

import hashlib
import hmac
import json
import shutil
from pathlib import Path

import pytest

from lineage_api.config import Settings
from lineage_api.services.intake import PushDelivery


PROJECT_ROOT = Path(__file__).parents[4]
SECRET = "baseline-test-secret"


def settings(root: Path, fixture_root: Path | None = None) -> Settings:
    return Settings(
        project_root=PROJECT_ROOT,
        data_directory=root,
        fixture_directory=fixture_root or PROJECT_ROOT / "fixtures",
        database_path=root / "lineage.db",
        object_directory=root / "objects",
        webhook_secret=SECRET,
    )


def delivery(
    event_id: str = "delivery-baseline-001",
    *,
    digest: str = "demo-digest-v2",
    repo: str = "payments-pipeline",
    system: str = "payments",
) -> PushDelivery:
    payload = {
        "eventId": event_id,
        "eventType": "baseline",
        "repo": repo,
        "digest": digest,
        "env": "staging",
        "system": system,
        "changedFiles": [],
        "receivedAt": "2026-08-05T12:00:00Z",
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return PushDelivery(payload, f"sha256={signature}")


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def test_baseline_executes_b1_b10_with_complete_full_scope_and_deterministic_replay(
    tmp_path: Path,
) -> None:
    from lineage_api.dependencies import build_services

    first_services = build_services(settings(tmp_path / "first"))
    first_services.reset()
    first = first_services.orchestration.process_push(delivery())

    second_services = build_services(settings(tmp_path / "second"))
    second_services.reset()
    second = second_services.orchestration.process_push(delivery())

    assert first["outcome"] == "ACCEPTED"
    assert first["command"]["workflowKind"] == "BASELINE"
    assert first["command"]["status"] == "COMPLETED"
    assert first["run"]["state"] == "IN_REVIEW"
    assert first["coverageManifest"]["state"] == "COMPLETE"
    assert first["coverageManifest"]["expectedScope"] == [
        "expected-lineage.json",
        "pipeline.py",
        "repository-evidence.json",
    ]
    assert first["coverageManifest"]["completedScope"] == ["pipeline.py"]
    assert first["coverageManifest"]["skippedScope"] == [
        "expected-lineage.json",
        "repository-evidence.json",
    ]
    assert first["coverageManifest"]["unsupportedScope"] == []
    assert first["evidenceManifest"]["residue"] == {
        "status": "SKIPPED_WITH_RECORD",
        "count": 1,
        "reason": "LLM_NOT_CONFIGURED",
    }
    assert canonical(first["coverageManifest"]) == canonical(second["coverageManifest"])
    assert canonical(first["evidenceManifest"]) == canonical(second["evidenceManifest"])

    with first_services.database.connection() as connection:
        stage_ids = {
            row[0].rsplit("/", 1)[-1].upper()
            for row in connection.execute(
                """
                SELECT object_key FROM evidence_objects
                WHERE kind = 'manifest' AND object_key LIKE 'workflow/%'
                """
            ).fetchall()
        }
        assert connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM coverage_manifests").fetchone()[0] == 1
    assert stage_ids == {f"B{index}" for index in range(1, 11)}


def test_baseline_unknown_classification_blocks_before_analysis(tmp_path: Path) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(settings(tmp_path))
    services.reset()

    result = services.orchestration.process_push(delivery(), classification_evidence=[])

    assert result["outcome"] == "BLOCKED"
    assert result["reason"] == "UNKNOWN_CLASSIFICATION"
    assert result["run"]["state"] == "FAILED"
    assert result["run"]["failedStage"] == "CLASSIFYING"
    assert result["proposal"] is None
    with services.database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 0
        checkpoint_ids = {
            row[0].rsplit("/", 1)[-1].upper()
            for row in connection.execute(
                """
                SELECT object_key FROM evidence_objects
                WHERE kind = 'manifest' AND object_key LIKE 'workflow/%'
                """
            ).fetchall()
        }
    assert checkpoint_ids == {"B1", "B2", "B3"}


def test_baseline_fully_accounted_empty_repository_records_no_lineage(
    tmp_path: Path,
) -> None:
    from lineage_api.dependencies import build_services
    from lineage_api.services.classification import ClassificationEvidence

    fixture_root = tmp_path / "fixtures"
    shutil.copytree(PROJECT_ROOT / "fixtures", fixture_root)
    repository = fixture_root / "repositories" / "docs-only"
    repository.mkdir()
    (repository / "README.md").write_text("No lineage sources.\n", encoding="utf-8")

    services = build_services(settings(tmp_path / "state", fixture_root))
    services.reset()
    result = services.orchestration.process_push(
        delivery(
            "delivery-baseline-docs",
            repo="docs-only",
            system="documentation",
        ),
        classification_evidence=[
            ClassificationEvidence(
                level=1,
                source="catalog",
                repository_class="DOCUMENTATION",
                ref="fixture://catalog/docs-only",
            )
        ],
    )

    assert result["outcome"] == "NO_LINEAGE"
    assert result["reason"] == "NO_LINEAGE_EVIDENCE"
    assert result["proposal"] is None
    assert result["run"]["state"] == "NO_LINEAGE"
    assert result["coverageManifest"]["state"] == "COMPLETE"
    assert result["coverageManifest"]["expectedScope"] == ["README.md"]
    assert result["coverageManifest"]["skippedScope"] == ["README.md"]
    assert result["evidenceManifest"]["edges"] == []


def test_baseline_planning_bounds_fanout_and_accounts_unsupported_packs(
    tmp_path: Path,
) -> None:
    try:
        from lineage_api.application.workflows.baseline import (
            BaselineFanoutExceeded,
            BaselineWorkflow,
        )
    except ModuleNotFoundError:
        pytest.fail("Baseline workflow is not implemented")

    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pipeline.py").write_text("pass\n", encoding="utf-8")
    (repository / "job.scala").write_text("object Job {}\n", encoding="utf-8")
    (repository / "manifest.json").write_text("{}\n", encoding="utf-8")
    (repository / "README.md").write_text("documentation\n", encoding="utf-8")

    plan = BaselineWorkflow.plan_repository(repository, max_fanout=4)

    assert plan == {
        "expectedScope": ["README.md", "job.scala", "manifest.json", "pipeline.py"],
        "recomputedScope": ["pipeline.py"],
        "skippedScope": ["README.md"],
        "unsupportedScope": ["job.scala", "manifest.json"],
        "fanout": [{"pack": "python-ast", "paths": ["pipeline.py"]}],
    }
    with pytest.raises(BaselineFanoutExceeded, match="4 exceeds limit 1"):
        BaselineWorkflow.plan_repository(repository, max_fanout=1)
