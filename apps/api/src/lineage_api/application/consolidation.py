from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Sequence

from lineage_api.domain.confidence import derive_band, normalize_transform


@dataclass(frozen=True, slots=True)
class ConsolidationDerivation:
    band: str
    corroboration: str
    status: str
    transform: str | None
    auto_publishable: bool


def edge_key_for(from_urns: Sequence[str], to_urn: str, edge_type: str) -> str:
    identity = json.dumps(
        {"from": sorted(from_urns), "to": to_urn, "edgeType": edge_type},
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"edge-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"


def derive_consolidation(
    provenance: Sequence[Mapping[str, object]],
) -> ConsolidationDerivation:
    if not provenance:
        raise ValueError("consolidation requires provenance")
    mechanisms: set[str] = set()
    for item in provenance:
        mechanism = str(item.get("mechanism", ""))
        if mechanism != "RUNTIME":
            mechanisms.add(mechanism)
        elif item.get("sessionComplete") is True and item.get("runtimeScope") == "ELEMENT":
            mechanisms.add("RUNTIME")

    runtime_scopes = {
        str(item.get("runtimeScope"))
        for item in provenance
        if item.get("mechanism") == "RUNTIME" and item.get("sessionComplete") is True
    }
    if "ELEMENT" in runtime_scopes:
        corroboration = "ELEMENT"
    elif "DATASET" in runtime_scopes:
        corroboration = "DATASET"
    else:
        corroboration = "NONE"

    transform_assertions = [
        item
        for item in provenance
        if item.get("mechanism") in {"SCA", "LLM"}
        and isinstance(item.get("transform"), str)
    ]
    normalized_transforms = {
        normalize_transform(str(item["transform"])) for item in transform_assertions
    }
    conflicting = len(normalized_transforms) > 1
    selected = next(
        (
            item
            for item in provenance
            if item.get("mechanism") == "SCA"
            and item.get("exact") is True
            and isinstance(item.get("transform"), str)
        ),
        transform_assertions[0] if transform_assertions else None,
    )
    transform = None if conflicting or selected is None else str(selected["transform"])
    auto_publishable = bool(
        not conflicting
        and any(
            item.get("mechanism") == "SCA"
            and item.get("exact") is True
            and isinstance(item.get("transform"), str)
            for item in provenance
        )
    )
    return ConsolidationDerivation(
        band=derive_band(mechanisms),
        corroboration=corroboration,
        status="CONFLICTING" if conflicting else "PROPOSED",
        transform=transform,
        auto_publishable=auto_publishable,
    )


__all__ = ["ConsolidationDerivation", "derive_consolidation", "edge_key_for"]
