from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    data_directory: Path
    fixture_directory: Path
    database_path: Path
    object_directory: Path
    webhook_secret: str

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
        )
