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
    print(
        "\nA zero here is the designed fail-closed outcome, not a crash. The codes name\n"
        "the compatibility boundary precisely; see docs/prototype-coverage.md L04."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
