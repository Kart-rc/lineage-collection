from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import Any

import pytest


MODULES = (
    "intake",
    "control_stage",
    "runtime_validation",
    "consolidation",
    "coverage",
    "proposal",
    "publication",
    "deployment",
)


@dataclass
class FakeExecutor:
    calls: list[tuple[str, dict[str, Any]]]

    def execute(self, stage: str, envelope: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((stage, envelope))
        return {
            "outcome": "SUCCEEDED",
            "output": {
                "bucket": "lineage-results",
                "key": f"results/{envelope['commandId']}/{stage}.json",
                "versionId": "v1",
                "sha256": "a" * 64,
                "sizeBytes": 128,
            },
        }


def event() -> dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "commandId": "cmd-001",
        "correlationId": "corr-001",
        "causationId": "cause-001",
        "idempotencyKey": "idem-001",
        "input": {
            "bucket": "lineage-input",
            "key": "commands/cmd-001.json",
            "versionId": "version-001",
            "sha256": "b" * 64,
            "sizeBytes": 512,
        },
    }


@pytest.mark.parametrize("module_name", MODULES)
def test_each_lambda_handler_uses_a_port_and_returns_a_bounded_reference(
    module_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module(f"lineage_api.entrypoints.aws.{module_name}")
    common = importlib.import_module("lineage_api.entrypoints.aws.common")
    fake = FakeExecutor([])
    monkeypatch.setattr(common, "executor_factory", lambda: fake)

    result = module.handler(event(), object())

    if module_name == "deployment":
        assert [call[1]["stageId"] for call in fake.calls] == [
            "D1",
            "D2",
            "D3",
            "D4",
            "D5",
            "D6",
        ]
    else:
        assert fake.calls == [(module.STAGE, event())]
    assert result["schemaVersion"] == "1.0.0"
    assert result["commandId"] == "cmd-001"
    assert result["correlationId"] == "corr-001"
    assert result["outcome"] == "SUCCEEDED"
    assert set(result["output"]) == {"bucket", "key", "versionId", "sha256", "sizeBytes"}
    assert len(json.dumps(result)) < 8_192


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schemaVersion", "2.0.0"),
        ("commandId", ""),
        ("correlationId", None),
        ("idempotencyKey", ""),
        ("input", {"bucket": "inline-payload"}),
    ],
)
def test_handler_rejects_invalid_or_unversioned_envelopes(
    field: str, value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("lineage_api.entrypoints.aws.intake")
    common = importlib.import_module("lineage_api.entrypoints.aws.common")
    fake = FakeExecutor([])
    monkeypatch.setattr(common, "executor_factory", lambda: fake)
    invalid = event()
    invalid[field] = value

    with pytest.raises(ValueError, match="envelope"):
        module.handler(invalid, object())
    assert fake.calls == []


def test_handlers_do_not_import_the_local_sqlite_composition_root() -> None:
    for module_name in MODULES:
        module = importlib.import_module(f"lineage_api.entrypoints.aws.{module_name}")
        source = module.__loader__.get_source(module.__name__)  # type: ignore[union-attr]
        assert "lineage_api.dependencies" not in source
        assert "sqlite" not in source.lower()
