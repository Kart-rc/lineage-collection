import pytest

from lineage_api.services.liveness import (
    LIVENESS_BANDS,
    EdgeLiveness,
    derive_liveness,
)


def test_the_band_vocabulary_is_closed() -> None:
    assert LIVENESS_BANDS == ("HOT", "WARM", "COLD", "UNOBSERVED")


@pytest.mark.parametrize(
    ("observations", "expected"),
    [
        (0, "UNOBSERVED"),
        (1, "COLD"),
        (9, "COLD"),
        (10, "WARM"),
        (99, "WARM"),
        (100, "HOT"),
        (5000, "HOT"),
    ],
)
def test_observation_counts_band_at_their_boundaries(
    observations: int, expected: str
) -> None:
    result = derive_liveness("edge-1", observations, "2026-08-12T10:00:00Z", True)

    assert result.band == expected


def test_an_unobserved_edge_reports_no_last_seen() -> None:
    result = derive_liveness("edge-1", 0, None, True)

    assert result == EdgeLiveness(
        edge_key="edge-1", observations=0, last_observed=None, band="UNOBSERVED"
    )


def test_an_incomplete_session_can_never_claim_an_edge_is_unobserved() -> None:
    """A partial session proves nothing about liveness, so it must not assert it."""
    result = derive_liveness("edge-1", 0, None, False)

    assert result.band == "UNOBSERVED"
    assert result.session_complete is False


def test_a_complete_session_marks_its_verdict_trustworthy() -> None:
    result = derive_liveness("edge-1", 0, None, True)

    assert result.session_complete is True


def test_a_negative_observation_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="observation count"):
        derive_liveness("edge-1", -1, None, True)


def test_observations_without_a_timestamp_are_rejected() -> None:
    with pytest.raises(ValueError, match="last observation"):
        derive_liveness("edge-1", 5, None, True)


# --- Task 2: demotion policy ----------------------------------------------------------

from lineage_api.services.liveness import may_demote  # noqa: E402


def test_a_complete_session_with_no_observations_may_demote() -> None:
    liveness = derive_liveness("edge-1", 0, None, True)

    assert may_demote(liveness) is True


def test_an_incomplete_session_may_never_demote() -> None:
    """An aborted or revoked session proves nothing, so it must not lower anything."""
    liveness = derive_liveness("edge-1", 0, None, False)

    assert may_demote(liveness) is False


def test_an_observed_edge_is_never_demoted() -> None:
    liveness = derive_liveness("edge-1", 42, "2026-08-12T10:00:00Z", True)

    assert may_demote(liveness) is False


def test_demotion_never_changes_the_static_band() -> None:
    """Runtime narrows and ranks; it never overturns static proof."""
    from lineage_api.services.liveness import demoted_band

    assert demoted_band("SINGLE", derive_liveness("e", 0, None, True)) == "SINGLE"
    assert demoted_band("HIGH", derive_liveness("e", 0, None, True)) == "HIGH"
