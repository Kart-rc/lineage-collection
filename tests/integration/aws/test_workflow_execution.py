from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

from lineage_api.application.sca_aggregation import BaselineScaAggregationUseCase
from lineage_api.application.stage_execution import StageExecutionContext
from lineage_api.application.stage_handlers import production_stage_use_cases
from lineage_api.application.stage_ownership import owner_for_stage
from lineage_api.application.workflows.definitions import WORKFLOWS


ROOT = Path(__file__).parents[3]
INPUTS = ROOT / "fixtures" / "aws" / "workflow-inputs"
REPOSITORY = ROOT / "fixtures" / "repositories" / "payments-pipeline"
CATALOG = ROOT / "fixtures" / "catalog" / "catalog-snapshot-v1.json"
EXPECTED_KINDS = {
    "BASELINE": [
        "baseline-intent",
        "baseline-pins",
        "classification-decision",
        "work-inventory",
        "sca-stage-result",
        "runtime-validation",
        "residue-decision",
        "consolidation-result",
        "proposal-decision",
        "publication-decision",
    ],
    "INCREMENTAL": [
        "incremental-base",
        "incremental-scope",
        "coverage-plan",
        "sca-work-unit",
        "sca-stage-result",
        "runtime-validation",
        "consolidation-result",
        "incremental-recheck",
        "proposal-decision",
        "publication-decision",
    ],
}


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class MemoryArtifacts:
    def __init__(self) -> None:
        self.documents: dict[str, object] = {}
        self.references: dict[str, dict[str, object]] = {}

    def register(self, reference: dict[str, object], document: object) -> None:
        key = str(reference["key"])
        self.documents[key] = deepcopy(document)
        self.references[key] = deepcopy(reference)

    def put(
        self, kind: str, key: str, body: object, schema_version: str
    ) -> dict[str, object]:
        encoded = _canonical(body)
        digest = hashlib.sha256(encoded).hexdigest()
        reference: dict[str, object] = {
            "bucket": "evidence",
            "key": key,
            "versionId": f"v-{digest[:24]}",
            "sha256": digest,
            "sizeBytes": len(encoded),
        }
        if key in self.documents:
            if self.documents[key] != body or self.references[key] != reference:
                raise AssertionError(f"immutable artifact conflict for {kind}/{schema_version}/{key}")
        else:
            self.documents[key] = deepcopy(body)
            self.references[key] = deepcopy(reference)
        return deepcopy(reference)

    def get(self, reference: object) -> object:
        assert isinstance(reference, dict)
        key = str(reference["key"])
        assert reference == self.references[key]
        return deepcopy(self.documents[key])


class Sources:
    @contextmanager
    def materialize(self, _reference: object):
        yield REPOSITORY


class Control:
    def __init__(self) -> None:
        self.pointer = {
            "environment": "staging",
            "graphVersion": "graph-v1",
            "graphChecksum": "b" * 64,
            "fence": 7,
            "correlationId": "fixture-publication",
        }
        self.proposals: dict[str, dict[str, Any]] = {}

    def active_pointer(self, _environment: str) -> dict[str, Any]:
        return deepcopy(self.pointer)

    def put_proposal(self, proposal: dict[str, Any]) -> dict[str, Any]:
        proposal_id = proposal["proposalId"]
        prior = self.proposals.get(proposal_id)
        if prior is not None and prior != proposal:
            raise AssertionError("proposal replay drifted")
        self.proposals[proposal_id] = deepcopy(proposal)
        return deepcopy(proposal)


class NoProjection:
    def __getattr__(self, name: str):
        raise AssertionError(f"projection operation {name} is forbidden before approval")


class MapResults:
    def __init__(
        self,
        manifest: dict[str, object],
        shard: dict[str, object],
        children: list[dict[str, object]],
    ) -> None:
        self.manifest = manifest
        self.shard = shard
        self.children = children

    def read_succeeded(
        self, bucket: str, manifest_key: str, map_run_arn: str, command_id: str
    ) -> dict[str, object]:
        assert bucket == "evidence"
        assert manifest_key == self.manifest["key"]
        assert map_run_arn.endswith(":fixture-map-run")
        assert command_id == "cmd-baseline-fixture"
        return {
            "manifestRef": deepcopy(self.manifest),
            "shardRefs": [deepcopy(self.shard)],
            "childResults": deepcopy(self.children),
        }


def _context(
    workflow_kind: str,
    stage_id: str,
    command_id: str,
    correlation_id: str,
    input_reference: dict[str, object],
) -> StageExecutionContext:
    workflow = WORKFLOWS[workflow_kind]
    stage = next(item for item in workflow.stages if item.stage_id == stage_id)
    return StageExecutionContext(
        target=owner_for_stage(workflow_kind, stage_id).value,
        workflow_kind=workflow_kind,
        workflow_version=workflow.version,
        stage_id=stage_id,
        stage_name=stage.name,
        command_id=command_id,
        correlation_id=correlation_id,
        input_reference=input_reference,
    )


def _checkpoint(
    artifacts: MemoryArtifacts,
    workflow_kind: str,
    command_id: str,
    stage_id: str,
    result: object,
) -> dict[str, object]:
    artifact_kind = result.artifact_kind
    assert artifact_kind not in {"stage-result", "generic-result", "input-echo"}
    assert "target" not in result.document
    assert "inputDocument" not in result.document
    reference = artifacts.put(
        artifact_kind,
        f"commands/{command_id}/stages/{stage_id}/fixture.json",
        result.document,
        result.schema_version,
    )
    assert artifacts.get(reference) == result.document
    assert reference["sha256"] == hashlib.sha256(_canonical(result.document)).hexdigest()
    assert artifacts.put(
        artifact_kind,
        str(reference["key"]),
        result.document,
        result.schema_version,
    ) == reference
    return reference


def _run(workflow_kind: str) -> tuple[list[str], dict[str, Any]]:
    command_id = f"cmd-{workflow_kind.lower()}-fixture"
    correlation_id = f"corr-{workflow_kind.lower()}-fixture"
    artifacts = MemoryArtifacts()
    fixture = json.loads((INPUTS / f"{workflow_kind.lower()}.json").read_text())
    fixture["context"]["acceptedAt"] = "2026-08-08T15:00:00Z"
    catalog_ref = fixture["context"]["catalogSnapshotRef"]
    artifacts.register(catalog_ref, json.loads(CATALOG.read_text()))
    source_ref = fixture["context"]["repositorySource"]
    artifacts.register(source_ref, {"fixture": "materialized by Sources"})
    input_ref = artifacts.put(
        "workflow-input",
        f"commands/{command_id}/input.json",
        fixture,
        "1.0.0",
    )
    control = Control()
    registry = production_stage_use_cases(
        artifacts,
        control,
        packages=artifacts,
        publication_control=control,
        projection=NoProjection(),
        sources=Sources(),
    )
    kinds: list[str] = []
    document: object = fixture
    for stage in WORKFLOWS[workflow_kind].stages:
        if workflow_kind == "BASELINE" and stage.stage_id == "B5":
            kinds.append("sca-stage-result")
            continue
        context = _context(
            workflow_kind, stage.stage_id, command_id, correlation_id, input_ref
        )
        use_case = registry[(workflow_kind, stage.stage_id)]
        first = use_case.execute(deepcopy(document), context)
        replay = use_case.execute(deepcopy(document), context)
        assert replay == first
        kinds.append(first.artifact_kind)
        stage_ref = _checkpoint(
            artifacts, workflow_kind, command_id, stage.stage_id, first
        )

        if workflow_kind == "BASELINE" and stage.stage_id == "B4":
            inventory = first.document
            children: list[dict[str, object]] = []
            for work_ref in inventory["workUnitRefs"]:
                work = artifacts.get(work_ref)
                sca_context = _context(
                    workflow_kind, "B5", command_id, correlation_id, work_ref
                )
                sca = registry[(workflow_kind, "B5")]
                sca_result = sca.execute(work, sca_context)
                assert sca.execute(work, sca_context) == sca_result
                sca_ref = _checkpoint(
                    artifacts, workflow_kind, command_id, "B5", sca_result
                )
                children.append({"outcome": "SUCCEEDED", "output": sca_ref})
            manifest = artifacts.put(
                "map-manifest",
                f"workflow-results/{command_id}/B5/fixture-map-run/manifest.json",
                {"MapRunArn": "fixture-map-run"},
                "1.0.0",
            )
            shard = artifacts.put(
                "map-result-shard",
                f"workflow-results/{command_id}/B5/fixture-map-run/SUCCEEDED_0.json",
                children,
                "1.0.0",
            )
            aggregate = BaselineScaAggregationUseCase(
                artifacts, MapResults(manifest, shard, children)
            )
            aggregate_event = {
                "schemaVersion": "1.0.0",
                "operation": "BASELINE_SCA_AGGREGATE",
                "workflowKind": "BASELINE",
                "workflowVersion": "1.0.0",
                "commandId": command_id,
                "correlationId": correlation_id,
                "causationId": "fixture-cause",
                "idempotencyKey": "fixture:B5A",
                "determinantDigest": "f" * 64,
                "workInventory": stage_ref,
                "mapResult": {
                    "MapRunArn": (
                        "arn:aws:states:us-east-1:111111111111:"
                        "mapRun:fixture/Map:fixture-map-run"
                    ),
                    "ResultWriterDetails": {
                        "Bucket": "evidence",
                        "Key": manifest["key"],
                    },
                },
            }
            aggregate_result = aggregate.execute(aggregate_event)
            assert aggregate.execute(aggregate_event) == aggregate_result
            input_ref = _checkpoint(
                artifacts, workflow_kind, command_id, "B5A", aggregate_result
            )
            document = aggregate_result.document
            continue

        input_ref = stage_ref
        document = first.document
    assert isinstance(document, dict)
    return kinds, document


def test_baseline_and_incremental_execute_real_immutable_replayable_chains() -> None:
    baseline_kinds, baseline = _run("BASELINE")
    incremental_kinds, incremental = _run("INCREMENTAL")

    assert baseline_kinds == EXPECTED_KINDS["BASELINE"]
    assert incremental_kinds == EXPECTED_KINDS["INCREMENTAL"]
    assert baseline["terminalOutcome"] == "AWAITING_APPROVAL"
    assert incremental["terminalOutcome"] == "AWAITING_APPROVAL"
