from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import pytest

from lineage_api.application.sca_execution import ScaStageUseCase
from lineage_api.application.stage_execution import StageExecutionContext
from lineage_api.application.stage_handlers import production_stage_use_cases


PROJECT_ROOT = Path(__file__).parents[4]
REPOSITORY_ROOT = PROJECT_ROOT / "fixtures" / "repositories" / "payments-pipeline"
CATALOG = PROJECT_ROOT / "fixtures" / "catalog" / "catalog-snapshot-v1.json"


class Artifacts:
    def __init__(self) -> None:
        import json

        self.documents = {"catalog/catalog-v1.json": json.loads(CATALOG.read_text())}
        self.writes: list[tuple[str, str, object, str]] = []
        self.references: dict[str, dict[str, object]] = {}

    def get(self, reference: object) -> object:
        return deepcopy(self.documents[reference["key"]])

    def put(self, kind: str, key: str, body: object, version: str) -> dict[str, object]:
        self.writes.append((kind, key, deepcopy(body), version))
        return deepcopy(
            self.references.setdefault(
                key,
                {
                    "bucket": "evidence",
                    "key": key,
                    "versionId": f"version-{len(self.references) + 1}",
                    "sha256": f"{len(self.references) + 1:064x}",
                    "sizeBytes": 2048,
                },
            )
        )


class Sources:
    def __init__(self) -> None:
        self.references: list[object] = []

    @contextmanager
    def materialize(self, reference: object):
        self.references.append(deepcopy(reference))
        yield REPOSITORY_ROOT


def _reference(key: str, version: str, digit: str) -> dict[str, object]:
    return {
        "bucket": "evidence",
        "key": key,
        "versionId": version,
        "sha256": digit * 64,
        "sizeBytes": 4096,
    }


def _work_unit() -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "artifactType": "sca-work-unit",
        "workUnitId": "cmd-b5-B5-00000",
        "repository": "payments-pipeline",
        "artifactDigest": "demo-digest-v2",
        "environment": "staging",
        "platform": "snowflake",
        "system": "payments",
        "pack": "python-ast",
        "paths": ["pipeline.py"],
        "repositorySource": _reference("sources/payments-v2.zip", "source-v2", "d"),
        "catalogSnapshotRef": _reference("catalog/catalog-v1.json", "catalog-v1", "c"),
        "catalogSnapshotId": "catalog-demo-v1",
        "resolverVersion": "1.0.0",
        "rulesetVersion": "python-demo-v1",
        "activeBaseVersion": "graph-v1",
        "activeBaseFence": 7,
        "acceptedAt": "2026-08-08T15:00:00Z",
        "runtimeManifestRefs": [],
        "coverage": {
            "expectedScope": ["pipeline.py"],
            "completedScope": ["pipeline.py"],
            "reusedScope": [],
            "skippedScope": [],
            "unsupportedScope": [],
            "quarantinedScope": [],
            "failedScope": [],
        },
        "correlationId": "corr-sca",
    }


def _context(workflow: str, stage: str, name: str) -> StageExecutionContext:
    return StageExecutionContext(
        target="sca",
        workflow_kind=workflow,
        workflow_version="1.0.0",
        stage_id=stage,
        stage_name=name,
        command_id=f"cmd-{stage.lower()}",
        correlation_id="corr-sca",
        input_reference=_reference("work/sca.json", "work-v1", "a"),
    )


@pytest.mark.parametrize(
    ("workflow", "stage", "name"),
    (
        ("BASELINE", "B5", "RUN_BOUNDED_STATIC_ANALYSIS"),
        ("INCREMENTAL", "I5", "PROVE_NO_IMPACT_OR_EXTRACT_EVIDENCE"),
        ("NIGHTLY", "N2", "SAMPLE_PUBLISHED_LINEAGE_AGAINST_CLEAN_ANALYSIS"),
    ),
)
def test_sca_stages_materialize_pinned_source_and_emit_deterministic_assertion_refs(
    workflow: str, stage: str, name: str
) -> None:
    artifacts = Artifacts()
    sources = Sources()
    use_case = ScaStageUseCase(artifacts, sources)

    first = use_case.execute(_work_unit(), _context(workflow, stage, name))
    replay = use_case.execute(_work_unit(), _context(workflow, stage, name))

    assert replay == first
    assert first.artifact_kind == "sca-stage-result"
    assert first.document["context"]["assertionRefs"][0]["versionId"] == "version-2"
    assert first.document["context"]["residueRefs"][0]["versionId"] == "version-3"
    assertion_set = artifacts.writes[1][2]
    assert len(assertion_set["assertions"]) == 3
    assert all(item["mechanism"] == "SCA" for item in assertion_set["assertions"])
    assert all(item["sessionComplete"] is True for item in assertion_set["assertions"])
    assert all(set(item["citation"]) == {"file", "line", "astPath"} for item in assertion_set["assertions"])
    raw_evidence = artifacts.writes[0][2]
    assert raw_evidence["stats"] == {
        "filesAnalyzed": 1,
        "edgesEmitted": 3,
        "residueCount": 1,
        "quarantinedCount": 0,
    }
    assert sources.references == [_work_unit()["repositorySource"], _work_unit()["repositorySource"]]


def test_sca_rejects_path_escape_before_materializing_source() -> None:
    artifacts = Artifacts()
    sources = Sources()
    work = _work_unit()
    work["paths"] = ["../secret.py"]

    with pytest.raises(ValueError, match="repository path"):
        ScaStageUseCase(artifacts, sources).execute(
            work, _context("BASELINE", "B5", "RUN_BOUNDED_STATIC_ANALYSIS")
        )

    assert sources.references == []
    assert artifacts.writes == []


@pytest.mark.parametrize(
    "mutation",
    (
        lambda work: work.update(activeBaseFence=True),
        lambda work: work.update(runtimeManifestRefs="not-an-array"),
        lambda work: work.update(coverage="not-an-object"),
        lambda work: work["catalogSnapshotRef"].update(sizeBytes=60_000_000),
        lambda work: work["repositorySource"].update(sizeBytes=300_000_000),
        lambda work: work["coverage"].update(unexpectedScope=[]),
    ),
)
def test_sca_rejects_invalid_control_fields_before_any_io(mutation) -> None:
    artifacts = Artifacts()
    sources = Sources()
    work = _work_unit()
    mutation(work)

    with pytest.raises(ValueError):
        ScaStageUseCase(artifacts, sources).execute(
            work, _context("BASELINE", "B5", "RUN_BOUNDED_STATIC_ANALYSIS")
        )

    assert sources.references == []
    assert artifacts.writes == []


def test_production_dispatcher_maps_all_three_sca_states_only_with_source_port() -> None:
    artifacts = Artifacts()
    sources = Sources()

    without_sources = production_stage_use_cases(artifacts)
    with_sources = production_stage_use_cases(artifacts, sources=sources)

    for identity in (("BASELINE", "B5"), ("INCREMENTAL", "I5"), ("NIGHTLY", "N2")):
        assert identity not in without_sources
        assert isinstance(with_sources[identity], ScaStageUseCase)
