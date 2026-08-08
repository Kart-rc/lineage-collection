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


@dataclass(frozen=True, slots=True)
class AdapterResult:
    observations: tuple[dict[str, object], ...]
    unsupported: tuple[AdapterIssue, ...]
    quarantined: tuple[AdapterIssue, ...]
    source_checksum: str
    normalized_checksum: str

    @classmethod
    def build(
        cls,
        *,
        observations: Sequence[Mapping[str, object]],
        unsupported: Sequence[AdapterIssue],
        quarantined: Sequence[AdapterIssue],
        source: object,
    ) -> AdapterResult:
        normalized_items: list[dict[str, object]] = []
        seen: set[str] = set()
        for observation in observations:
            encoded = canonical(observation)
            if encoded in seen:
                continue
            seen.add(encoded)
            normalized_items.append(json.loads(encoded))
        normalized = tuple(normalized_items)
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
        return cls(
            observations=normalized,
            unsupported=unsupported_tuple,
            quarantined=quarantined_tuple,
            source_checksum=checksum(source),
            normalized_checksum=checksum(normalized_payload),
        )


__all__ = ["AdapterIssue", "AdapterResult", "canonical", "checksum"]
