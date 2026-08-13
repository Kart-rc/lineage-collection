"""Extract the lineage expectation directly from the product prototype document.

The prototype is a working UI whose data model *is* the specification: the vocabularies
it colours, bands, and groups by are the ones a lineage platform has to produce. This
script reads the actual `.dc.html`, pulls those vocabularies out of its JavaScript, and
writes them to a JSON file that `verify_prototype_alignment.py` then checks the platform
against.

The point is that the expectation is *derived*, not paraphrased: if the prototype
changes, re-running this changes the target, and the verifier fails until the platform
catches up.

Run:
  uv run --project apps/api python scripts/extract_prototype_expectation.py \
      "<path to Throughline - Agentic.dc.html>"
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DEFAULT_SOURCE = Path(
    "/Users/sowmiyamohankumar/Documents/element-level-lineage/deliverables/"
    "latest_source/Data Lineage Impact Platform-10/Throughline - Agentic.dc.html"
)
OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "architecture" / "prototype-expectation.json"


def _script(html: str) -> str:
    blocks = re.findall(r"(?is)<script[^>]*>(.*?)</script>", html)
    if not blocks:
        raise SystemExit("prototype contains no script block")
    return max(blocks, key=len)


def _field_signature(js: str) -> list[str]:
    match = re.search(r"const f = \(([^)]*)\)", js)
    if not match:
        raise SystemExit("could not locate the field constructor")
    return [part.strip() for part in match.group(1).split(",")]


def _confidence_thresholds(js: str) -> dict[str, int]:
    match = re.search(
        r"bandOf\(pct\)\s*\{\s*return pct >= (\d+) \? '(\w+)' : pct >= (\d+) \? '(\w+)' : '(\w+)'",
        js,
    )
    if not match:
        raise SystemExit("could not locate bandOf()")
    high, high_name, mid, mid_name, low_name = match.groups()
    return {high_name: int(high), mid_name: int(mid), low_name: 0}


def _band_notes(js: str) -> dict[str, str]:
    block = re.search(r"bandMeta\(b\)\s*\{.*?\}\[b\];", js, re.S)
    if not block:
        raise SystemExit("could not locate bandMeta()")
    return {
        name: note
        for name, note in re.findall(
            r"(\w+):\s*\{[^}]*?note:\s*'([^']+)'", block.group(0)
        )
    }


def _named_map_keys(js: str, function_name: str) -> list[str]:
    block = re.search(rf"{function_name}\((\w+)\)\s*\{{.*?\}}\[\1\]", js, re.S)
    if not block:
        raise SystemExit(f"could not locate {function_name}()")
    keys = re.findall(r"(?:^|[{,\s])'?([A-Za-z?]+)'?:\s*\{", block.group(0))
    return sorted({key for key in keys if key not in {"return"}})


def _interaction_channels(js: str) -> list[str]:
    block = re.search(r"interactions\(\)\s*\{\s*return \[(.*?)\n    \];", js, re.S)
    if not block:
        raise SystemExit("could not locate interactions()")
    return sorted({value for value in re.findall(r"ch:\s*'(\w+)'", block.group(1))})


def _interaction_keys(js: str) -> list[str]:
    block = re.search(r"interactions\(\)\s*\{\s*return \[(.*?)\n    \];", js, re.S)
    first = re.search(r"\{\s*from:.*?\n", block.group(1), re.S)
    return sorted({key for key in re.findall(r"(\w+):", first.group(0))})


def _dataset_kinds(js: str) -> list[str]:
    return sorted({value for value in re.findall(r"dtype:\s*'(\w+)'", js)})


def _signals(js: str) -> list[str]:
    found: set[str] = set()
    for group in re.findall(r"signals:\s*\[([^\]]*)\]", js):
        found.update(re.findall(r"'(\w+)'", group))
    return sorted(found)


def _code_path_keys(js: str) -> list[str]:
    block = re.search(r"codePaths\(id\)\s*\{.*?\}\[id\]", js, re.S)
    if not block:
        raise SystemExit("could not locate codePaths()")
    first = re.search(r"\{\s*id:.*?\}", block.group(0), re.S)
    return sorted({key for key in re.findall(r"(\w+):", first.group(0))})


def main() -> int:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    if not source.exists():
        raise SystemExit(f"prototype not found: {source}")
    js = _script(source.read_text(errors="replace"))

    expectation = {
        "schemaVersion": "1.0.0",
        "source": source.name,
        "sourceBytes": source.stat().st_size,
        "elementFieldSignature": _field_signature(js),
        "confidenceThresholds": _confidence_thresholds(js),
        "confidenceNotes": _band_notes(js),
        "confidenceSignals": _signals(js),
        "livenessBands": _named_map_keys(js, "freqStyle"),
        "severityBands": _named_map_keys(js, "sevStyle"),
        "interactionChannels": _interaction_channels(js),
        "interactionKeys": _interaction_keys(js),
        "datasetKinds": _dataset_kinds(js),
        "codePathKeys": _code_path_keys(js),
    }

    OUTPUT.write_text(json.dumps(expectation, indent=2, sort_keys=True) + "\n")
    print(f"extracted from {source.name} ({expectation['sourceBytes']} bytes)")
    for key, value in expectation.items():
        if key in {"schemaVersion", "source", "sourceBytes"}:
            continue
        print(f"  {key}: {value}")
    print(f"\nwritten to {OUTPUT.relative_to(Path.cwd())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
