from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Sequence


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def checksum(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class AdapterIssue:
    code: str
    location: str


def _deduplicated(items: Sequence[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
    normalized_items: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in items:
        encoded = canonical(item)
        if encoded in seen:
            continue
        seen.add(encoded)
        normalized_items.append(json.loads(encoded))
    return tuple(normalized_items)


@dataclass(frozen=True, slots=True)
class AdapterResult:
    observations: tuple[dict[str, object], ...]
    unsupported: tuple[AdapterIssue, ...]
    quarantined: tuple[AdapterIssue, ...]
    source_checksum: str
    normalized_checksum: str
    # Service-interaction observations live on their own plane; they are never mixed
    # into the dataset-lineage observations above.
    interactions: tuple[dict[str, object], ...] = ()

    @classmethod
    def build(
        cls,
        *,
        observations: Sequence[Mapping[str, object]],
        unsupported: Sequence[AdapterIssue],
        quarantined: Sequence[AdapterIssue],
        source: object,
        interactions: Sequence[Mapping[str, object]] = (),
    ) -> AdapterResult:
        normalized = _deduplicated(observations)
        normalized_interactions = _deduplicated(interactions)
        unsupported_tuple = tuple(sorted(unsupported, key=lambda item: (item.code, item.location)))
        quarantined_tuple = tuple(
            sorted(quarantined, key=lambda item: (item.code, item.location))
        )
        normalized_payload = {
            "observations": normalized,
            "unsupported": [
                {"code": item.code, "location": item.location}
                for item in unsupported_tuple
            ],
            "quarantined": [
                {"code": item.code, "location": item.location}
                for item in quarantined_tuple
            ],
        }
        if normalized_interactions:
            # Added only when present so dataset-only payloads keep the checksum
            # shape they had before the interactions plane existed.
            normalized_payload["interactions"] = normalized_interactions
        return cls(
            observations=normalized,
            unsupported=unsupported_tuple,
            quarantined=quarantined_tuple,
            source_checksum=checksum(source),
            normalized_checksum=checksum(normalized_payload),
            interactions=normalized_interactions,
        )


__all__ = ["AdapterIssue", "AdapterResult", "canonical", "checksum"]
