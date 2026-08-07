from __future__ import annotations

import os
from pathlib import Path

import pytest

from lineage_api.testing.evidence import AcceptanceEvidenceWriter, digest_tree


PROJECT_ROOT = Path(__file__).parents[2]


@pytest.fixture
def acceptance_writer(tmp_path: Path) -> AcceptanceEvidenceWriter:
    configured = os.environ.get("LINEAGE_ACCEPTANCE_OUTPUT")
    output = Path(configured) if configured else tmp_path / "acceptance"
    artifact_digest = digest_tree(
        PROJECT_ROOT,
        (
            "apps/api/src",
            "packages/contracts",
            "tests/acceptance",
            "package.json",
            "Makefile",
        ),
    )
    return AcceptanceEvidenceWriter(output, artifact_digest=artifact_digest)

