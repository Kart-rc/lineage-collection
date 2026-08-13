"""Petclinic proof — the real, unmodified upstream Spring Petclinic checkout through the
*product* collection path: `repository_collection.collect(descriptor, runtime_execution=True)`.

This freezes the result of the Task 6 / Task 6b iteration loop (see
`.superpowers/sdd/2026-08-12-runtime-collection-product-path/task-6-report.md` and
`task-6b-report.md`), which is a documented structural finding, not the band-HIGH proof
the overall goal set out to reach:

Task 6 found that the `java-spring-data-jpa-v1` analyzer (`services/java_spring_sca.py`)
compiled every Spring Data repository call into a **dataset-scoped** edge only —
`service://repo/Type#method` on one end, the bare `urn:ldp:...:table` dataset URN on the
other — never a `urn:ldp:...:table#column` element-scoped URN, even though the residue-
guarded `spring.query-element` facts (real, proven per-column evidence) already existed
and were simply discarded before edge construction.

Task 6b wired those facts into the edges: `VisitRepository.findByPetId`/`findByPetIdIn`
(both a same-entity derived predicate on `visits.pet_id`) now additionally emit an
element-scoped edge, alongside the existing bare dataset edge. `PetRepository`'s two
`@Query` methods remain unwired by design (`findPetTypeById`/`findPetTypes` project onto
`PetType`/`types`, a different entity than `PetRepository`'s own declared `Pet`/`pets`; the
query-element fact for them still resolves against `Pet`'s fields, so its table never
matches the redirected candidate's `types` table -- an honest mismatch, not element-scoped,
per Task 6b's own boundary against ever scoping a candidate by a fact that does not
describe it).

Task 6c (see `task-6c-report.md`) widened `OrchestrationService._execute_runtime_session`'s
Java edge selection to accept an element-scoped `urn:ldp:` URN on EITHER end (not just
`to`), extended `java_runtime_stage._edge_parts`/`match_edges` for both orientations,
added the `endpoint` SDK payload form (`application/runtime_emission.py`, `services/
runtime.py:_parse_sdk`) so a service-anchored element edge can be observed at all, and
added the matching `merge_runtime_observation` endpoint branch (exact string equality on
the service side, catalog-resolved URN on the dataset side, never `LineageUrn.parse` on
the service URN). `VisitRepository.findByPetId(In)`'s element edge is now genuinely
selected and handed to the Java runtime stage -- but band HIGH still is not reached here,
because the stage's harness compiles every `.java` path in the whole multi-module
checkout (including unrelated services' Spring Boot entry points, which need dependencies
the harness's classpath does not have), so the real run fails with a `javac` error before
it ever gets to witness a field. Runtime execution therefore still fails closed with
`execution-failed`, and every consolidated edge still stays at band SINGLE -- for a
different, more advanced reason than before 6c. Narrowing the harness's compile scope to
entity/repository/injection-site files is Task 6d's job, not this one's.

This test now locks the Task 6c state: at least one element-scoped SCA edge is on the
consolidated graph (Task 6b), the selection/wiring genuinely reaches the Java runtime
stage for it (Task 6c), and band HIGH and the runtime status stay exactly what they were
before 6c -- because the harness compile-scope wall, not the selection wall, is what now
stands between this checkout and HIGH. Any future change that alters this state without
being a deliberate, reviewed step of 6d should fail this test.

One real bug *was* found and fixed by the Task 6 iteration, and is covered here too:
before the fix, `_execute_runtime_session` treated any `#` in an edge's `to` as proof of
element scope. A Java READ edge's `to` is `service://repo/Type#method` — the `#`
separates method from type, not dataset from column — so `LineageUrn.parse` raised and the
whole collection failed with `PIPELINE_FAILED` instead of failing closed.
`test_collection_runtime_acceptance.py` carries the minimal regression for that fix; this
test proves it holds at full whole-repository scale on the real checkout too.
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
from lineage_api.domain.urns import LineageUrn, is_element_scoped_dataset_urn


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


def _is_element_scoped_edge(edge: dict) -> bool:
    # Since Task 6b, a Java element edge's `.element` can land on EITHER end: READS
    # orientation puts it on `from` (the dataset reads into the service), WRITES puts it
    # on `to`.
    from_urns = edge["from"] if isinstance(edge["from"], (list, tuple)) else [edge["from"]]
    return is_element_scoped_dataset_urn(str(edge["to"])) or any(
        is_element_scoped_dataset_urn(str(urn)) for urn in from_urns
    )


def test_petclinic_collects_through_the_product_path_with_element_edges_but_still_no_runtime_corroboration() -> None:
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

        element_scoped = [e for e in edges if _is_element_scoped_edge(e)]
        high = [e for e in edges if e["band"] == "HIGH"]

        # Task 6b wired `spring.query-element` facts into the edges: at least
        # `VisitRepository.findByPetId(In)` -> `visits#pet_id` is now on the graph.
        assert element_scoped, "expected at least one element-scoped SCA edge (Task 6b)"
        assert any(
            LineageUrn.parse(
                str(edge["from"][0] if isinstance(edge["from"], (list, tuple)) else edge["from"])
            ).element
            == "pet_id"
            for edge in element_scoped
        )

        # Still a wall, but a different one now (see module docstring and
        # task-6c-report.md): the selection genuinely reaches the Java runtime stage for
        # this READS edge (element on `from`) since Task 6c, but the harness's own compile
        # scope (every `.java` path in the whole multi-module checkout) fails to build, so
        # the stage never gets to witness a field. No edge reaches HIGH yet. Narrowing the
        # harness's compile scope is Task 6d's job.
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
