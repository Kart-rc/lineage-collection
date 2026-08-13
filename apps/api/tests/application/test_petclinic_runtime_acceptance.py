"""Petclinic proof — the real, unmodified upstream Spring Petclinic checkout through the
*product* collection path: `repository_collection.collect(descriptor, runtime_execution=True)`.

This freezes the result of the Task 6 iteration loop (see
`.superpowers/sdd/2026-08-12-runtime-collection-product-path/task-6-report.md`), which is
a documented structural finding, not the band-HIGH proof the task set out to reach:

The `java-spring-data-jpa-v1` analyzer (`services/java_spring_sca.py`) compiles every
Spring Data repository call into a **dataset-scoped** edge — `service://repo/Type#method`
on one end, the bare `urn:ldp:...:table` dataset URN on the other — never a
`urn:ldp:...:table#column` element-scoped URN. That is true for every repository in this
checkout (`OwnerRepository`, `PetRepository`, `VetRepository`, `VisitRepository`): all of
petclinic's real endpoints are `findAll` / `findById` / `save`-style whole-entity CRUD,
never a query that a proof rule grounds down to individual columns.

`OrchestrationService._execute_runtime_session` only ever hands the Java runtime stage
edges whose `to` is a genuine `urn:ldp:` dataset URN carrying a `.element` — by design, the
Java runtime harness (`java_runtime_stage.match_edges`) only ever witnesses table+field
pairs, so a dataset-scoped edge is not a claim it can corroborate. With no element-scoped
SCA edge to hand it, the Java runtime stage is never invoked at all: runtime execution
fails closed with `execution-failed`, and every consolidated edge stays at band SINGLE.

Reaching band HIGH here would require the SCA analyzer itself to ground repository calls
down to individual columns (real, proven work in `_emit_query_elements`/
`spring.query-element`, computed today but never wired into the edges it emits) — a
change to the SCA *cell* squarely out of this task's scope, and the task's own boundary
is explicit: report this as a structural wall with evidence rather than invent an element
edge to force a match. This test locks that honest, fail-closed behaviour precisely so
that any future change which starts producing element-scoped Java SCA edges is caught
here and must be a deliberate, reviewed decision, not a silent regression.

One real bug *was* found and fixed by this iteration, and is covered here too: before the
fix, `_execute_runtime_session` treated any `#` in an edge's `to` as proof of element
scope. A Java READ edge's `to` is `service://repo/Type#method` — the `#` separates method
from type, not dataset from column — so `LineageUrn.parse` raised and the whole collection
failed with `PIPELINE_FAILED` instead of failing closed. `test_collection_runtime_acceptance.py`
carries the minimal regression for that fix; this test proves it holds at full
whole-repository scale on the real checkout too.
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path

import pytest

from lineage_api.application.java_runtime_stage import java_home_or_none
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
from lineage_api.domain.urns import LineageUrn


PROJECT_ROOT = Path(__file__).resolve().parents[4]
CHECKOUT = Path("/private/tmp/lineage-estate/spring-petclinic-microservices")
ORIGIN = "https://github.com/spring-petclinic/spring-petclinic-microservices"
REPOSITORY = "spring-petclinic-microservices"
SECRET = "petclinic-runtime-acceptance-secret"

ANALYZER_PACK = "java-spring-data-jpa-v1"
RULESET = "spring-data-rules-v1"
SOURCE_KIND = "git-checkout"
FRAMEWORK = "spring-data-jpa"
SCHEMA_PROFILE = "mysql"
ENVIRONMENT = "staging"
SYSTEM = "petclinic"

pytestmark = pytest.mark.skipif(
    not CHECKOUT.is_dir() or java_home_or_none() is None,
    reason="petclinic checkout or JVM not present",
)


def _revision() -> str:
    result = subprocess.run(
        ["git", "-C", str(CHECKOUT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result.stdout.strip() or "0" * 40


def _snapshot(revision: str) -> RepositorySnapshot:
    paths = tuple(
        sorted(
            item.relative_to(CHECKOUT).as_posix()
            for item in CHECKOUT.rglob("*")
            if item.is_file()
            and ".git/" not in item.relative_to(CHECKOUT).as_posix()
            and "/target/" not in f"/{item.relative_to(CHECKOUT).as_posix()}"
        )
    )

    def read_source(relative_path: str) -> bytes:
        return (CHECKOUT / relative_path).read_bytes()

    return RepositorySnapshot(
        descriptor=RepositoryCheckoutDescriptor(
            origin=ORIGIN,
            repository=REPOSITORY,
            revision=revision,
            checkout_root=CHECKOUT,
            environment=ENVIRONMENT,
            platform=SCHEMA_PROFILE,
            system=SYSTEM,
            analyzer_pack=ANALYZER_PACK,
            ruleset=RULESET,
        ),
        paths=paths,
        # A whole-repository scope digest only has to be a valid, exact sha256 token;
        # `RepositorySnapshot` does not cross-check it against content (same convention
        # as `scripts/measure_real_petclinic.py` and `scripts/verify_petclinic_runtime_confidence.py`).
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


def _is_element_scoped_dataset_urn(value: str) -> bool:
    if "#" not in value:
        return False
    try:
        return LineageUrn.parse(value).element is not None
    except ValueError:
        return False


def test_petclinic_collects_through_the_product_path_and_fails_closed_on_the_sca_element_gap() -> None:
    revision = _revision()
    snapshot = _snapshot(revision)
    descriptor = _descriptor(snapshot)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        settings = Settings(
            project_root=PROJECT_ROOT,
            data_directory=tmp_path,
            fixture_directory=PROJECT_ROOT / "fixtures",
            database_path=tmp_path / "lineage.db",
            object_directory=tmp_path / "objects",
            webhook_secret=SECRET,
        )
        services = build_services(settings)

        summary = services.repository_collection.collect(descriptor, runtime_execution=True)

        # The product path completes on the real checkout: no crash, real edges.
        assert summary["outcome"] == "ACCEPTED"
        assert summary["counts"]["edges"] > 0

        edges = [
            edge.as_dict()
            for edge in services.orchestration._consolidation._latest_edges()
        ]
        assert edges

        element_scoped = [e for e in edges if _is_element_scoped_dataset_urn(str(e["to"]))]
        high = [e for e in edges if e["band"] == "HIGH"]

        # Structural wall (see module docstring and task-6-report.md): every edge this
        # real checkout's repositories produce is dataset-scoped, so the Java runtime
        # stage is never invoked and no edge can reach HIGH without inventing an
        # element edge, which the task boundary forbids.
        assert element_scoped == []
        assert high == []
        assert summary["runtimeStatus"] == "NOT_PROVIDED"
        assert summary["runtimeReasons"] == ["execution-failed"]
        assert all(edge["band"] == "SINGLE" for edge in edges), [e["band"] for e in edges]

        # The regression this test guards at full scale: a Java READ edge's `to` is a
        # `service://repo/Type#method` endpoint URN. Its '#' must never be mistaken for
        # element scope and must never crash `_execute_runtime_session`.
        read_edges = [e for e in edges if str(e["to"]).startswith("service://")]
        assert read_edges
        assert all("#" in e["to"] for e in read_edges)
