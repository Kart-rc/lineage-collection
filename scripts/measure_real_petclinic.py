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

from lineage_api.services.analyzer_registry import AnalyzerRegistry, AnalyzerSelection

SELECTION = AnalyzerSelection(
    "java-spring-data-jpa-v1",
    "spring-data-rules-v1",
    "git-checkout",
    "spring-data-jpa",
    "mysql",
)


class _ModuleSnapshot:
    environment = "staging"
    platform = "mysql"
    system = "petclinic"
    analyzer_pack = "java-spring-data-jpa-v1"
    ruleset = "spring-data-rules-v1"
    scope_digest = "sha256:" + "3" * 64
    origin = "https://github.com/spring-petclinic/spring-petclinic-microservices"

    def __init__(self, root: Path, module: str, revision: str) -> None:
        self.repository = module
        self.revision = revision
        self._root = root / module
        self.paths = tuple(
            sorted(
                item.relative_to(self._root).as_posix()
                for item in self._root.rglob("*")
                if item.is_file()
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
    modules = sorted(
        item.name
        for item in root.iterdir()
        if item.is_dir() and (item / "src" / "main" / "java").is_dir()
    )

    print(f"repository: {root.name}")
    print(f"revision:   {revision}")
    print(f"modules with production Java: {len(modules)}\n")

    registry = AnalyzerRegistry.default()
    total_edges = 0
    all_codes: set[str] = set()

    for module in modules:
        snapshot = _ModuleSnapshot(root, module, revision)
        try:
            result = registry.analyze(snapshot, SELECTION, f"run-{module}", "corr")
        except Exception as error:  # noqa: BLE001 - a measurement, not a pipeline
            print(f"  {module:44s} ERROR {type(error).__name__}: {error}")
            continue
        codes = sorted({item["code"] for item in result.document["residue"]})
        all_codes.update(codes)
        total_edges += result.edge_count
        print(
            f"  {module:44s} {result.status:22s} files={len(snapshot.paths):3d} "
            f"edges={result.edge_count}"
        )
        for edge in result.document["edges"]:
            print(f"      {edge['edgeType']:7s} {edge['transform']}")
        if codes:
            print(f"      residue: {', '.join(codes)}")

    print(f"\ntotal edges across the real checkout: {total_edges}")
    print(f"distinct residue codes: {sorted(all_codes)}")
    print(
        "\nA zero here is the designed fail-closed outcome, not a crash. The codes name\n"
        "the compatibility boundary precisely; see docs/prototype-coverage.md L04."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
