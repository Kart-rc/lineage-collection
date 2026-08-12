"""Project the internal confidence model onto the bands the product displays.

This is a pure projection. `derive_band` decides what the platform believes; this
module decides only how that belief is shown. The percentage is a deterministic
function of the band — it drives ordering and colour, and is deliberately not a
calibrated probability. Never present it as one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

DISPLAY_BANDS = ("VERIFIED", "PROBABLE", "INFERRED")

# Internal band -> (display band, percent). The display band must mean what the
# product legend says it means, so only runtime-corroborated edges reach VERIFIED.
_BAND_PROJECTION = {
    "HIGHEST": ("VERIFIED", 96),
    "HIGH": ("VERIFIED", 92),
    "MEDIUM": ("PROBABLE", 78),
    "SINGLE": ("PROBABLE", 70),
    "LOWEST": ("INFERRED", 55),
}


@dataclass(frozen=True, slots=True)
class ProductConfidence:
    display_band: str
    percent: int
    signals: tuple[str, ...]
    last_observed: str | None


def project_confidence(
    band: str, provenance: Sequence[Mapping[str, object]]
) -> ProductConfidence:
    projection = _BAND_PROJECTION.get(band)
    if projection is None:
        raise ValueError(f"unknown confidence band: {band!r}")
    display_band, percent = projection

    signals = tuple(
        sorted(
            {
                str(item.get("mechanism", ""))
                for item in provenance
                if item.get("mechanism")
            }
        )
    )

    # Only a runtime assertion witnesses an edge; a static timestamp says when the
    # code was read, not when the edge last fired.
    observations = sorted(
        str(item["observedAt"])
        for item in provenance
        if item.get("mechanism") == "RUNTIME"
        and isinstance(item.get("observedAt"), str)
    )
    return ProductConfidence(
        display_band=display_band,
        percent=percent,
        signals=signals,
        last_observed=observations[-1] if observations else None,
    )
