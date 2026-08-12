"""Object-store identity is catalog-owned, keyed by locator.

The earlier attempt normalised the path on both sides and called it done. That was
wrong, and the test that "proved" it only passed because the fixture named the bucket
identically to the owning system. With a realistic bucket:

  * `system` is the ownership axis — publication rejects an edge set that crosses it
    (`stage_handlers.py:1300`, `nightly_execution.py:375`), so the bucket cannot live
    there;
  * the vocabulary gate is applied to the derived host, so a real bucket quarantines;
  * the runtime path minted `system` from the wire, so it disagreed with the resolver.

The fix: normalisation produces a *lookup key*; the catalog row produces the *identity*.
"""

from pathlib import Path

import pytest

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
CURATED = "urn:ldp:staging:s3:lakehouse:curated/orders"


def _resolve(value: str):
    resolver = Resolver.from_path(CATALOG)
    return resolver.resolve(
        RawName("dataset", value, "SCA", ()),
        ResolveContext(
            env="staging",
            platform="s3",
            system="lakehouse",
            repo="r",
            digest="d" * 40,
            config={},
            snapshot_id=resolver.snapshot_id,
        ),
    )


def test_a_real_bucket_name_resolves_and_keeps_the_owning_system() -> None:
    """The bucket is `raw-lake`; the owning system is `lakehouse`. Both must hold."""
    result = _resolve("s3://raw-lake/raw/events/orders")

    assert isinstance(result, ResolvedName), getattr(result, "reason", None)
    assert str(result.urn) == LANDING


@pytest.mark.parametrize(
    "value",
    [
        "s3://raw-lake/raw/events/orders",
        "s3a://raw-lake/raw/events/orders",
        "s3n://raw-lake/raw/events/orders/",
        "s3://raw-lake/raw/events/orders/dt=2024-01-01",
        "s3a://raw-lake/raw/events/orders/year=2024/month=01/part-0000.parquet",
    ],
)
def test_scheme_variants_partitions_and_files_fold_onto_the_table(value) -> None:
    """Longest-prefix subsumes partition stripping: the residual is not a dataset."""
    result = _resolve(value)

    assert isinstance(result, ResolvedName), value
    assert str(result.urn) == LANDING


def test_prefix_matching_respects_segment_boundaries() -> None:
    """`orders_archive` must not match the `orders` locator."""
    result = _resolve("s3://raw-lake/raw/events/orders_archive")

    assert not isinstance(result, ResolvedName)


def test_two_locators_stay_distinct() -> None:
    landing = _resolve("s3://raw-lake/raw/events/orders")
    curated = _resolve("s3://raw-lake/curated/orders")

    assert str(landing.urn) == LANDING
    assert str(curated.urn) == CURATED


def test_the_same_key_in_another_bucket_does_not_resolve() -> None:
    """Retaining the authority is what stops a cross-bucket collision."""
    result = _resolve("s3://other-lake/curated/orders")

    assert not isinstance(result, ResolvedName)


def test_an_ungoverned_prefix_quarantines_with_a_typed_reason() -> None:
    result = _resolve("s3://raw-lake/scratch/whatever")

    assert not isinstance(result, ResolvedName)
    assert result.reason == "OBJECT_STORE_UNGOVERNED_PREFIX"


def test_an_unknown_scheme_is_not_treated_as_an_object_store() -> None:
    result = _resolve("wasb://raw-lake/curated/orders")

    assert not isinstance(result, ResolvedName)


def test_object_store_paths_are_not_case_folded() -> None:
    """S3 keys are case-sensitive; folding them would collide two distinct objects."""
    result = _resolve("s3://raw-lake/curated/Orders")

    assert not isinstance(result, ResolvedName)


def test_a_key_containing_a_fragment_is_refused_not_truncated() -> None:
    """Truncating at `#` would silently resolve to the wrong dataset."""
    result = _resolve("s3://raw-lake/curated/orders#2024/part-0")

    assert not isinstance(result, ResolvedName)
    assert result.reason == "OBJECT_STORE_KEY_UNREPRESENTABLE"


def test_the_runtime_path_resolves_to_the_same_urn_as_the_analyzer() -> None:
    """The whole point: an edge and its own observation must be the same identity."""
    from lineage_api.services.consolidation import ConsolidationService

    service = ConsolidationService(
        database=None, resolver=Resolver.from_path(CATALOG)  # type: ignore[arg-type]
    )
    analyzer_urn = str(_resolve("s3://raw-lake/raw/events/orders").urn)

    for observed in (
        "s3://raw-lake/raw/events/orders",
        "s3a://raw-lake/raw/events/orders",
        "s3://raw-lake/raw/events/orders/dt=2024-01-01",
    ):
        assert service._resolve_runtime_dataset(observed, "staging") == analyzer_urn


def test_an_object_store_observation_is_refused_without_a_catalog() -> None:
    """Minting an object-store URN from the wire is what caused the original defect."""
    from lineage_api.services.consolidation import ConsolidationService

    service = ConsolidationService(database=None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="requires a catalog resolver"):
        service._resolve_runtime_dataset("s3://raw-lake/raw/events/orders", "staging")
