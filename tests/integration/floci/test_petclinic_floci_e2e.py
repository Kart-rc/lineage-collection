"""Real end-to-end run of Baseline / Incremental / Deployment / PR-gate impact for
spring-petclinic against a floci-emulated AWS data plane.

Every AWS side effect is real emulator state: DynamoDB stage leases, run ledger,
fenced pointer transactions and outbox rows; versioned immutable S3 evidence and
package artifacts; SQS FIFO lane intake; Kinesis runtime observations; Step
Functions ``StartExecution`` name-idempotency at intake. Stage logic is the
production dispatcher with the Java seams from ``java_stage_overrides`` registered
over the python-only defaults. The graph projection runs on SQLite because floci's
Neptune is a Gremlin server without the ``neptunedata`` openCypher API (probed).

Gate: set ALLOW_LINEAGE_FLOCI_E2E=1 and have floci provisioned
(scripts/floci/up.sh && scripts/floci/provision_floci.py) plus a spring-petclinic
checkout at /tmp/spring-petclinic pinned to 88e37c15.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = ROOT / "scripts" / "floci" / "floci.env"
CHECKOUT = Path(os.environ.get("LINEAGE_PETCLINIC_CHECKOUT", "/tmp/spring-petclinic"))
PINNED_REVISION = "88e37c15cf6fc8490b01bc3e8e2c800cec1ac272"
ENDPOINT = "http://localhost:4566"
ACCEPTED_AT = "2026-08-14T12:00:00Z"
JAVA_HOME = "/opt/homebrew/opt/openjdk@21"

ORACLE_EDGES = 23
ORACLE_READS = 18
ORACLE_WRITES = 5
ORACLE_ELEMENT_HIGH = 8

pytestmark = pytest.mark.skipif(
    os.environ.get("ALLOW_LINEAGE_FLOCI_E2E") != "1",
    reason="floci E2E requires ALLOW_LINEAGE_FLOCI_E2E=1",
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


class E2ESession:
    def __init__(self) -> None:
        if ENV_FILE.exists():
            for line in ENV_FILE.read_text().splitlines():
                if line and "=" in line and not line.startswith("#"):
                    name, _, value = line.partition("=")
                    os.environ.setdefault(name.strip(), value.split("#", 1)[0].strip())
        os.environ.setdefault("LINEAGE_JAVA_HOME", JAVA_HOME)

        import boto3
        from botocore.config import Config

        from lineage_api.application.sca_aggregation import BaselineScaAggregationUseCase
        from lineage_api.application.stage_execution import StageDispatcher
        from lineage_api.application.stage_handlers import production_stage_use_cases
        from lineage_api.infrastructure.aws.composition import (
            AwsScaAggregationExecutor,
            AwsStageExecutor,
            StepFunctionsWorkflowStarter,
        )
        from lineage_api.infrastructure.aws.config import AwsRuntimeConfig
        from lineage_api.infrastructure.aws.dynamodb_control import DynamoDbControlAdapter
        from lineage_api.infrastructure.aws.kinesis_runtime import KinesisRuntimeAdapter
        from lineage_api.infrastructure.aws.query_projection import AwsProductQueryProjection
        from lineage_api.infrastructure.aws.s3_artifacts import S3ArtifactStore
        from lineage_api.infrastructure.aws.s3_map_results import S3MapResultReader
        from lineage_api.infrastructure.aws.s3_sources import S3SourceArchiveStore
        from lineage_api.infrastructure.aws.sqs_broker import SqsLaneBroker

        from java_stage_overrides import (
            JavaConsolidationStageUseCase,
            JavaControlStageUseCase,
            JavaCoverageStageUseCase,
            JavaPrGateStageUseCase,
            JavaPublicationStageUseCase,
            JavaScaStageUseCase,
        )
        from sqlite_projection import SqliteStageProjection

        def client(name: str) -> Any:
            return boto3.client(
                name,
                endpoint_url=ENDPOINT,
                region_name=os.environ["AWS_REGION"],
                aws_access_key_id="test",
                aws_secret_access_key="test",
                config=Config(retries={"max_attempts": 3}, read_timeout=60),
            )

        self.run_id = datetime.now(UTC).strftime("floci-%Y%m%dT%H%M%SZ")
        self.evidence_dir = ROOT / "data" / "acceptance" / self.run_id
        self.config = AwsRuntimeConfig.from_env(os.environ)
        self.ddb = client("dynamodb")
        self.s3 = client("s3")
        self.sqs = client("sqs")
        self.kinesis_client = client("kinesis")
        self.sfn = client("stepfunctions")

        self.control = DynamoDbControlAdapter(
            self.ddb,
            self.config.control_table,
            self.config.ledger_table,
            self.config.pointer_table,
            proposal_table=self.config.proposal_table,
        )
        self.artifacts = S3ArtifactStore(self.s3, self.config.evidence_bucket)
        self.packages = S3ArtifactStore(self.s3, self.config.package_bucket)
        self.sources = S3SourceArchiveStore(self.s3)
        self.kinesis = KinesisRuntimeAdapter(self.kinesis_client, self.config.runtime_stream)
        self.broker = SqsLaneBroker(
            self.sqs,
            {
                "interactive": os.environ["LINEAGE_INTERACTIVE_QUEUE_URL"],
                "events": os.environ["LINEAGE_EVENTS_QUEUE_URL"],
                "batch": os.environ["LINEAGE_BATCH_QUEUE_URL"],
            },
        )
        self.starter = StepFunctionsWorkflowStarter(
            self.sfn,
            {
                "BASELINE": os.environ["LINEAGE_BASELINE_WORKFLOW_ALIAS_ARN"],
                "INCREMENTAL": os.environ["LINEAGE_INCREMENTAL_WORKFLOW_ALIAS_ARN"],
                "PR_GATE": os.environ["LINEAGE_PR_GATE_WORKFLOW_ALIAS_ARN"],
                "NIGHTLY": os.environ["LINEAGE_NIGHTLY_WORKFLOW_ALIAS_ARN"],
            },
            baseline_map_concurrency=1,
        )
        projection_path = ROOT / "data" / "floci" / f"projection-{self.run_id}.db"
        self.projection = SqliteStageProjection(projection_path)
        self.map_reader = S3MapResultReader(self.s3, self.config.evidence_bucket)

        registry = production_stage_use_cases(
            self.artifacts,
            self.control,
            packages=self.packages,
            publication_control=self.control,
            projection=self.projection,
            sources=self.sources,
            pr_gate_control=self.control,
            impact_projection=self.projection,
        )
        java_coverage = JavaCoverageStageUseCase(self.artifacts, chunk_size=1_000)
        registry[("BASELINE", "B4")] = java_coverage
        registry[("INCREMENTAL", "I3")] = java_coverage
        registry[("INCREMENTAL", "I4")] = JavaControlStageUseCase(
            self.artifacts, self.control
        )
        java_sca = JavaScaStageUseCase(
            self.artifacts,
            self.sources,
            origin="https://github.com/spring-projects/spring-petclinic",
        )
        registry[("BASELINE", "B5")] = java_sca
        registry[("INCREMENTAL", "I5")] = java_sca
        java_consolidation = JavaConsolidationStageUseCase(self.artifacts)
        registry[("BASELINE", "B8")] = java_consolidation
        registry[("INCREMENTAL", "I7")] = java_consolidation
        java_publication = JavaPublicationStageUseCase(
            self.artifacts, self.packages, self.control, self.projection
        )
        registry[("BASELINE", "B10")] = java_publication
        registry[("INCREMENTAL", "I10")] = java_publication
        java_pr_gate = JavaPrGateStageUseCase(self.artifacts, self.control, self.projection)
        for index in range(1, 9):
            registry[("PR_GATE", f"P{index}")] = java_pr_gate

        self.executor = AwsStageExecutor(
            self.config,
            self.control,
            self.artifacts,
            self.kinesis,
            self.projection,
            packages=self.packages,
            broker=self.broker,
            workflow_starter=self.starter,
            sources=self.sources,
            dispatcher=StageDispatcher(registry),
        )
        self.aggregation = AwsScaAggregationExecutor(
            self.control,
            self.artifacts,
            BaselineScaAggregationUseCase(self.artifacts, self.map_reader),
        )
        self.product = AwsProductQueryProjection(
            self.ddb,
            self.control,
            self.artifacts,
            self.projection,
            control_table=self.config.control_table,
            ledger_table=self.config.ledger_table,
            proposal_table=self.config.proposal_table,
        )

        catalog_body = json.loads(
            (ROOT / "fixtures" / "catalog" / "catalog-snapshot-v1.json").read_text()
        )
        self.catalog_snapshot_id = str(
            catalog_body.get("snapshotId")
            or catalog_body.get("catalogSnapshotId")
            or "catalog-demo-v1"
        )
        self.resolver_version = str(catalog_body.get("resolverVersion") or "1.0.0")
        self.catalog_ref = self.artifacts.put(
            "catalog-snapshot",
            f"catalog/{self.run_id}/catalog-snapshot-v1.json",
            catalog_body,
            "1.0.0",
        )

        head = subprocess.run(
            ["git", "-C", str(CHECKOUT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert head == PINNED_REVISION, f"checkout at {head}, expected {PINNED_REVISION}"
        zip_v1 = subprocess.run(
            ["git", "-C", str(CHECKOUT), "archive", "--format=zip", "HEAD"],
            capture_output=True,
            check=True,
        ).stdout
        self.source_v1 = self._upload_source("v1", zip_v1)
        self.inventory = sorted(
            name
            for name in zipfile.ZipFile(io.BytesIO(zip_v1)).namelist()
            if not name.endswith("/")
        )

        self.changed_java = (
            "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java"
        )
        zip_v2 = self._touched_zip(zip_v1, self.changed_java)
        self.source_v2 = self._upload_source("v2", zip_v2)

        # Populated as the paths run.
        self.baseline: dict[str, Any] = {}
        self.incremental: dict[str, Any] = {}
        self.deployment: dict[str, Any] = {}
        self.pr_gate: dict[str, Any] = {}

    # ---------------------------------------------------------------- helpers

    def _upload_source(self, label: str, payload: bytes) -> dict[str, Any]:
        digest = hashlib.sha256(payload).hexdigest()
        key = f"sources/{self.run_id}/spring-petclinic-{label}.zip"
        response = self.s3.put_object(
            Bucket=self.config.evidence_bucket, Key=key, Body=payload
        )
        return {
            "reference": {
                "bucket": self.config.evidence_bucket,
                "key": key,
                "versionId": response["VersionId"],
                "sha256": digest,
                "sizeBytes": len(payload),
            },
            "artifactDigest": f"sha256:{digest}",
        }

    @staticmethod
    def _touched_zip(payload: bytes, target_path: str) -> bytes:
        source = zipfile.ZipFile(io.BytesIO(payload))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as rebuilt:
            for info in source.infolist():
                if info.filename.endswith("/"):
                    continue
                body = source.read(info.filename)
                if info.filename == target_path:
                    body += b"\n// touched for the floci incremental run\n"
                rebuilt.writestr(info.filename, body)
        return out.getvalue()

    def execute_stage(
        self,
        workflow_kind: str,
        stage_id: str,
        command_id: str,
        correlation_id: str,
        input_ref: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        from lineage_api.application.stage_ownership import owner_for_stage
        from lineage_api.application.workflows.definitions import WORKFLOWS

        workflow = WORKFLOWS[workflow_kind]
        stage = next(item for item in workflow.stages if item.stage_id == stage_id)
        envelope = {
            "schemaVersion": "1.0.0",
            "commandId": command_id,
            "correlationId": correlation_id,
            "causationId": f"{command_id}:{stage_id}",
            "idempotencyKey": idempotency_key or f"{command_id}:{stage_id}",
            "determinantDigest": "sha256:" + "0" * 64,
            "workflowKind": workflow_kind,
            "workflowVersion": workflow.version,
            "stageId": stage_id,
            "stageName": stage.name,
            "input": input_ref,
        }
        result = self.executor.execute(
            owner_for_stage(workflow_kind, stage_id).value, envelope
        )
        assert result["outcome"] == "SUCCEEDED", f"{stage_id}: {result}"
        return result

    def read_java_sources(self, source_ref: dict[str, Any]) -> dict[str, str]:
        collected: dict[str, str] = {}
        with self.sources.materialize(source_ref) as root:
            for path in sorted(root.rglob("*.java")):
                collected[path.relative_to(root).as_posix()] = path.read_text()
        return collected

    def intent_context(
        self, source: dict[str, Any], *, changed: list[str], closure: list[str]
    ) -> dict[str, Any]:
        return {
            "repository": "spring-petclinic",
            "artifactDigest": source["artifactDigest"],
            "environment": "staging",
            "platform": "postgres",
            "system": "petclinic",
            "acceptedAt": ACCEPTED_AT,
            "repositorySource": source["reference"],
            "repositoryInventory": self.inventory,
            "catalogSnapshotId": self.catalog_snapshot_id,
            "catalogSnapshotRef": self.catalog_ref,
            "resolverVersion": self.resolver_version,
            "rulesetVersion": "spring-data-rules-v1",
            "classificationPolicyVersion": "1.0.0",
            "changedPaths": changed,
            "removedPaths": [],
            "dependencyClosure": closure,
            "runtimeManifestRefs": [],
        }

    def put_intent(self, command_id: str, context: dict[str, Any]) -> dict[str, Any]:
        intent = {
            "schemaVersion": "1.0.0",
            "artifactType": "lineage-intent",
            "context": context,
            "classificationEvidence": [
                {
                    "level": 4,
                    "source": "APPLICATION_RUNTIME",
                    "repositoryClass": "APPLICATION_RUNTIME",
                    "ref": f"source-scope://{context['artifactDigest']}",
                }
            ],
        }
        return self.artifacts.put(
            "workflow-input", f"commands/{command_id}/input.json", intent, "1.0.0"
        )

    def run_intake(
        self, workflow_kind: str, command_id: str, correlation_id: str, input_ref: dict
    ) -> None:
        envelope = {
            "schemaVersion": "1.0.0",
            "commandId": command_id,
            "correlationId": correlation_id,
            "causationId": f"{command_id}:intake",
            "idempotencyKey": f"{command_id}:intake",
            "stageId": "intake",
            "workflowKind": workflow_kind,
            "workflowVersion": "1.0.0",
            "input": input_ref,
        }
        self.broker.publish(
            "events",
            f"repo:spring-petclinic:{workflow_kind}",
            json.dumps(envelope, sort_keys=True, separators=(",", ":")),
            correlation_id,
            message_id=f"{command_id}-intake",
        )
        message = self.broker.claim("events", "floci-e2e-driver")
        assert message is not None, "intake message did not arrive on the events lane"
        claimed_envelope = json.loads(message.payload_ref)
        first = self.executor.execute("intake", claimed_envelope)
        assert first["outcome"] == "SUCCEEDED", first
        self.broker.acknowledge(message)
        replay = self.executor.execute("intake", claimed_envelope)
        assert replay["outcome"] == "SKIPPED", f"intake replay was not exactly-once: {replay}"

    def approve_and_publish(
        self, proposal: dict[str, Any], correlation_id: str
    ) -> dict[str, Any]:
        review = self.product.review_proposal(
            proposal_id=proposal["proposalId"],
            action="APPROVE",
            version=int(proposal["version"]),
            expected_lock_version=int(proposal["lockVersion"]),
            actor="release-engineer",
            rationale="floci E2E oracle-checked approval",
            corrected_edges=None,
            principal="user:floci-e2e",
            correlation_id=correlation_id,
        )
        assert review["publication"] == "OUTBOX_PENDING", review
        envelope = self.pending_outbox_envelope(correlation_id)
        result = self.executor.execute("publication", envelope)
        assert result["outcome"] == "SUCCEEDED", result
        assert result.get("terminalOutcome") == "PUBLISHED", result
        return result

    def pending_outbox_envelope(self, correlation_id: str) -> dict[str, Any]:
        paginator = self.ddb.get_paginator("scan")
        for page in paginator.paginate(TableName=self.config.ledger_table):
            for item in page.get("Items", []):
                if (
                    item.get("topic", {}).get("S") == "PROPOSAL_APPROVED"
                    and item.get("status", {}).get("S") == "PENDING"
                    and item.get("correlationId", {}).get("S") == correlation_id
                ):
                    envelope = json.loads(item["payload"]["S"])
                    envelope["outboxId"] = item["pk"]["S"].removeprefix("OUTBOX#")
                    return envelope
        raise AssertionError("no pending PROPOSAL_APPROVED outbox event found")

    def scan_table(self, table: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        paginator = self.ddb.get_paginator("scan")
        for page in paginator.paginate(TableName=table):
            items.extend(page.get("Items", []))
        return items


@pytest.fixture(scope="module")
def sess() -> E2ESession:
    return E2ESession()


def test_01_baseline_publishes_the_petclinic_oracle(sess: E2ESession) -> None:
    from lineage_api.domain.product_confidence import project_confidence

    from java_stage_overrides import run_runtime_corroboration

    command_id = f"cmd-baseline-{sess.run_id}"
    correlation_id = f"corr-baseline-{sess.run_id}"
    context = sess.intent_context(sess.source_v1, changed=[], closure=[])
    input_ref = sess.put_intent(command_id, context)

    sess.run_intake("BASELINE", command_id, correlation_id, input_ref)

    current = input_ref
    for stage_id in ("B1", "B2", "B3", "B4"):
        current = sess.execute_stage(
            "BASELINE", stage_id, command_id, correlation_id, current
        )["output"]

    inventory_doc = sess.artifacts.get(current)
    work_refs = inventory_doc["workUnitRefs"]
    assert len(work_refs) == 1
    children = []
    for index, work_ref in enumerate(work_refs):
        child = sess.execute_stage(
            "BASELINE",
            "B5",
            command_id,
            correlation_id,
            work_ref,
            idempotency_key=f"{command_id}:B5:{index:05d}",
        )
        children.append({"outcome": "SUCCEEDED", "output": child["output"]})

    map_token = f"map-{sess.run_id}"
    prefix = f"workflow-results/{command_id}/B5/{map_token}/"
    manifest_ref = sess.artifacts.put(
        "map-manifest", f"{prefix}manifest.json", {"MapRunArn": map_token}, "1.0.0"
    )
    sess.artifacts.put("map-result-shard", f"{prefix}SUCCEEDED_0.json", children, "1.0.0")
    aggregate = sess.aggregation.execute(
        {
            "schemaVersion": "1.0.0",
            "operation": "BASELINE_SCA_AGGREGATE",
            "workflowKind": "BASELINE",
            "workflowVersion": "1.0.0",
            "commandId": command_id,
            "correlationId": correlation_id,
            "causationId": f"{command_id}:B5A",
            "idempotencyKey": f"{command_id}:B5A",
            "determinantDigest": "sha256:" + "0" * 64,
            "workInventory": current,
            "mapResult": {
                "MapRunArn": (
                    f"arn:aws:states:us-east-1:000000000000:mapRun:lineage/Map:{map_token}"
                ),
                "ResultWriterDetails": {
                    "Bucket": sess.config.evidence_bucket,
                    "Key": manifest_ref["key"],
                },
            },
        }
    )
    assert aggregate["outcome"] == "SUCCEEDED", aggregate
    aggregate_doc = sess.artifacts.get(aggregate["output"])

    sca_result = sess.artifacts.get(children[0]["output"])
    evidence_doc = sess.artifacts.get(sca_result["evidenceRef"])
    static_edges = evidence_doc["edges"]
    assert len(static_edges) == ORACLE_EDGES

    runtime = run_runtime_corroboration(
        artifacts=sess.artifacts,
        kinesis=sess.kinesis,
        sources=sess.read_java_sources(sess.source_v1["reference"]),
        static_edges=static_edges,
        command_id=command_id,
        work_unit_id=sca_result["workUnitId"],
        correlation_id=correlation_id,
        common=aggregate_doc["context"],
        observed_at=ACCEPTED_AT,
        java_home=JAVA_HOME,
    )
    assert runtime["verdict"] == "CORROBORATED", runtime["verdict"]
    assert runtime["corroborated"] == ORACLE_ELEMENT_HIGH

    augmented = dict(aggregate_doc)
    augmented["context"] = {
        **aggregate_doc["context"],
        "runtimeManifestRefs": [runtime["manifestRef"]],
        "assertionRefs": [
            *aggregate_doc["context"]["assertionRefs"],
            runtime["assertionRef"],
        ],
    }
    current = sess.artifacts.put(
        "sca-batch-result",
        f"commands/{command_id}/stages/B5A/runtime-augmented.json",
        augmented,
        "1.0.0",
    )

    for stage_id in ("B6", "B7", "B8", "B9", "B10"):
        result = sess.execute_stage(
            "BASELINE", stage_id, command_id, correlation_id, current
        )
        current = result["output"]
        document = sess.artifacts.get(current)
        if stage_id == "B6":
            assert document["runtimeCoverage"]["status"] == "VALIDATED", document[
                "runtimeCoverage"
            ]
        if stage_id == "B8":
            assert document["edgeCount"] == ORACLE_EDGES, document["bands"]
            assert document["bands"] == {"HIGH": ORACLE_ELEMENT_HIGH, "SINGLE": 15}
            assert document["coverage"]["state"] == "COMPLETE"
            sess.baseline["edgeSetRef"] = document["edgeSetRef"]
        if stage_id == "B9":
            assert document["decision"] == "PROPOSAL_CREATED"
            sess.baseline["proposal"] = document["proposal"]
        if stage_id == "B10":
            assert result.get("terminalOutcome") == "AWAITING_APPROVAL"

    published = sess.approve_and_publish(sess.baseline["proposal"], correlation_id)
    receipt = sess.artifacts.get(published["output"])
    pointer = sess.control.active_pointer("staging")
    assert pointer["fence"] == 1
    assert pointer["graphVersion"] == receipt["graphVersion"]
    assert pointer.get("package") is not None
    sess.baseline["graphVersion"] = receipt["graphVersion"]
    sess.baseline["packageRef"] = receipt["packageRef"]
    sess.baseline["commandId"] = command_id
    sess.baseline["correlationId"] = correlation_id

    edge_set = sess.artifacts.get(sess.baseline["edgeSetRef"])
    reads = [edge for edge in edge_set["edges"] if edge["edgeType"] == "READS"]
    writes = [edge for edge in edge_set["edges"] if edge["edgeType"] == "WRITES"]
    assert (len(reads), len(writes)) == (ORACLE_READS, ORACLE_WRITES)
    high = [edge for edge in edge_set["edges"] if edge["band"] == "HIGH"]
    assert len(high) == ORACLE_ELEMENT_HIGH
    for edge in high:
        assert edge["corroboration"] == "ELEMENT"
        display = project_confidence(edge["band"], edge["provenance"])
        assert (display.display_band, display.percent) == ("VERIFIED", 92)
    sess.baseline["edges"] = edge_set["edges"]

    projected = sess.projection.namespace_checksum(receipt["graphVersion"])
    assert len(projected) == ORACLE_EDGES


def test_02_incremental_publishes_delta_and_docs_change_proves_no_impact(
    sess: E2ESession,
) -> None:
    from java_stage_overrides import java_scope_partition, run_runtime_corroboration

    assert sess.baseline, "baseline must publish first"
    command_id = f"cmd-incremental-{sess.run_id}"
    correlation_id = f"corr-incremental-{sess.run_id}"
    closure, _, _ = java_scope_partition(sess.inventory, "postgres")
    context = sess.intent_context(
        sess.source_v2, changed=[sess.changed_java], closure=closure
    )
    input_ref = sess.put_intent(command_id, context)
    sess.run_intake("INCREMENTAL", command_id, correlation_id, input_ref)

    current = input_ref
    for stage_id in ("I1", "I2", "I3", "I4", "I5"):
        result = sess.execute_stage(
            "INCREMENTAL", stage_id, command_id, correlation_id, current
        )
        current = result["output"]
        if stage_id == "I1":
            pinned = sess.artifacts.get(current)["context"]
            assert pinned["activeBaseVersion"] == sess.baseline["graphVersion"]
            assert pinned["activeBaseFence"] == 1

    sca_result = sess.artifacts.get(current)
    evidence_doc = sess.artifacts.get(sca_result["evidenceRef"])
    static_edges = evidence_doc["edges"]
    assert len(static_edges) == ORACLE_EDGES

    runtime = run_runtime_corroboration(
        artifacts=sess.artifacts,
        kinesis=sess.kinesis,
        sources=sess.read_java_sources(sess.source_v2["reference"]),
        static_edges=static_edges,
        command_id=command_id,
        work_unit_id=sca_result["workUnitId"],
        correlation_id=correlation_id,
        common=sca_result["context"],
        observed_at=ACCEPTED_AT,
        java_home=JAVA_HOME,
    )
    assert runtime["verdict"] == "CORROBORATED"

    augmented = dict(sca_result)
    augmented["context"] = {
        **sca_result["context"],
        "runtimeManifestRefs": [runtime["manifestRef"]],
        "assertionRefs": [*sca_result["context"]["assertionRefs"], runtime["assertionRef"]],
    }
    current = sess.artifacts.put(
        "sca-stage-result",
        f"commands/{command_id}/stages/I5/runtime-augmented.json",
        augmented,
        "1.0.0",
    )

    for stage_id in ("I6", "I7", "I8", "I9", "I10"):
        result = sess.execute_stage(
            "INCREMENTAL", stage_id, command_id, correlation_id, current
        )
        current = result["output"]
        document = sess.artifacts.get(current)
        if stage_id == "I6":
            assert document["runtimeCoverage"]["status"] == "VALIDATED"
        if stage_id == "I7":
            assert document["edgeCount"] == ORACLE_EDGES
            assert document["bands"] == {"HIGH": ORACLE_ELEMENT_HIGH, "SINGLE": 15}
        if stage_id == "I9":
            assert document["decision"] == "PROPOSAL_CREATED"
            assert document["proposal"]["proposalType"] == "DELTA"
            sess.incremental["proposal"] = document["proposal"]
        if stage_id == "I10":
            assert result.get("terminalOutcome") == "AWAITING_APPROVAL"

    published = sess.approve_and_publish(sess.incremental["proposal"], correlation_id)
    receipt = sess.artifacts.get(published["output"])
    pointer = sess.control.active_pointer("staging")
    assert pointer["fence"] == 2
    assert pointer["graphVersion"] == receipt["graphVersion"]
    sess.incremental["graphVersion"] = receipt["graphVersion"]
    sess.incremental["commandId"] = command_id

    # Docs-only change: honest NO_LINEAGE_IMPACT terminal without an approval.
    docs_command = f"cmd-docs-{sess.run_id}"
    docs_correlation = f"corr-docs-{sess.run_id}"
    docs_file = next(path for path in sess.inventory if path.lower() == "readme.md")
    docs_context = sess.intent_context(sess.source_v2, changed=[docs_file], closure=[])
    docs_context["artifactDigest"] = sess.source_v2["artifactDigest"]
    docs_ref = sess.put_intent(docs_command, docs_context)
    current = docs_ref
    for stage_id in ("I1", "I2", "I3", "I4", "I5", "I6", "I7", "I8", "I9", "I10"):
        result = sess.execute_stage(
            "INCREMENTAL", stage_id, docs_command, docs_correlation, current
        )
        current = result["output"]
    assert result.get("terminalOutcome") == "NO_LINEAGE_IMPACT", sess.artifacts.get(current)
    sess.incremental["docsTerminal"] = result["terminalOutcome"]


def test_03_deployment_promotes_the_exact_approved_package(sess: E2ESession) -> None:
    import lineage_api.entrypoints.aws.common as aws_common
    from lineage_api.entrypoints.aws import deployment as deployment_entrypoint

    assert sess.incremental.get("graphVersion"), "incremental must publish first"
    command_id = f"cmd-deploy-{sess.run_id}"
    correlation_id = f"corr-deploy-{sess.run_id}"
    event = {
        "schemaVersion": "1.0.0",
        "eventId": f"deploy-{sess.run_id}",
        "eventType": "DEPLOYMENT",
        "provider": "github-actions",
        "providerSequence": 1,
        "attempt": 1,
        "system": "petclinic",
        "environment": "staging",
        "outcome": "SUCCEEDED",
        "artifactDigest": sess.source_v1["artifactDigest"],
        "correlationId": correlation_id,
        "auditRef": f"audit://github-actions/{sess.run_id}",
        "occurredAt": ACCEPTED_AT,
    }
    event_digest = hashlib.sha256(_canonical(event)).hexdigest()
    receipt_ref = sess.artifacts.put(
        "deployment-authentication-receipt",
        f"commands/{command_id}/authentication.json",
        {
            "schemaVersion": "1.0.0",
            "artifactType": "deployment-authentication-receipt",
            "decision": "AUTHENTICATED",
            "eventDigest": event_digest,
            "provider": "github-actions",
            "principal": "ci:github-actions",
            "verifiedAt": ACCEPTED_AT,
        },
        "1.0.0",
    )
    input_ref = sess.artifacts.put(
        "deployment-event",
        f"commands/{command_id}/input.json",
        {
            "schemaVersion": "1.0.0",
            "artifactType": "deployment-event",
            "context": {
                "repository": "spring-petclinic",
                "artifactDigest": sess.source_v1["artifactDigest"],
                "environment": "staging",
                "platform": "postgres",
                "system": "petclinic",
            },
            "event": event,
            "authenticationRef": receipt_ref,
        },
        "1.0.0",
    )

    original_factory = aws_common.executor_factory
    aws_common.executor_factory = lambda: sess.executor
    try:
        result = deployment_entrypoint.handler(
            {
                "schemaVersion": "1.0.0",
                "commandId": command_id,
                "correlationId": correlation_id,
                "causationId": f"{command_id}:deploy",
                "idempotencyKey": f"{command_id}:deploy",
                "input": input_ref,
            },
            None,
        )
    finally:
        aws_common.executor_factory = original_factory

    assert result["outcome"] == "SUCCEEDED", result
    assert result["completedStages"] == ["D1", "D2", "D3", "D4", "D5", "D6"], result
    final = sess.artifacts.get(result["output"])
    assert final["terminalOutcome"] == "PROMOTED", final
    assert final["graphVersion"] == sess.baseline["graphVersion"]

    pointer = sess.control.active_pointer("staging")
    assert pointer["fence"] == 3, pointer
    assert pointer["graphVersion"] == sess.baseline["graphVersion"]
    state = sess.control.deployment_state("petclinic", "staging")
    assert state is not None and state.get("terminalOutcome") == "PROMOTED"
    assert state.get("graphVersion") == sess.baseline["graphVersion"]
    sess.deployment["terminal"] = final["terminalOutcome"]
    sess.deployment["pointer"] = pointer
    sess.deployment["state"] = dict(state)
    sess.deployment["commandId"] = command_id


def test_04_pr_gate_blocks_a_destructive_change_against_the_live_graph(
    sess: E2ESession,
) -> None:
    assert sess.deployment.get("terminal") == "PROMOTED", "deployment must promote first"
    command_id = f"cmd-pr-{sess.run_id}"
    correlation_id = f"corr-pr-{sess.run_id}"
    subject = "urn:ldp:staging:postgres:petclinic:owners#last_name"
    event = {
        "eventId": f"github-pr-{sess.run_id}",
        "eventType": "PULL_REQUEST",
        "provider": "github",
        "providerSequence": 21,
        "repository": "spring-petclinic",
        "prNumber": 7,
        "headSha": "f" * 40,
        "targetEnvironment": "staging",
        "system": "petclinic",
        "candidateArtifactDigest": sess.source_v2["artifactDigest"],
        "acceptedAt": ACCEPTED_AT,
        "deadlineAt": "2026-08-14T12:02:00Z",
        "correlationId": correlation_id,
    }
    auth_ref = sess.artifacts.put(
        "pr-authentication-receipt",
        f"commands/{command_id}/auth.json",
        {
            "schemaVersion": "1.0.0",
            "artifactType": "pr-authentication-receipt",
            "decision": "AUTHENTICATED",
            "eventDigest": hashlib.sha256(_canonical(event)).hexdigest(),
            "provider": "github",
            "principal": "github-app:lineage",
            "verifiedAt": ACCEPTED_AT,
        },
        "1.0.0",
    )
    candidate_ref = sess.artifacts.put(
        "pr-candidate-analysis",
        f"commands/{command_id}/candidate.json",
        {
            "schemaVersion": "1.0.0",
            "artifactType": "pr-candidate-analysis",
            "headSha": event["headSha"],
            "candidateArtifactDigest": event["candidateArtifactDigest"],
            "changes": [
                {
                    "changeType": "COLUMN_DROP",
                    "subject": subject,
                    "evidenceMechanisms": ["SCA"],
                    "changedPaths": [sess.changed_java],
                }
            ],
            "coverage": {
                "expectedScope": [sess.changed_java],
                "completedScope": [sess.changed_java],
                "reusedScope": [],
                "skippedScope": [],
                "unsupportedScope": [],
                "quarantinedScope": [],
                "failedScope": [],
            },
        },
        "1.0.0",
    )
    intent_ref = sess.artifacts.put(
        "pr-gate-intent",
        f"commands/{command_id}/intent.json",
        {
            "schemaVersion": "1.0.0",
            "artifactType": "pr-gate-intent",
            "event": event,
            "authenticationRef": auth_ref,
            "candidateAnalysisRef": candidate_ref,
            "policyVersion": "pr-policy-v1",
            "cohortVersion": "enforce-v1",
            "impactDepth": 5,
            "impactLimit": 500,
        },
        "1.0.0",
    )

    current = intent_ref
    final_result: dict[str, Any] = {}
    for stage_id in ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"):
        final_result = sess.execute_stage(
            "PR_GATE", stage_id, command_id, correlation_id, current
        )
        current = final_result["output"]
    check = sess.artifacts.get(current)
    assert check["terminalOutcome"] in {"BLOCK", "WARN"}, check
    assert check["verdict"] == check["terminalOutcome"]
    assert check["environmentVersion"] == sess.baseline["graphVersion"]
    stored = sess.control.pr_check(check["checkId"])
    assert stored is not None
    impact = sess.projection.impact(
        sess.baseline["graphVersion"], subject, "COLUMN_DROP", depth=5, limit=500
    )
    assert impact["affected"], "published graph shows no downstream impact"
    sess.pr_gate["check"] = check
    sess.pr_gate["impact"] = impact
    sess.pr_gate["commandId"] = command_id


def test_05_capture_run_evidence(sess: E2ESession) -> None:
    from lineage_api.domain.product_confidence import project_confidence

    assert sess.pr_gate.get("check"), "all paths must complete first"
    out = sess.evidence_dir
    out.mkdir(parents=True, exist_ok=True)

    (out / "ledger-items.json").write_text(
        json.dumps(sess.scan_table(sess.config.ledger_table), indent=2, default=str)
    )
    (out / "control-items.json").write_text(
        json.dumps(sess.scan_table(sess.config.control_table), indent=2, default=str)
    )
    (out / "pointer.json").write_text(
        json.dumps(sess.control.active_pointer("staging"), indent=2)
    )
    (out / "edge-set.json").write_text(json.dumps(sess.baseline["edges"], indent=2))
    confidence = [
        {
            "edgeKey": edge["edgeKey"],
            "from": edge["from"],
            "to": edge["to"],
            "edgeType": edge["edgeType"],
            "band": edge["band"],
            "corroboration": edge["corroboration"],
            "transform": edge.get("transform"),
            "mechanisms": sorted({p["mechanism"] for p in edge["provenance"]}),
            "display": project_confidence(edge["band"], edge["provenance"]).display_band,
            "percent": project_confidence(edge["band"], edge["provenance"]).percent,
            "provenance": edge["provenance"],
        }
        for edge in sess.baseline["edges"]
    ]
    (out / "confidence.json").write_text(json.dumps(confidence, indent=2))

    inventory = []
    for bucket in (sess.config.evidence_bucket, sess.config.package_bucket):
        paginator = sess.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            inventory.extend(
                {"bucket": bucket, "key": item["Key"], "size": item["Size"]}
                for item in page.get("Contents", [])
            )
    (out / "s3-inventory.json").write_text(json.dumps(inventory, indent=2, default=str))

    shard = sess.kinesis_client.describe_stream(StreamName=sess.config.runtime_stream)[
        "StreamDescription"
    ]["Shards"][0]["ShardId"]
    iterator = sess.kinesis_client.get_shard_iterator(
        StreamName=sess.config.runtime_stream,
        ShardId=shard,
        ShardIteratorType="TRIM_HORIZON",
    )["ShardIterator"]
    records = []
    for _ in range(10):
        batch = sess.kinesis_client.get_records(ShardIterator=iterator, Limit=100)
        records.extend(json.loads(record["Data"]) for record in batch["Records"])
        iterator = batch.get("NextShardIterator")
        if not batch["Records"] or iterator is None:
            break
    (out / "kinesis-observations.json").write_text(json.dumps(records, indent=2))

    (out / "pr-check.json").write_text(json.dumps(sess.pr_gate["check"], indent=2))
    (out / "impact.json").write_text(json.dumps(sess.pr_gate["impact"], indent=2))
    (out / "deployment-state.json").write_text(
        json.dumps(sess.deployment["state"], indent=2, default=str)
    )
    summary = {
        "runId": sess.run_id,
        "revision": PINNED_REVISION,
        "artifactDigestV1": sess.source_v1["artifactDigest"],
        "artifactDigestV2": sess.source_v2["artifactDigest"],
        "baseline": {
            "commandId": sess.baseline["commandId"],
            "graphVersion": sess.baseline["graphVersion"],
            "terminal": "PUBLISHED",
            "edges": ORACLE_EDGES,
            "reads": ORACLE_READS,
            "writes": ORACLE_WRITES,
            "elementHigh": ORACLE_ELEMENT_HIGH,
        },
        "incremental": {
            "commandId": sess.incremental["commandId"],
            "graphVersion": sess.incremental["graphVersion"],
            "terminal": "PUBLISHED",
            "docsChangeTerminal": sess.incremental["docsTerminal"],
        },
        "deployment": {
            "commandId": sess.deployment["commandId"],
            "terminal": sess.deployment["terminal"],
            "pointerFence": sess.deployment["pointer"]["fence"],
        },
        "prGate": {
            "commandId": sess.pr_gate["commandId"],
            "verdict": sess.pr_gate["check"]["verdict"],
            "affected": len(sess.pr_gate["impact"]["affected"]),
        },
        "runtimeObservationsOnKinesis": len(records),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nEvidence captured to {out}")
