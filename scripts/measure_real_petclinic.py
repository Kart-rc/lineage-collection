"""Measure the Java cell against a real upstream Spring Petclinic checkout.

The fixtures in `fixtures/repositories/` are purpose-built to resolve. This script does
the opposite job: it points the cell at an unmodified upstream checkout and records what
actually happens, so the compatibility boundary is measured rather than asserted.

It takes a path so nothing depends on a machine-local checkout:

  uv run --project apps/api python scripts/measure_real_petclinic.py \\
      /path/to/spring-petclinic-microservices

Exit status is always 0: a repository that fails closed is a valid measurement, not a
script failure.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import json

from lineage_api.services.analyzer_registry import AnalyzerRegistry, AnalyzerSelection
from lineage_api.services.composition import compose_repository_documents
from lineage_api.services.impact_simulation import simulate_impact
from lineage_api.services.java_interaction_sca import analyze_java_interactions

SELECTION = AnalyzerSelection(
    "java-spring-data-jpa-v1",
    "spring-data-rules-v1",
    "git-checkout",
    "spring-data-jpa",
    "mysql",
)


class _RepositorySnapshot:
    """The whole multi-module repository, which is the unit the cell must analyse.

    Analysing a module in isolation cannot work: its pom names a parent that is not in
    scope, so the Boot evidence is unreachable. The repository root is the smallest
    scope that contains a complete build closure.
    """

    environment = "staging"
    platform = "mysql"
    system = "petclinic"
    analyzer_pack = "java-spring-data-jpa-v1"
    ruleset = "spring-data-rules-v1"
    scope_digest = "sha256:" + "3" * 64
    origin = "https://github.com/spring-petclinic/spring-petclinic-microservices"

    def __init__(self, root: Path, revision: str) -> None:
        self.repository = root.name
        self.revision = revision
        self._root = root
        self.paths = tuple(
            sorted(
                item.relative_to(root).as_posix()
                for item in root.rglob("*")
                if item.is_file()
                and ".git/" not in item.relative_to(root).as_posix()
                and "/target/" not in f"/{item.relative_to(root).as_posix()}"
            )
        )

    def read_bytes(self, relative_path: str) -> bytes:
        return (self._root / relative_path).read_bytes()


def _revision(root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout.strip() or "0" * 40
    except OSError:
        return "0" * 40


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")

    revision = _revision(root)
    snapshot = _RepositorySnapshot(root, revision)

    print(f"repository: {root.name}")
    print(f"revision:   {revision}")
    print(f"files in scope: {len(snapshot.paths)}\n")

    registry = AnalyzerRegistry.default()
    result = registry.analyze(snapshot, SELECTION, "run-real", "corr-real")

    print(f"  status: {result.status}")
    print(
        f"  edges={result.edge_count} reads={result.read_count} "
        f"writes={result.write_count} residue={result.residue_count}"
    )
    for edge in result.document["edges"]:
        print(f"    {edge['edgeType']:7s} {edge['transform']}")
    codes: dict[str, int] = {}
    for item in result.document["residue"]:
        codes[item["code"]] = codes.get(item["code"], 0) + 1

    print(f"\ntotal edges across the real checkout: {result.edge_count}")
    print(f"residue codes: {dict(sorted(codes.items()))}")
    # --- the interactions plane, from the same real sources -------------------------
    sources = {
        path: snapshot.read_bytes(path).decode("utf-8", errors="replace")
        for path in snapshot.paths
        if path.endswith(".java") and "/test/" not in f"/{path}"
    }
    interactions = analyze_java_interactions(sources, service=root.name)
    print(f"\ninteractions: {len(interactions.inbound)} inbound, "
          f"{len(interactions.outbound)} outbound, {len(interactions.residue)} residue")
    for call in interactions.outbound:
        print(f"    OUT {call.channel:8s} {call.from_service} -> {call.to_service:22s} {call.operation}")
    for endpoint in interactions.inbound[:6]:
        print(f"    IN  {endpoint.channel:8s} {endpoint.operation:36s} {endpoint.handler}")
    if len(interactions.inbound) > 6:
        print(f"    ... and {len(interactions.inbound) - 6} more inbound endpoints")

    # --- composition and blast radius over the real graph ---------------------------
    graph = compose_repository_documents((result.document,))
    print(f"\ncomposed graph: {len(graph.edges)} edges over "
          f"{len(graph.contributions[0].datasets)} datasets")
    dataset_urns = sorted(
        {str(edge["to"]) for edge in graph.edges if str(edge["to"]).startswith("urn:")}
    )
    if dataset_urns:
        radius = simulate_impact(graph, dataset_urns[0])
        print(f"blast radius from {dataset_urns[0].split(':')[-1]}: "
              f"{len(radius.impacted)} impacted, maxHops={radius.max_hops}")

    # --- score against the expectation extracted from the prototype document --------
    expectation_path = ROOT / "docs" / "architecture" / "prototype-expectation.json"
    if expectation_path.exists():
        expectation = json.loads(expectation_path.read_text())
        channels_seen = {c.channel for c in interactions.outbound} | {
            e.channel for e in interactions.inbound
        }
        rows = [
            ("analysis completes", result.status == "COMPLETE"),
            ("dataset access edges", result.edge_count > 0),
            ("reads and writes both proven", result.read_count > 0 and result.write_count > 0),
            ("explicit @Query resolved to its own entity",
             any("JPQL" in e["transform"] for e in result.document["edges"])),
            ("inbound API endpoints", bool(interactions.inbound)),
            ("outbound service-to-service calls", bool(interactions.outbound)),
            ("field-level request/response contracts",
             all(e.request_fields or e.response_fields for e in interactions.inbound)),
            ("unprovable call sites refused, not guessed",
             any(r.code == "dynamic-endpoint" for r in interactions.residue)),
            ("channels observed are prototype channels",
             channels_seen <= {"REST", "GRPC", "GRAPHQL", "ASYNC_EVENT"}),
        ]
        print(f"\nscored against {expectation['source']} "
              f"({expectation['sourceBytes']} bytes):")
        for label, ok in rows:
            print(f"  [{'x' if ok else ' '}] {label}")
        met = sum(1 for _, ok in rows if ok)
        print(f"\n  {met}/{len(rows)} met ON THE REAL CHECKOUT")

    print(
        "\nEvery number above comes from the unmodified upstream checkout, not a fixture."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
