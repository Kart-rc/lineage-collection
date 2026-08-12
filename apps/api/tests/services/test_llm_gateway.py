"""The LLM mechanism, built to the guardrails in docs/component-prds/05.

The band model has always known about three mechanisms, but nothing ever produced an
`LLM` assertion, so `HIGHEST` was unreachable by construction. This module closes that,
without weakening anything: the PRD's guardrail chain runs in order — schema, citation
present in the analysed chunk, URN resolves through the catalog, budget — and the first
failure stops the chain and records a reject (L05-FR-005). A proposal that names a
dataset the catalog does not know cannot become an edge, so the model cannot invent
lineage even if it hallucinates.

The transport is deliberately pluggable and defaults to absent. A live Bedrock gateway
is NOT_CONFIGURED in this repository, so tests drive a recorded response — which is
labelled as recorded everywhere it is used rather than passed off as a live call.
"""

from pathlib import Path

import pytest

from lineage_api.services.llm_gateway import (
    LlmGateway,
    LlmProposal,
    RecordedTransport,
)
from lineage_api.services.resolver import Resolver

CATALOG = (
    Path(__file__).resolve().parents[4] / "fixtures" / "catalog" / "catalog-snapshot-v1.json"
)
RAW = "urn:ldp:staging:snowflake:payments:raw.transactions"
DAILY = "urn:ldp:staging:snowflake:payments:analytics.daily_revenue"
CHUNK = "INSERT INTO analytics.daily_revenue SELECT customer_id, SUM(amount) FROM raw.transactions"


def _gateway(proposals: list[dict], *, budget: int = 10) -> LlmGateway:
    return LlmGateway(
        resolver=Resolver.from_path(CATALOG),
        transport=RecordedTransport({"any": proposals}),
        model_id="recorded-model-v1",
        prompt_version="lineage-residue-v1",
        max_proposals=budget,
    )


def _proposal(**overrides) -> dict:
    payload = {
        "fromUrn": f"{RAW}#amount",
        "toUrn": f"{DAILY}#gross_revenue",
        "transform": "SUM(amount)",
        "citation": "SUM(amount)",
    }
    payload.update(overrides)
    return payload


def test_a_gateway_without_a_transport_is_not_configured() -> None:
    """The honest default: no live gateway exists in this repository."""
    gateway = LlmGateway(resolver=Resolver.from_path(CATALOG))

    result = gateway.propose(chunk=CHUNK, known_urns=(RAW, DAILY))

    assert result.accepted == ()
    assert [r.code for r in result.rejects] == ["NOT_CONFIGURED"]


def test_a_well_formed_proposal_is_accepted() -> None:
    result = _gateway([_proposal()]).propose(chunk=CHUNK, known_urns=(RAW, DAILY))

    assert result.rejects == ()
    assert result.accepted == (
        LlmProposal(
            from_urn=f"{RAW}#amount",
            to_urn=f"{DAILY}#gross_revenue",
            transform="SUM(amount)",
            citation="SUM(amount)",
        ),
    )


def test_a_malformed_proposal_is_a_schema_reject() -> None:
    result = _gateway([{"fromUrn": RAW}]).propose(chunk=CHUNK, known_urns=(RAW, DAILY))

    assert result.accepted == ()
    assert [r.code for r in result.rejects] == ["SCHEMA_REJECT"]


def test_a_citation_absent_from_the_chunk_is_rejected() -> None:
    """The model must point at text that is actually there."""
    result = _gateway([_proposal(citation="MEDIAN(amount)")]).propose(
        chunk=CHUNK, known_urns=(RAW, DAILY)
    )

    assert result.accepted == ()
    assert [r.code for r in result.rejects] == ["CITATION_REJECT"]


def test_an_unknown_dataset_cannot_be_invented() -> None:
    """This is the guardrail that makes fabrication structurally impossible."""
    result = _gateway(
        [_proposal(toUrn="urn:ldp:staging:snowflake:payments:invented.table#x")]
    ).propose(chunk=CHUNK, known_urns=(RAW, DAILY))

    assert result.accepted == ()
    assert [r.code for r in result.rejects] == ["URN_REJECT"]


def test_a_dataset_outside_the_analysed_scope_is_rejected() -> None:
    """Even a catalogued dataset is refused if this analysis never saw it."""
    result = _gateway(
        [_proposal(toUrn="urn:ldp:staging:snowflake:payments:risk.customer_features#lifetime_value")]
    ).propose(chunk=CHUNK, known_urns=(RAW, DAILY))

    assert result.accepted == ()
    assert [r.code for r in result.rejects] == ["URN_REJECT"]


def test_the_budget_stops_an_overlong_response() -> None:
    result = _gateway([_proposal(), _proposal()], budget=1).propose(
        chunk=CHUNK, known_urns=(RAW, DAILY)
    )

    assert len(result.accepted) == 1
    assert [r.code for r in result.rejects] == ["BUDGET_REJECT"]


def test_the_guardrail_chain_stops_at_the_first_failure() -> None:
    """L05-FR-005: order is schema, citation, URN, budget — first failure wins."""
    result = _gateway(
        [{"fromUrn": RAW, "toUrn": "urn:ldp:staging:snowflake:payments:invented.t#x"}]
    ).propose(chunk=CHUNK, known_urns=(RAW, DAILY))

    assert [r.code for r in result.rejects] == ["SCHEMA_REJECT"]


def test_an_identical_chunk_is_served_from_cache_without_a_second_call() -> None:
    """L05-FR-004: cache key is chunk digest + promptVersion + modelId."""
    transport = RecordedTransport({"any": [_proposal()]})
    gateway = LlmGateway(
        resolver=Resolver.from_path(CATALOG),
        transport=transport,
        model_id="recorded-model-v1",
        prompt_version="lineage-residue-v1",
    )

    gateway.propose(chunk=CHUNK, known_urns=(RAW, DAILY))
    gateway.propose(chunk=CHUNK, known_urns=(RAW, DAILY))

    assert transport.calls == 1


def test_a_recorded_transport_is_labelled_as_recorded() -> None:
    """Nothing may present a replayed response as a live model call."""
    assert RecordedTransport({}).is_live is False
