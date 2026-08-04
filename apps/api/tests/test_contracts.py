from __future__ import annotations

from pathlib import Path

import pytest


CONTRACTS_DIR = Path(__file__).parents[3] / "packages" / "contracts"


def _contract_registry_type():
    try:
        from lineage_api.contracts import ContractRegistry
    except ModuleNotFoundError:
        pytest.fail("ContractRegistry is not implemented")
    return ContractRegistry


def test_contract_registry_loads_all_platform_contracts() -> None:
    contract_registry = _contract_registry_type()

    registry = contract_registry(CONTRACTS_DIR)

    assert registry.names() == {
        "accepted-manifest",
        "consolidated-edge",
        "event-envelope",
        "evidence-ref",
        "impact-response",
        "proposal",
    }


def test_contracts_have_versioned_strict_object_roots() -> None:
    contract_registry = _contract_registry_type()
    registry = contract_registry(CONTRACTS_DIR)

    for name in registry.names():
        schema = registry.schema(name)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].endswith(f"/{name}/1.0.0")
        assert schema["title"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


def test_event_envelope_requires_correlation_contract() -> None:
    contract_registry = _contract_registry_type()
    registry = contract_registry(CONTRACTS_DIR)

    errors = registry.validate("event-envelope", {"eventId": "delivery-1"})

    assert {error.path for error in errors} >= {
        "eventType",
        "correlationId",
        "repo",
        "digest",
        "env",
        "system",
    }
