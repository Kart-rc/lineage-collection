import pytest

from lineage_api.domain.product_confidence import (
    DISPLAY_BANDS,
    ProductConfidence,
    project_confidence,
)


def _sca(observed_at: str | None = None) -> dict:
    payload: dict = {"mechanism": "SCA"}
    if observed_at is not None:
        payload["observedAt"] = observed_at
    return payload


def test_display_bands_match_the_product_vocabulary() -> None:
    assert DISPLAY_BANDS == ("VERIFIED", "PROBABLE", "INFERRED")


def test_runtime_corroborated_edges_display_as_verified() -> None:
    result = project_confidence(
        "HIGH", [_sca(), {"mechanism": "RUNTIME", "observedAt": "2026-08-12T10:00:00Z"}]
    )

    assert result == ProductConfidence(
        display_band="VERIFIED",
        percent=92,
        signals=("RUNTIME", "SCA"),
        last_observed="2026-08-12T10:00:00Z",
    )


def test_all_three_mechanisms_are_the_strongest_verified() -> None:
    result = project_confidence(
        "HIGHEST",
        [
            _sca(),
            {"mechanism": "LLM"},
            {"mechanism": "RUNTIME", "observedAt": "2026-08-12T09:00:00Z"},
        ],
    )

    assert result.display_band == "VERIFIED"
    assert result.percent == 96
    assert result.signals == ("LLM", "RUNTIME", "SCA")


def test_static_only_is_probable_and_never_observed() -> None:
    result = project_confidence("SINGLE", [_sca()])

    assert result.display_band == "PROBABLE"
    assert result.percent == 70
    assert result.last_observed is None


def test_static_plus_llm_is_probable() -> None:
    result = project_confidence("MEDIUM", [_sca(), {"mechanism": "LLM"}])

    assert result.display_band == "PROBABLE"
    assert result.percent == 78


def test_llm_only_is_inferred() -> None:
    result = project_confidence("LOWEST", [{"mechanism": "LLM"}])

    assert result.display_band == "INFERRED"
    assert result.percent == 55
    assert result.last_observed is None


def test_the_most_recent_runtime_observation_wins() -> None:
    result = project_confidence(
        "HIGH",
        [
            _sca(),
            {"mechanism": "RUNTIME", "observedAt": "2026-08-12T08:00:00Z"},
            {"mechanism": "RUNTIME", "observedAt": "2026-08-12T11:30:00Z"},
        ],
    )

    assert result.last_observed == "2026-08-12T11:30:00Z"


def test_a_non_runtime_timestamp_is_not_treated_as_an_observation() -> None:
    result = project_confidence("SINGLE", [_sca("2026-08-12T10:00:00Z")])

    assert result.last_observed is None


def test_an_unknown_band_is_rejected_rather_than_defaulted() -> None:
    with pytest.raises(ValueError, match="confidence band"):
        project_confidence("SOMETHING", [_sca()])


# --- the ceiling is a property of the model, not of the implementation -----------------


def test_no_combination_of_sca_and_runtime_can_reach_highest() -> None:
    """HIGHEST is *defined* as all three mechanisms agreeing.

    Asking for HIGHEST from SCA and runtime alone is a category error rather than a
    coverage shortfall: the band names a three-way agreement, so two mechanisms cannot
    express it however many edges they corroborate. This test enumerates every subset of
    {SCA, RUNTIME} so the claim is proven rather than asserted in prose.
    """
    from itertools import chain, combinations

    from lineage_api.domain.confidence import derive_band

    available = {"SCA", "RUNTIME"}
    subsets = chain.from_iterable(
        combinations(sorted(available), size) for size in range(1, len(available) + 1)
    )

    bands = {derive_band(set(subset)) for subset in subsets}

    assert bands == {"SINGLE", "HIGH"}
    assert "HIGHEST" not in bands


def test_highest_requires_exactly_the_third_mechanism() -> None:
    from lineage_api.domain.confidence import derive_band

    assert derive_band({"SCA", "RUNTIME"}) == "HIGH"
    assert derive_band({"SCA", "RUNTIME", "LLM"}) == "HIGHEST"
