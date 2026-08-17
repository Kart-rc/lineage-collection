"""The signing secret and API token must fail closed, not fall back to a known value.

`LINEAGE_WEBHOOK_SECRET` authenticates every signed delivery the platform accepts:
push deliveries, deployment outcomes, and (via a derived key) runtime observations.
A deployment that silently fell back to the demo secret published in this repository
would let anyone who can read the source forge lineage and deployment promotions, so
the resolution refuses to guess.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lineage_api.config import DEMO_WEBHOOK_SECRET, Settings
from lineage_api.main import create_app


STRONG_SECRET = "a-properly-long-production-secret"
STRONG_TOKEN = "a-properly-long-api-token-value"


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LINEAGE_WEBHOOK_SECRET", "LINEAGE_DEV_MODE", "LINEAGE_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def test_missing_secret_is_refused_rather_than_defaulted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    with pytest.raises(ValueError, match="LINEAGE_WEBHOOK_SECRET must be set"):
        Settings.from_environment()


def test_published_demo_secret_is_refused_outside_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("LINEAGE_WEBHOOK_SECRET", DEMO_WEBHOOK_SECRET)
    with pytest.raises(ValueError, match="published demo secret"):
        Settings.from_environment()


def test_short_secret_is_refused_outside_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("LINEAGE_WEBHOOK_SECRET", "short")
    with pytest.raises(ValueError, match="at least"):
        Settings.from_environment()


def test_development_mode_permits_the_demo_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("LINEAGE_DEV_MODE", "1")
    assert Settings.from_environment().webhook_secret == DEMO_WEBHOOK_SECRET


def test_strong_secret_is_accepted_and_token_defaults_to_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("LINEAGE_WEBHOOK_SECRET", STRONG_SECRET)
    settings = Settings.from_environment()
    assert settings.webhook_secret == STRONG_SECRET
    assert settings.api_token is None


def test_short_api_token_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("LINEAGE_WEBHOOK_SECRET", STRONG_SECRET)
    monkeypatch.setenv("LINEAGE_API_TOKEN", "tiny")
    with pytest.raises(ValueError, match="LINEAGE_API_TOKEN must be at least"):
        Settings.from_environment()


def _settings(tmp_path: Path, *, api_token: str | None) -> Settings:
    data = tmp_path / "state"
    return Settings(
        project_root=tmp_path,
        data_directory=data,
        fixture_directory=Path(__file__).resolve().parents[3] / "fixtures",
        database_path=data / "lineage.db",
        object_directory=data / "objects",
        webhook_secret=STRONG_SECRET,
        api_token=api_token,
    )


def test_configured_token_gates_reads_and_writes_but_not_liveness(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(_settings(tmp_path, api_token=STRONG_TOKEN)))

    # Liveness stays open so an orchestrator can probe without a credential.
    assert client.get("/healthz").status_code == 200

    # Reads are gated alongside writes: the graph discloses the estate's schema
    # and topology, so it is not safe merely because it mutates nothing.
    assert client.get("/api/overview").status_code == 401
    assert client.post("/api/demo/reset").status_code == 401

    wrong = {"Authorization": "Bearer " + "x" * len(STRONG_TOKEN)}
    assert client.get("/api/overview", headers=wrong).status_code == 401

    malformed = {"Authorization": STRONG_TOKEN}
    assert client.get("/api/overview", headers=malformed).status_code == 401

    correct = {"Authorization": f"Bearer {STRONG_TOKEN}"}
    assert client.get("/api/overview", headers=correct).status_code == 200


def test_absent_token_leaves_the_local_operator_run_open(tmp_path: Path) -> None:
    client = TestClient(create_app(_settings(tmp_path, api_token=None)))
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/overview").status_code == 200


def test_importing_the_module_does_not_require_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ASGI app is built on attribute access, not at import.

    Tooling that supplies its own Settings must be able to import this module
    without the environment of a running server.
    """
    _clear(monkeypatch)
    import importlib

    module = importlib.import_module("lineage_api.main")
    importlib.reload(module)
    assert callable(module.create_app)
