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


def _is_element_scoped_dataset_urn():
    try:
        from lineage_api.domain.urns import is_element_scoped_dataset_urn
    except ModuleNotFoundError:
        pytest.fail("is_element_scoped_dataset_urn is not implemented")
    return is_element_scoped_dataset_urn


def test_element_scoped_ldp_urn_is_recognized() -> None:
    is_element_scoped_dataset_urn = _is_element_scoped_dataset_urn()

    assert is_element_scoped_dataset_urn(
        "urn:ldp:staging:snowflake:payments:raw.transactions#amount"
    )


def test_dataset_scoped_ldp_urn_is_not_element_scoped() -> None:
    is_element_scoped_dataset_urn = _is_element_scoped_dataset_urn()

    assert not is_element_scoped_dataset_urn(
        "urn:ldp:staging:snowflake:payments:raw.transactions"
    )


def test_service_endpoint_urn_hash_is_never_mistaken_for_element_scope() -> None:
    # A Java analyzer's `service://repo/Type#method` URN also contains '#', but that
    # '#' separates a Java type from a method, not a dataset from a column -- it must
    # never parse as, or be mistaken for, a `urn:ldp:` element-scoped dataset URN.
    is_element_scoped_dataset_urn = _is_element_scoped_dataset_urn()

    assert not is_element_scoped_dataset_urn(
        "service://spring-petclinic-microservices/"
        "org.springframework.samples.petclinic.customers.web.OwnerResource#findAll"
    )


def test_bare_hash_string_without_urn_prefix_is_not_element_scoped() -> None:
    is_element_scoped_dataset_urn = _is_element_scoped_dataset_urn()

    assert not is_element_scoped_dataset_urn("not-a-urn#fragment")
