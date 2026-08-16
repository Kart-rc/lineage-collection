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


def test_every_spring_data_base_interface_is_recognized_as_a_repository() -> None:
    # spring-petclinic's VetRepository extends `Repository<Vet, Integer>` -- the
    # deliberately minimal Spring Data marker -- and other real repositories extend
    # the Crud/ListCrud/PagingAndSorting bases. All of them are Spring Data
    # repositories the proxy seam can instrument, not just JpaRepository.
    template = (
        "package example;\n"
        "import org.springframework.data.repository.{base};\n"
        "interface {name} extends {base}<Vet, Integer> {{\n"
        "    java.util.List<Vet> findAll();\n"
        "}}\n"
    )
    sources = {
        f"src/{name}.java": template.format(base=base, name=name)
        for base, name in (
            ("Repository", "VetRepository"),
            ("CrudRepository", "CrudVetRepository"),
            ("ListCrudRepository", "ListCrudVetRepository"),
            ("PagingAndSortingRepository", "PagingVetRepository"),
        )
    }

    repositories = read_repositories(sources)

    assert {(r.type_name, r.entity_type) for r in repositories} == {
        ("VetRepository", "Vet"),
        ("CrudVetRepository", "Vet"),
        ("ListCrudVetRepository", "Vet"),
        ("PagingVetRepository", "Vet"),
    }


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
    assert "getDeclaredConstructors()" in generated
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
    # The recorder reports the physical @Column name (`pet_id`), not the Java field
    # name (`petId`): the physical column is what an SCA element edge names.
    assert "pet_id" in observations[0].fields


PETCLINIC_REST_SHAPE = {
    "src/model/Team.java": (
        "package example;\n"
        "import jakarta.persistence.Entity;\n"
        "import jakarta.persistence.Table;\n"
        "@Entity @Table(name=\"teams\")\n"
        "public class Team {}\n"
    ),
    "src/model/Owner.java": (
        "package example;\n"
        "import jakarta.persistence.Entity;\n"
        "import jakarta.persistence.JoinColumn;\n"
        "import jakarta.persistence.ManyToOne;\n"
        "import jakarta.persistence.Table;\n"
        "@Entity @Table(name=\"owners\")\n"
        "public class Owner {\n"
        "    private String lastName;\n"
        "    @ManyToOne @JoinColumn(name = \"team_id\") private Team team;\n"
        "}\n"
    ),
    "src/repository/OwnerRepository.java": (
        "package example;\n"
        "import java.util.Collection;\n"
        "public interface OwnerRepository {\n"
        "    Collection<Owner> findByLastName(String lastName);\n"
        "    void save(Owner owner);\n"
        "}\n"
    ),
    "src/springdatajpa/SpringDataOwnerRepository.java": (
        "package example;\n"
        "import org.springframework.data.repository.Repository;\n"
        "public interface SpringDataOwnerRepository extends OwnerRepository, "
        "Repository<Owner, Integer> {\n"
        "}\n"
    ),
    "src/service/ClinicServiceImpl.java": (
        "package example;\n"
        "import java.util.Collection;\n"
        "class ClinicServiceImpl {\n"
        "    private final OwnerRepository ownerRepository;\n"
        "    ClinicServiceImpl(OwnerRepository ownerRepository) {\n"
        "        this.ownerRepository = ownerRepository;\n"
        "    }\n"
        "    public Collection<Owner> findOwnerByLastName(String lastName) {\n"
        "        return ownerRepository.findByLastName(lastName);\n"
        "    }\n"
        "}\n"
    ),
}


def test_a_spring_data_base_anywhere_in_the_extends_clause_is_recognized() -> None:
    # spring-petclinic-rest declares `SpringDataOwnerRepository extends
    # OwnerRepository, Repository<Owner, Integer>` -- the Spring Data base is not
    # first in the clause, and the project's own abstract interface is remembered as
    # a parent so injection sites typed against it can still be instrumented.
    repositories = read_repositories(PETCLINIC_REST_SHAPE)

    assert [(r.type_name, r.entity_type) for r in repositories] == [
        ("SpringDataOwnerRepository", "Owner")
    ]
    assert repositories[0].parents == ("OwnerRepository",)


def test_an_injection_site_typed_by_the_parent_interface_is_instrumented() -> None:
    # ClinicServiceImpl injects `OwnerRepository` -- the abstract project interface
    # the Spring Data repository extends -- never the Spring Data type itself.
    repositories = read_repositories(PETCLINIC_REST_SHAPE)

    sites = read_injection_sites(PETCLINIC_REST_SHAPE, repositories)

    assert [site.type_name for site in sites] == ["ClinicServiceImpl"]
    assert sites[0].repository_fields == (("ownerRepository", "OwnerRepository"),)


@pytest.mark.skipif(_javac() is None, reason="no JDK available; set LINEAGE_JAVA_HOME")
def test_the_parent_interface_injection_shape_compiles_and_witnesses(tmp_path: Path) -> None:
    javac = _javac()
    assert javac is not None
    java = str(Path(javac).with_name("java"))

    workspace = tmp_path / "src"
    for relative, text in {**PETCLINIC_REST_SHAPE, **generate_harness(PETCLINIC_REST_SHAPE)}.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    listing = tmp_path / "files.txt"
    listing.write_text(
        "\n".join(str(path) for path in sorted(workspace.rglob("*.java"))), encoding="utf-8"
    )
    compiled = subprocess.run(
        [javac, "-d", str(tmp_path / "classes"), f"@{listing}"],
        capture_output=True, text=True, timeout=180, check=False,
    )
    assert compiled.returncode == 0, compiled.stderr
    ran = subprocess.run(
        [java, "-cp", str(tmp_path / "classes"), "harness.GeneratedRuntimeTest"],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert ran.returncode == 0, ran.stderr

    observations = parse_observations(ran.stdout)

    assert {(o.method, o.table, o.operation) for o in observations} >= {
        ("findByLastName", "owners", "READ"),
    }
    owner_read = next(o for o in observations if o.method == "findByLastName")
    # A to-one association's physical @JoinColumn name is the witnessable element --
    # SCA resolves `findVisitsByPetId` to `pet_id`, never to the Java field `pet`.
    assert "team_id" in owner_read.fields
    assert "lastName" in owner_read.fields


def test_stub_source_map_declares_every_stub_exactly_once() -> None:
    """A duplicated dict key in the stub map silently shadows the earlier (often
    richer) definition -- jhipster-sample-app lost `Cacheable#cacheNames` exactly
    this way. The literal must declare each stub path once."""
    import collections
    import inspect
    import re

    import lineage_api.services.java_runtime_verification as module

    source = inspect.getsource(module)
    keys = re.findall(r'"([\w/]+\.java)":', source)
    duplicated = [key for key, count in collections.Counter(keys).items() if count > 1]
    assert duplicated == [], f"stub paths declared more than once: {duplicated}"


@pytest.mark.skipif(_javac() is None, reason="no JDK available; set LINEAGE_JAVA_HOME")
def test_a_jhipster_shaped_resource_compiles_and_runs(tmp_path: Path) -> None:
    """The jhipster-sample-app shape: `@Column(precision, scale)`, an inline
    fully-qualified `@org.springframework.data.annotation.Transient`, a repository
    with `@Cacheable(cacheNames, unless)`, a resource using `@PatchMapping`,
    `existsById`, paged `findAll(Pageable)` behind an inline
    `@org.springdoc.core.annotations.ParameterObject`, and
    `HttpStatus.BAD_REQUEST.value()`. Every one of these failed the harness
    compile before the stub surface grew to cover them."""
    javac = _javac()
    assert javac is not None
    java = str(Path(javac).with_name("java"))

    sources = {
        "bank/domain/BankAccount.java": (
            "package bank.domain;\n"
            "import jakarta.persistence.*;\n"
            "@Entity @Table(name = \"bank_account\")\n"
            "public class BankAccount {\n"
            "    @Id @GeneratedValue(strategy = GenerationType.IDENTITY) private Long id;\n"
            "    @Column(name = \"balance\", precision = 21, scale = 2, nullable = false)\n"
            "    private java.math.BigDecimal balance;\n"
            "    @org.springframework.data.annotation.Transient\n"
            "    private boolean audited;\n"
            "    public Long getId() { return id; }\n"
            "}\n"
        ),
        "bank/repository/BankAccountRepository.java": (
            "package bank.repository;\n"
            "import bank.domain.BankAccount;\n"
            "import org.springframework.cache.annotation.Cacheable;\n"
            "import org.springframework.data.jpa.repository.JpaRepository;\n"
            "public interface BankAccountRepository extends JpaRepository<BankAccount, Long> {\n"
            "    @Cacheable(cacheNames = \"accounts\", unless = \"#result == null\")\n"
            "    java.util.Optional<BankAccount> findOneById(Long id);\n"
            "}\n"
        ),
        "bank/web/BankAccountResource.java": (
            "package bank.web;\n"
            "import bank.domain.BankAccount;\n"
            "import bank.repository.BankAccountRepository;\n"
            "import org.springframework.data.domain.Page;\n"
            "import org.springframework.data.domain.Pageable;\n"
            "import org.springframework.http.HttpStatus;\n"
            "import org.springframework.web.bind.annotation.GetMapping;\n"
            "import org.springframework.web.bind.annotation.PatchMapping;\n"
            "import org.springframework.web.bind.annotation.RestController;\n"
            "@RestController\n"
            "class BankAccountResource {\n"
            "    private final BankAccountRepository bankAccountRepository;\n"
            "    BankAccountResource(BankAccountRepository bankAccountRepository) {\n"
            "        this.bankAccountRepository = bankAccountRepository;\n"
            "    }\n"
            "    @GetMapping(\"/api/bank-accounts\")\n"
            "    public java.util.List<BankAccount> all() {\n"
            "        return bankAccountRepository.findAll();\n"
            "    }\n"
            "    @GetMapping(\"/api/bank-accounts/paged\")\n"
            "    public int paged(@org.springdoc.core.annotations.ParameterObject Pageable pageable) {\n"
            "        Page<BankAccount> page = bankAccountRepository.findAll(pageable);\n"
            "        return page == null ? HttpStatus.BAD_REQUEST.value() : page.getTotalPages();\n"
            "    }\n"
            "    @PatchMapping(value = \"/{id}\", consumes = { \"application/json\", \"application/merge-patch+json\" })\n"
            "    public boolean exists(Long id) {\n"
            "        return bankAccountRepository.existsById(id);\n"
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
    assert any(
        observation.table == "bank_account" and observation.operation == "READ"
        for observation in observations
    ), f"expected a bank_account read observation, got {observations!r}"


@pytest.mark.skipif(_javac() is None, reason="no JDK available; set LINEAGE_JAVA_HOME")
def test_an_injection_site_with_non_repository_constructor_params_still_records(
    tmp_path: Path,
) -> None:
    """jhipster's UserService shape: the constructor mixes repositories with
    collaborators the harness does not provide (a PasswordEncoder, a CacheManager).
    The generated test must construct the site anyway — proxies for repository
    parameters, null for the rest — so repository calls still record."""
    javac = _javac()
    assert javac is not None
    java = str(Path(javac).with_name("java"))

    sources = {
        "acct/domain/Account.java": (
            "package acct;\n"
            "import jakarta.persistence.*;\n"
            "@Entity @Table(name = \"accounts\")\n"
            "public class Account {\n"
            "    @Id @GeneratedValue(strategy = GenerationType.IDENTITY) private Long id;\n"
            "    @Column(name = \"login\") private String login;\n"
            "}\n"
        ),
        "acct/repository/AccountRepository.java": (
            "package acct;\n"
            "import org.springframework.data.jpa.repository.JpaRepository;\n"
            "public interface AccountRepository extends JpaRepository<Account, Long> {\n"
            "    java.util.Optional<Account> findOneByLogin(String login);\n"
            "}\n"
        ),
        "acct/service/AccountService.java": (
            "package acct;\n"
            "import org.springframework.cache.CacheManager;\n"
            "import org.springframework.security.crypto.password.PasswordEncoder;\n"
            "import org.springframework.stereotype.Service;\n"
            "@Service\n"
            "public class AccountService {\n"
            "    private final AccountRepository accountRepository;\n"
            "    private final PasswordEncoder passwordEncoder;\n"
            "    private final CacheManager cacheManager;\n"
            "    public AccountService(AccountRepository accountRepository, PasswordEncoder passwordEncoder, CacheManager cacheManager) {\n"
            "        this.accountRepository = accountRepository;\n"
            "        this.passwordEncoder = passwordEncoder;\n"
            "        this.cacheManager = cacheManager;\n"
            "    }\n"
            "    public java.util.Optional<Account> lookup(String login) {\n"
            "        return accountRepository.findOneByLogin(login);\n"
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
    assert any(
        observation.table == "accounts" and observation.operation == "READ"
        for observation in observations
    ), f"expected an accounts read via the mixed-constructor site, got {observations!r}"
