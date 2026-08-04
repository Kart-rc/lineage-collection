from __future__ import annotations

from pathlib import Path

import pytest

from lineage_api.db import Database
from lineage_api.domain.errors import DomainError


def _store_type():
    try:
        from lineage_api.services.evidence_store import EvidenceStore
    except ModuleNotFoundError:
        pytest.fail("Evidence store is not implemented")
    return EvidenceStore


@pytest.fixture
def store(tmp_path: Path):
    database = Database(tmp_path / "lineage.db")
    database.initialize()
    return _store_type()(
        database,
        tmp_path / "objects",
        clock=lambda: "2026-08-04T15:00:00Z",
    )


def test_put_is_write_once_checksummed_and_idempotent_for_same_content(store) -> None:
    body = {"schemaVersion": "1.0.0", "edges": [{"edgeKey": "edge-1"}]}

    first = store.put("sca", "payments/demo-digest-v2/python-demo-v1", body, "1.0.0")
    second = store.put("sca", "payments/demo-digest-v2/python-demo-v1", body, "1.0.0")

    assert first == second
    assert first.kind == "sca"
    assert first.key == "payments/demo-digest-v2/python-demo-v1"
    assert len(first.checksum) == 64
    assert store.get(first) == body
    assert store.object_count() == 1


def test_different_content_cannot_overwrite_existing_truth(store) -> None:
    key = "payments/demo-digest-v2/python-demo-v1"
    store.put("sca", key, {"schemaVersion": "1.0.0", "edges": []}, "1.0.0")

    with pytest.raises(DomainError) as captured:
        store.put(
            "sca",
            key,
            {"schemaVersion": "1.0.0", "edges": [{"edgeKey": "changed"}]},
            "1.0.0",
        )

    assert captured.value.code == "OVERWRITE_ATTEMPT"


def test_checksum_mismatch_is_refused(store) -> None:
    reference = store.put(
        "sca",
        "payments/demo-digest-v2/python-demo-v1",
        {"schemaVersion": "1.0.0", "edges": []},
        "1.0.0",
    )
    store.path_for(reference).write_bytes(b"corrupted")

    with pytest.raises(DomainError) as captured:
        store.get(reference)

    assert captured.value.code == "CHECKSUM_MISMATCH"


def test_unknown_kinds_and_unsafe_keys_are_rejected(store) -> None:
    with pytest.raises(DomainError, match="UNKNOWN_EVIDENCE_KIND"):
        store.put("mystery", "x", {}, "1.0.0")
    with pytest.raises(DomainError, match="INVALID_EVIDENCE_KEY"):
        store.put("sca", "../escape", {}, "1.0.0")
