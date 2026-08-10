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
            webhook_secret=os.getenv("LINEAGE_WEBHOOK_SECRET", "local-lineage-demo-secret"),
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
