"""Per-edge liveness: how often a proven edge is actually exercised.

This is the one thing static analysis cannot establish. The analyzer can prove a call
site exists; only a runtime session can say whether it ever fires. The product ranks
blast radius by this — the difference between "47 things might break" and "3 things
will".

The prototype calls the bottom band `DEAD?`. This module calls it `UNOBSERVED`,
deliberately: "dead" asserts the code never runs, whereas all the evidence supports is
that *this session* did not witness it. `session_complete` travels with the verdict so
callers can tell a trustworthy zero from an inconclusive one.
"""

from __future__ import annotations

from dataclasses import dataclass

LIVENESS_BANDS = ("HOT", "WARM", "COLD", "UNOBSERVED")


@dataclass(frozen=True, slots=True)
class EdgeLiveness:
    edge_key: str
    observations: int
    last_observed: str | None
    band: str
    session_complete: bool = True


def _band(observations: int) -> str:
    if observations >= 100:
        return "HOT"
    if observations >= 10:
        return "WARM"
    if observations >= 1:
        return "COLD"
    return "UNOBSERVED"


def derive_liveness(
    edge_key: str,
    observations: int,
    last_observed: str | None,
    session_complete: bool,
) -> EdgeLiveness:
    if observations < 0:
        raise ValueError("observation count must not be negative")
    if observations > 0 and last_observed is None:
        raise ValueError("last observation timestamp is required when observed")
    return EdgeLiveness(
        edge_key=edge_key,
        observations=observations,
        last_observed=last_observed,
        band=_band(observations),
        session_complete=session_complete,
    )


def may_demote(liveness: EdgeLiveness) -> bool:
    """Whether this verdict is entitled to lower an edge's standing.

    Only a *complete* session that observed nothing carries that entitlement. An
    aborted, revoked, or partial session proves nothing about liveness, and an edge
    that was observed is evidence for the edge, never against it.
    """
    return liveness.session_complete and liveness.observations == 0


def demoted_band(band: str, liveness: EdgeLiveness) -> str:
    """Liveness never overturns static proof.

    Demotion narrows and ranks — it moves an edge down the list and marks it
    UNOBSERVED — but the confidence band stays whatever the evidence justifies. A call
    site that provably exists still provably exists whether or not a test exercised it.
    """
    return band
