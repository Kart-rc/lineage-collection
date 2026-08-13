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


def session_scope(observations: Sequence[dict]) -> tuple[str, ...]:
    datasets = {
        wire_dataset(urn)
        for observation in observations
        for urn in (*observation["from"], observation["to"])
    }
    return tuple(sorted(datasets))


def sdk_payloads(
    observations: Sequence[dict], *, artifact_digest: str, run_id: str
) -> list[dict]:
    payloads: list[dict] = []
    sequence = 0
    for observation in observations:
        for from_urn in observation["from"]:
            sequence += 1
            payloads.append(
                {
                    "schemaVersion": "1.0.0",
                    "observationId": f"stage-{run_id}-{sequence:04d}",
                    "sequence": sequence,
                    "artifactDigest": artifact_digest,
                    "source": {
                        "dataset": wire_dataset(from_urn),
                        "field": _element(from_urn),
                    },
                    "target": {
                        "dataset": wire_dataset(observation["to"]),
                        "field": _element(observation["to"]),
                    },
                    "edgeType": str(observation["edgeType"]),
                    "transform": "",
                    "observedAt": str(observation["observedAt"]),
                }
            )
    return payloads
