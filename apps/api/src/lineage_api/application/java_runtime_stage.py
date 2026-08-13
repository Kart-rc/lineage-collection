"""Run the Java harness as a runtime stage and reduce it to element observations."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping, Sequence

from lineage_api.application.runtime_stage import RuntimeStageResult
from lineage_api.domain.urns import is_element_scoped_dataset_urn
from lineage_api.services.java_runtime_verification import (
    JavaObservation,
    generate_harness,
    parse_observations,
)
from lineage_api.services.liveness import derive_liveness


def java_home_or_none() -> str | None:
    configured = os.environ.get("LINEAGE_JAVA_HOME")
    if configured and (Path(configured) / "bin" / "javac").exists():
        return configured
    return None if shutil.which("javac") is None else ""


def _column_matches(field: str, column: str) -> bool:
    lowered = field.lower()
    return lowered == column.lower() or lowered == column.lower().replace("_", "")


def _element_urn_parts(urn_text: str) -> tuple[str, str]:
    dataset_part, _, element = urn_text.rpartition("#")
    table = dataset_part.rsplit(":", 1)[1]
    return table, element


def _from_urns(edge: dict) -> list[str]:
    value = edge["from"]
    items = value if isinstance(value, (list, tuple)) else [value]
    return [str(item) for item in items]


def _edge_parts(edge: dict) -> tuple[str, str, str]:
    """Locate the edge's element-scoped `dataset#column` side and derive an operation.

    Since element-ground Java SCA edges, the element side can land on EITHER end: a
    Java analyzer's READS edge puts it in `from` (the dataset is read into the service
    endpoint named by `to`); a WRITES edge puts it in `to` (the service endpoint named
    by `from` writes into the dataset). `to` is checked first — a `service://repo/
    Type#method` endpoint URN's own '#' never parses as an element-scoped ldp URN
    (`is_element_scoped_dataset_urn` guards that), so this never confuses the two.
    """
    to_urn = str(edge["to"])
    if is_element_scoped_dataset_urn(to_urn):
        table, element = _element_urn_parts(to_urn)
        return table, element, "WRITE"
    from_urn = _from_urns(edge)[0]
    table, element = _element_urn_parts(from_urn)
    return table, element, "READ"


def match_edges(
    static_edges: Sequence[dict], observations: Sequence[JavaObservation]
) -> tuple[list[dict], list[dict]]:
    matched: list[dict] = []
    unmatched: list[dict] = []
    # Dataset-scope edges (neither end an element-scoped ldp URN) are not claims this
    # element seam can judge — it only ever witnesses table+field pairs — so they are
    # neither corroborated nor static_only; they are simply excluded from the verdict
    # this stage computes. A `service://repo/Type#method` endpoint's own '#' never
    # counts as element scope either, so a Java READ edge's `to` never false-positives
    # here.
    element_edges = [
        edge
        for edge in static_edges
        if is_element_scoped_dataset_urn(str(edge["to"]))
        or any(is_element_scoped_dataset_urn(urn) for urn in _from_urns(edge))
    ]
    for edge in element_edges:
        table, element, operation = _edge_parts(edge)
        witnessed = any(
            observation.table == table
            and observation.operation == operation
            and any(_column_matches(field, element) for field in observation.fields)
            for observation in observations
            if observation.operation != "UNKNOWN"
        )
        (matched if witnessed else unmatched).append(edge)
    return matched, unmatched


def element_observations(edges: Sequence[dict], observed_at: str) -> list[dict]:
    return [
        {
            "mechanism": "RUNTIME",
            "runtimeScope": "ELEMENT",
            "sessionComplete": True,
            "observedAt": observed_at,
            "from": list(edge["from"]),
            "to": edge["to"],
            "edgeType": edge["edgeType"],
            "exact": False,
        }
        for edge in edges
    ]


def run_java_runtime_stage(
    *,
    sources: Mapping[str, str],
    static_edges: Sequence[dict],
    observed_at: str,
    java_home: str | None,
) -> RuntimeStageResult:
    if java_home is None:
        raise RuntimeError("jvm-unavailable")
    harness = generate_harness(dict(sources))
    prefix = (Path(java_home) / "bin") if java_home else Path("")
    javac = str(prefix / "javac") if java_home else "javac"
    java = str(prefix / "java") if java_home else "java"
    with tempfile.TemporaryDirectory() as workdir:
        root = Path(workdir)
        files = []
        for relative, text in {**dict(sources), **harness}.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            if relative.endswith(".java"):
                files.append(str(path))
        subprocess.run(
            [javac, "-d", str(root / "classes"), *files],
            check=True, capture_output=True, timeout=120,
        )
        completed = subprocess.run(
            [java, "-cp", str(root / "classes"), "harness.GeneratedRuntimeTest"],
            check=True, capture_output=True, text=True, timeout=120,
        )
    observations = parse_observations(completed.stdout)
    matched, unmatched = match_edges(static_edges, observations)
    stage_observations = element_observations(matched, observed_at)
    liveness = [
        derive_liveness(f"{e['from'][0]}->{e['to']}", 1, observed_at, session_complete=True)
        for e in matched
    ] + [
        derive_liveness(f"{e['from'][0]}->{e['to']}", 0, None, session_complete=True)
        for e in unmatched
    ]
    verdict = (
        "NOT_PROVIDED" if not matched and not unmatched
        else "PARTIALLY_CORROBORATED" if unmatched
        else "CORROBORATED"
    )
    return RuntimeStageResult(
        verdict=verdict,
        corroborated=len(matched),
        static_only=len(unmatched),
        # Table-level observations without a matching static edge are not element
        # evidence: the harness only reports dataset+field scope, not which specific
        # SCA edge they'd correspond to, so there is nothing to count as runtime-only
        # here.
        runtime_only=0,
        observations=tuple(stage_observations),
        liveness=tuple(liveness),
        executed=tuple(sorted({o.method for o in observations})),
    )
