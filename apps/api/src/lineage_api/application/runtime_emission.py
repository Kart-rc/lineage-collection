"""Map corroborated runtime-stage observations to SDK-mechanism session payloads.

Wire identity is derived from the edge URNs, not from workload names, so the
consolidation join re-derives exactly the URN the analyzer produced. Transform
text is never forwarded: corroboration matches on URNs, and the intake plane is
metadata-only by contract.
"""
from __future__ import annotations

from typing import Sequence

from lineage_api.domain.urns import LineageUrn


def wire_dataset(urn_text: str) -> str:
    urn = LineageUrn.parse(urn_text)
    return f"{urn.platform}://{urn.system}/{urn.dataset}"


def _element(urn_text: str) -> str:
    # The fragment after '#'; using LineageUrn accessor confirmed in Step 1.
    urn = LineageUrn.parse(urn_text)
    return urn.element or ""


def _is_ldp_urn(urn_text: str) -> bool:
    try:
        LineageUrn.parse(urn_text)
        return True
    except ValueError:
        return False


def session_scope(observations: Sequence[dict]) -> tuple[str, ...]:
    # Only the dataset side ever enters session scope. A service-anchored edge's other
    # end (e.g. `service://repo/Type#method`) is not a dataset the runtime session
    # grants access to observe against -- it is the endpoint identity carried alongside
    # the observation, matched by exact string equality downstream, never by URN scope.
    datasets = {
        wire_dataset(urn)
        for observation in observations
        for urn in (*observation["from"], observation["to"])
        if _is_ldp_urn(urn)
    }
    return tuple(sorted(datasets))


def sdk_payloads(
    observations: Sequence[dict], *, artifact_digest: str, run_id: str
) -> list[dict]:
    payloads: list[dict] = []
    sequence = 0
    for observation in observations:
        to_urn = str(observation["to"])
        to_is_ldp = _is_ldp_urn(to_urn)
        for from_urn in observation["from"]:
            from_urn = str(from_urn)
            from_is_ldp = _is_ldp_urn(from_urn)
            sequence += 1
            base = {
                "schemaVersion": "1.0.0",
                "observationId": f"stage-{run_id}-{sequence:04d}",
                "sequence": sequence,
                "artifactDigest": artifact_digest,
                "edgeType": str(observation["edgeType"]),
                "transform": "",
                "observedAt": str(observation["observedAt"]),
            }
            if to_is_ldp and from_is_ldp:
                # Ordinary dataset-to-dataset element edge (the Python DERIVES pipeline).
                payload = {
                    **base,
                    "source": {"dataset": wire_dataset(from_urn), "field": _element(from_urn)},
                    "target": {"dataset": wire_dataset(to_urn), "field": _element(to_urn)},
                }
            elif from_is_ldp:
                # READS orientation: the element side is `from`, the service endpoint
                # is `to`. Carry the element side as `source`; the endpoint replaces
                # `target` entirely -- it is not a dataset element.
                payload = {
                    **base,
                    "source": {"dataset": wire_dataset(from_urn), "field": _element(from_urn)},
                    "endpoint": {"service": to_urn},
                }
            elif to_is_ldp:
                # WRITES orientation: the element side is `to`, the service endpoint
                # is `from`.
                payload = {
                    **base,
                    "source": {"dataset": wire_dataset(to_urn), "field": _element(to_urn)},
                    "endpoint": {"service": from_urn},
                }
            else:
                # Neither side is a dataset element URN -- nothing to emit. Should be
                # unreachable: the stage only ever selects edges with at least one
                # element-scoped ldp side.
                continue
            payloads.append(payload)
    return payloads
