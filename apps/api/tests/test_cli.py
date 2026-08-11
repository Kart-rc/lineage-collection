from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


class FakeOrchestration:
    def __init__(self) -> None:
        self.once_calls = 0

    def worker_once(self) -> dict[str, object] | None:
        self.once_calls += 1
        return {"commandId": f"command-{self.once_calls}", "status": "COMPLETED"}

    def worker_drain(self, max_messages: int) -> list[dict[str, object]]:
        return [self.worker_once() for _ in range(min(max_messages, 2))]


@pytest.fixture
def fake_services(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    services = SimpleNamespace(orchestration=FakeOrchestration())
    monkeypatch.setattr("lineage_api.cli.build_services", lambda _: services)
    monkeypatch.setattr("lineage_api.cli.Settings.from_environment", lambda: object())
    return services


def test_worker_once_runs_the_shared_bounded_handler(
    fake_services: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    from lineage_api.cli import run

    assert run(["worker", "--once"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "processed": 1,
        "results": [{"commandId": "command-1", "status": "COMPLETED"}],
    }


def test_worker_drain_honors_the_max_messages_bound(
    fake_services: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    from lineage_api.cli import run

    assert run(["worker", "--drain", "--max-messages", "2"]) == 0
    assert json.loads(capsys.readouterr().out)["processed"] == 2
