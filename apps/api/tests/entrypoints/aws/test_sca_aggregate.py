from __future__ import annotations

from typing import Any

import pytest

from lineage_api.entrypoints.aws import control_stage, sca_aggregate


def _reference(key: str) -> dict[str, object]:
    return {
        "bucket": "evidence",
        "key": key,
        "versionId": "v1",
        "sha256": "a" * 64,
        "sizeBytes": 1024,
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
        "workInventory": _reference("inventory.json"),
        "mapResult": {
            "MapRunArn": "arn:aws:states:us-east-1:111111111111:mapRun:baseline/Map:map-1",
            "ResultWriterDetails": {
                "Bucket": "evidence",
                "Key": "workflow-results/cmd-baseline/B5/map-1/manifest.json",
            },
        },
    }


def test_control_lambda_routes_only_the_explicit_baseline_aggregation_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class Executor:
        def execute(self, event: dict[str, Any]) -> dict[str, Any]:
            calls.append(event)
            return {
                "outcome": "SUCCEEDED",
                "output": _reference("commands/cmd-baseline/stages/B5A/result.json"),
            }

    monkeypatch.setattr(sca_aggregate, "executor_factory", lambda: Executor())

    result = control_stage.handler(_event(), object())

    assert calls == [_event()]
    assert result["outcome"] == "SUCCEEDED"
    assert result["output"]["key"].endswith("result.json")
    assert result["commandId"] == "cmd-baseline"


def test_aggregation_handler_rejects_an_unbounded_or_drifted_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Executor:
        def execute(self, _event: dict[str, Any]) -> dict[str, Any]:
            return {
                "outcome": "SUCCEEDED",
                "output": _reference("result.json"),
                "unexpected": True,
            }

    monkeypatch.setattr(sca_aggregate, "executor_factory", lambda: Executor())

    with pytest.raises(ValueError, match="aggregation outcome"):
        sca_aggregate.handler(_event(), object())
