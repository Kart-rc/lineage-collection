from __future__ import annotations

from copy import deepcopy

import pytest

from lineage_api.application.sca_aggregation import BaselineScaAggregationUseCase


def _reference(key: str, digit: str) -> dict[str, object]:
    return {
        "bucket": "evidence",
        "key": key,
        "versionId": f"{digit}-v1",
        "sha256": digit * 64,
        "sizeBytes": 1024,
    }


PLAN_REF = _reference("commands/cmd-baseline/coverage/B4.json", "1")
WORK_REFS = [
    _reference("commands/cmd-baseline/work-units/00000.json", "2"),
    _reference("commands/cmd-baseline/work-units/00001.json", "3"),
]
RESULT_REFS = [
    _reference("commands/cmd-baseline/stages/B5/idem-B5-0.json", "4"),
    _reference("commands/cmd-baseline/stages/B5/idem-B5-1.json", "5"),
]
INVENTORY_REF = _reference("commands/cmd-baseline/stages/B4/idem-B4.json", "6")
MANIFEST_REF = _reference("workflow-results/cmd-baseline/B5/map-1/manifest.json", "7")
SHARD_REF = _reference("workflow-results/cmd-baseline/B5/map-1/SUCCEEDED_0.json", "8")


def _common_context() -> dict[str, object]:
    return {
        "repository": "payments-pipeline",
        "artifactDigest": "sha256:source-v1",
        "environment": "staging",
        "platform": "snowflake",
        "system": "payments",
        "repositorySource": _reference("sources/payments.zip", "a"),
        "catalogSnapshotRef": _reference("catalog/catalog-v1.json", "b"),
        "catalogSnapshotId": "catalog-demo-v1",
        "resolverVersion": "1.0.0",
        "rulesetVersion": "python-demo-v1",
        "activeBaseVersion": "graph-v1",
        "activeBaseFence": 7,
        "acceptedAt": "2026-08-08T15:00:00Z",
        "runtimeManifestRefs": [],
    }


def _work(index: int, path: str) -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "artifactType": "sca-work-unit",
        "workUnitId": f"cmd-baseline-B5-{index:05d}",
        "paths": [path],
        "coveragePlan": PLAN_REF,
        "correlationId": "corr-baseline",
    }


def _sca(index: int, path: str) -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "artifactType": "sca-stage-result",
        "workflowKind": "BASELINE",
        "workflowVersion": "1.0.0",
        "stageId": "B5",
        "commandId": "cmd-baseline",
        "correlationId": "corr-baseline",
        "source": WORK_REFS[index],
        "workUnitId": f"cmd-baseline-B5-{index:05d}",
        "context": {
            **_common_context(),
            "assertionRefs": [_reference(f"assertions/{index}.json", str(index + 1))],
            "residueRefs": [_reference(f"residue/{index}.json", str(index + 3))],
            "coverage": {
                "expectedScope": [path],
                "completedScope": [path],
                "reusedScope": [],
                "skippedScope": [],
                "unsupportedScope": [],
                "quarantinedScope": [],
                "failedScope": [],
            },
        },
    }


class Artifacts:
    def __init__(self) -> None:
        self.documents = {
            INVENTORY_REF["key"]: {
                "schemaVersion": "1.0.0",
                "artifactType": "work-inventory",
                "workflowKind": "BASELINE",
                "workflowVersion": "1.0.0",
                "stageId": "B4",
                "commandId": "cmd-baseline",
                "correlationId": "corr-baseline",
                "context": _common_context(),
                "coveragePlanRef": PLAN_REF,
                "workUnitRefs": WORK_REFS,
            },
            PLAN_REF["key"]: {
                "schemaVersion": "1.0.0",
                "artifactType": "coverage-plan",
                "commandId": "cmd-baseline",
                "correlationId": "corr-baseline",
                "coverage": {
                    "expectedScope": ["README.md", "a.py", "b.py", "job.scala"],
                    "recomputedScope": ["a.py", "b.py"],
                    "skippedScope": ["README.md"],
                    "unsupportedScope": ["job.scala"],
                },
            },
            WORK_REFS[0]["key"]: _work(0, "a.py"),
            WORK_REFS[1]["key"]: _work(1, "b.py"),
            RESULT_REFS[0]["key"]: _sca(0, "a.py"),
            RESULT_REFS[1]["key"]: _sca(1, "b.py"),
        }

    def get(self, reference: object) -> object:
        assert isinstance(reference, dict)
        return deepcopy(self.documents[reference["key"]])

    def put(self, *_args: object) -> object:
        raise AssertionError("the executor, not the aggregation use case, persists its result")


class Results:
    def __init__(self) -> None:
        self.child_results = [
            {"outcome": "SUCCEEDED", "output": RESULT_REFS[0]},
            {"outcome": "SUCCEEDED", "output": RESULT_REFS[1]},
        ]

    def read_succeeded(
        self, bucket: str, manifest_key: str, map_run_arn: str, command_id: str
    ) -> dict[str, object]:
        assert bucket == "evidence"
        assert manifest_key == MANIFEST_REF["key"]
        assert map_run_arn.endswith("Map:map-1")
        assert command_id == "cmd-baseline"
        return {
            "manifestRef": MANIFEST_REF,
            "shardRefs": [SHARD_REF],
            "childResults": deepcopy(self.child_results),
        }


def _event() -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "operation": "BASELINE_SCA_AGGREGATE",
        "workflowKind": "BASELINE",
        "workflowVersion": "1.0.0",
        "commandId": "cmd-baseline",
        "correlationId": "corr-baseline",
        "causationId": "cause-baseline",
        "idempotencyKey": "idem-baseline:B5A",
        "determinantDigest": "d" * 64,
        "workInventory": INVENTORY_REF,
        "mapResult": {
            "MapRunArn": "arn:aws:states:us-east-1:111111111111:mapRun:baseline/Map:map-1",
            "ResultWriterDetails": {
                "Bucket": "evidence",
                "Key": MANIFEST_REF["key"],
            },
        },
    }


def test_baseline_sca_aggregation_proves_complete_static_evidence_and_replays() -> None:
    artifacts = Artifacts()
    results = Results()
    use_case = BaselineScaAggregationUseCase(artifacts, results)

    first = use_case.execute(_event())
    replay = use_case.execute(_event())

    assert replay == first
    assert first.artifact_kind == "sca-batch-result"
    assert first.document["workUnitIds"] == [
        "cmd-baseline-B5-00000",
        "cmd-baseline-B5-00001",
    ]
    assert first.document["context"]["coverage"] == {
        "expectedScope": ["README.md", "a.py", "b.py", "job.scala"],
        "completedScope": ["a.py", "b.py"],
        "reusedScope": [],
        "skippedScope": ["README.md"],
        "unsupportedScope": ["job.scala"],
        "quarantinedScope": [],
        "failedScope": [],
        "state": "INCOMPLETE",
    }
    assert len(first.document["context"]["assertionRefs"]) == 2
    assert first.document["mapExport"] == {
        "manifestRef": MANIFEST_REF,
        "shardRefs": [SHARD_REF],
    }
    assert "target" not in first.document
    assert "inputDocument" not in first.document


def test_baseline_sca_aggregation_rejects_missing_child_output() -> None:
    artifacts = Artifacts()
    results = Results()
    results.child_results.pop()

    with pytest.raises(ValueError, match="exactly one successful result"):
        BaselineScaAggregationUseCase(artifacts, results).execute(_event())


def test_baseline_sca_aggregation_rejects_an_inventory_gap_or_bucket_escape() -> None:
    artifacts = Artifacts()
    plan = artifacts.documents[PLAN_REF["key"]]
    assert isinstance(plan, dict)
    coverage = plan["coverage"]
    assert isinstance(coverage, dict)
    coverage["expectedScope"] = ["README.md", "a.py", "b.py", "c.py", "job.scala"]
    coverage["recomputedScope"] = ["a.py", "b.py", "c.py"]

    with pytest.raises(ValueError, match="work inventory does not match"):
        BaselineScaAggregationUseCase(artifacts, Results()).execute(_event())

    artifacts = Artifacts()
    inventory = artifacts.documents[INVENTORY_REF["key"]]
    assert isinstance(inventory, dict)
    escaped = dict(PLAN_REF, bucket="another-bucket")
    inventory["coveragePlanRef"] = escaped

    with pytest.raises(ValueError, match="command evidence bucket"):
        BaselineScaAggregationUseCase(artifacts, Results()).execute(_event())


def test_baseline_sca_aggregation_binds_result_determinants_to_inventory() -> None:
    artifacts = Artifacts()
    for result_ref in RESULT_REFS:
        result = artifacts.documents[result_ref["key"]]
        assert isinstance(result, dict)
        context = result["context"]
        assert isinstance(context, dict)
        context["repository"] = "drifted-repository"

    with pytest.raises(ValueError, match="differ from work inventory"):
        BaselineScaAggregationUseCase(artifacts, Results()).execute(_event())
