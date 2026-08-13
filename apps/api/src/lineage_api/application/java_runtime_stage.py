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
    read_entities,
    read_injection_sites,
    read_repositories,
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


def _is_service_endpoint_urn(value: str) -> bool:
    return value.startswith("service://")


def is_java_service_anchored_edge(edge: dict) -> bool:
    """True only when the edge has an ldp element-scoped dataset URN on exactly ONE
    end and a `service://` endpoint URN on the other -- the only shape the Java seam
    can ground. A Python-shape DERIVES edge (both ends ldp element URNs, no service
    anchor at all) must be excluded: nothing here witnesses a dataset-to-dataset
    transform, so admitting it risks a coincidental table-write observation
    "corroborating" a derivation that never ran. A dataset-scope edge (neither end
    element-scoped) is excluded the same way it always was. `from` must also carry
    exactly one URN -- a service-anchored edge is never a multi-source merge.
    """
    to_urn = str(edge.get("to", ""))
    from_urns = _from_urns(edge)
    to_element = is_element_scoped_dataset_urn(to_urn)
    from_element = any(is_element_scoped_dataset_urn(urn) for urn in from_urns)
    if to_element == from_element or len(from_urns) != 1:
        return False
    return _is_service_endpoint_urn(from_urns[0] if to_element else to_urn)


def _edge_parts(edge: dict) -> tuple[str, str, str]:
    """Locate the edge's element-scoped `dataset#column` side and derive an operation.

    Since element-ground Java SCA edges, the element side can land on EITHER end: a
    Java analyzer's READS edge puts it in `from` (the dataset is read into the service
    endpoint named by `to`); a WRITES edge puts it in `to` (the service endpoint named
    by `from` writes into the dataset). `to` is checked first — a `service://repo/
    Type#method` endpoint URN's own '#' never parses as an element-scoped ldp URN
    (`is_element_scoped_dataset_urn` guards that), so this never confuses the two.
    """
    from_urns = _from_urns(edge)
    assert len(from_urns) == 1, "service-anchored Java edge must carry exactly one from URN"
    to_urn = str(edge["to"])
    if is_element_scoped_dataset_urn(to_urn):
        table, element = _element_urn_parts(to_urn)
        return table, element, "WRITE"
    table, element = _element_urn_parts(from_urns[0])
    return table, element, "READ"


def match_edges(
    static_edges: Sequence[dict], observations: Sequence[JavaObservation]
) -> tuple[list[dict], list[dict]]:
    matched: list[dict] = []
    unmatched: list[dict] = []
    # Dataset-scope edges (neither end an element-scoped ldp URN) and both-ends-ldp
    # edges (a Python-shape DERIVES edge with no `service://` anchor at all) are not
    # claims this element seam can judge — it only ever witnesses table+field pairs
    # reached through a service call — so they are neither corroborated nor
    # static_only; they are simply excluded from the verdict this stage computes.
    # Defense in depth: this mirrors (does not just rely on) the caller's own
    # eligibility filter.
    element_edges = [edge for edge in static_edges if is_java_service_anchored_edge(edge)]
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


def _target_type_names(static_edges: Sequence[dict]) -> set[str]:
    """The simple type names an edge's own `service://repo/FQCN#method` endpoint
    names -- read straight off the edge, never guessed."""
    names: set[str] = set()
    for edge in static_edges:
        for urn in (str(edge.get("to", "")), *_from_urns(edge)):
            if urn.startswith("service://") and "#" in urn:
                fqcn = urn.split("/", 3)[-1].split("#", 1)[0]
                names.add(fqcn.rsplit(".", 1)[-1])
    return names


def _module_of(path: str) -> str:
    """The top-level checkout directory a path lives under -- a Maven module's own
    root in a multi-module checkout, or '' for a path with no directory component."""
    return path.split("/", 1)[0] if "/" in path else ""


def select_relevant_java_sources(
    sources: Mapping[str, str], static_edges: Sequence[dict]
) -> dict[str, str]:
    """Narrow the harness's compile scope from the whole checkout to what it actually
    needs: entity/repository/injection-site files (the shapes `generate_harness` reads
    to build the harness), scoped first to the module(s) that declare a type one of
    `static_edges`' own `service://repo/FQCN#method` endpoints names.

    A whole multi-module checkout compiles as one `javac` invocation, so one unrelated
    module's missing dependency (another service's Spring Boot entry point, wired for
    a runtime container this harness never provides) fails the WHOLE build -- silently
    starving every edge, including ones this seam could otherwise witness. Module
    scoping is not a second filter bolted on for convenience: it is what keeps the
    structural filter's own promise (compile only what an edge under test needs)
    honest when the checkout is multi-module. Falls back to the full structural
    filter, unscoped, when no edge names a type this checkout declares (e.g. a
    dataset-only edge already excluded upstream) -- narrower is only safe when it is
    still complete for the edges being verified.
    """
    target_names = _target_type_names(static_edges)
    modules = {
        _module_of(path)
        for path in sources
        if path.rsplit("/", 1)[-1].removesuffix(".java") in target_names
    }
    scoped = (
        {path: text for path, text in sources.items() if _module_of(path) in modules}
        if modules
        else sources
    )
    repositories = read_repositories(scoped)
    return {
        path: text
        for path, text in scoped.items()
        if read_entities({path: text})
        or read_repositories({path: text})
        or read_injection_sites({path: text}, repositories)
    }


def _minimal_subprocess_env(java_home: str | None) -> dict[str, str]:
    """A minimal environment for compiling and running repo-derived Java code.

    Only PATH (to resolve the OS's own shared libraries/tools) and JAVA_HOME (when a
    JVM was resolved from one) are passed through, plus HOME/TMPDIR when the host
    process has them -- the JVM itself can genuinely consult these (e.g. user
    preferences, class data sharing, temp file placement) even though this harness
    always names an explicit working directory. Nothing else from the orchestrator's
    own environment -- secrets, cloud credentials, unrelated service config -- is
    passed through to `javac`/`java` while they execute code drawn from the repo
    under collection.
    """
    env: dict[str, str] = {}
    path = os.environ.get("PATH")
    if path:
        env["PATH"] = path
    if java_home:
        env["JAVA_HOME"] = java_home
    for key in ("HOME", "TMPDIR"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def run_java_runtime_stage(
    *,
    sources: Mapping[str, str],
    static_edges: Sequence[dict],
    observed_at: str,
    java_home: str | None,
) -> RuntimeStageResult:
    if java_home is None:
        raise RuntimeError("jvm-unavailable")
    scoped_sources = select_relevant_java_sources(sources, static_edges)
    harness = generate_harness(scoped_sources)
    prefix = (Path(java_home) / "bin") if java_home else Path("")
    javac = str(prefix / "javac") if java_home else "javac"
    java = str(prefix / "java") if java_home else "java"
    env = _minimal_subprocess_env(java_home)
    with tempfile.TemporaryDirectory() as workdir:
        root = Path(workdir)
        files = []
        for relative, text in {**scoped_sources, **harness}.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            if relative.endswith(".java"):
                files.append(str(path))
        subprocess.run(
            [javac, "-d", str(root / "classes"), *files],
            check=True, capture_output=True, timeout=120, env=env,
        )
        completed = subprocess.run(
            [java, "-cp", str(root / "classes"), "harness.GeneratedRuntimeTest"],
            check=True, capture_output=True, text=True, timeout=120, env=env,
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
