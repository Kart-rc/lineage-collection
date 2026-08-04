from __future__ import annotations

import pytest

from lineage_api.domain.errors import DomainError


def _proposal_functions():
    try:
        from lineage_api.domain.proposals import assert_transition, allowed_transitions
    except ModuleNotFoundError:
        pytest.fail("Proposal lifecycle is not implemented")
    return assert_transition, allowed_transitions


def test_proposal_transition_graph_is_server_owned_and_explicit() -> None:
    _, allowed_transitions = _proposal_functions()

    assert allowed_transitions("DRAFT") == {"IN_REVIEW"}
    assert allowed_transitions("IN_REVIEW") == {"APPROVED", "REJECTED", "SUPERSEDED"}
    assert allowed_transitions("APPROVED") == {"FINALIZED"}
    assert allowed_transitions("REJECTED") == set()
    assert allowed_transitions("SUPERSEDED") == set()
    assert allowed_transitions("FINALIZED") == set()


def test_illegal_transition_raises_typed_domain_error() -> None:
    assert_transition, _ = _proposal_functions()

    with pytest.raises(DomainError) as captured:
        assert_transition("APPROVED", "REJECTED", "corr-001")

    assert captured.value.code == "INVALID_PROPOSAL_TRANSITION"
    assert captured.value.details == {"current": "APPROVED", "requested": "REJECTED"}
