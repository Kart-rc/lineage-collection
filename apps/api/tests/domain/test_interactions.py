import pytest

from lineage_api.domain.interactions import (
    INTERACTION_CHANNELS,
    INTERACTION_CLASSIFICATIONS,
    InteractionField,
    InteractionObservation,
)


def _observation(**overrides) -> InteractionObservation:
    payload = {
        "observation_id": "int-1",
        "from_service": "orders-service",
        "to_service": "customer-service",
        "channel": "REST",
        "operation": "GET /customers/{id}",
        "mechanism": "SCA",
        "exact": True,
        "observed_at": "2026-08-12T10:00:00Z",
    }
    payload.update(overrides)
    return InteractionObservation(**payload)


def test_the_channel_vocabulary_is_closed() -> None:
    assert INTERACTION_CHANNELS == frozenset(
        {"REST", "GRPC", "GRAPHQL", "ASYNC_EVENT"}
    )


def test_the_classification_vocabulary_is_closed() -> None:
    assert INTERACTION_CLASSIFICATIONS == frozenset({"NONE", "PII", "SECRET"})


def test_a_valid_observation_round_trips() -> None:
    observation = _observation(
        response_fields=(
            InteractionField("email", "string", "PII"),
            InteractionField("tier", "enum"),
        )
    )

    payload = observation.as_dict()

    assert payload["fromService"] == "orders-service"
    assert payload["operation"] == "GET /customers/{id}"
    assert payload["responseFields"] == [
        {"name": "email", "type": "string", "classification": "PII"},
        {"name": "tier", "type": "enum", "classification": "NONE"},
    ]


def test_an_unknown_channel_is_rejected() -> None:
    with pytest.raises(ValueError, match="channel"):
        _observation(channel="SOAP")


def test_an_unknown_mechanism_is_rejected() -> None:
    with pytest.raises(ValueError, match="mechanism"):
        _observation(mechanism="GUESS")


def test_classification_defaults_to_none_rather_than_being_guessed() -> None:
    field = InteractionField("email", "string")

    assert field.classification == "NONE"


def test_an_unknown_classification_is_rejected() -> None:
    with pytest.raises(ValueError, match="classification"):
        InteractionField("email", "string", "SENSITIVE")


def test_a_field_carrying_a_value_is_refused() -> None:
    """The metadata-only boundary applies here exactly as it does to dataset lineage."""
    with pytest.raises(ValueError, match="value"):
        InteractionField.from_dict(
            {"name": "email", "type": "string", "value": "a@b.com"}
        )


def test_a_field_without_a_value_parses() -> None:
    field = InteractionField.from_dict(
        {"name": "email", "type": "string", "classification": "PII"}
    )

    assert field == InteractionField("email", "string", "PII")


def test_an_empty_operation_is_rejected() -> None:
    with pytest.raises(ValueError, match="operation"):
        _observation(operation="")
