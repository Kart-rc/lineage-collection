"""Measure lineage for ANY open-source Java/Spring checkout through the product path.

Generalizes `verify_petclinic_runtime_confidence.py`: same snapshot construction, same
`repository_collection.collect(descriptor, runtime_execution=True)` seam, but the
repository identity is taken from the command line instead of being hardcoded. This is
a measurement tool, not a stop condition: it reports what the java-spring-data-jpa-v1
cell and the runtime seam honestly produce for an arbitrary checkout — edges, element
scoping, corroboration, bands, and the residue codes that explain every refusal.

Run: uv run --project apps/api python scripts/measure_java_oss_lineage.py \\
         /path/to/checkout --origin https://github.com/owner/name \\
         [--system NAME] [--schema-profile mysql|postgres|h2]

Exit is 0 when the collection ran (whatever its outcome); non-zero only on a crash.
"""

from __future__ import annotations

import argparse
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
from lineage_api.domain.urns import is_element_scoped_dataset_urn

SECRET = "measure-java-oss-lineage-secret"
ANALYZER_PACK = "java-spring-data-jpa-v1"
RULESET = "spring-data-rules-v1"
SOURCE_KIND = "git-checkout"
FRAMEWORK = "spring-data-jpa"
ENVIRONMENT = "staging"


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


def _snapshot(
    root: Path, revision: str, *, origin: str, repository: str, system: str, profile: str
) -> RepositorySnapshot:
    paths = tuple(
        sorted(
            item.relative_to(root).as_posix()
            for item in root.rglob("*")
            if item.is_file()
            and ".git/" not in item.relative_to(root).as_posix()
            and "/target/" not in f"/{item.relative_to(root).as_posix()}"
            and "/node_modules/" not in f"/{item.relative_to(root).as_posix()}"
        )
    )

    def read_source(relative_path: str) -> bytes:
        return (root / relative_path).read_bytes()

    return RepositorySnapshot(
        descriptor=RepositoryCheckoutDescriptor(
            origin=origin,
            repository=repository,
            revision=revision,
            checkout_root=root,
            environment=ENVIRONMENT,
            platform=profile,
            system=system,
            analyzer_pack=ANALYZER_PACK,
            ruleset=RULESET,
        ),
        paths=paths,
        scope_digest="sha256:" + hashlib.sha256(revision.encode()).hexdigest(),
        _reader=read_source,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--system", default=None)
    parser.add_argument(
        "--schema-profile", default="mysql", choices=("h2", "mysql", "postgres")
    )
    args = parser.parse_args()

    root = args.checkout.resolve()
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")
    repository = args.origin.rstrip("/").rsplit("/", 1)[-1]
    system = args.system or repository

    revision = _revision(root)
    print(f"repository: {repository}  system: {system}  profile: {args.schema_profile}")
    print(f"HEAD digest: {revision}")

    snapshot = _snapshot(
        root,
        revision,
        origin=args.origin,
        repository=repository,
        system=system,
        profile=args.schema_profile,
    )
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
        descriptor = RepositoryCollectionDescriptor(
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
                schema_profile=args.schema_profile,
            ),
            snapshot=snapshot,
        )

        summary = services.repository_collection.collect(
            descriptor, runtime_execution=True
        )
        print(f"outcome: {summary['outcome']}  reasonCode: {summary['reasonCode']}")
        print(f"analysisStatus: {summary['analysisStatus']}")
        print(f"statusReasons: {summary['statusReasons']}")
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

        def _edge_is_element_scoped(edge: dict) -> bool:
            from_urns = (
                edge["from"] if isinstance(edge["from"], (list, tuple)) else [edge["from"]]
            )
            return is_element_scoped_dataset_urn(str(edge["to"])) or any(
                is_element_scoped_dataset_urn(str(urn)) for urn in from_urns
            )

        element_scoped = [e for e in edges if _edge_is_element_scoped(e)]
        high = [e for e in edges if e["band"] == "HIGH"]
        print(f"consolidated edges: {len(edges)}")
        print(f"  element-scoped: {len(element_scoped)}")
        print(f"  at band HIGH:   {len(high)}\n")

        for edge in sorted(edges, key=lambda e: e["edgeKey"]):
            mechanisms = {p["mechanism"] for p in edge["provenance"]}
            display = project_confidence(edge["band"], edge["provenance"])
            print(
                f"  {edge['from']} -> {edge['to']}\n"
                f"    band={edge['band']:8s} corroboration={edge['corroboration']:8s} "
                f"mechanisms={sorted(mechanisms)} display={display.display_band} "
                f"{display.percent}%"
            )

        return 0


if __name__ == "__main__":
    sys.exit(main())
