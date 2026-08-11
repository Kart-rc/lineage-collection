from __future__ import annotations

import json
import hashlib
import time
from pathlib import Path

import pytest

from lineage_api.testing.evidence import (
    AcceptanceEvidenceWriter,
    evidence_exit_code,
    load_manifests,
)


def test_local_smoke_has_a_bounded_threshold_and_schema_valid_evidence(
    acceptance_writer: AcceptanceEvidenceWriter,
) -> None:
    samples = []
    with acceptance_writer.scenario(
        build_id="B15",
        acceptance_id="B15-AC-001",
        scenario_id="local-evidence-writer-smoke",
        environment="hermetic-local",
        thresholds={"p95Milliseconds": 25},
    ) as evidence:
        for index in range(200):
            started = time.perf_counter()
            json.dumps({"index": index, "payload": "lineage"}, sort_keys=True)
            samples.append((time.perf_counter() - started) * 1000)
        p95 = sorted(samples)[int(len(samples) * 0.95) - 1]
        assert p95 < 25
        evidence.metric("sampleCount", len(samples))
        evidence.metric("p95Milliseconds", round(p95, 6))
        corpus = json.dumps(
            {"sampleCount": len(samples), "payload": "lineage"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        evidence.checksum("sampleCorpus", f"sha256:{hashlib.sha256(corpus).hexdigest()}")

    manifests = load_manifests(acceptance_writer.output_root)
    assert any(manifest["acceptanceId"] == "B15-AC-001" for manifest in manifests)
    assert evidence.manifest is not None
    reference = evidence.manifest["evidenceRefs"][-1]
    relative, claimed_digest = reference.removeprefix("acceptance:").rsplit("#sha256:", 1)
    evidence_bytes = (acceptance_writer.output_root / relative).read_bytes()
    assert hashlib.sha256(evidence_bytes).hexdigest() == claimed_digest
    assert evidence_exit_code(acceptance_writer.output_root) == 0


@pytest.mark.parametrize(
    ("build_id", "acceptance_id", "scenario", "metrics"),
    [
        ("B04", "B04-AC-001", "aws-sustained-and-burst", {"ratePerSecond": 100, "burst": 10_000}),
        ("B05", "B05-AC-001", "aws-baseline-window", {"repositories": 10_000, "windowHours": 12}),
        ("B15", "B15-AC-001", "aws-availability-window", {"tier1MonthlyPercent": 99.9}),
        ("B16", "B16-AC-001", "aws-warm-standby-dr", {"rpoMinutes": 15, "rtoHours": 4}),
    ],
)
def test_aws_only_nfrs_are_never_reported_as_local_passes(
    acceptance_writer: AcceptanceEvidenceWriter,
    build_id: str,
    acceptance_id: str,
    scenario: str,
    metrics: dict[str, float],
) -> None:
    manifest = acceptance_writer.record(
        build_id=build_id,
        acceptance_id=acceptance_id,
        scenario_id=scenario,
        outcome="AWS_REQUIRED",
        environment="aws-ephemeral-or-dr",
        metrics=metrics,
        thresholds=metrics,
    )

    assert manifest["outcome"] == "AWS_REQUIRED"


def test_fail_evidence_forces_a_nonzero_runner_status(tmp_path: Path) -> None:
    writer = AcceptanceEvidenceWriter(tmp_path, artifact_digest="sha256:" + "a" * 64)
    writer.record(
        build_id="B15",
        acceptance_id="B15-AC-001",
        scenario_id="forced-failure",
        outcome="FAIL",
        environment="hermetic-local",
        metrics={"observed": 2},
        thresholds={"maximum": 1},
    )

    assert evidence_exit_code(tmp_path) == 1


def test_writer_rejects_non_exact_artifact_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="sha256"):
        AcceptanceEvidenceWriter(tmp_path, artifact_digest="working-tree")


def test_writer_rejects_path_shaped_build_and_acceptance_ids(tmp_path: Path) -> None:
    writer = AcceptanceEvidenceWriter(tmp_path, artifact_digest="sha256:" + "a" * 64)

    with pytest.raises(ValueError, match="build ID"):
        writer.record(
            build_id="../../B15",
            acceptance_id="../../B15-AC-001",
            scenario_id="path-traversal",
            outcome="FAIL",
            environment="hermetic-local",
        )
