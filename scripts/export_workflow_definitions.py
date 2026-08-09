#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from lineage_api.application.stage_ownership import StageOwner, owner_for_stage
from lineage_api.application.workflows.definitions import WORKFLOWS, StageDefinition, WorkflowDefinition


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "infra/workflows/generated/workflow-contracts.json"
ASL_PATHS = {
    "BASELINE": ROOT / "infra/workflows/baseline.asl.json",
    "INCREMENTAL": ROOT / "infra/workflows/incremental.asl.json",
    "PR_GATE": ROOT / "infra/workflows/pr-gate.asl.json",
    "NIGHTLY": ROOT / "infra/workflows/nightly.asl.json",
}
TARGET_BY_OWNER = {
    StageOwner.CONTROL: "ControlAliasArn",
    StageOwner.CLASSIFICATION: "ClassificationAliasArn",
    StageOwner.COVERAGE: "CoverageAliasArn",
    StageOwner.SCA: "ScaTask",
    StageOwner.RUNTIME_VALIDATION: "RuntimeValidationAliasArn",
    StageOwner.CONSOLIDATION: "ConsolidationAliasArn",
    StageOwner.PROPOSAL: "ProposalAliasArn",
    StageOwner.PUBLICATION: "PublicationAliasArn",
}


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _stage(stage: StageDefinition) -> dict[str, Any]:
    return {
        "stageId": stage.stage_id,
        "name": stage.name,
        "inputContracts": list(stage.input_contracts),
        "outputContracts": list(stage.output_contracts),
        "timeoutSeconds": stage.timeout_seconds,
        "maxAttempts": stage.max_attempts,
        "retryClass": stage.retry_class,
        "sideEffectMode": stage.side_effect_mode,
        "idempotencyDeterminants": list(stage.idempotency_determinants),
        "onSuccess": list(stage.on_success),
        "onError": list(stage.on_error),
    }


def contract_document() -> dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "workflows": {
            kind: {
                "kind": workflow.kind,
                "version": workflow.version,
                "entryStage": workflow.entry_stage,
                "workflowTimeoutSeconds": workflow.workflow_timeout_seconds,
                "stages": [_stage(stage) for stage in workflow.stages],
                "terminalStates": list(workflow.terminal_states),
            }
            for kind, workflow in sorted(WORKFLOWS.items())
        },
    }


def _terminal_state(name: str, workflow: WorkflowDefinition) -> dict[str, Any]:
    successful = name in {workflow.terminal_states[0], "NO_LINEAGE", "NO_LINEAGE_IMPACT", "PASS", "WARN", "BLOCK", "PROPOSALS_RAISED"}
    return {"Type": "Succeed"} if successful else {"Type": "Fail", "Error": name}


def _terminal_route(workflow: WorkflowDefinition) -> dict[str, Any]:
    default = workflow.terminal_states[0]
    return {
        "Type": "Choice",
        "Choices": [
            {
                "Variable": "$.terminalOutcome",
                "StringEquals": terminal,
                "Next": terminal,
            }
            for terminal in workflow.terminal_states
            if terminal != default
        ],
        "Default": default,
    }


def _retry(stage: StageDefinition) -> list[dict[str, Any]]:
    if stage.retry_class == "NONE":
        return []
    return [
        {
            "ErrorEquals": [
                "Lambda.ServiceException",
                "Lambda.SdkClientException",
                "Lambda.TooManyRequestsException",
                "ECS.AmazonECSException",
                "States.Timeout",
            ],
            "IntervalSeconds": 2,
            "BackoffRate": 2,
            "MaxAttempts": stage.max_attempts,
        }
    ]


def _error_terminal(workflow: WorkflowDefinition) -> str:
    first_error = workflow.stages[0].on_error[0]
    return first_error.removeprefix("terminal:")


def _input_path(previous: StageDefinition | None, previous_target: str | None) -> str:
    if previous is None:
        return "$.input"
    # A distributed Map's ResultWriter result is an export manifest, not a
    # single stage artifact reference. The executable slice continues from the
    # original pinned input while B5's individual outputs remain in S3.
    if previous.stage_id == "B5":
        return "$.input"
    if previous_target == "ScaTask":
        return f"$._{previous.stage_id}.output"
    return f"$._{previous.stage_id}.Payload.output"


def _lambda_task(
    workflow: WorkflowDefinition,
    stage: StageDefinition,
    target: str,
    input_path: str,
    next_state: str,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "Type": "Task",
        "Resource": "arn:aws:states:::lambda:invoke",
        "TimeoutSeconds": stage.timeout_seconds,
        "Parameters": {
            "FunctionName": f"${{{target}}}",
            "Payload": {
                "schemaVersion.$": "$.schemaVersion",
                "commandId.$": "$.commandId",
                "correlationId.$": "$.correlationId",
                "causationId.$": "$.causationId",
                "idempotencyKey.$": f"States.Format('{{}}:{stage.stage_id}', $.idempotencyKey)",
                "workflowKind": workflow.kind,
                "workflowVersion": workflow.version,
                "stageId": stage.stage_id,
                "stageName": stage.name,
                "determinantDigest.$": "$.determinantDigest",
                "input.$": input_path,
            },
        },
        "ResultPath": f"$._{stage.stage_id}",
        "Retry": _retry(stage),
        "Catch": [
            {
                "ErrorEquals": ["States.ALL"],
                "ResultPath": "$.workflowError",
                "Next": _error_terminal(workflow),
            }
        ],
        "Next": f"{stage.stage_id}Outcome",
    }
    return state


def _ecs_task(
    workflow: WorkflowDefinition,
    stage: StageDefinition,
    next_state: str,
) -> dict[str, Any]:
    return {
        "Type": "Task",
        "Resource": "arn:aws:states:::ecs:runTask.waitForTaskToken",
        "TimeoutSeconds": stage.timeout_seconds,
        "HeartbeatSeconds": max(30, min(300, stage.timeout_seconds // 3)),
        "Parameters": {
            "Cluster": "${ScaClusterArn}",
            "TaskDefinition": "${ScaTaskDefinitionArn}",
            "LaunchType": "FARGATE",
            "NetworkConfiguration": {
                "AwsvpcConfiguration": {
                    "AssignPublicIp": "DISABLED",
                    "Subnets": ["${Subnet0}", "${Subnet1}", "${Subnet2}"],
                    "SecurityGroups": ["${RuntimeSecurityGroupId}"],
                }
            },
            "Overrides": {
                "ContainerOverrides": [
                    {
                        "Name": "lineage-sca",
                        "Environment": [
                            {"Name": "LINEAGE_TASK_TOKEN", "Value.$": "$$.Task.Token"},
                            {"Name": "LINEAGE_STAGE_ENVELOPE", "Value.$": "States.JsonToString($)"},
                            {"Name": "LINEAGE_STAGE_ID", "Value": stage.stage_id},
                            {"Name": "LINEAGE_STAGE_NAME", "Value": stage.name},
                            {"Name": "LINEAGE_WORKFLOW_KIND", "Value": workflow.kind},
                            {"Name": "LINEAGE_WORKFLOW_VERSION", "Value": workflow.version},
                        ],
                    }
                ]
            },
        },
        "ResultPath": f"$._{stage.stage_id}",
        "Retry": _retry(stage),
        "Catch": [
            {
                "ErrorEquals": ["States.ALL"],
                "ResultPath": "$.workflowError",
                "Next": _error_terminal(workflow),
            }
        ],
        "Next": f"{stage.stage_id}Outcome",
    }


def _outcome_route(stage: StageDefinition, next_state: str, target: str, error: str) -> dict[str, Any]:
    outcome_path = f"$._{stage.stage_id}.outcome" if target == "ScaTask" else f"$._{stage.stage_id}.Payload.outcome"
    return {
        "Type": "Choice",
        "Choices": [
            {"Variable": outcome_path, "StringEquals": "REDRIVE_REQUIRED", "Next": error}
        ],
        "Default": next_state,
    }


def asl_document(workflow: WorkflowDefinition) -> dict[str, Any]:
    states: dict[str, Any] = {}
    previous: StageDefinition | None = None
    previous_target: str | None = None
    for index, stage in enumerate(workflow.stages):
        target = TARGET_BY_OWNER[owner_for_stage(workflow.kind, stage.stage_id)]
        next_state = workflow.stages[index + 1].stage_id if index + 1 < len(workflow.stages) else "TerminalRoute"
        input_path = _input_path(previous, previous_target)
        if stage.stage_id == "B5":
            worker = _ecs_task(workflow, stage, "B5WorkerOutcome")
            worker["Catch"] = [
                {
                    "ErrorEquals": ["States.ALL"],
                    "ResultPath": "$.workerError",
                    "Next": "B5WorkerFailed",
                }
            ]
            states[stage.stage_id] = {
                "Type": "Map",
                "ItemReader": {
                    "Resource": "arn:aws:states:::s3:getObject",
                    "ReaderConfig": {"InputType": "JSON"},
                    "Parameters": {
                        "Bucket.$": "$._B4.Payload.output.bucket",
                        "Key.$": "$._B4.Payload.output.key",
                        "VersionId.$": "$._B4.Payload.output.versionId",
                    },
                },
                "ItemProcessor": {
                    "ProcessorConfig": {"Mode": "DISTRIBUTED", "ExecutionType": "STANDARD"},
                    "StartAt": "B5Worker",
                    "States": {
                        "B5Worker": worker,
                        "B5WorkerOutcome": {
                            "Type": "Choice",
                            "Choices": [
                                {
                                    "Variable": "$._B5.outcome",
                                    "StringEquals": "REDRIVE_REQUIRED",
                                    "Next": "B5WorkerFailed",
                                }
                            ],
                            "Default": "B5WorkerSucceeded",
                        },
                        "B5WorkerSucceeded": {"Type": "Succeed"},
                        "B5WorkerFailed": {
                            "Type": "Fail",
                            "Error": "REDRIVE_REQUIRED",
                        },
                    },
                },
                "ItemSelector": {
                    "schemaVersion.$": "$.schemaVersion",
                    "commandId.$": "$.commandId",
                    "correlationId.$": "$.correlationId",
                    "causationId.$": "$.causationId",
                    "idempotencyKey.$": "States.Format('{}:B5:{}', $.idempotencyKey, $$.Map.Item.Index)",
                    "determinantDigest.$": "$.determinantDigest",
                    "input.$": "$$.Map.Item.Value",
                },
                "MaxConcurrencyPath": "$.baselineMapConcurrency",
                "ResultWriter": {
                    "Resource": "arn:aws:states:::s3:putObject",
                    "Parameters": {
                        "Bucket": "${EvidenceBucketName}",
                        "Prefix.$": "States.Format('workflow-results/{}/B5', $.commandId)",
                    },
                },
                "ResultPath": "$._B5",
                "Catch": [
                    {"ErrorEquals": ["States.ALL"], "ResultPath": "$.workflowError", "Next": _error_terminal(workflow)}
                ],
                "Next": next_state,
            }
        elif target == "ScaTask":
            states[stage.stage_id] = _ecs_task(workflow, stage, next_state)
            states[f"{stage.stage_id}Outcome"] = _outcome_route(
                stage, next_state, target, _error_terminal(workflow)
            )
        else:
            states[stage.stage_id] = _lambda_task(workflow, stage, target, input_path, next_state)
            states[f"{stage.stage_id}Outcome"] = _outcome_route(
                stage, next_state, target, _error_terminal(workflow)
            )
        previous = stage
        previous_target = target
    states["TerminalRoute"] = _terminal_route(workflow)
    for terminal in workflow.terminal_states:
        states[terminal] = _terminal_state(terminal, workflow)
    return {
        "Comment": f"Generated {workflow.kind} {workflow.version}; large payloads remain versioned S3 references",
        "StartAt": workflow.entry_stage,
        "TimeoutSeconds": workflow.workflow_timeout_seconds,
        "States": states,
    }


def outputs() -> dict[Path, str]:
    rendered = {CONTRACT_PATH: _json(contract_document())}
    for kind, path in ASL_PATHS.items():
        rendered[path] = _json(asl_document(WORKFLOWS[kind]))
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    drift: list[str] = []
    for path, rendered in outputs().items():
        if args.check:
            if not path.exists() or path.read_text() != rendered:
                drift.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered)
    if drift:
        raise SystemExit("workflow definition drift: " + ", ".join(drift))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
