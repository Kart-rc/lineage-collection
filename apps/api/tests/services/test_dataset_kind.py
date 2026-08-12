from pathlib import Path

import pytest

from lineage_api.services.resolver import (
    DATASET_KINDS,
    RawName,
    ResolveContext,
    ResolvedName,
    Resolver,
)

CATALOG = (
    Path(__file__).resolve().parents[4] / "fixtures" / "catalog" / "catalog-snapshot-v1.json"
)


def _catalog(datasets: list[dict]) -> dict:
    return {
        "schemaVersion": "1.0.0",
        "snapshotId": "s1",
        "resolverVersion": "1.0.0",
        "vocabulary": {
            "environments": ["staging"],
            "platforms": ["snowflake"],
            "systems": ["payments"],
        },
        "datasets": datasets,
    }


def _context(snapshot_id: str) -> ResolveContext:
    return ResolveContext(
        env="staging",
        platform="snowflake",
        system="payments",
        repo="r",
        digest="d" * 40,
        config={},
        snapshot_id=snapshot_id,
    )


def test_the_closed_kind_vocabulary_matches_the_product_model() -> None:
    assert DATASET_KINDS == frozenset(
        {
            "DATASTORE",
            "STREAM",
            "LAKE_LANDING",
            "LAKE_FILE",
            "CACHE",
            "SEARCH",
            "UNKNOWN",
        }
    )


def test_a_catalog_dataset_carries_its_declared_kind() -> None:
    resolver = Resolver.from_path(CATALOG)

    result = resolver.resolve(
        RawName("dataset", "raw.transactions", "SCA", ()),
        _context(resolver.snapshot_id),
    )

    assert isinstance(result, ResolvedName)
    assert result.kind == "DATASTORE"


def test_a_dataset_without_a_declared_kind_resolves_as_unknown() -> None:
    resolver = Resolver(
        _catalog(
            [
                {
                    "catalogRef": "catalog://payments/untyped",
                    "env": "staging",
                    "platform": "snowflake",
                    "system": "payments",
                    "name": "untyped",
                    "aliases": [],
                    "elements": [],
                }
            ]
        )
    )

    result = resolver.resolve(RawName("dataset", "untyped", "SCA", ()), _context("s1"))

    assert isinstance(result, ResolvedName)
    assert result.kind == "UNKNOWN"


def test_an_unrecognised_kind_is_rejected_rather_than_passed_through() -> None:
    with pytest.raises(ValueError, match="dataset kind"):
        Resolver(
            _catalog(
                [
                    {
                        "catalogRef": "catalog://payments/x",
                        "env": "staging",
                        "platform": "snowflake",
                        "system": "payments",
                        "name": "x",
                        "aliases": [],
                        "elements": [],
                        "kind": "MONGO",
                    }
                ]
            )
        )
