from __future__ import annotations

import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parents[4]
CATALOG_PATH = PROJECT_ROOT / "fixtures" / "catalog" / "catalog-snapshot-v1.json"


def _resolver_types():
    try:
        from lineage_api.services.resolver import RawName, ResolveContext, Resolver
    except ModuleNotFoundError:
        pytest.fail("Resolver is not implemented")
    return RawName, ResolveContext, Resolver


@pytest.fixture
def context():
    _, resolve_context, _ = _resolver_types()
    return resolve_context(
        env="staging",
        platform="snowflake",
        system="payments",
        repo="payments-pipeline",
        digest="demo-digest-v2",
        config={},
    )


@pytest.fixture
def resolver():
    _, _, resolver_type = _resolver_types()
    return resolver_type.from_path(CATALOG_PATH)


def test_alias_resolves_to_catalog_backed_dataset_and_elements(resolver, context) -> None:
    raw_name, _, _ = _resolver_types()

    result = resolver.resolve(
        raw_name(
            kind="dataset",
            value="SNOWFLAKE://PAYMENTS/RAW.TRANSACTIONS",
            source="SCA",
            elements=("customer_id", "amount"),
        ),
        context,
    )

    assert result.status == "RESOLVED"
    assert str(result.urn) == "urn:ldp:staging:snowflake:payments:raw.transactions"
    assert [str(urn) for urn in result.element_urns] == [
        "urn:ldp:staging:snowflake:payments:raw.transactions#customer_id",
        "urn:ldp:staging:snowflake:payments:raw.transactions#amount",
    ]
    assert result.catalog_ref == "catalog://payments/raw.transactions"
    assert result.rules_applied == ("decode", "case-fold", "catalog-match")
    assert result.snapshot_id == "catalog-demo-v1"
    assert result.resolver_version == "1.0.0"


def test_unknown_name_is_quarantined_and_never_guessed(resolver, context) -> None:
    raw_name, _, _ = _resolver_types()

    result = resolver.resolve(raw_name(kind="dataset", value="missing.table", source="SCA"), context)

    assert result.status == "QUARANTINED"
    assert result.reason == "UNKNOWN_DATASET"
    assert result.candidates == ()
    assert result.gap_report["normalizedValue"] == "missing.table"
    assert not hasattr(result, "urn")


def test_multiple_alias_matches_are_quarantined_as_ambiguous(tmp_path: Path, context) -> None:
    raw_name, _, resolver_type = _resolver_types()
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    catalog["datasets"][0]["aliases"].append("shared.transactions")
    duplicate = dict(catalog["datasets"][0])
    duplicate["catalogRef"] = "catalog://payments/archive.transactions"
    duplicate["name"] = "archive.transactions"
    duplicate["aliases"] = ["shared.transactions"]
    catalog["datasets"].append(duplicate)
    ambiguous_path = tmp_path / "catalog.json"
    ambiguous_path.write_text(json.dumps(catalog), encoding="utf-8")
    resolver = resolver_type.from_path(ambiguous_path)

    result = resolver.resolve(
        raw_name(kind="dataset", value="shared.transactions", source="SCA"), context
    )

    assert result.status == "QUARANTINED"
    assert result.reason == "AMBIGUOUS"
    assert result.candidates == (
        "catalog://payments/archive.transactions",
        "catalog://payments/raw.transactions",
    )


def test_config_substitution_is_all_or_quarantine(resolver, context) -> None:
    raw_name, resolve_context, _ = _resolver_types()
    configured = resolve_context(
        env=context.env,
        platform=context.platform,
        system=context.system,
        repo=context.repo,
        digest=context.digest,
        config={"SOURCE_TABLE": "raw.transactions"},
    )

    resolved = resolver.resolve(
        raw_name(kind="dataset", value="${SOURCE_TABLE}", source="SCA"), configured
    )
    quarantined = resolver.resolve(
        raw_name(kind="dataset", value="${MISSING_TABLE}", source="SCA"), configured
    )

    assert resolved.status == "RESOLVED"
    assert "config-substitution" in resolved.rules_applied
    assert quarantined.status == "QUARANTINED"
    assert quarantined.reason == "UNRESOLVED_CONFIG"


def test_batch_preserves_order_and_uses_one_snapshot(resolver, context) -> None:
    raw_name, _, _ = _resolver_types()
    values = [
        raw_name(kind="dataset", value="analytics.daily_revenue", source="SCA"),
        raw_name(kind="dataset", value="missing.table", source="SCA"),
        raw_name(kind="dataset", value="raw.transactions", source="SCA"),
    ]

    results = resolver.resolve_batch(values, context)

    assert [result.status for result in results] == ["RESOLVED", "QUARANTINED", "RESOLVED"]
    assert {result.snapshot_id for result in results} == {"catalog-demo-v1"}


def test_credentials_are_removed_from_quarantined_connection_names(resolver, context) -> None:
    raw_name, _, _ = _resolver_types()

    result = resolver.resolve(
        raw_name(
            kind="dataset",
            value="jdbc:snowflake://demo_user:super-secret@account/missing.table?token=also-secret",
            source="SCA",
        ),
        context,
    )

    assert result.status == "QUARANTINED"
    assert "super-secret" not in result.raw.value
    assert "also-secret" not in result.raw.value
    assert result.raw.value == "jdbc:snowflake://account/missing.table"
