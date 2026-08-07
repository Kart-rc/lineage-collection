from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from lineage_api.config import Settings
from lineage_api.services.intake import PushDelivery
from lineage_api.testing.evidence import AcceptanceEvidenceWriter
from lineage_api.testing.faults import FailpointTriggered, NamedFailpoints


PROJECT_ROOT = Path(__file__).parents[2]
SECRET = "acceptance-replay-secret"


def _settings(root: Path) -> Settings:
    return Settings(
        project_root=PROJECT_ROOT,
        data_directory=root,
        fixture_directory=PROJECT_ROOT / "fixtures",
        database_path=root / "lineage.db",
        object_directory=root / "objects",
        webhook_secret=SECRET,
    )


def _delivery() -> PushDelivery:
    payload = {
        "eventId": "acceptance-incremental-replay",
        "eventType": "repo.push",
        "repo": "payments-pipeline",
        "digest": "acceptance-digest-v1",
        "env": "staging",
        "system": "payments",
        "changedFiles": ["pipeline.py"],
        "receivedAt": "2026-08-06T12:00:00Z",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), encoded, hashlib.sha256).hexdigest()
    return PushDelivery(payload, f"sha256={signature}")


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def test_named_fault_is_one_shot_and_must_be_observed() -> None:
    faults = NamedFailpoints.once("AFTER_DURABLE_WRITE")

    with pytest.raises(FailpointTriggered, match="AFTER_DURABLE_WRITE"):
        faults.hit("AFTER_DURABLE_WRITE")
    faults.hit("AFTER_DURABLE_WRITE")

    faults.assert_all_triggered()
    assert faults.observed == {"AFTER_DURABLE_WRITE": 2}


def test_incremental_redrive_matches_clean_result_and_emits_manifest(
    tmp_path: Path,
    acceptance_writer: AcceptanceEvidenceWriter,
) -> None:
    from lineage_api.dependencies import build_services

    with acceptance_writer.scenario(
        build_id="B05",
        acceptance_id="B05-AC-001",
        scenario_id="incremental-i5-redrive",
        environment="hermetic-local",
        fault_point="I5",
        thresholds={"duplicateEffects": 0, "checksumDivergence": 0},
    ) as evidence:
        crashing = build_services(_settings(tmp_path / "crashing"))
        crashing.reset()
        faults = NamedFailpoints.once("I5")
        crashing.orchestration.set_fault_injector(faults.hit)
        with pytest.raises(FailpointTriggered, match="I5"):
            crashing.orchestration.process_push(_delivery())
        faults.assert_all_triggered()

        crashing.orchestration.set_fault_injector(None)
        recovered_results = crashing.orchestration.worker_drain(max_messages=5)
        assert len(recovered_results) == 1
        recovered = recovered_results[0]

        clean = build_services(_settings(tmp_path / "clean"))
        clean.reset()
        expected = clean.orchestration.process_push(_delivery())
        recovered_checksum = _digest(
            {
                "coverage": recovered["coverageManifest"],
                "evidence": recovered["evidenceManifest"],
            }
        )
        expected_checksum = _digest(
            {
                "coverage": expected["coverageManifest"],
                "evidence": expected["evidenceManifest"],
            }
        )
        assert recovered_checksum == expected_checksum
        with crashing.database.connection() as connection:
            proposal_count = int(connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0])
            stage_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM evidence_objects WHERE kind = 'manifest'"
                ).fetchone()[0]
            )
        assert proposal_count == 1
        assert stage_count == 10
        evidence.metric("recoveredCommands", 1)
        evidence.metric("proposalCount", proposal_count)
        evidence.metric("stageCount", stage_count)
        evidence.metric("checksumDivergence", 0)
        evidence.checksum("recovered", recovered_checksum)
        evidence.checksum("clean", expected_checksum)

    assert evidence.manifest is not None
    assert evidence.manifest["outcome"] == "PASS"
    assert evidence.manifest["artifactDigest"] == acceptance_writer.artifact_digest

