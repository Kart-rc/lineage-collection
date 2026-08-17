from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _environment_boolean(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    parsed = {"0": False, "1": True, "false": False, "true": True}.get(value)
    if parsed is None:
        raise ValueError(f"{name} must be a bounded boolean")
    return parsed


# The demo secret is published in this repository, so anything signed with it is
# forgeable by anyone who can read the source. It authenticates every signed input
# the platform accepts -- push deliveries, deployment outcomes and (key-derived)
# runtime observations -- so a deployment that silently fell back to it would let a
# stranger publish lineage and deployment promotions. It is therefore usable only
# when the operator has explicitly asked for demo mode.
DEMO_WEBHOOK_SECRET = "local-lineage-demo-secret"

_MINIMUM_WEBHOOK_SECRET_BYTES = 16


def _webhook_secret() -> str:
    """Resolve the signing secret, failing closed rather than defaulting to the demo value."""
    configured = os.environ.get("LINEAGE_WEBHOOK_SECRET")
    dev_mode = _environment_boolean("LINEAGE_DEV_MODE", False)
    if configured is None:
        if dev_mode:
            return DEMO_WEBHOOK_SECRET
        raise ValueError(
            "LINEAGE_WEBHOOK_SECRET must be set. It signs every accepted push, "
            "deployment and runtime delivery. Set LINEAGE_DEV_MODE=1 to use the "
            "published demo secret for local development only."
        )
    if configured == DEMO_WEBHOOK_SECRET and not dev_mode:
        raise ValueError(
            "LINEAGE_WEBHOOK_SECRET is the published demo secret and is forgeable "
            "by anyone who can read this repository. Set a private value, or set "
            "LINEAGE_DEV_MODE=1 for local development only."
        )
    if not dev_mode and len(configured.encode()) < _MINIMUM_WEBHOOK_SECRET_BYTES:
        raise ValueError(
            "LINEAGE_WEBHOOK_SECRET must be at least "
            f"{_MINIMUM_WEBHOOK_SECRET_BYTES} bytes outside development mode"
        )
    return configured


def _api_token() -> str | None:
    """Optional bearer token guarding state-changing endpoints.

    Unset leaves the API open, which is correct for a single-operator local run and
    is what the demo walkthrough expects. Setting it makes every mutating route
    require the token, so a deployed instance can be closed without a code change.
    """
    token = os.environ.get("LINEAGE_API_TOKEN")
    if token is None:
        return None
    if len(token.encode()) < _MINIMUM_WEBHOOK_SECRET_BYTES:
        raise ValueError(
            "LINEAGE_API_TOKEN must be at least "
            f"{_MINIMUM_WEBHOOK_SECRET_BYTES} bytes"
        )
    return token


def _environment_integer(
    name: str, default: int, *, minimum: int, maximum: int
) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    if (
        not value
        or len(value) > 10
        or not value.isascii()
        or not value.isdecimal()
    ):
        raise ValueError(f"{name} must be a bounded integer")
    parsed = int(value)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{name} must be a bounded integer")
    return parsed


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    data_directory: Path
    fixture_directory: Path
    database_path: Path
    object_directory: Path
    webhook_secret: str
    allow_local_repository_sources: bool = False
    repository_clone_timeout_seconds: int = 60
    repository_git_output_limit_bytes: int = 64 * 1024
    api_token: str | None = None

    @classmethod
    def from_environment(cls, project_root: Path | None = None) -> "Settings":
        root = project_root or Path(__file__).resolve().parents[4]
        data_directory = Path(os.getenv("LINEAGE_DATA_DIR", root / "data"))
        return cls(
            project_root=root,
            data_directory=data_directory,
            fixture_directory=root / "fixtures",
            database_path=data_directory / "lineage.db",
            object_directory=data_directory / "objects",
            webhook_secret=_webhook_secret(),
            api_token=_api_token(),
            allow_local_repository_sources=_environment_boolean(
                "LINEAGE_ALLOW_LOCAL_REPOSITORY_SOURCES", False
            ),
            repository_clone_timeout_seconds=_environment_integer(
                "LINEAGE_REPOSITORY_CLONE_TIMEOUT_SECONDS",
                60,
                minimum=1,
                maximum=600,
            ),
            repository_git_output_limit_bytes=_environment_integer(
                "LINEAGE_REPOSITORY_GIT_OUTPUT_LIMIT_BYTES",
                64 * 1024,
                minimum=1,
                maximum=1024 * 1024,
            ),
        )
