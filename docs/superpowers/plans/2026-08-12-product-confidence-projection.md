# Product Confidence Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Project the platform's internal confidence model onto the three bands the product prototype displays — Verified / Probable / Inferred — with the signal set and observation recency each edge carries.

**Architecture:** The internal model already exists and is correct: `derive_band` in `domain/confidence.py` returns `LOWEST | SINGLE | MEDIUM | HIGH | HIGHEST` from the set of mechanisms that asserted an edge, and `ConsolidatedEdge` carries `band` plus full `provenance`. What is missing is the *product projection*: a display band, a percentage, the list of contributing signals, and how recently the edge was last observed. This plan adds a pure projection layer — it does not change how bands are derived, so no existing trust decision moves.

**Tech Stack:** Python 3.12, pytest, existing `lineage_api.domain.confidence`, `lineage_api.services.consolidation`.

## Global Constraints

- **Do not change `derive_band`.** The internal vocabulary and its rules are load-bearing for auto-publish decisions. This plan reads them; it never redefines them.
- **No invented percentages.** A displayed percentage is a deterministic function of the band and signal count, documented in the code, not a heuristic score. It exists to drive ordering and colour, and must never be presented as a calibrated probability.
- **Recency is observed, never assumed.** An edge with no runtime observation reports `last_observed = None` and displays as `never`, matching the prototype's `seen: 'never'`.
- **The prototype's percentages are mockups.** `fieldConf()` in the prototype derives them from a character-code hash. Match the *shape* (band, signals, recency), not those numbers.
- Test command prefix: `uv run --project apps/api python -m pytest`

---

## File Structure

| File | Responsibility |
|---|---|
| `apps/api/src/lineage_api/domain/product_confidence.py` (create) | The pure projection: internal band + provenance → display band, percentage, signals, recency. |
| `apps/api/tests/domain/test_product_confidence.py` (create) | Every band mapping and the recency rule. |

---

### Task 1: The projection

**Files:**
- Create: `apps/api/src/lineage_api/domain/product_confidence.py`
- Test: `apps/api/tests/domain/test_product_confidence.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True, slots=True) ProductConfidence(display_band: str, percent: int, signals: tuple[str, ...], last_observed: str | None)`
  - `project_confidence(band: str, provenance: Sequence[Mapping[str, object]]) -> ProductConfidence`
  - `DISPLAY_BANDS: tuple[str, ...] = ("VERIFIED", "PROBABLE", "INFERRED")`

Mapping, chosen so the display band means what the prototype's legend says it means:

| Internal band | Mechanisms | Display | Percent | Prototype legend |
|---|---|---|---|---|
| `HIGHEST` | SCA + LLM + RUNTIME | `VERIFIED` | 96 | "Runtime-confirmed by multiple signals" |
| `HIGH` | SCA + RUNTIME | `VERIFIED` | 92 | "Runtime-confirmed by multiple signals" |
| `MEDIUM` | SCA + LLM | `PROBABLE` | 78 | "Static + LLM, partially observed at runtime" |
| `SINGLE` | one mechanism (SCA) | `PROBABLE` | 70 | static only, not yet observed |
| `LOWEST` | LLM only | `INFERRED` | 55 | "LLM-derived, not yet observed at runtime" |

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/domain/test_product_confidence.py
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
        [_sca(), {"mechanism": "LLM"}, {"mechanism": "RUNTIME", "observedAt": "2026-08-12T09:00:00Z"}],
    )

    assert result.display_band == "VERIFIED"
    assert result.percent == 96
    assert result.signals == ("LLM", "RUNTIME", "SCA")


def test_static_only_is_probable_and_never_observed() -> None:
    result = project_confidence("SINGLE", [_sca()])

    assert result.display_band == "PROBABLE"
    assert result.percent == 70
    assert result.last_observed is None


def test_llm_only_is_inferred() -> None:
    result = project_confidence("LOWEST", [{"mechanism": "LLM"}])

    assert result.display_band == "INFERRED"
    assert result.percent == 55


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


def test_an_unknown_band_is_rejected_rather_than_defaulted() -> None:
    with pytest.raises(ValueError, match="confidence band"):
        project_confidence("SOMETHING", [_sca()])


def test_display_bands_match_the_product_vocabulary() -> None:
    assert DISPLAY_BANDS == ("VERIFIED", "PROBABLE", "INFERRED")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --project apps/api python -m pytest apps/api/tests/domain/test_product_confidence.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# apps/api/src/lineage_api/domain/product_confidence.py
"""Project the internal confidence model onto the bands the product displays.

This is a pure projection. `derive_band` decides what the platform believes; this
module decides only how that belief is shown. The percentage is a deterministic
function of the band — it drives ordering and colour, and is not a calibrated
probability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

DISPLAY_BANDS = ("VERIFIED", "PROBABLE", "INFERRED")

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

    signals = tuple(sorted({str(item.get("mechanism", "")) for item in provenance if item.get("mechanism")}))

    observations = sorted(
        str(item["observedAt"])
        for item in provenance
        if item.get("mechanism") == "RUNTIME" and isinstance(item.get("observedAt"), str)
    )
    return ProductConfidence(
        display_band=display_band,
        percent=percent,
        signals=signals,
        last_observed=observations[-1] if observations else None,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --project apps/api python -m pytest apps/api/tests/domain/test_product_confidence.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/lineage_api/domain/product_confidence.py apps/api/tests/domain/test_product_confidence.py
git commit -m "feat: project internal confidence bands onto the product vocabulary"
```

---

### Task 2: Carry the projection onto consolidated edges

**Files:**
- Modify: `apps/api/src/lineage_api/services/consolidation.py`
- Test: `apps/api/tests/services/test_consolidation_product_confidence.py` (create)

**Interfaces:**
- Consumes: `project_confidence` (Task 1).
- Produces: `ConsolidatedEdge.as_dict()` gains a `"productConfidence"` object with keys `displayBand`, `percent`, `signals`, `lastObserved`.

The field is **additive**. `consolidated-edge.schema.json` must gain it as an optional property so existing stored edges stay valid.

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/services/test_consolidation_product_confidence.py
from lineage_api.services.consolidation import ConsolidatedEdge, MechanismAssertion


def _assertion(mechanism: str, **extra) -> MechanismAssertion:
    return MechanismAssertion(
        provenance_id=f"prov-{mechanism.lower()}",
        from_urns=("urn:ldp:staging:snowflake:payments:raw.transactions#amount",),
        to_urn="urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue",
        edge_type="DERIVES",
        transform="SUM(amount)",
        mechanism=mechanism,
        exact=True,
        evidence_ref={},
        repo="warehouse-sql",
        run_id="run-1",
        correlation_id="corr-1",
        **extra,
    )


def test_a_consolidated_edge_exposes_its_product_confidence() -> None:
    edge = ConsolidatedEdge(
        schema_version="1.0.0",
        edge_key="edge-1",
        version=1,
        from_urns=("urn:ldp:staging:snowflake:payments:raw.transactions#amount",),
        to_urn="urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue",
        edge_type="DERIVES",
        band="SINGLE",
        corroboration="NONE",
        status="PROPOSED",
        transform="SUM(amount)",
        provenance=(_assertion("SCA"),),
        auto_publishable=True,
        system="payments",
        updated_at="2026-08-12T10:00:00Z",
    )

    payload = edge.as_dict()

    assert payload["productConfidence"] == {
        "displayBand": "PROBABLE",
        "percent": 70,
        "signals": ["SCA"],
        "lastObserved": None,
    }
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL — `KeyError: 'productConfidence'`

- [ ] **Step 3: Implement**

In `consolidation.py`, import the projection and add to `ConsolidatedEdge.as_dict()` before the `transform` block:

```python
        confidence = project_confidence(
            self.band, [item.as_dict() for item in self.provenance]
        )
        payload["productConfidence"] = {
            "displayBand": confidence.display_band,
            "percent": confidence.percent,
            "signals": list(confidence.signals),
            "lastObserved": confidence.last_observed,
        }
```

Add the optional property to `packages/contracts/consolidated-edge.schema.json` under `properties`:

```json
    "productConfidence": {
      "type": "object",
      "additionalProperties": false,
      "required": ["displayBand", "percent", "signals", "lastObserved"],
      "properties": {
        "displayBand": { "enum": ["VERIFIED", "PROBABLE", "INFERRED"] },
        "percent": { "type": "integer", "minimum": 0, "maximum": 100 },
        "signals": { "type": "array", "items": { "type": "string" } },
        "lastObserved": { "type": ["string", "null"] }
      }
    }
```

- [ ] **Step 4: Run the full suite**

Run: `uv run --project apps/api python -m pytest apps/api/tests -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/lineage_api/services/consolidation.py packages/contracts/consolidated-edge.schema.json apps/api/tests/services/test_consolidation_product_confidence.py
git commit -m "feat: expose product confidence on consolidated edges"
```

---

## Final verification

- [ ] Full suite green
- [ ] An SCA-only edge displays `PROBABLE` with `lastObserved: null` — the honest state for everything the platform produces today, since runtime is not yet on the collection path (see the runtime plan)
