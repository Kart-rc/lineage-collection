from __future__ import annotations

import pytest


def _urn_type():
    try:
        from lineage_api.domain.urns import LineageUrn
    except ModuleNotFoundError:
        pytest.fail("LineageUrn is not implemented")
    return LineageUrn


def test_render_parse_round_trip() -> None:
    lineage_urn = _urn_type()
    value = "urn:ldp:staging:snowflake:payments:raw.transactions#amount"

    parsed = lineage_urn.parse(value)

    assert str(parsed) == value
    assert parsed.dataset_urn == "urn:ldp:staging:snowflake:payments:raw.transactions"
    assert parsed.element == "amount"


def test_environment_is_part_of_identity() -> None:
    lineage_urn = _urn_type()
    staging = lineage_urn.parse("urn:ldp:staging:snowflake:payments:raw.transactions")
    production = lineage_urn.parse("urn:ldp:prod:snowflake:payments:raw.transactions")

    assert staging != production
    assert staging.same_asset_as(production)


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "raw.transactions",
        "urn:ldp:staging:snowflake:payments:",
        "urn:ldp::snowflake:payments:raw.transactions",
        "urn:ldp:staging:snowflake:payments:raw.transactions#",
    ],
)
def test_invalid_urns_are_rejected(invalid: str) -> None:
    lineage_urn = _urn_type()

    with pytest.raises(ValueError, match="Invalid lineage URN"):
        lineage_urn.parse(invalid)
