"""Provision the floci AWS emulator with the lineage platform's resources.

Creates the DynamoDB tables (with the GSIs the adapters query), versioned S3
buckets, SQS lanes, the Kinesis runtime stream, the intake EventBridge bus, and
four minimal Step Functions state machines so the real ``StepFunctionsWorkflowStarter``
can exercise ``states:StartExecution`` name-idempotency.

Ends with a compatibility probe covering every AWS behaviour the adapters rely
on; aborts with a written report if any probe fails.

Usage:
    uv run --project apps/api python scripts/floci/provision_floci.py
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

ENDPOINT = "http://localhost:4566"
REGION = "us-east-1"
ENV_FILE = Path(__file__).parent / "floci.env"
REPORT_FILE = Path(__file__).parent / "floci-incompatibility-report.md"

TABLES = {
    "lineage-control": None,
    "lineage-ledger": "RunsByUpdatedAt",
    "lineage-proposal": "ProposalsByState",
    "lineage-pointer": None,
}
BUCKETS = ["lineage-evidence", "lineage-packages"]
STATE_MACHINES = ["lineage-baseline", "lineage-incremental", "lineage-pr-gate", "lineage-nightly"]


def client(name: str):
    return boto3.client(
        name,
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(retries={"max_attempts": 3}),
    )


def create_tables(ddb) -> None:
    existing = ddb.list_tables()["TableNames"]
    for table, gsi in TABLES.items():
        if table in existing:
            continue
        spec: dict = {
            "TableName": table,
            "AttributeDefinitions": [
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            "KeySchema": [
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
        }
        if gsi:
            spec["AttributeDefinitions"] += [
                {"AttributeName": "queryPk", "AttributeType": "S"},
                {"AttributeName": "querySk", "AttributeType": "S"},
            ]
            spec["GlobalSecondaryIndexes"] = [
                {
                    "IndexName": gsi,
                    "KeySchema": [
                        {"AttributeName": "queryPk", "KeyType": "HASH"},
                        {"AttributeName": "querySk", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ]
        ddb.create_table(**spec)
        ddb.get_waiter("table_exists").wait(TableName=table)


def create_buckets(s3) -> None:
    for bucket in BUCKETS:
        try:
            s3.create_bucket(Bucket=bucket)
        except ClientError as error:
            if error.response["Error"]["Code"] not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                raise
        s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})


def create_queues(sqs) -> dict[str, str]:
    urls = {}
    for name, attrs in [
        ("lineage-interactive.fifo", {"FifoQueue": "true", "ContentBasedDeduplication": "true", "VisibilityTimeout": "360"}),
        ("lineage-events.fifo", {"FifoQueue": "true", "ContentBasedDeduplication": "true", "VisibilityTimeout": "360"}),
        ("lineage-batch", {"VisibilityTimeout": "360"}),
    ]:
        urls[name] = sqs.create_queue(QueueName=name, Attributes=attrs)["QueueUrl"]
    return urls


def create_stream(kinesis) -> None:
    try:
        kinesis.create_stream(StreamName="lineage-runtime", ShardCount=1)
    except ClientError as error:
        if error.response["Error"]["Code"] != "ResourceInUseException":
            raise
    kinesis.get_waiter("stream_exists").wait(StreamName="lineage-runtime")


def create_bus(events) -> None:
    events.create_event_bus(Name="lineage-intake")


def create_state_machines(sfn) -> dict[str, str]:
    role = "arn:aws:iam::000000000000:role/lineage-sfn"
    definition = json.dumps({"StartAt": "Done", "States": {"Done": {"Type": "Succeed"}}})
    arns = {}
    existing = {m["name"]: m["stateMachineArn"] for m in sfn.list_state_machines()["stateMachines"]}
    for name in STATE_MACHINES:
        if name in existing:
            arns[name] = existing[name]
        else:
            arns[name] = sfn.create_state_machine(name=name, definition=definition, roleArn=role)["stateMachineArn"]
    return arns


def probe(ddb, s3, sqs, kinesis, sfn) -> list[str]:
    failures: list[str] = []
    marker = uuid.uuid4().hex

    # S3: versioned put must return a VersionId; IfNoneMatch="*" must conflict.
    try:
        put = s3.put_object(Bucket="lineage-evidence", Key=f"probe/{marker}", Body=b"one", IfNoneMatch="*")
        if not put.get("VersionId"):
            failures.append("S3 put_object returned no VersionId on a versioned bucket")
        else:
            got = s3.get_object(Bucket="lineage-evidence", Key=f"probe/{marker}", VersionId=put["VersionId"])
            if got["Body"].read() != b"one":
                failures.append("S3 versioned get returned wrong body")
        try:
            s3.put_object(Bucket="lineage-evidence", Key=f"probe/{marker}", Body=b"two", IfNoneMatch="*")
            failures.append("S3 IfNoneMatch='*' did not raise on existing key")
        except ClientError as error:
            code = error.response["Error"]["Code"]
            if code not in {"PreconditionFailed", "412", "ConditionalRequestConflict"}:
                failures.append(f"S3 IfNoneMatch conflict raised unexpected code {code}")
    except ClientError as error:
        failures.append(f"S3 probe failed outright: {error}")

    # DynamoDB: conditional put, TransactWriteItems with ClientRequestToken,
    # condition failure surfaced, GSI query.
    try:
        ddb.put_item(
            TableName="lineage-control",
            Item={"pk": {"S": f"PROBE#{marker}"}, "sk": {"S": "STATE"}, "value": {"N": "1"}},
            ConditionExpression="attribute_not_exists(pk)",
        )
        try:
            ddb.put_item(
                TableName="lineage-control",
                Item={"pk": {"S": f"PROBE#{marker}"}, "sk": {"S": "STATE"}, "value": {"N": "2"}},
                ConditionExpression="attribute_not_exists(pk)",
            )
            failures.append("DynamoDB conditional put did not fail on existing item")
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                failures.append(f"DynamoDB conditional failure code: {error.response['Error']['Code']}")
        ddb.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": "lineage-control",
                        "Key": {"pk": {"S": f"PROBE#{marker}"}, "sk": {"S": "STATE"}},
                        "UpdateExpression": "SET #v = :v",
                        "ConditionExpression": "#v = :prev",
                        "ExpressionAttributeNames": {"#v": "value"},
                        "ExpressionAttributeValues": {":v": {"N": "2"}, ":prev": {"N": "1"}},
                    }
                }
            ],
            ClientRequestToken=marker[:32],
        )
        try:
            ddb.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": "lineage-control",
                            "Key": {"pk": {"S": f"PROBE#{marker}"}, "sk": {"S": "STATE"}},
                            "UpdateExpression": "SET #v = :v",
                            "ConditionExpression": "#v = :prev",
                            "ExpressionAttributeNames": {"#v": "value"},
                            "ExpressionAttributeValues": {":v": {"N": "9"}, ":prev": {"N": "1"}},
                        }
                    }
                ],
                ClientRequestToken=uuid.uuid4().hex[:32],
            )
            failures.append("DynamoDB transact condition did not fail when stale")
        except ClientError as error:
            if error.response["Error"]["Code"] not in {"TransactionCanceledException", "ConditionalCheckFailedException"}:
                failures.append(f"DynamoDB transact failure code: {error.response['Error']['Code']}")
        ddb.put_item(
            TableName="lineage-ledger",
            Item={
                "pk": {"S": f"PROBE#{marker}"},
                "sk": {"S": "RUN"},
                "queryPk": {"S": "RUN"},
                "querySk": {"S": f"2026-01-01T00:00:00Z#{marker}"},
            },
        )
        result = ddb.query(
            TableName="lineage-ledger",
            IndexName="RunsByUpdatedAt",
            KeyConditionExpression="queryPk = :run",
            ExpressionAttributeValues={":run": {"S": "RUN"}},
            Limit=1,
        )
        if not result["Items"]:
            failures.append("DynamoDB GSI query returned no items")
    except ClientError as error:
        failures.append(f"DynamoDB probe failed outright: {error}")

    # SQS FIFO: send/receive with group + dedupe.
    try:
        url = sqs.get_queue_url(QueueName="lineage-events.fifo")["QueueUrl"]
        sqs.send_message(QueueUrl=url, MessageBody=json.dumps({"probe": marker}), MessageGroupId="probe")
        messages = sqs.receive_message(QueueUrl=url, WaitTimeSeconds=2).get("Messages", [])
        if not messages:
            failures.append("SQS FIFO receive returned nothing")
        else:
            sqs.delete_message(QueueUrl=url, ReceiptHandle=messages[0]["ReceiptHandle"])
    except ClientError as error:
        failures.append(f"SQS probe failed outright: {error}")

    # Kinesis: put + read back through a shard iterator.
    try:
        kinesis.put_record(StreamName="lineage-runtime", Data=json.dumps({"probe": marker}).encode(), PartitionKey="probe")
        shard = kinesis.describe_stream(StreamName="lineage-runtime")["StreamDescription"]["Shards"][0]["ShardId"]
        iterator = kinesis.get_shard_iterator(
            StreamName="lineage-runtime", ShardId=shard, ShardIteratorType="TRIM_HORIZON"
        )["ShardIterator"]
        records = kinesis.get_records(ShardIterator=iterator)["Records"]
        if not any(marker.encode() in r["Data"] for r in records):
            failures.append("Kinesis get_records did not return the probe record")
    except ClientError as error:
        failures.append(f"Kinesis probe failed outright: {error}")

    # Step Functions: StartExecution twice with the same name must conflict.
    try:
        arn = next(
            m["stateMachineArn"] for m in sfn.list_state_machines()["stateMachines"] if m["name"] == "lineage-baseline"
        )
        name = f"probe-{marker[:16]}"
        sfn.start_execution(stateMachineArn=arn, name=name, input="{}")
        try:
            sfn.start_execution(stateMachineArn=arn, name=name, input="{}")
            failures.append("Step Functions duplicate execution name did not conflict")
        except ClientError as error:
            if error.response["Error"]["Code"] != "ExecutionAlreadyExists":
                failures.append(f"Step Functions duplicate code: {error.response['Error']['Code']}")
    except (ClientError, StopIteration) as error:
        failures.append(f"Step Functions probe failed outright: {error}")

    return failures


def write_env(queue_urls: dict[str, str], machine_arns: dict[str, str]) -> None:
    lines = [
        f"AWS_ENDPOINT_URL={ENDPOINT}",
        "AWS_ACCESS_KEY_ID=test",
        "AWS_SECRET_ACCESS_KEY=test",
        f"AWS_REGION={REGION}",
        f"AWS_DEFAULT_REGION={REGION}",
        "LINEAGE_CONTROL_TABLE=lineage-control",
        "LINEAGE_LEDGER_TABLE=lineage-ledger",
        "LINEAGE_PROPOSAL_TABLE=lineage-proposal",
        "LINEAGE_POINTER_TABLE=lineage-pointer",
        "LINEAGE_EVIDENCE_BUCKET=lineage-evidence",
        "LINEAGE_PACKAGE_BUCKET=lineage-packages",
        "LINEAGE_RUNTIME_STREAM=lineage-runtime",
        "LINEAGE_NEPTUNE_ENDPOINT=localhost",
        "LINEAGE_ENTERPRISE_ENDPOINT=https://enterprise.invalid",
        f"LINEAGE_INTERACTIVE_QUEUE_URL={queue_urls['lineage-interactive.fifo']}",
        f"LINEAGE_EVENTS_QUEUE_URL={queue_urls['lineage-events.fifo']}",
        f"LINEAGE_BATCH_QUEUE_URL={queue_urls['lineage-batch']}",
        f"LINEAGE_BASELINE_WORKFLOW_ALIAS_ARN={machine_arns['lineage-baseline']}",
        f"LINEAGE_INCREMENTAL_WORKFLOW_ALIAS_ARN={machine_arns['lineage-incremental']}",
        f"LINEAGE_PR_GATE_WORKFLOW_ALIAS_ARN={machine_arns['lineage-pr-gate']}",
        f"LINEAGE_NIGHTLY_WORKFLOW_ALIAS_ARN={machine_arns['lineage-nightly']}",
        "LINEAGE_BASELINE_MAP_CONCURRENCY=1",
        "LINEAGE_JAVA_HOME=/opt/homebrew/opt/openjdk@21",
    ]
    ENV_FILE.write_text("\n".join(lines) + "\n")


def main() -> int:
    ddb = client("dynamodb")
    s3 = client("s3")
    sqs = client("sqs")
    kinesis = client("kinesis")
    events = client("events")
    sfn = client("stepfunctions")

    create_tables(ddb)
    create_buckets(s3)
    queue_urls = create_queues(sqs)
    create_stream(kinesis)
    create_bus(events)
    machine_arns = create_state_machines(sfn)
    write_env(queue_urls, machine_arns)

    failures = probe(ddb, s3, sqs, kinesis, sfn)
    # The probe writes marker rows; remove them so they never pollute product
    # queries (the run listing scans the RunsByUpdatedAt GSI).
    for table in ("lineage-ledger", "lineage-control"):
        for page in ddb.get_paginator("scan").paginate(TableName=table):
            for item in page["Items"]:
                if item["pk"]["S"].startswith("PROBE#"):
                    ddb.delete_item(
                        TableName=table, Key={"pk": item["pk"], "sk": item["sk"]}
                    )
    if failures:
        report = "# floci compatibility probe failures\n\n" + "\n".join(f"- {f}" for f in failures) + "\n"
        REPORT_FILE.write_text(report)
        print(report, file=sys.stderr)
        print(f"PROBE FAILED — report written to {REPORT_FILE}", file=sys.stderr)
        return 1
    print(f"PROBE OK — env written to {ENV_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
