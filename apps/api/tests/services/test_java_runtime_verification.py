from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from lineage_api.services.java_runtime_verification import (
    JavaHarnessError,
    JavaObservation,
    classify_operation,
    generate_harness,
    parse_observations,
    qualified_type_names,
    read_entities,
    read_injection_sites,
    read_repositories,
    verify_java_lineage,
)


ROOT = Path(__file__).resolve().parents[4]
CORPUS = ROOT / "fixtures" / "repositories" / "java-spring-corpus" / "src" / "main" / "java"


def _corpus() -> dict[str, str]:
    return {
        str(path.relative_to(CORPUS)): path.read_text(encoding="utf-8")
        for path in sorted(CORPUS.rglob("*.java"))
    }


def _works(javac: str) -> bool:
    """macOS ships a stub /usr/bin/javac with no JDK behind it, so existing is not enough."""
    try:
        probe = subprocess.run(
            [javac, "-version"], capture_output=True, timeout=60, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def _javac() -> str | None:
    """A JDK is optional: the generation and verification tests never need one."""
    configured = os.environ.get("LINEAGE_JAVA_HOME")
    if configured:
        candidate = Path(configured) / "bin" / "javac"
        if candidate.exists() and _works(str(candidate)):
            return str(candidate)
    found = shutil.which("javac")
    return found if found and _works(found) else None


# --- reading the analyzed sources -----------------------------------------------------


def test_entities_repositories_and_injection_sites_are_read_from_the_corpus() -> None:
    sources = _corpus()

    entities = read_entities(sources)
    repositories = read_repositories(sources)
    sites = read_injection_sites(sources, repositories)

    assert [(e.type_name, e.table) for e in entities] == [("Owner", "owners")]
    assert [(r.type_name, r.entity_type) for r in repositories] == [
        ("OwnerRepository", "Owner")
    ]
    assert [s.type_name for s in sites] == ["OwnerController"]
    assert sites[0].repository_fields == (("owners", "OwnerRepository"),)
    assert set(sites[0].methods) == {"find", "save"}


def test_a_commented_out_entity_never_registers_a_table() -> None:
    # The corpus deliberately contains `// @Entity class FakeOwner {}` and a string
    # literal mentioning a repository, to catch analyzers that match on raw text.
    entities = read_entities(_corpus())

    assert [entity.type_name for entity in entities] == ["Owner"]


@pytest.mark.parametrize(
    ("method", "operation"),
    [
        ("findById", "READ"),
        ("getOne", "READ"),
        ("countByLastName", "READ"),
        ("save", "WRITE"),
        ("deleteById", "WRITE"),
        ("flush", "WRITE"),
        ("mysteriousCall", "UNKNOWN"),
    ],
)
def test_operations_are_classified_or_left_unknown(method: str, operation: str) -> None:
    assert classify_operation(method) == operation


def test_generation_fails_closed_without_an_injected_repository() -> None:
    with pytest.raises(JavaHarnessError):
        generate_harness({"A.java": "package example;\npublic class A {}\n"})


# --- harness generation ---------------------------------------------------------------


def test_harness_emits_stubs_recorder_and_a_generated_test() -> None:
    harness = generate_harness(_corpus())

    assert "harness/Recorder.java" in harness
    assert "harness/GeneratedRuntimeTest.java" in harness
    # Stubs let the production sources compile with nothing on the classpath.
    assert "jakarta/persistence/Table.java" in harness
    assert "org/springframework/data/jpa/repository/JpaRepository.java" in harness

    generated = harness["harness/GeneratedRuntimeTest.java"]
    # Construction is fully reflective (Class.forName + setAccessible), not a
    # compile-time `new OwnerController(...)`: a real injection site is routinely
    # package-private, which a direct reference from the harness's own package could
    # never construct.
    assert 'Class.forName("example.OwnerController")' in generated
    assert 'Class.forName("example.OwnerRepository")' in generated
    assert 'Class.forName("example.Owner")' in generated
    assert "getDeclaredConstructor(" in generated
    assert ".setAccessible(true)" in generated
    assert "Recorder.proxy(repoClass" in generated
    for method in ("find", "save"):
        assert f'invokeQuiet(site0, "{method}")' in generated


# --- verification ---------------------------------------------------------------------


def _observation(method: str, table: str = "owners") -> JavaObservation:
    return JavaObservation(
        repository_type="OwnerRepository",
        method=method,
        table=table,
        operation=classify_operation(method),
    )


def test_parsing_rejects_a_malformed_observation_line() -> None:
    with pytest.raises(JavaHarnessError):
        parse_observations("OBSERVED\tonly\ttwo\n")


def test_matching_access_is_corroborated() -> None:
    verification = verify_java_lineage(
        [("owners", "READ"), ("owners", "WRITE")],
        [_observation("findById"), _observation("save")],
    )

    assert verification.verdict == "CORROBORATED"
    assert verification.static_only == ()
    assert verification.runtime_only == ()


def test_an_unobserved_static_claim_is_reported() -> None:
    verification = verify_java_lineage(
        [("owners", "READ"), ("visits", "WRITE")], [_observation("findById")]
    )

    assert verification.verdict == "PARTIALLY_CORROBORATED"
    assert verification.static_only == (("visits", "WRITE"),)


def test_runtime_never_invents_an_edge() -> None:
    verification = verify_java_lineage([], [_observation("save", table="visits")])

    assert verification.corroborated == ()
    assert [o.table for o in verification.runtime_only] == ["visits"]
    assert verification.verdict == "NOT_PROVIDED"


def test_an_unknown_operation_can_never_corroborate() -> None:
    verification = verify_java_lineage(
        [("owners", "READ")], [_observation("mysteriousCall")]
    )

    assert verification.corroborated == ()
    assert verification.static_only == (("owners", "READ"),)


# --- Task 6d: fully-qualified, reflective harness construction ------------------------


def test_qualified_type_names_reads_each_files_own_package_declaration() -> None:
    sources = {
        "visits/model/Visit.java": "package visits.model;\npublic class Visit {}\n",
        "visits/web/VisitResource.java": "package visits.web;\nclass VisitResource {}\n",
        "NoPackage.java": "public class NoPackage {}\n",
    }

    qualified = qualified_type_names(sources)

    assert qualified["Visit"] == "visits.model.Visit"
    assert qualified["VisitResource"] == "visits.web.VisitResource"
    assert qualified["NoPackage"] == "NoPackage"


def test_generated_harness_qualifies_every_reference_instead_of_a_fixed_package() -> None:
    # A real multi-module checkout scatters entities, repositories, and injection
    # sites across many packages -- never a single fixed harness package -- so the
    # generated harness must load and construct each type by its own real package.
    sources = {
        "visits/model/Visit.java": (
            "package visits.model;\n"
            "import jakarta.persistence.*;\n"
            "@Entity @Table(name = \"visits\")\n"
            "public class Visit {}\n"
        ),
        "visits/model/VisitRepository.java": (
            "package visits.model;\n"
            "import org.springframework.data.jpa.repository.JpaRepository;\n"
            "public interface VisitRepository extends JpaRepository<Visit, Integer> {}\n"
        ),
        "visits/web/VisitResource.java": (
            "package visits.web;\n"
            "import visits.model.VisitRepository;\n"
            "class VisitResource {\n"
            "    private final VisitRepository visitRepository;\n"
            "    VisitResource(VisitRepository visitRepository) {\n"
            "        this.visitRepository = visitRepository;\n"
            "    }\n"
            "    public java.util.List read() { return visitRepository.findAll(); }\n"
            "}\n"
        ),
    }

    generated = generate_harness(sources)["harness/GeneratedRuntimeTest.java"]

    assert "import example.*;" not in generated
    assert 'Class.forName("visits.model.VisitRepository")' in generated
    assert 'Class.forName("visits.model.Visit")' in generated
    assert 'Class.forName("visits.web.VisitResource")' in generated
    assert ".setAccessible(true)" in generated


# --- the real thing: compile and run on a JVM -----------------------------------------


@pytest.mark.skipif(_javac() is None, reason="no JDK available; set LINEAGE_JAVA_HOME")
def test_generated_harness_compiles_and_runs_on_a_real_jvm(tmp_path: Path) -> None:
    javac = _javac()
    assert javac is not None
    java = str(Path(javac).with_name("java"))

    sources = _corpus()
    workspace = tmp_path / "src"
    # Production sources are written byte-for-byte as committed; only harness files are
    # generated alongside them.
    for relative, text in sources.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for relative, text in generate_harness(sources).items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    listing = tmp_path / "files.txt"
    listing.write_text(
        "\n".join(str(path) for path in sorted(workspace.rglob("*.java"))), encoding="utf-8"
    )
    compiled = subprocess.run(
        [javac, "-d", str(tmp_path / "classes"), f"@{listing}"],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stderr

    ran = subprocess.run(
        [java, "-cp", str(tmp_path / "classes"), "harness.GeneratedRuntimeTest"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr

    observations = parse_observations(ran.stdout)
    assert {(o.method, o.table, o.operation) for o in observations} == {
        ("findById", "owners", "READ"),
        ("save", "owners", "WRITE"),
    }

    verification = verify_java_lineage(
        [("owners", "READ"), ("owners", "WRITE")], observations
    )
    assert verification.verdict == "CORROBORATED"


@pytest.mark.skipif(_javac() is None, reason="no JDK available; set LINEAGE_JAVA_HOME")
def test_a_package_private_injection_site_across_packages_compiles_and_runs(
    tmp_path: Path,
) -> None:
    """The real petclinic shape: an `@RestController` injection site that is
    package-private (no `public` on the class), in a different package than the
    entity/repository it injects, using the Spring MVC / Bean Validation / JPA-column
    / logging annotations `generate_stub_sources` grew for Task 6d. Before Task 6d's
    reflective construction, a compile-time `new VisitResource(...)` from the
    harness's own package would fail with "VisitResource is not public"; before its
    extended stubs, every one of these imports would fail to resolve.
    """
    javac = _javac()
    assert javac is not None
    java = str(Path(javac).with_name("java"))

    sources = {
        "visits/model/Visit.java": (
            "package visits.model;\n"
            "import jakarta.persistence.*;\n"
            "@Entity @Table(name = \"visits\")\n"
            "public class Visit {\n"
            "    @Id @GeneratedValue(strategy = GenerationType.IDENTITY) private Integer id;\n"
            "    @Column(name = \"pet_id\") private int petId;\n"
            "}\n"
        ),
        "visits/model/VisitRepository.java": (
            "package visits.model;\n"
            "import org.springframework.data.jpa.repository.JpaRepository;\n"
            "public interface VisitRepository extends JpaRepository<Visit, Integer> {\n"
            "    java.util.List<Visit> findByPetId(int petId);\n"
            "}\n"
        ),
        "visits/web/VisitResource.java": (
            "package visits.web;\n"
            "import jakarta.validation.constraints.Min;\n"
            "import org.slf4j.Logger;\n"
            "import org.slf4j.LoggerFactory;\n"
            "import org.springframework.web.bind.annotation.GetMapping;\n"
            "import org.springframework.web.bind.annotation.PathVariable;\n"
            "import org.springframework.web.bind.annotation.RestController;\n"
            "import visits.model.Visit;\n"
            "import visits.model.VisitRepository;\n"
            "@RestController\n"
            "class VisitResource {\n"
            "    private static final Logger log = LoggerFactory.getLogger(VisitResource.class);\n"
            "    private final VisitRepository visitRepository;\n"
            "    VisitResource(VisitRepository visitRepository) {\n"
            "        this.visitRepository = visitRepository;\n"
            "    }\n"
            "    @GetMapping(\"owners/*/pets/{petId}/visits\")\n"
            "    public java.util.List<Visit> read(@PathVariable(\"petId\") @Min(1) int petId) {\n"
            "        log.info(\"reading {}\", petId);\n"
            "        return visitRepository.findByPetId(petId);\n"
            "    }\n"
            "}\n"
        ),
    }
    workspace = tmp_path / "src"
    for relative, text in {**sources, **generate_harness(sources)}.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    listing = tmp_path / "files.txt"
    listing.write_text(
        "\n".join(str(path) for path in sorted(workspace.rglob("*.java"))), encoding="utf-8"
    )
    compiled = subprocess.run(
        [javac, "-d", str(tmp_path / "classes"), f"@{listing}"],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stderr

    ran = subprocess.run(
        [java, "-cp", str(tmp_path / "classes"), "harness.GeneratedRuntimeTest"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr

    observations = parse_observations(ran.stdout)
    assert observations, "expected the proxy to record the invoked repository call"
    assert observations[0].table == "visits"
    assert observations[0].operation == "READ"
    assert "petId" in observations[0].fields
