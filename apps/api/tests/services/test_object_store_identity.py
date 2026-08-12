"""The analyzer and the runtime must name the same S3 object identically.

If they do not, `merge_runtime_observation` (which requires exact URN equality) can
never corroborate an edge with its own observation, and every object-store edge is stuck
at PROBABLE forever. These tests hold both paths to one answer.
"""

from pathlib import Path

import pytest

from lineage_api.services.consolidation import ConsolidationService
from lineage_api.services.resolver import (
    RawName,
    ResolveContext,
    ResolvedName,
    Resolver,
)

CATALOG = (
    Path(__file__).resolve().parents[4] / "fixtures" / "catalog" / "catalog-snapshot-v1.json"
)
LANDING = "urn:ldp:staging:s3:lakehouse:raw/events/orders"


def _resolver() -> Resolver:
    return Resolver.from_path(CATALOG)


def _context(resolver: Resolver) -> ResolveContext:
    return ResolveContext(
        env="staging",
        platform="s3",
        system="lakehouse",
        repo="r",
        digest="d" * 40,
        config={},
        snapshot_id=resolver.snapshot_id,
    )


def _resolve(value: str, elements: tuple[str, ...] = ()):
    resolver = _resolver()
    return resolver.resolve(
        RawName("dataset", value, "SCA", elements), _context(resolver)
    )


def test_a_hierarchical_s3_prefix_resolves_to_its_full_key() -> None:
    result = _resolve("s3://lakehouse/raw/events/orders")

    assert isinstance(result, ResolvedName)
    assert str(result.urn) == LANDING
    assert result.kind == "LAKE_LANDING"


@pytest.mark.parametrize(
    "value",
    [
        "s3://lakehouse/raw/events/orders",
        "s3a://lakehouse/raw/events/orders",
        "s3n://lakehouse/raw/events/orders/",
        "s3://lakehouse/raw/events/orders/dt=2024-01-01",
        "s3a://lakehouse/raw/events/orders/year=2024/month=01",
    ],
)
def test_every_scheme_variant_and_partition_resolves_to_one_dataset(value) -> None:
    result = _resolve(value)

    assert isinstance(result, ResolvedName), value
    assert str(result.urn) == LANDING


def test_two_prefixes_ending_in_the_same_segment_stay_distinct() -> None:
    """The old rule collapsed both of these to 'orders'."""
    landing = _resolve("s3://lakehouse/raw/events/orders")
    curated = _resolve("s3://lakehouse/curated/orders")

    assert isinstance(landing, ResolvedName)
    assert isinstance(curated, ResolvedName)
    assert str(landing.urn) != str(curated.urn)


def test_an_unlisted_prefix_still_quarantines() -> None:
    """Identity stays catalog-owned; a path is never guessed into existence."""
    result = _resolve("s3://lakehouse/raw/events/nope")

    assert not isinstance(result, ResolvedName)


@pytest.mark.parametrize(
    "observed",
    [
        "s3://lakehouse/raw/events/orders",
        "s3a://lakehouse/raw/events/orders",
        "s3://lakehouse/raw/events/orders/dt=2024-01-01",
    ],
)
def test_the_runtime_path_produces_the_same_urn_as_the_analyzer(observed) -> None:
    """This is the defect these tests exist for."""
    analyzer_urn = str(_resolve("s3://lakehouse/raw/events/orders").urn)
    runtime_urn = ConsolidationService._runtime_dataset_urn(observed, "staging")

    assert runtime_urn == analyzer_urn == LANDING


def test_relational_names_are_untouched_by_the_object_store_branch() -> None:
    """The last-segment rule is correct for jdbc/relational names and must survive."""
    resolver = _resolver()
    result = resolver.resolve(
        RawName("dataset", "raw.transactions", "SCA", ()),
        ResolveContext(
            env="staging",
            platform="snowflake",
            system="payments",
            repo="r",
            digest="d" * 40,
            config={},
            snapshot_id=resolver.snapshot_id,
        ),
    )

    assert isinstance(result, ResolvedName)
    assert str(result.urn) == "urn:ldp:staging:snowflake:payments:raw.transactions"
