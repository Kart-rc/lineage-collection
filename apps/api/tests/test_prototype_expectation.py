"""The product expectation is derived from the prototype document, not paraphrased.

`scripts/extract_prototype_expectation.py` parses the actual `.dc.html` and writes the
vocabularies it models to `docs/architecture/prototype-expectation.json`. These tests
assert the platform implements every term in that file, so a change to the prototype
breaks the build rather than silently drifting.
"""

import json
from pathlib import Path

from lineage_api.domain.interactions import INTERACTION_CHANNELS
from lineage_api.domain.product_confidence import DISPLAY_BANDS
from lineage_api.services.java_interaction_sca import SUPPORTED_CHANNELS
from lineage_api.services.liveness import LIVENESS_BANDS
from lineage_api.services.resolver import DATASET_KINDS

EXPECTATION = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "architecture"
    / "prototype-expectation.json"
)

KIND_MAP = {
    "datastore": "DATASTORE",
    "kafka": "STREAM",
    "s3land": "LAKE_LANDING",
    "s3file": "LAKE_FILE",
    "cache": "CACHE",
    "search": "SEARCH",
}
CHANNEL_MAP = {"rest": "REST", "grpc": "GRPC", "graphql": "GRAPHQL", "async": "ASYNC_EVENT"}
BAND_MAP = {"verified": "VERIFIED", "probable": "PROBABLE", "inferred": "INFERRED"}
LIVENESS_MAP = {"HOT": "HOT", "WARM": "WARM", "COLD": "COLD", "DEAD?": "UNOBSERVED"}


def _expectation() -> dict:
    return json.loads(EXPECTATION.read_text())


def test_the_expectation_was_extracted_from_the_prototype_document() -> None:
    expectation = _expectation()

    assert expectation["source"].endswith(".dc.html")
    assert expectation["sourceBytes"] > 100_000


def test_every_prototype_dataset_kind_has_a_platform_kind() -> None:
    for term in _expectation()["datasetKinds"]:
        assert KIND_MAP[term] in DATASET_KINDS, term


def test_every_prototype_channel_has_a_platform_channel_and_an_extractor() -> None:
    channels = _expectation()["interactionChannels"]

    for term in channels:
        assert CHANNEL_MAP[term] in INTERACTION_CHANNELS, term
    assert SUPPORTED_CHANNELS == {CHANNEL_MAP[term] for term in channels}


def test_every_prototype_confidence_band_has_a_platform_band() -> None:
    for term in _expectation()["confidenceThresholds"]:
        assert BAND_MAP[term] in DISPLAY_BANDS, term


def test_every_prototype_liveness_band_has_a_platform_band() -> None:
    for term in _expectation()["livenessBands"]:
        assert LIVENESS_MAP[term] in LIVENESS_BANDS, term


def test_the_element_field_signature_is_what_the_edge_model_carries() -> None:
    """up/down/xf are the endpoints and transform every DERIVES edge must supply."""
    signature = _expectation()["elementFieldSignature"]

    assert signature == ["name", "type", "tag", "up", "down", "xf"]
