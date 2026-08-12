"""One canonical identity rule for object-store datasets.

The SCA resolver and the runtime corroboration path each normalised object paths
differently — `s3://bucket/raw/events/2024/` became `2024` on one side and
`raw/events/2024` on the other — so an edge and its own runtime observation could never
match. These tests pin the single rule both sides must use.
"""

import pytest

from lineage_api.domain.object_store import (
    OBJECT_STORE_SCHEMES,
    canonical_object_key,
    canonical_scheme,
    is_object_store,
)


def test_the_object_store_scheme_set_is_closed() -> None:
    assert set(OBJECT_STORE_SCHEMES) == {"s3", "s3a", "s3n", "gs", "gcs", "abfss", "abfs"}


@pytest.mark.parametrize(
    ("scheme", "expected"),
    [("s3", "s3"), ("s3a", "s3"), ("s3n", "s3"), ("gs", "gs"), ("gcs", "gs"),
     ("abfss", "abfs"), ("abfs", "abfs")],
)
def test_scheme_variants_collapse_to_one_canonical_platform(scheme, expected) -> None:
    """s3a:// and s3:// are the same store; different URNs would split the graph."""
    assert canonical_scheme(scheme) == expected


def test_a_relational_scheme_is_not_an_object_store() -> None:
    assert is_object_store("s3") is True
    assert is_object_store("s3a") is True
    assert is_object_store("postgres") is False
    assert is_object_store("snowflake") is False
    assert is_object_store("kafka") is False


def test_a_hierarchical_key_is_preserved_in_full() -> None:
    """The defect: this used to collapse to 'events'."""
    assert canonical_object_key("raw/events/orders") == "raw/events/orders"


def test_leading_and_trailing_separators_are_ignored() -> None:
    assert canonical_object_key("/raw/events/") == "raw/events"
    assert canonical_object_key("raw//events") == "raw/events"


def test_a_single_segment_key_is_unchanged() -> None:
    assert canonical_object_key("orders") == "orders"


@pytest.mark.parametrize(
    "key",
    [
        "warehouse/orders/dt=2024-01-01",
        "warehouse/orders/year=2024/month=01",
        "warehouse/orders/year=2024/month=01/day=05",
        "warehouse/orders/dt=2024-01-01/",
    ],
)
def test_hive_partitions_resolve_to_their_table(key) -> None:
    """A partition is not a separate dataset; it is one physical slice of one table."""
    assert canonical_object_key(key) == "warehouse/orders"


def test_a_segment_containing_an_equals_sign_mid_path_is_not_stripped() -> None:
    """Only trailing partition segments are partitions; an inner one is part of the name."""
    assert canonical_object_key("raw/dt=2024/events") == "raw/dt=2024/events"


def test_an_empty_key_is_rejected_rather_than_silently_empty() -> None:
    with pytest.raises(ValueError, match="object key"):
        canonical_object_key("/")


def test_a_key_that_is_only_partitions_is_rejected() -> None:
    """Stripping every segment would leave no dataset to name."""
    with pytest.raises(ValueError, match="object key"):
        canonical_object_key("dt=2024-01-01/hour=03")
