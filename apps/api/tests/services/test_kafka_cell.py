"""The Kafka cell turns bindings into dataset edges a topic can be joined on.

This asserts against a real upstream checkout rather than a fixture, so it needs that
checkout present. It is skipped when absent instead of failing: an external repository
is not implicit test input, and a fresh clone must be able to run the suite green.

    git clone https://github.com/spring-cloud/spring-cloud-stream-samples \\
        "${LINEAGE_ESTATE_DIR:-/private/tmp/lineage-estate}/spring-cloud-stream-samples"
"""

import os
from pathlib import Path

import pytest

from lineage_api.services.analyzer_registry import AnalyzerRegistry, AnalyzerSelection
from lineage_api.services.resolver import Resolver

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "fixtures" / "catalog" / "catalog-snapshot-v1.json"
ESTATE = Path(os.environ.get("LINEAGE_ESTATE_DIR", "/private/tmp/lineage-estate"))
REAL = ESTATE / "spring-cloud-stream-samples"
PROCESSOR = REAL / "kafka-streams-samples/kafka-streams-inventory-count"

def _has_binding_configuration() -> bool:
    """True only when the corpus actually carries the config this cell reads.

    `is_dir()` alone is not enough. This corpus conventionally lives under
    `/private/tmp`, which macOS reaps, and the reap leaves the directory tree in
    place while removing every file. The analyzer then sees an empty repository and
    reports zero edges, so the assertions fail for a reason unrelated to the code
    under test. Require the binding configuration itself before claiming the corpus
    is present.
    """
    if not PROCESSOR.is_dir():
        return False
    patterns = ("application*.yml", "application*.yaml", "application*.properties")
    return any(any(PROCESSOR.rglob(pattern)) for pattern in patterns)


pytestmark = pytest.mark.skipif(
    not _has_binding_configuration(),
    reason=(
        f"stream-samples corpus missing or empty at {PROCESSOR}. "
        "Clone it there, or set LINEAGE_ESTATE_DIR, to run this proof."
    ),
)

SELECTION = AnalyzerSelection(
    "kafka-streams-v1", "kafka-binding-rules-v1", "git-checkout", "spring-cloud-stream", "kafka"
)


class _Snapshot:
    environment = "staging"
    platform = "kafka"
    system = "inventory"
    analyzer_pack = "kafka-streams-v1"
    ruleset = "kafka-binding-rules-v1"
    revision = "1" * 40
    scope_digest = "sha256:" + "2" * 64
    repository = "kafka-streams-inventory-count"

    def __init__(self, root: Path) -> None:
        self._root = root
        self.paths = tuple(
            sorted(
                item.relative_to(root).as_posix()
                for item in root.rglob("*")
                if item.is_file() and "/target/" not in f"/{item.relative_to(root)}"
            )
        )

    def read_bytes(self, relative_path: str) -> bytes:
        return (self._root / relative_path).read_bytes()


def _analyze():
    registry = AnalyzerRegistry.default(kafka_resolver=Resolver.from_path(CATALOG))
    return registry.analyze(_Snapshot(PROCESSOR), SELECTION, "run", "corr")


def test_a_real_stream_processor_reads_and_writes_its_topics() -> None:
    result = _analyze()

    assert result.status == "COMPLETE", result.status_reasons
    assert result.edge_count == 2
    assert sorted(result.document["datasetsSeen"]) == [
        "urn:ldp:staging:kafka:inventory:inventory-count-events",
        "urn:ldp:staging:kafka:inventory:inventory-update-events",
    ]


def test_the_input_binding_is_a_read_and_the_output_a_write() -> None:
    result = _analyze()

    by_type = {edge["edgeType"]: edge for edge in result.document["edges"]}
    assert by_type["READS"]["from"][0].endswith(":inventory-update-events")
    assert by_type["WRITES"]["to"].endswith(":inventory-count-events")


def test_the_edge_cites_the_binding_that_proves_it() -> None:
    result = _analyze()

    edge = result.document["edges"][0]
    assert edge["file"].endswith("application.yml")
    assert edge["line"] > 0
    assert "process" in edge["transform"]
