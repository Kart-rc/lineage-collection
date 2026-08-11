from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from lineage_api.config import Settings
from lineage_api.services.intake import PushDelivery


PROJECT_ROOT = Path(__file__).parents[4]
SECRET = "incremental-test-secret"


class InjectedWorkflowCrash(RuntimeError):
    pass


def settings(root: Path) -> Settings:
    return Settings(
        project_root=PROJECT_ROOT,
        data_directory=root,
        fixture_directory=PROJECT_ROOT / "fixtures",
        database_path=root / "lineage.db",
        object_directory=root / "objects",
        webhook_secret=SECRET,
    )


def delivery(*, runtime_observation: dict[str, object] | None = None) -> PushDelivery:
    payload: dict[str, object] = {
        "eventId": "delivery-incremental-resume",
        "eventType": "repo.push",
        "repo": "payments-pipeline",
        "digest": "demo-digest-v2",
        "env": "staging",
        "system": "payments",
        "changedFiles": ["pipeline.py"],
        "receivedAt": "2026-08-05T12:00:00Z",
    }
    if runtime_observation is not None:
        payload["runtimeObservation"] = runtime_observation
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return PushDelivery(payload, f"sha256={signature}")


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize(
    "crash_boundary",
    ("I4", "I5", "I6", "I7", "I9", "I10", "COMMAND_COMPLETED"),
)
def test_incremental_redrive_reuses_checkpoints_and_matches_clean_execution(
    tmp_path: Path, crash_boundary: str
) -> None:
    from lineage_api.dependencies import build_services

    crashing = build_services(settings(tmp_path / "crashing"))
    crashing.reset()

    def crash(stage_id: str) -> None:
        if stage_id == crash_boundary:
            raise InjectedWorkflowCrash(f"crash after {stage_id}")

    crashing.orchestration.set_fault_injector(crash)
    with pytest.raises(InjectedWorkflowCrash, match=crash_boundary):
        crashing.orchestration.process_push(delivery())

    crashing.orchestration.set_fault_injector(None)
    recovered_results = crashing.orchestration.worker_drain(max_messages=5)
    assert len(recovered_results) == 1
    recovered = recovered_results[0]

    clean = build_services(settings(tmp_path / "clean"))
    clean.reset()
    clean_result = clean.orchestration.process_push(delivery())

    assert canonical(recovered["coverageManifest"]) == canonical(
        clean_result["coverageManifest"]
    )
    assert canonical(recovered["evidenceManifest"]) == canonical(
        clean_result["evidenceManifest"]
    )
    with crashing.database.connection() as connection:
        command = connection.execute("SELECT status, attempt FROM commands").fetchone()
        proposal_versions = connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
        coverage_versions = connection.execute(
            "SELECT COUNT(*) FROM coverage_manifests"
        ).fetchone()[0]
        stage_ids = {
            row[0].rsplit("/", 1)[-1].upper()
            for row in connection.execute(
                """
                SELECT object_key FROM evidence_objects
                WHERE kind = 'manifest' AND object_key LIKE 'workflow/%'
                """
            ).fetchall()
        }
    assert command["status"] == "COMPLETED"
    assert command["attempt"] in {1, 2}
    assert proposal_versions == 1
    assert coverage_versions == 1
    assert stage_ids == {f"I{index}" for index in range(1, 11)}


def test_runtime_observation_is_optional_and_requires_exact_artifact_binding(
    tmp_path: Path,
) -> None:
    from lineage_api.dependencies import build_services

    no_runtime = build_services(settings(tmp_path / "none"))
    no_runtime.reset()
    absent = no_runtime.orchestration.process_push(delivery())

    mismatched = build_services(settings(tmp_path / "mismatched"))
    mismatched.reset()
    rejected = mismatched.orchestration.process_push(
        delivery(
            runtime_observation={
                "schemaVersion": "1.0.0",
                "artifactDigest": "different-digest",
                "complete": True,
                "scope": "ELEMENT",
                "assertions": [],
            }
        )
    )

    assert absent["evidenceManifest"]["runtime"] == {"status": "NOT_PROVIDED"}
    assert rejected["evidenceManifest"]["runtime"] == {
        "status": "REJECTED",
        "reason": "ARTIFACT_MISMATCH",
    }
