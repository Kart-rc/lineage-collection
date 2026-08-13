"""Drive the real upstream Spring Petclinic checkout through the *product* collection
path — `repository_collection.collect(descriptor, runtime_execution=True)` — and report
whether any consolidated edge reaches band HIGH (display VERIFIED 92%).

This is the iterate-loop for the runtime-collection-product-path goal: petclinic is a
real, unmodified upstream checkout (never edited), so every mismatch it surfaces gets
fixed in the cells or seams the product path is built from, never in the checkout.

The snapshot construction mirrors `scripts/measure_real_petclinic.py` verbatim (same
`AnalyzerSelection`, same whole-repository scope) but instead of calling the Java SCA
cell directly, it goes through `RepositoryCollectionService.collect()` — the same seam
`apps/api/tests/application/test_collection_runtime_acceptance.py` drives for the
payments-pipeline fixture — so the full real session lifecycle (grant/observe/drain/
close) and consolidation merge run for real.

Run: uv run --project apps/api python scripts/verify_petclinic_runtime_confidence.py \\
         /path/to/spring-petclinic-microservices

Exit status is non-zero when no edge reaches band HIGH, so this script is a
machine-readable stop condition for the iteration loop.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from lineage_api.application.repository_collection import (
    AnalyzerIdentity,
    RepositoryCollectionDescriptor,
    RepositoryIdentity,
)
from lineage_api.application.repository_sources import (
    RepositoryCheckoutDescriptor,
    RepositorySnapshot,
)
from lineage_api.config import Settings
from lineage_api.dependencies import build_services
from lineage_api.domain.product_confidence import project_confidence
from lineage_api.domain.urns import LineageUrn

ORIGIN = "https://github.com/spring-petclinic/spring-petclinic-microservices"
REPOSITORY = "spring-petclinic-microservices"
SECRET = "verify-petclinic-runtime-confidence-secret"

# Same determinants `measure_real_petclinic.py` uses for the java-spring-data-jpa-v1
# pack: git-checkout / spring-data-jpa / mysql. Task 5 made these registry-derived for
# every pack; they are still spelled out here because they are also the descriptor's
# own validated fields.
ANALYZER_PACK = "java-spring-data-jpa-v1"
RULESET = "spring-data-rules-v1"
SOURCE_KIND = "git-checkout"
FRAMEWORK = "spring-data-jpa"
SCHEMA_PROFILE = "mysql"
ENVIRONMENT = "staging"
SYSTEM = "petclinic"


def _revision(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        digest = result.stdout.strip()
    except OSError:
        digest = ""
    return digest or "0" * 40


def _snapshot(root: Path, revision: str) -> RepositorySnapshot:
    paths = tuple(
        sorted(
            item.relative_to(root).as_posix()
            for item in root.rglob("*")
            if item.is_file()
            and ".git/" not in item.relative_to(root).as_posix()
            and "/target/" not in f"/{item.relative_to(root).as_posix()}"
        )
    )

    def read_source(relative_path: str) -> bytes:
        return (root / relative_path).read_bytes()

    return RepositorySnapshot(
        descriptor=RepositoryCheckoutDescriptor(
            origin=ORIGIN,
            repository=REPOSITORY,
            revision=revision,
            checkout_root=root,
            environment=ENVIRONMENT,
            platform=SCHEMA_PROFILE,
            system=SYSTEM,
            analyzer_pack=ANALYZER_PACK,
            ruleset=RULESET,
        ),
        paths=paths,
        # A whole-repository scope digest is not a hash of every byte (208 files); it
        # only has to be a valid, exact sha256 token — `RepositorySnapshot` does not
        # cross-check it against content, mirroring `measure_real_petclinic.py`.
        scope_digest="sha256:" + hashlib.sha256(revision.encode()).hexdigest(),
        _reader=read_source,
    )


def _descriptor(snapshot: RepositorySnapshot) -> RepositoryCollectionDescriptor:
    return RepositoryCollectionDescriptor(
        repository=RepositoryIdentity(
            origin=snapshot.origin,
            repository=snapshot.repository,
            revision=snapshot.revision,
            environment=snapshot.environment,
            platform=snapshot.platform,
            system=snapshot.system,
        ),
        analyzer=AnalyzerIdentity(
            analyzer_pack=snapshot.analyzer_pack,
            ruleset=snapshot.ruleset,
            source_kind=SOURCE_KIND,
            framework=FRAMEWORK,
            schema_profile=SCHEMA_PROFILE,
        ),
        snapshot=snapshot,
    )


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")

    revision = _revision(root)
    print(f"repository: {root.name}")
    print(f"HEAD digest: {revision}")

    snapshot = _snapshot(root, revision)
    print(f"files in scope: {len(snapshot.paths)}\n")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        settings = Settings(
            project_root=ROOT,
            data_directory=tmp_path,
            fixture_directory=ROOT / "fixtures",
            database_path=tmp_path / "lineage.db",
            object_directory=tmp_path / "objects",
            webhook_secret=SECRET,
        )
        services = build_services(settings)
        descriptor = _descriptor(snapshot)

        summary = services.repository_collection.collect(descriptor, runtime_execution=True)
        print(f"outcome: {summary['outcome']}  reasonCode: {summary['reasonCode']}")
        print(f"runtimeStatus: {summary['runtimeStatus']}")
        print(f"runtimeReasons: {summary['runtimeReasons']}")
        print(
            f"counts: edges={summary['counts']['edges']} reads={summary['counts']['reads']} "
            f"writes={summary['counts']['writes']} residue={summary['counts']['residue']}\n"
        )

        edges = [
            edge.as_dict()
            for edge in services.orchestration._consolidation._latest_edges()
        ]
        print(f"consolidated edges: {len(edges)}")

        def _is_element_scoped_dataset_urn(value: str) -> bool:
            # A raw '#' substring is not proof of element scope: the Java analyzer's
            # `to` can be a `service://repo/Owner#findAll` endpoint URN, whose '#'
            # separates method from type, not dataset from column.
            if "#" not in value:
                return False
            try:
                return LineageUrn.parse(value).element is not None
            except ValueError:
                return False

        element_scoped = [e for e in edges if _is_element_scoped_dataset_urn(str(e["to"]))]
        print(f"  of which element-scoped (real dataset#column 'to'): {len(element_scoped)}")

        high_edges = []
        for edge in sorted(edges, key=lambda e: e["edgeKey"]):
            mechanisms = {p["mechanism"] for p in edge["provenance"]}
            display = project_confidence(edge["band"], edge["provenance"])
            print(
                f"  {edge['from']} -> {edge['to']}\n"
                f"    band={edge['band']:8s} corroboration={edge['corroboration']:8s} "
                f"mechanisms={sorted(mechanisms)} runtimeStatus={summary['runtimeStatus']} "
                f"runtimeReasons={summary['runtimeReasons']}\n"
                f"    display={display.display_band} {display.percent}%"
            )
            if edge["band"] == "HIGH":
                high_edges.append(edge)

        print(f"\nedges at band HIGH: {len(high_edges)} of {len(edges)}")
        if not high_edges:
            print("\nno consolidated edge reached band HIGH.")
            if not element_scoped:
                print(
                    "STRUCTURAL WALL: every SCA edge for this checkout is dataset-scoped "
                    "(no '#element' in 'to'). `_execute_runtime_session` only ever hands "
                    "element-scoped edges to the Java runtime stage, so runtime "
                    "corroboration — and therefore band HIGH — is unreachable for this "
                    "repository's shape without inventing an element edge, which the "
                    "task boundary forbids. Sample edge URNs:"
                )
                for edge in sorted(edges, key=lambda e: e["edgeKey"])[:6]:
                    print(f"    {edge['from']} -> {edge['to']}")
            return 1

        print("\nband HIGH reached: SCA + ELEMENT runtime corroboration proven on the")
        print("real, unmodified upstream checkout.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
