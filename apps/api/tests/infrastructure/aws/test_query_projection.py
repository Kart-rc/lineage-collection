from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from lineage_api.application.product_api import ProductApiError, ProductApiService
from lineage_api.infrastructure.aws.query_projection import AwsProductQueryProjection


class Client:
    def __init__(self, **responses: Any) -> None:
        self.responses = {name: list(values) for name, values in responses.items()}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str):
        def call(**kwargs: Any) -> Any:
            self.calls.append((name, kwargs))
            values = self.responses.get(name, [{}])
            value = values.pop(0) if values else {}
            if isinstance(value, Exception):
                raise value
            return value

        return call


class Control:
    def __init__(self, pointer: dict[str, Any] | None = None) -> None:
        self.pointer = pointer or {
            "environment": "staging",
            "graphVersion": "graph-v7",
            "graphChecksum": "a" * 64,
            "fence": 7,
            "correlationId": "corr-publication",
        }

    def active_pointer(self, environment: str) -> dict[str, Any]:
        return {**self.pointer, "environment": environment}


class Artifacts:
    def __init__(self, documents: dict[str, object] | None = None) -> None:
        self.writes: list[tuple[str, str, object, str]] = []
        self.documents = documents or {}
        self.reads: list[object] = []

    def put(self, kind: str, key: str, body: object, version: str) -> dict[str, object]:
        self.writes.append((kind, key, body, version))
        return {
            "bucket": "evidence",
            "key": key,
            "versionId": f"v{len(self.writes)}",
            "sha256": f"{len(self.writes):064x}",
            "sizeBytes": 200,
        }

    def get(self, reference: object) -> object:
        self.reads.append(reference)
        assert isinstance(reference, dict)
        return self.documents[reference["key"]]


class Projection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def lineage(self, namespace: str, subject: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(
            ("lineage", {"namespace": namespace, "subject": subject, **kwargs})
        )
        return {"namespaceVersion": namespace, "nodes": [], "edges": []}

    def edge_detail(self, namespace: str, edge_key: str) -> dict[str, Any] | None:
        self.calls.append(
            ("edge_detail", {"namespace": namespace, "edge_key": edge_key})
        )
        return {"namespaceVersion": namespace, "edgeKey": edge_key}

    def impact(self, namespace: str, subject: str, change_type: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(
            (
                "impact",
                {
                    "namespace": namespace,
                    "subject": subject,
                    "change_type": change_type,
                    **kwargs,
                },
            )
        )
        return {"namespaceVersion": namespace, "affected": []}


def _port(
    client: Client,
    *,
    control: Control | None = None,
    artifacts: Artifacts | None = None,
    projection: Projection | None = None,
    sqs: Client | None = None,
    queue_urls: tuple[str, ...] = (),
    dead_letter_queue_urls: tuple[str, ...] = (),
) -> AwsProductQueryProjection:
    return AwsProductQueryProjection(
        client,
        control or Control(),
        artifacts or Artifacts(),
        projection or Projection(),
        control_table="control",
        ledger_table="ledger",
        proposal_table="proposal",
        clock=lambda: datetime(2026, 8, 8, 18, 0, tzinfo=UTC),
        sqs=sqs,
        queue_urls=queue_urls,
        dead_letter_queue_urls=dead_letter_queue_urls,
    )


def _run_summary(command_id: str = "cmd-1") -> dict[str, Any]:
    return {
        "pk": {"S": f"RUN#{command_id}"},
        "sk": {"S": "SUMMARY"},
        "commandId": {"S": command_id},
        "workflowKind": {"S": "INCREMENTAL"},
        "workflowVersion": {"S": "1.0.0"},
        "currentStageId": {"S": "I10"},
        "currentStageName": {"S": "PUBLISH_WITH_FENCED_PROTOCOL"},
        "status": {"S": "PUBLISHED"},
        "terminalOutcome": {"S": "PUBLISHED"},
        "correlationId": {"S": "corr-1"},
        "environment": {"S": "staging"},
        "system": {"S": "payments"},
        "createdAt": {"S": "2026-08-08T17:00:00Z"},
        "updatedAt": {"S": "2026-08-08T17:05:00Z"},
        "output": {
            "S": json.dumps(
                {
                    "bucket": "evidence",
                    "key": "I10.json",
                    "versionId": "v1",
                    "sha256": "a" * 64,
                    "sizeBytes": 100,
                }
            )
        },
    }


def _proposal(state: str = "IN_REVIEW") -> dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "proposalId": "proposal-1",
        "version": 1,
        "proposalType": "DELTA",
        "commandId": "cmd-1",
        "repository": "payments-pipeline",
        "artifactDigest": "sha256:source-v2",
        "system": "payments",
        "environment": "staging",
        "state": state,
        "expectedBaseVersion": "graph-v7",
        "diff": {
            "edgeSetRef": {
                "bucket": "evidence",
                "key": "edge-set.json",
                "versionId": "v1",
                "sha256": "b" * 64,
                "sizeBytes": 100,
            },
            "addedEdgeIds": ["edge-1"],
            "removedEdgeIds": [],
            "bandChangedEdgeIds": [],
        },
        "correlationId": "corr-1",
        "createdAt": "2026-08-08T17:04:00Z",
        "lockVersion": 1,
    }


def _corrected_edge() -> dict[str, Any]:
    source = "urn:ldp:staging:snowflake:payments:raw.transactions"
    target = "urn:ldp:staging:snowflake:payments:analytics.daily_revenue"
    return {
        "schemaVersion": "1.0.0",
        "edgeKey": "edge-6b5daa02c9980c8786a73558",
        "version": 1,
        "from": [source],
        "to": target,
        "edgeType": "DERIVES",
        "band": "HIGH",
        "corroboration": "DATASET",
        "status": "PROPOSED",
        "provenance": [{"provenanceId": "manual-reviewed-1"}],
        "autoPublishable": False,
        "system": "payments",
        "transform": "SUM(amount)",
    }


def test_run_listing_uses_the_run_index_and_an_opaque_validated_cursor() -> None:
    item = _run_summary()
    client = Client(
        query=[
            {
                "Items": [item],
                "LastEvaluatedKey": {
                    "pk": item["pk"],
                    "sk": item["sk"],
                    "queryPk": {"S": "RUN"},
                    "querySk": {"S": "2026-08-08T17:05:00Z#cmd-1"},
                },
            },
            {"Items": [item]},
        ]
    )
    port = _port(client)

    first = port.list_runs(
        limit=25,
        cursor=None,
        workflow=None,
        status=None,
        environment=None,
        principal="reader",
        correlation_id="corr-api",
    )
    second = port.list_runs(
        limit=25,
        cursor=str(first["nextCursor"]),
        workflow=None,
        status=None,
        environment=None,
        principal="reader",
        correlation_id="corr-api",
    )

    assert first["items"][0]["runId"] == "cmd-1"
    assert second["items"][0]["terminalOutcome"] == "PUBLISHED"
    requests = [kwargs for name, kwargs in client.calls if name == "query"]
    assert requests[0]["IndexName"] == "RunsByUpdatedAt"
    assert "FilterExpression" not in requests[0]
    assert requests[0]["ScanIndexForward"] is False
    assert requests[1]["ExclusiveStartKey"]["queryPk"] == {"S": "RUN"}

    with pytest.raises(ProductApiError, match="cursor"):
        port.list_runs(
            limit=25,
            cursor="not-a-valid-cursor",
            workflow=None,
            status=None,
            environment=None,
            principal="reader",
            correlation_id="corr-api",
        )


def test_run_detail_is_a_bounded_exact_partition_timeline() -> None:
    client = Client(
        get_item=[{"Item": _run_summary()}],
        query=[
            {
                "Items": [
                    {
                        "sk": {"S": "STAGE#I10#idem"},
                        "status": {"S": "COMPLETED"},
                        "stageId": {"S": "I10"},
                        "stageName": {"S": "PUBLISH_WITH_FENCED_PROTOCOL"},
                        "completedAt": {"S": "2026-08-08T17:05:00Z"},
                        "output": _run_summary()["output"],
                    }
                ]
            }
        ],
    )

    detail = _port(client).get_run(
        run_id="cmd-1", principal="reader", correlation_id="corr-api"
    )

    assert detail["run"]["runId"] == "cmd-1"
    assert detail["stages"][0]["stageId"] == "I10"
    request = next(kwargs for name, kwargs in client.calls if name == "query")
    assert request["Limit"] == 64
    assert request["ExpressionAttributeValues"][":pk"] == {"S": "COMMAND#cmd-1"}


def test_proposal_queue_and_latest_version_use_queries_not_scans() -> None:
    proposal = _proposal()
    item = {"document": {"S": json.dumps(proposal)}}
    client = Client(query=[{"Items": [item]}, {"Items": [item]}])
    port = _port(client)

    listed = port.list_proposals(
        limit=10,
        cursor=None,
        state="IN_REVIEW",
        principal="reader",
        correlation_id="corr-api",
    )
    latest = port.get_proposal(
        proposal_id="proposal-1",
        version=None,
        principal="reader",
        correlation_id="corr-api",
    )

    assert listed == {"items": [proposal], "nextCursor": None}
    assert latest == proposal
    assert all(name == "query" for name, _kwargs in client.calls)
    assert client.calls[0][1]["IndexName"] == "ProposalsByState"
    assert client.calls[1][1]["ScanIndexForward"] is False


def test_graph_reads_pin_the_active_pointer_unless_an_exact_version_is_requested() -> None:
    projection = Projection()
    port = _port(Client(), projection=projection)

    lineage = port.lineage(
        subject="urn:ldp:staging:snowflake:payments:raw.transactions",
        direction="down",
        depth=3,
        limit=250,
        version=None,
        principal="reader",
        correlation_id="corr-api",
    )
    edge = port.edge_detail(
        edge_key="edge-1",
        version="graph-v6",
        principal="reader",
        correlation_id="corr-api",
    )
    impact = port.impact(
        subject="urn:ldp:staging:snowflake:payments:raw.transactions",
        change_type="COLUMN_DROP",
        depth=5,
        limit=100,
        version=None,
        principal="reader",
        correlation_id="corr-api",
    )

    assert lineage["namespaceVersion"] == "graph-v7"
    assert edge["namespaceVersion"] == "graph-v6"
    assert impact["namespaceVersion"] == "graph-v7"
    assert projection.calls[0][1]["limit"] == 250


def test_runtime_kill_switch_is_a_conditional_audited_control_mutation() -> None:
    client = Client(transact_write_items=[{}])
    result = _port(client).set_runtime_kill_switch(
        scope_type="ENVIRONMENT",
        scope_value="production",
        active=True,
        expected_version=3,
        actor="operator@example.com",
        rationale="incident containment",
        principal="arn:aws:iam::111111111111:role/operator",
        correlation_id="corr-api",
    )

    assert result["version"] == 4
    transaction = client.calls[0][1]["TransactItems"]
    update = transaction[0]["Update"]
    audit = transaction[1]["Put"]["Item"]
    assert "version = :expectedVersion" in update["ConditionExpression"]
    assert audit["principal"]["S"].endswith("role/operator")
    assert audit["action"] == {"S": "RUNTIME_KILL_SWITCH_CHANGED"}


def test_approval_is_one_conditional_decision_audit_and_publication_outbox_transaction() -> None:
    proposal = _proposal()
    client = Client(
        get_item=[{"Item": {"document": {"S": json.dumps(proposal)}}}],
        transact_write_items=[{}],
    )
    artifacts = Artifacts()
    port = _port(client, artifacts=artifacts)

    result = port.review_proposal(
        proposal_id="proposal-1",
        action="APPROVE",
        version=1,
        expected_lock_version=1,
        actor="reviewer@example.com",
        rationale="evidence verified",
        corrected_edges=None,
        principal="arn:aws:iam::111111111111:role/reviewer",
        correlation_id="corr-api",
    )

    assert result["proposal"]["state"] == "APPROVED"
    assert result["proposal"]["lockVersion"] == 2
    assert result["publication"] == "OUTBOX_PENDING"
    assert [write[0] for write in artifacts.writes] == [
        "approval",
        "proposal-decision",
    ]
    decision = artifacts.writes[1][2]
    assert isinstance(decision, dict)
    assert decision["proposal"]["approvalRef"] == result["approvalRef"]
    transaction = client.calls[-1][1]["TransactItems"]
    assert len(transaction) == 3
    update = transaction[0]["Update"]
    assert "lockVersion = :expectedLockVersion" in update["ConditionExpression"]
    assert update["ExpressionAttributeValues"][":inReview"] == {"S": "IN_REVIEW"}
    audit = transaction[1]["Put"]["Item"]
    outbox = transaction[2]["Put"]["Item"]
    assert audit["principal"]["S"].endswith("role/reviewer")
    assert outbox["topic"] == {"S": "PROPOSAL_APPROVED"}
    envelope = json.loads(outbox["payload"]["S"])
    assert envelope["workflowKind"] == "INCREMENTAL"
    assert envelope["stageId"] == "I10"
    assert envelope["input"]["versionId"] == "v2"
    assert outbox["pk"] == {"S": f"OUTBOX#{envelope['outboxId']}"}


def test_exact_approval_retry_returns_the_committed_decision_without_new_side_effects() -> None:
    proposal = {
        **_proposal("APPROVED"),
        "lockVersion": 2,
        "approvalRef": {
            "bucket": "evidence",
            "key": "approval.json",
            "versionId": "v1",
            "sha256": "c" * 64,
            "sizeBytes": 200,
        },
        "decision": {
            "decision": "APPROVED",
            "actor": "reviewer@example.com",
            "principal": "arn:aws:iam::111111111111:role/reviewer",
            "rationale": "evidence verified",
        },
    }
    client = Client(
        get_item=[{"Item": {"document": {"S": json.dumps(proposal)}}}]
    )
    artifacts = Artifacts()

    result = _port(client, artifacts=artifacts).review_proposal(
        proposal_id="proposal-1",
        action="APPROVE",
        version=1,
        expected_lock_version=1,
        actor="reviewer@example.com",
        rationale="evidence verified",
        corrected_edges=None,
        principal="arn:aws:iam::111111111111:role/reviewer",
        correlation_id="corr-retry",
    )

    assert result == {
        "proposal": proposal,
        "approvalRef": proposal["approvalRef"],
        "publication": "OUTBOX_PENDING",
    }
    assert artifacts.writes == []
    assert [name for name, _kwargs in client.calls] == ["get_item"]


def test_reconciliation_approval_creates_a_separate_bounded_publication_run() -> None:
    proposal = {
        **_proposal(),
        "proposalType": "RECONCILIATION",
        "commandId": "nightly-command-1",
    }
    client = Client(
        get_item=[{"Item": {"document": {"S": json.dumps(proposal)}}}],
        transact_write_items=[{}],
    )

    _port(client).review_proposal(
        proposal_id="proposal-1",
        action="APPROVE",
        version=1,
        expected_lock_version=1,
        actor="reviewer@example.com",
        rationale="reconciliation verified",
        corrected_edges=None,
        principal="arn:aws:iam::111111111111:role/reviewer",
        correlation_id="corr-api",
    )

    transaction = client.calls[-1][1]["TransactItems"]
    envelope = json.loads(transaction[2]["Put"]["Item"]["payload"]["S"])
    assert envelope["workflowKind"] == "INCREMENTAL"
    assert envelope["stageId"] == "I10"
    assert envelope["commandId"].startswith("publication-")
    assert envelope["commandId"] != "nightly-command-1"


def test_correction_requires_a_complete_scope_consistent_edge_document() -> None:
    proposal = _proposal()
    client = Client(
        get_item=[
            {"Item": {"document": {"S": json.dumps(proposal)}}},
            {"Item": {"document": {"S": json.dumps(proposal)}}},
        ],
        transact_write_items=[{}],
    )
    artifacts = Artifacts()
    port = _port(client, artifacts=artifacts)

    result = port.review_proposal(
        proposal_id="proposal-1",
        action="CORRECT",
        version=1,
        expected_lock_version=1,
        actor="reviewer@example.com",
        rationale="corrected relationship",
        corrected_edges=[_corrected_edge()],
        principal="arn:aws:iam::111111111111:role/reviewer",
        correlation_id="corr-api",
    )

    assert result["proposal"]["version"] == 2
    assert result["proposal"]["diff"]["addedEdgeIds"] == [
        "edge-6b5daa02c9980c8786a73558"
    ]
    assert artifacts.writes[0][0] == "consolidated-edge-set"
    assert len(client.calls[-1][1]["TransactItems"]) == 3

    invalid = _corrected_edge()
    invalid["to"] = "urn:ldp:production:snowflake:payments:analytics.daily_revenue"
    with pytest.raises(ProductApiError, match="scope"):
        port.review_proposal(
            proposal_id="proposal-1",
            action="CORRECT",
            version=1,
            expected_lock_version=1,
            actor="reviewer@example.com",
            rationale="invalid relationship",
            corrected_edges=[invalid],
            principal="arn:aws:iam::111111111111:role/reviewer",
            correlation_id="corr-api",
        )


def test_product_service_defaults_the_queue_to_in_review() -> None:
    client = Client(query=[{"Items": []}])
    response = ProductApiService(_port(client)).handle(
        method="GET",
        path="/api/proposals",
        query={},
        body=None,
        principal="reader",
        correlation_id="corr-api",
    )

    assert response.status_code == 200
    request = client.calls[0][1]
    assert request["ExpressionAttributeValues"][":state"] == {
        "S": "PROPOSAL_STATE#IN_REVIEW"
    }


def _collection_item(command_id: str = "cmd-1") -> dict[str, Any]:
    return {
        "pk": {"S": f"COLLECTION#{command_id}"},
        "sk": {"S": "STATUS"},
        "document": {
            "S": json.dumps(
                {
                    "commandId": command_id,
                    "collectionId": command_id,
                    "statusUrl": f"/api/collections/{command_id}",
                    "commandStatus": "SUCCEEDED",
                    "terminal": True,
                    "sourceType": "GIT",
                    "origin": "https://github.com/acme/demo",
                    "revision": "a" * 40,
                    "runtimeStatus": "NOT_PROVIDED",
                }
            )
        },
    }


def test_collection_status_reads_the_ledger_projection_consistently() -> None:
    client = Client(get_item=[{"Item": _collection_item()}])

    document = _port(client).get_collection(
        command_id="cmd-1", principal="reader", correlation_id="corr-api"
    )

    assert document["commandId"] == "cmd-1"
    assert document["terminal"] is True
    assert document["runtimeStatus"] == "NOT_PROVIDED"
    name, request = client.calls[0]
    assert name == "get_item"
    assert request["TableName"] == "ledger"
    assert request["Key"] == {"pk": {"S": "COLLECTION#cmd-1"}, "sk": {"S": "STATUS"}}
    assert request["ConsistentRead"] is True


def test_collection_status_of_an_unknown_command_is_a_stable_not_found() -> None:
    client = Client(get_item=[{}])

    with pytest.raises(ProductApiError) as caught:
        _port(client).get_collection(
            command_id="cmd-missing", principal="reader", correlation_id="corr-api"
        )

    assert caught.value.status_code == 404
    assert caught.value.code == "COLLECTION_NOT_FOUND"


def test_collection_status_is_reachable_through_the_neutral_product_route() -> None:
    client = Client(get_item=[{"Item": _collection_item("cmd-7")}])

    response = ProductApiService(_port(client)).handle(
        method="GET",
        path="/api/collections/cmd-7",
        query={},
        body=None,
        principal="reader",
        correlation_id="corr-api",
    )

    assert response.status_code == 200
    assert response.document["commandId"] == "cmd-7"
    assert response.headers == {}


def test_collection_submit_fails_closed_until_the_acquisition_stage_exists() -> None:
    client = Client()

    with pytest.raises(ProductApiError) as caught:
        _port(client).submit_collection(
            body={"sourceType": "GIT"}, principal="writer", correlation_id="corr-api"
        )

    assert caught.value.status_code == 501
    assert caught.value.code == "COLLECTION_SUBMIT_NOT_CONFIGURED"
    assert client.calls == [], "a refused submit must not touch AWS"


def _assert_snapshot_is_structurally_complete(snapshot: dict[str, Any]) -> None:
    # The web client only checks the three top-level blocks before casting the
    # rest, so any missing nested field would crash the control room.
    assert set(snapshot) == {
        "schemaVersion",
        "capturedAt",
        "status",
        "correlation",
        "queue",
        "coverage",
        "review",
        "publication",
        "productionSignals",
    }
    assert set(snapshot["correlation"]) == {"status", "trackedCount", "missingCount"}
    assert set(snapshot["queue"]) == {
        "status",
        "depth",
        "oldestAgeSeconds",
        "saturation",
        "retryCount",
        "deadLetterCount",
        "leaseStealCount",
    }
    assert set(snapshot["queue"]["saturation"]) == {"status", "observedDepth", "capacity"}
    assert set(snapshot["coverage"]) == {"status", "incompleteCount", "runtimeJoin", "baseline"}
    assert set(snapshot["coverage"]["runtimeJoin"]) == {"status", "joined", "eligible", "rate"}
    assert set(snapshot["coverage"]["baseline"]) == {"status", "ageSeconds", "maxAgeSeconds"}
    assert set(snapshot["review"]) == {"status", "oldestApprovalAgeSeconds"}
    assert set(snapshot["publication"]) == {
        "status",
        "publishLagSeconds",
        "pointerPackage",
        "watermark",
    }
    assert set(snapshot["publication"]["pointerPackage"]) == {
        "status",
        "activeVersion",
        "packageVersion",
    }
    assert set(snapshot["publication"]["watermark"]) == {
        "status",
        "version",
        "updatedAt",
        "ageSeconds",
    }
    assert set(snapshot["productionSignals"]) == {
        "replication",
        "errorBudgetBurn",
        "unitCost",
    }
    for signal in snapshot["productionSignals"].values():
        assert signal == {"status": "NOT_CONFIGURED", "value": None}


def test_resilience_reports_not_available_signals_when_nothing_is_recorded() -> None:
    client = Client(query=[{"Items": []}, {"Items": []}, {"Items": []}])
    control = Control(
        pointer={
            "environment": "staging",
            "graphVersion": "NONE",
            "fence": 0,
            "correlationId": None,
        }
    )

    snapshot = _port(client, control=control).resilience(
        environment=None, principal="reader", correlation_id="corr-api"
    )

    _assert_snapshot_is_structurally_complete(snapshot)
    assert snapshot["capturedAt"] == "2026-08-08T18:00:00Z"
    assert snapshot["status"] == "NOT_AVAILABLE"
    assert snapshot["queue"]["status"] == "NOT_AVAILABLE"
    assert snapshot["queue"]["oldestAgeSeconds"] is None
    assert snapshot["queue"]["saturation"]["status"] == "NOT_CONFIGURED"
    assert snapshot["correlation"] == {
        "status": "NOT_AVAILABLE",
        "trackedCount": 0,
        "missingCount": 0,
    }
    assert snapshot["coverage"]["status"] == "NOT_AVAILABLE"
    assert snapshot["coverage"]["runtimeJoin"] == {
        "status": "NOT_AVAILABLE",
        "joined": 0,
        "eligible": 0,
        "rate": None,
    }
    assert snapshot["coverage"]["baseline"] == {
        "status": "NOT_AVAILABLE",
        "ageSeconds": None,
        "maxAgeSeconds": 86_400,
    }
    assert snapshot["review"] == {"status": "HEALTHY", "oldestApprovalAgeSeconds": None}
    assert snapshot["publication"]["status"] == "NOT_AVAILABLE"
    assert snapshot["publication"]["pointerPackage"]["activeVersion"] is None
    assert snapshot["publication"]["watermark"]["status"] == "NOT_AVAILABLE"
    assert all(name == "query" for name, _kwargs in client.calls)


def _baseline_summary() -> dict[str, Any]:
    item = _run_summary("cmd-base")
    item["workflowKind"] = {"S": "BASELINE"}
    item["updatedAt"] = {"S": "2026-08-08T17:30:00Z"}
    return item


def _stage_item(stage_id: str, key: str) -> dict[str, Any]:
    return {
        "sk": {"S": f"STAGE#{stage_id}#idem"},
        "stageId": {"S": stage_id},
        "stageName": {"S": stage_id},
        "status": {"S": "COMPLETED"},
        "completedAt": {"S": "2026-08-08T17:05:00Z"},
        "output": {
            "S": json.dumps(
                {
                    "bucket": "evidence",
                    "key": key,
                    "versionId": "v1",
                    "sha256": "a" * 64,
                    "sizeBytes": 100,
                }
            )
        },
    }


def test_resilience_derives_signals_from_the_ledger_proposals_pointer_and_queues() -> None:
    deployment = _run_summary("cmd-deploy")
    deployment["workflowKind"] = {"S": "DEPLOYMENT"}
    del deployment["correlationId"]
    client = Client(
        query=[
            {"Items": [_run_summary("cmd-1"), deployment]},
            {"Items": [{"document": {"S": json.dumps(_proposal())}}]},
            {"Items": [_baseline_summary()]},
            {"Items": [_stage_item("I6", "I6.json"), _stage_item("I7", "I7.json")]},
        ]
    )
    sqs = Client(
        get_queue_attributes=[
            {
                "Attributes": {
                    "ApproximateNumberOfMessages": "2",
                    "ApproximateAgeOfOldestMessage": "12",
                }
            },
            {
                "Attributes": {
                    "ApproximateNumberOfMessages": "1",
                    "ApproximateAgeOfOldestMessage": "45",
                }
            },
            {"Attributes": {"ApproximateNumberOfMessages": "0"}},
        ]
    )
    control = Control()
    control.pointer["package"] = {
        "bucket": "packages",
        "key": "package.json",
        "versionId": "v1",
        "sha256": "d" * 64,
        "sizeBytes": 100,
    }
    control.pointer["activatedAt"] = "2026-08-08T17:58:30Z"
    artifacts = Artifacts(
        documents={
            "I6.json": {"runtimeCoverage": {"status": "VALIDATED"}},
            "I7.json": {"coverage": {"state": "COMPLETE"}},
        }
    )

    snapshot = _port(
        client,
        control=control,
        artifacts=artifacts,
        sqs=sqs,
        queue_urls=("http://queues/interactive", "http://queues/batch"),
        dead_letter_queue_urls=("http://queues/interactive-dlq",),
    ).resilience(environment=None, principal="reader", correlation_id="corr-api")

    _assert_snapshot_is_structurally_complete(snapshot)
    assert snapshot["queue"]["status"] == "HEALTHY"
    assert snapshot["queue"]["depth"] == 3
    assert snapshot["queue"]["oldestAgeSeconds"] == 45
    assert snapshot["queue"]["deadLetterCount"] == 0
    assert snapshot["correlation"] == {
        "status": "INCOMPLETE",
        "trackedCount": 2,
        "missingCount": 1,
    }
    assert snapshot["coverage"]["status"] == "COMPLETE"
    assert snapshot["coverage"]["runtimeJoin"] == {
        "status": "COMPLETE",
        "joined": 1,
        "eligible": 1,
        "rate": 1.0,
    }
    assert snapshot["coverage"]["baseline"]["status"] == "CURRENT"
    assert snapshot["coverage"]["baseline"]["ageSeconds"] == 1_800
    assert snapshot["review"]["status"] == "DEGRADED"
    assert snapshot["review"]["oldestApprovalAgeSeconds"] == 3_360
    assert snapshot["publication"]["status"] == "HEALTHY"
    assert snapshot["publication"]["pointerPackage"] == {
        "status": "IN_SYNC",
        "activeVersion": "graph-v7",
        "packageVersion": "graph-v7",
    }
    assert snapshot["publication"]["watermark"] == {
        "status": "CURRENT",
        "version": "graph-v7",
        "updatedAt": "2026-08-08T17:58:30Z",
        "ageSeconds": 90,
    }
    assert snapshot["status"] == "DEGRADED"
    queries = [kwargs for name, kwargs in client.calls if name == "query"]
    assert all(name == "query" for name, _kwargs in client.calls)
    assert queries[1]["ScanIndexForward"] is True, "oldest approval must come first"
    assert queries[2]["FilterExpression"].startswith("workflowKind")
    assert queries[3]["ExpressionAttributeValues"][":pk"] == {"S": "COMMAND#cmd-1"}
    assert [name for name, _kwargs in sqs.calls] == ["get_queue_attributes"] * 3
    assert [reference["key"] for reference in artifacts.reads] == ["I6.json", "I7.json"]


def test_resilience_queue_signal_degrades_on_dead_letters_and_old_messages() -> None:
    client = Client(query=[{"Items": []}, {"Items": []}, {"Items": []}])
    sqs = Client(
        get_queue_attributes=[
            {
                "Attributes": {
                    "ApproximateNumberOfMessages": "1",
                    "ApproximateAgeOfOldestMessage": "600",
                }
            },
            {"Attributes": {"ApproximateNumberOfMessages": "2"}},
        ]
    )

    control = Control(
        pointer={
            "environment": "staging",
            "graphVersion": "NONE",
            "fence": 0,
            "correlationId": None,
        }
    )

    snapshot = _port(
        client,
        control=control,
        sqs=sqs,
        queue_urls=("http://queues/batch",),
        dead_letter_queue_urls=("http://queues/batch-dlq",),
    ).resilience(environment=None, principal="reader", correlation_id="corr-api")

    assert snapshot["queue"]["status"] == "DEGRADED"
    assert snapshot["queue"]["depth"] == 1
    assert snapshot["queue"]["oldestAgeSeconds"] == 600
    assert snapshot["queue"]["deadLetterCount"] == 2
    assert snapshot["status"] == "DEGRADED"


def test_build_product_api_wires_the_lane_queues_from_the_environment() -> None:
    from lineage_api.infrastructure.aws.query_projection import build_product_api

    dynamodb = Client(query=[{"Items": []}, {"Items": []}, {"Items": []}])
    dynamodb.responses["get_item"] = [{}]
    sqs = Client(get_queue_attributes=[{"Attributes": {}}] * 3)
    service = build_product_api(
        env={
            "LINEAGE_CONTROL_TABLE": "control",
            "LINEAGE_LEDGER_TABLE": "ledger",
            "LINEAGE_PROPOSAL_TABLE": "proposal",
            "LINEAGE_POINTER_TABLE": "pointer",
            "LINEAGE_EVIDENCE_BUCKET": "evidence",
            "LINEAGE_PACKAGE_BUCKET": "packages",
            "LINEAGE_RUNTIME_STREAM": "runtime",
            "LINEAGE_NEPTUNE_ENDPOINT": "neptune.example.com",
            "LINEAGE_ENTERPRISE_ENDPOINT": "https://enterprise.example.com",
            "AWS_REGION": "us-east-1",
            "LINEAGE_BATCH_QUEUE_URL": "http://queues/batch",
            "LINEAGE_EVENTS_QUEUE_URL": "http://queues/events",
            "LINEAGE_BATCH_DLQ_URL": "http://queues/batch-dlq",
        },
        clients={"dynamodb": dynamodb, "s3": Client(), "neptunedata": Client(), "sqs": sqs},
    )

    response = service.handle(
        method="GET",
        path="/api/operations/resilience",
        query={},
        body=None,
        principal="reader",
        correlation_id="corr-api",
    )

    assert response.status_code == 200
    assert response.document["queue"]["status"] in {"HEALTHY", "DEGRADED"}
    polled = sorted(kwargs["QueueUrl"] for _name, kwargs in sqs.calls)
    assert polled == [
        "http://queues/batch",
        "http://queues/batch-dlq",
        "http://queues/events",
    ]


def test_overview_embeds_the_resilience_snapshot() -> None:
    client = Client(query=[{"Items": []}] * 5)

    overview = _port(client).overview(
        environment=None, principal="reader", correlation_id="corr-api"
    )

    assert overview["activeVersion"] == "graph-v7"
    _assert_snapshot_is_structurally_complete(overview["resilience"])
    assert (
        overview["resilience"]["publication"]["pointerPackage"]["activeVersion"]
        == "graph-v7"
    )
