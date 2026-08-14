"""apps/api/tests/application/test_java_runtime_stage.py"""
from lineage_api.application.java_runtime_stage import (
    element_observations,
    is_java_service_anchored_edge,
    match_edges,
    select_relevant_java_sources,
)
from lineage_api.services.java_runtime_verification import JavaObservation

# A Java WRITE edge always anchors its service endpoint on `from` and the
# dataset#column it writes on `to` -- see the READS/WRITES orientation cases below.
EDGE = {
    "from": ["service://petclinic-microservices/OwnerResource#save"],
    "to": "urn:ldp:staging:postgres:petclinic:owners#first_name",
    "edgeType": "WRITE",
}
OBS = JavaObservation(
    repository_type="OwnerRepository",
    method="save",
    table="owners",
    operation="WRITE",
    fields=("firstName", "lastName"),
)


def test_witnessed_table_operation_and_field_corroborates_the_edge():
    matched, unmatched = match_edges([EDGE], [OBS])
    assert matched == [EDGE] and unmatched == []


def test_unwitnessed_field_is_static_only():
    other = JavaObservation("OwnerRepository", "save", "owners", "WRITE", ("city",))
    matched, unmatched = match_edges([EDGE], [other])
    assert matched == [] and unmatched == [EDGE]


def test_unknown_operation_never_corroborates():
    unknown = JavaObservation("OwnerRepository", "audit", "owners", "UNKNOWN",
                              ("firstName",))
    matched, _ = match_edges([EDGE], [unknown])
    assert matched == []


def test_element_observations_take_stage_shape():
    observations = element_observations([EDGE], "2026-08-12T10:00:00Z")
    assert observations[0]["runtimeScope"] == "ELEMENT"
    assert observations[0]["sessionComplete"] is True
    assert observations[0]["from"] == EDGE["from"]


def test_dataset_scope_edge_is_ignored_not_unmatched():
    dataset_edge = {
        "from": ["urn:ldp:staging:postgres:petclinic:owners"],
        "to": "urn:ldp:staging:postgres:petclinic:owners",
        "edgeType": "WRITE",
    }
    matched, unmatched = match_edges([dataset_edge, EDGE], [OBS])
    assert matched == [EDGE]
    assert dataset_edge not in matched and dataset_edge not in unmatched


# --- Orientation cases (Task 6c): a service-anchored Java element edge can carry its
# `dataset#column` side on EITHER end -- READS puts it in `from` (the dataset side reads
# into the service), WRITES puts it in `to` (the service writes into the dataset). Both
# orientations must corroborate; only the `service://...#method` endpoint's own '#' must
# never be mistaken for element scope.

READS_EDGE = {
    "from": ["urn:ldp:staging:mysql:petclinic:visits#pet_id"],
    "to": "service://spring-petclinic-microservices/"
    "org.springframework.samples.petclinic.visits.web.VisitResource#read",
    "edgeType": "READS",
}
READS_OBS = JavaObservation(
    repository_type="VisitRepository",
    method="findByPetId",
    table="visits",
    operation="READ",
    fields=("petId",),
)


def test_reads_orientation_element_on_from_corroborates_the_edge():
    matched, unmatched = match_edges([READS_EDGE], [READS_OBS])
    assert matched == [READS_EDGE] and unmatched == []


def test_reads_orientation_service_endpoint_hash_is_never_mistaken_for_element_scope():
    # If the endpoint's '#' were mistaken for an element URN, `_edge_parts` would try
    # to `LineageUrn.parse` a `service://` string and either crash or silently match
    # the wrong table. Neither happens: the edge still corroborates through `from`.
    matched, _ = match_edges([READS_EDGE], [READS_OBS])
    assert matched == [READS_EDGE]


def test_reads_orientation_wrong_table_is_static_only():
    other = JavaObservation("OwnerRepository", "findByLastName", "owners", "READ", ("lastName",))
    matched, unmatched = match_edges([READS_EDGE], [other])
    assert matched == [] and unmatched == [READS_EDGE]


# --- Critical 2: a Python-shape DERIVES edge (both ends ldp element URNs, no
# `service://` anchor on either side) must never enter the Java seam. Nothing there
# witnesses a dataset-to-dataset transform, so admitting it risks a coincidental
# table-write observation "corroborating" a derivation that never ran.

BOTH_ENDS_LDP_EDGE = {
    "from": ["urn:ldp:staging:snowflake:payments:raw.transactions#amount"],
    "to": "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue",
    "edgeType": "DERIVES",
    "transform": "SUM(amount)",
}


def test_both_ends_ldp_edge_is_ignored_by_match_edges_not_unmatched():
    # A table-write observation on the very table/column the `to` side names must not
    # be able to "corroborate" this edge -- it is excluded entirely, exactly like the
    # dataset-scope case above.
    matched, unmatched = match_edges([BOTH_ENDS_LDP_EDGE, EDGE], [OBS])
    assert matched == [EDGE]
    assert BOTH_ENDS_LDP_EDGE not in matched and BOTH_ENDS_LDP_EDGE not in unmatched


def test_is_java_service_anchored_edge_requires_exactly_one_service_end():
    assert is_java_service_anchored_edge(EDGE) is True
    assert is_java_service_anchored_edge(READS_EDGE) is True
    assert is_java_service_anchored_edge(BOTH_ENDS_LDP_EDGE) is False


def test_is_java_service_anchored_edge_rejects_dataset_scope_edge():
    dataset_edge = {
        "from": ["urn:ldp:staging:postgres:petclinic:owners"],
        "to": "urn:ldp:staging:postgres:petclinic:owners",
        "edgeType": "WRITE",
    }
    assert is_java_service_anchored_edge(dataset_edge) is False


def test_is_java_service_anchored_edge_rejects_multi_from():
    multi_from_edge = {
        "from": [
            "service://petclinic-microservices/OwnerResource#save",
            "service://petclinic-microservices/OtherResource#save",
        ],
        "to": "urn:ldp:staging:postgres:petclinic:owners#first_name",
        "edgeType": "WRITE",
    }
    assert is_java_service_anchored_edge(multi_from_edge) is False


# --- Task 6d: compile-scope narrowing --------------------------------------------------
#
# `select_relevant_java_sources` is the fix for the wall Task 6d closed: a whole
# multi-module checkout's `.java` paths handed straight to `generate_harness` made the
# harness's single `javac` invocation compile every module, so one unrelated module's
# missing dependency (another service's Spring Boot entry point) failed the whole
# build -- starving even a module the edge under test could genuinely witness.

VISIT_ENTITY = (
    "package visits.model;\n"
    "import jakarta.persistence.*;\n"
    "@Entity @Table(name = \"visits\")\n"
    "public class Visit { @Column(name = \"pet_id\") private int petId; }\n"
)
VISIT_REPOSITORY = (
    "package visits.model;\n"
    "import org.springframework.data.jpa.repository.JpaRepository;\n"
    "public interface VisitRepository extends JpaRepository<Visit, Integer> {\n"
    "    java.util.List<Visit> findByPetId(int petId);\n"
    "}\n"
)
VISIT_RESOURCE = (
    "package visits.web;\n"
    "import visits.model.VisitRepository;\n"
    "class VisitResource {\n"
    "    private final VisitRepository visitRepository;\n"
    "    VisitResource(VisitRepository visitRepository) { this.visitRepository = visitRepository; }\n"
    "    public java.util.List read(int petId) { return visitRepository.findByPetId(petId); }\n"
    "}\n"
)
VISIT_APPLICATION = (
    "package visits;\n"
    "public class VisitsServiceApplication { public static void main(String[] a) {} }\n"
)
OWNER_ENTITY = (
    "package owners.model;\n"
    "import jakarta.persistence.*;\n"
    "@Entity @Table(name = \"owners\")\n"
    "public class Owner { @Column(name = \"last_name\") private String lastName; }\n"
)
OWNER_REPOSITORY = (
    "package owners.model;\n"
    "import org.springframework.data.jpa.repository.JpaRepository;\n"
    "public interface OwnerRepository extends JpaRepository<Owner, Integer> {\n"
    "    java.util.List<Owner> findByLastName(String lastName);\n"
    "}\n"
)
OWNER_RESOURCE = (
    "package owners.web;\n"
    "import owners.model.OwnerRepository;\n"
    "class OwnerResource {\n"
    "    private final OwnerRepository ownerRepository;\n"
    "    OwnerResource(OwnerRepository ownerRepository) { this.ownerRepository = ownerRepository; }\n"
    "    public java.util.List findAll() { return ownerRepository.findAll(); }\n"
    "}\n"
)

MULTI_MODULE_SOURCES = {
    "spring-petclinic-visits-service/src/main/java/visits/model/Visit.java": VISIT_ENTITY,
    "spring-petclinic-visits-service/src/main/java/visits/model/VisitRepository.java": VISIT_REPOSITORY,
    "spring-petclinic-visits-service/src/main/java/visits/web/VisitResource.java": VISIT_RESOURCE,
    "spring-petclinic-visits-service/src/main/java/visits/VisitsServiceApplication.java": VISIT_APPLICATION,
    "spring-petclinic-customers-service/src/main/java/owners/model/Owner.java": OWNER_ENTITY,
    "spring-petclinic-customers-service/src/main/java/owners/model/OwnerRepository.java": OWNER_REPOSITORY,
    "spring-petclinic-customers-service/src/main/java/owners/web/OwnerResource.java": OWNER_RESOURCE,
}

VISIT_STATIC_EDGE = {
    "from": ["urn:ldp:staging:mysql:petclinic:visits#pet_id"],
    "to": "service://spring-petclinic-microservices/visits.web.VisitResource#read",
    "edgeType": "READS",
}


def test_narrowing_drops_non_entity_repository_injection_site_files():
    scoped = select_relevant_java_sources(MULTI_MODULE_SOURCES, [VISIT_STATIC_EDGE])
    # The Spring Boot entry point matches none of the three structural shapes and is
    # dropped, even though it lives in the same module as the files that are kept.
    assert (
        "spring-petclinic-visits-service/src/main/java/visits/VisitsServiceApplication.java"
        not in scoped
    )


def test_narrowing_keeps_the_entity_repository_and_injection_site_for_the_targeted_edge():
    scoped = select_relevant_java_sources(MULTI_MODULE_SOURCES, [VISIT_STATIC_EDGE])
    assert set(scoped) == {
        "spring-petclinic-visits-service/src/main/java/visits/model/Visit.java",
        "spring-petclinic-visits-service/src/main/java/visits/model/VisitRepository.java",
        "spring-petclinic-visits-service/src/main/java/visits/web/VisitResource.java",
    }


def test_narrowing_scopes_out_an_unrelated_module_even_though_it_matches_structurally():
    # OwnerResource/OwnerRepository/Owner structurally match too, but the edge under
    # test names a type only the visits module declares -- an unrelated module's own
    # compile problems must never be able to sink an edge this seam can witness.
    scoped = select_relevant_java_sources(MULTI_MODULE_SOURCES, [VISIT_STATIC_EDGE])
    assert not any(path.startswith("spring-petclinic-customers-service/") for path in scoped)


BASE_ENTITY = (
    "package petclinic.model;\n"
    "import jakarta.persistence.*;\n"
    "@MappedSuperclass\n"
    "public class BaseEntity { @Id private Integer id; }\n"
)
PERSON = (
    "package petclinic.model;\n"
    "public class Person extends BaseEntity { private String firstName; }\n"
)
PETCLINIC_OWNER = (
    "package petclinic.owner;\n"
    "import jakarta.persistence.*;\n"
    "import petclinic.model.Person;\n"
    "@Entity @Table(name = \"owners\")\n"
    "public class Owner extends Person { @Column(name = \"last_name\") private String lastName; }\n"
)
PETCLINIC_OWNER_REPOSITORY = (
    "package petclinic.owner;\n"
    "import org.springframework.data.jpa.repository.JpaRepository;\n"
    "public interface OwnerRepository extends JpaRepository<Owner, Integer> {\n"
    "    java.util.List<Owner> findByLastName(String lastName);\n"
    "}\n"
)
PETCLINIC_OWNER_CONTROLLER = (
    "package petclinic.owner;\n"
    "class OwnerController {\n"
    "    private final OwnerRepository owners;\n"
    "    OwnerController(OwnerRepository owners) { this.owners = owners; }\n"
    "    public java.util.List findOwner(String lastName) { return owners.findByLastName(lastName); }\n"
    "}\n"
)
PETCLINIC_APPLICATION = (
    "package petclinic;\n"
    "public class PetClinicApplication { public static void main(String[] a) {} }\n"
)

SINGLE_MODULE_SOURCES = {
    "src/main/java/petclinic/model/BaseEntity.java": BASE_ENTITY,
    "src/main/java/petclinic/model/Person.java": PERSON,
    "src/main/java/petclinic/owner/Owner.java": PETCLINIC_OWNER,
    "src/main/java/petclinic/owner/OwnerRepository.java": PETCLINIC_OWNER_REPOSITORY,
    "src/main/java/petclinic/owner/OwnerController.java": PETCLINIC_OWNER_CONTROLLER,
    "src/main/java/petclinic/PetClinicApplication.java": PETCLINIC_APPLICATION,
}

OWNER_STATIC_EDGE = {
    "from": ["urn:ldp:staging:postgres:petclinic:owners#last_name"],
    "to": "service://spring-petclinic/petclinic.owner.OwnerController#findOwner",
    "edgeType": "READS",
}


def test_narrowing_keeps_transitive_project_types_the_kept_files_reference():
    # spring-petclinic's Owner extends Person extends BaseEntity (@MappedSuperclass in
    # another package; Person reaches BaseEntity without an import, same package).
    # Neither base matches the entity/repository/injection-site shapes, but the kept
    # files cannot compile without them -- narrowing must keep the reference closure.
    scoped = select_relevant_java_sources(SINGLE_MODULE_SOURCES, [OWNER_STATIC_EDGE])
    assert "src/main/java/petclinic/model/Person.java" in scoped
    assert "src/main/java/petclinic/model/BaseEntity.java" in scoped


def test_the_reference_closure_never_resurrects_unreferenced_files():
    # The closure keeps what the kept files need, not everything: the Spring Boot
    # entry point is referenced by nothing that was kept and stays dropped.
    scoped = select_relevant_java_sources(SINGLE_MODULE_SOURCES, [OWNER_STATIC_EDGE])
    assert "src/main/java/petclinic/PetClinicApplication.java" not in scoped


def test_narrowing_falls_back_to_every_module_when_no_edge_names_a_declared_type():
    # A static edge whose endpoint names a type this source set does not declare (e.g.
    # a differently-shaped fixture) must not silently narrow to nothing; the structural
    # filter still runs, just unscoped by module.
    unmatched_edge = {
        "from": ["urn:ldp:staging:mysql:petclinic:visits#pet_id"],
        "to": "service://spring-petclinic-microservices/nowhere.Missing#read",
        "edgeType": "READS",
    }
    scoped = select_relevant_java_sources(MULTI_MODULE_SOURCES, [unmatched_edge])
    assert any(path.startswith("spring-petclinic-customers-service/") for path in scoped)
    assert any(path.startswith("spring-petclinic-visits-service/") for path in scoped)
    assert not any(path.endswith("VisitsServiceApplication.java") for path in scoped)


# --- Important 4: `javac`/`java` must run under a minimal environment, not the full
# parent environment, while executing repo-derived code.


def test_javac_and_java_run_under_a_minimal_env_without_leaking_secrets(monkeypatch):
    import subprocess as subprocess_module

    import lineage_api.application.java_runtime_stage as java_runtime_stage

    monkeypatch.setenv("FAKE_SECRET", "shhh-do-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "also-should-not-leak")
    # A JAVA_HOME already sitting in the orchestrator's own environment must never be
    # the one that reaches the subprocess -- only the resolved `java_home` argument.
    monkeypatch.setenv("JAVA_HOME", "/should/not/leak/via/parent/env")

    captured_envs: list[dict[str, str]] = []

    def _fake_run(args, **kwargs):
        captured_envs.append(kwargs.get("env"))
        return subprocess_module.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(java_runtime_stage.subprocess, "run", _fake_run)

    sources = {
        "src/main/java/visits/model/Visit.java": VISIT_ENTITY,
        "src/main/java/visits/model/VisitRepository.java": VISIT_REPOSITORY,
        "src/main/java/visits/web/VisitResource.java": VISIT_RESOURCE,
    }
    java_runtime_stage.run_java_runtime_stage(
        sources=sources,
        static_edges=[VISIT_STATIC_EDGE],
        observed_at="2026-08-12T10:00:00Z",
        java_home="/fake/resolved/java-home",
    )

    assert len(captured_envs) == 2  # javac, then java
    for env in captured_envs:
        assert env is not None
        assert "FAKE_SECRET" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert env["JAVA_HOME"] == "/fake/resolved/java-home"
        assert "PATH" in env
        assert set(env) <= {"PATH", "JAVA_HOME", "HOME", "TMPDIR"}


# --- the real spring-petclinic shape: superclass hierarchies, to-many join columns,
# the `Repository` marker base, MVC controllers, and entity fields declared on
# @MappedSuperclass ancestors. This is what the harness must compile and witness to
# corroborate the main petclinic repository, not just the flat microservices modules.

import pytest as _pytest

from lineage_api.application.java_runtime_stage import (
    java_home_or_none as _java_home_or_none,
    run_java_runtime_stage as _run_java_runtime_stage,
)

PETCLINIC_SHAPE_SOURCES = {
    "src/main/java/petclinic/model/BaseEntity.java": (
        "package petclinic.model;\n"
        "import jakarta.persistence.GeneratedValue;\n"
        "import jakarta.persistence.GenerationType;\n"
        "import jakarta.persistence.Id;\n"
        "import jakarta.persistence.MappedSuperclass;\n"
        "@MappedSuperclass\n"
        "public class BaseEntity {\n"
        "    @Id @GeneratedValue(strategy = GenerationType.IDENTITY) private Integer id;\n"
        "    public Integer getId() { return id; }\n"
        "    public boolean isNew() { return this.id == null; }\n"
        "}\n"
    ),
    "src/main/java/petclinic/model/Person.java": (
        "package petclinic.model;\n"
        "import jakarta.persistence.Column;\n"
        "import jakarta.persistence.MappedSuperclass;\n"
        "import jakarta.validation.constraints.NotBlank;\n"
        "@MappedSuperclass\n"
        "public class Person extends BaseEntity {\n"
        "    @Column(name = \"first_name\") @NotBlank private String firstName;\n"
        "    @Column(name = \"last_name\") @NotBlank private String lastName;\n"
        "    public String getLastName() { return lastName; }\n"
        "}\n"
    ),
    "src/main/java/petclinic/model/NamedEntity.java": (
        "package petclinic.model;\n"
        "import jakarta.persistence.Column;\n"
        "import jakarta.persistence.MappedSuperclass;\n"
        "import jakarta.validation.constraints.NotBlank;\n"
        "import org.springframework.core.style.ToStringCreator;\n"
        "@MappedSuperclass\n"
        "public class NamedEntity extends BaseEntity {\n"
        "    @Column(name = \"name\") @NotBlank private String name;\n"
        "    @Override public String toString() {\n"
        "        return new ToStringCreator(this).append(\"name\", this.name).toString();\n"
        "    }\n"
        "}\n"
    ),
    "src/main/java/petclinic/owner/PetType.java": (
        "package petclinic.owner;\n"
        "import jakarta.persistence.Entity;\n"
        "import jakarta.persistence.Table;\n"
        "import petclinic.model.NamedEntity;\n"
        "@Entity @Table(name = \"types\")\n"
        "public class PetType extends NamedEntity {}\n"
    ),
    "src/main/java/petclinic/owner/Pet.java": (
        "package petclinic.owner;\n"
        "import jakarta.persistence.CascadeType;\n"
        "import jakarta.persistence.Column;\n"
        "import jakarta.persistence.Entity;\n"
        "import jakarta.persistence.FetchType;\n"
        "import jakarta.persistence.JoinColumn;\n"
        "import jakarta.persistence.ManyToOne;\n"
        "import jakarta.persistence.OneToMany;\n"
        "import jakarta.persistence.OrderBy;\n"
        "import jakarta.persistence.Table;\n"
        "import java.time.LocalDate;\n"
        "import java.util.LinkedHashSet;\n"
        "import java.util.Set;\n"
        "import org.springframework.format.annotation.DateTimeFormat;\n"
        "import petclinic.model.NamedEntity;\n"
        "import petclinic.visit.Visit;\n"
        "@Entity @Table(name = \"pets\")\n"
        "public class Pet extends NamedEntity {\n"
        "    @Column(name = \"birth_date\") @DateTimeFormat(pattern = \"yyyy-MM-dd\")\n"
        "    private LocalDate birthDate;\n"
        "    @ManyToOne @JoinColumn(name = \"type_id\") private PetType type;\n"
        "    @OneToMany(cascade = CascadeType.ALL, fetch = FetchType.EAGER)\n"
        "    @JoinColumn(name = \"pet_id\") @OrderBy(\"date ASC\")\n"
        "    private final Set<Visit> visits = new LinkedHashSet<>();\n"
        "}\n"
    ),
    "src/main/java/petclinic/visit/Visit.java": (
        "package petclinic.visit;\n"
        "import jakarta.persistence.Column;\n"
        "import jakarta.persistence.Entity;\n"
        "import jakarta.persistence.Table;\n"
        "import java.time.LocalDate;\n"
        "import org.springframework.format.annotation.DateTimeFormat;\n"
        "import petclinic.model.BaseEntity;\n"
        "@Entity @Table(name = \"visits\")\n"
        "public class Visit extends BaseEntity {\n"
        "    @Column(name = \"visit_date\") @DateTimeFormat(pattern = \"yyyy-MM-dd\")\n"
        "    private LocalDate date = LocalDate.now();\n"
        "}\n"
    ),
    "src/main/java/petclinic/owner/Owner.java": (
        "package petclinic.owner;\n"
        "import jakarta.persistence.CascadeType;\n"
        "import jakarta.persistence.Column;\n"
        "import jakarta.persistence.Entity;\n"
        "import jakarta.persistence.FetchType;\n"
        "import jakarta.persistence.JoinColumn;\n"
        "import jakarta.persistence.OneToMany;\n"
        "import jakarta.persistence.OrderBy;\n"
        "import jakarta.persistence.Table;\n"
        "import jakarta.validation.constraints.NotBlank;\n"
        "import jakarta.validation.constraints.Pattern;\n"
        "import java.util.ArrayList;\n"
        "import java.util.List;\n"
        "import org.springframework.core.style.ToStringCreator;\n"
        "import petclinic.model.Person;\n"
        "@Entity @Table(name = \"owners\")\n"
        "public class Owner extends Person {\n"
        "    @Column(name = \"city\") @NotBlank private String city;\n"
        "    @Column(name = \"telephone\") @NotBlank\n"
        "    @Pattern(regexp = \"\\\\d{10}\", message = \"{telephone.invalid}\")\n"
        "    private String telephone;\n"
        "    @OneToMany(cascade = CascadeType.ALL, fetch = FetchType.EAGER)\n"
        "    @JoinColumn(name = \"owner_id\") @OrderBy(\"name\")\n"
        "    private final List<Pet> pets = new ArrayList<>();\n"
        "    @Override public String toString() {\n"
        "        return new ToStringCreator(this).append(\"city\", this.city).toString();\n"
        "    }\n"
        "}\n"
    ),
    "src/main/java/petclinic/owner/OwnerRepository.java": (
        "package petclinic.owner;\n"
        "import java.util.Optional;\n"
        "import org.springframework.data.domain.Page;\n"
        "import org.springframework.data.domain.Pageable;\n"
        "import org.springframework.data.jpa.repository.JpaRepository;\n"
        "public interface OwnerRepository extends JpaRepository<Owner, Integer> {\n"
        "    Page<Owner> findByLastNameStartingWith(String lastName, Pageable pageable);\n"
        "}\n"
    ),
    "src/main/java/petclinic/owner/OwnerController.java": (
        "package petclinic.owner;\n"
        "import java.util.Optional;\n"
        "import org.springframework.data.domain.Page;\n"
        "import org.springframework.data.domain.PageRequest;\n"
        "import org.springframework.data.domain.Pageable;\n"
        "import org.springframework.stereotype.Controller;\n"
        "import org.springframework.ui.Model;\n"
        "import org.springframework.validation.BindingResult;\n"
        "import org.springframework.web.bind.WebDataBinder;\n"
        "import org.springframework.web.bind.annotation.GetMapping;\n"
        "import org.springframework.web.bind.annotation.InitBinder;\n"
        "import org.springframework.web.bind.annotation.ModelAttribute;\n"
        "import org.springframework.web.bind.annotation.PathVariable;\n"
        "import org.springframework.web.bind.annotation.RequestParam;\n"
        "import org.springframework.web.servlet.ModelAndView;\n"
        "import org.springframework.web.servlet.mvc.support.RedirectAttributes;\n"
        "import org.springframework.util.Assert;\n"
        "import org.springframework.util.StringUtils;\n"
        "@Controller\n"
        "class OwnerController {\n"
        "    private final OwnerRepository owners;\n"
        "    OwnerController(OwnerRepository owners) { this.owners = owners; }\n"
        "    @InitBinder\n"
        "    public void setAllowedFields(WebDataBinder dataBinder) {\n"
        "        dataBinder.setDisallowedFields(\"id\");\n"
        "    }\n"
        "    @GetMapping(\"/owners\")\n"
        "    public String findPaginated(@RequestParam(defaultValue = \"\") String lastName) {\n"
        "        Assert.notNull(owners, \"repository required\");\n"
        "        Pageable pageable = PageRequest.of(1, 5);\n"
        "        Page<Owner> results = owners.findByLastNameStartingWith(\n"
        "            StringUtils.hasText(lastName) ? lastName : \"\", pageable);\n"
        "        return results.isEmpty() ? \"notFound\" : \"ownersList\";\n"
        "    }\n"
        "    @GetMapping(\"/owners/{ownerId}\")\n"
        "    public ModelAndView showOwner(@PathVariable Integer ownerId) {\n"
        "        Optional<Owner> owner = owners.findById(ownerId == null ? 1 : ownerId);\n"
        "        ModelAndView mav = new ModelAndView(\"ownerDetails\");\n"
        "        owner.ifPresent(value -> mav.addObject(value));\n"
        "        return mav;\n"
        "    }\n"
        "    public String processForm(RedirectAttributes redirectAttributes) {\n"
        "        owners.saveAndFlush(new Owner());\n"
        "        redirectAttributes.addFlashAttribute(\"message\", \"saved\");\n"
        "        return \"redirect:/owners\";\n"
        "    }\n"
        "    public String addModel(Model model) {\n"
        "        model.addAttribute(\"owner\", new Owner());\n"
        "        return \"form\";\n"
        "    }\n"
        "    public String validate(BindingResult result) {\n"
        "        if (result.hasErrors()) { result.rejectValue(\"telephone\", \"invalid\"); }\n"
        "        return result.hasErrors() ? \"form\" : \"ok\";\n"
        "    }\n"
        "}\n"
    ),
    "src/main/java/petclinic/vet/Specialty.java": (
        "package petclinic.vet;\n"
        "import jakarta.persistence.Entity;\n"
        "import jakarta.persistence.Table;\n"
        "import petclinic.model.NamedEntity;\n"
        "@Entity @Table(name = \"specialties\")\n"
        "public class Specialty extends NamedEntity {}\n"
    ),
    "src/main/java/petclinic/vet/Vet.java": (
        "package petclinic.vet;\n"
        "import jakarta.persistence.Entity;\n"
        "import jakarta.persistence.FetchType;\n"
        "import jakarta.persistence.JoinColumn;\n"
        "import jakarta.persistence.JoinTable;\n"
        "import jakarta.persistence.ManyToMany;\n"
        "import jakarta.persistence.Table;\n"
        "import jakarta.xml.bind.annotation.XmlElement;\n"
        "import java.util.HashSet;\n"
        "import java.util.Set;\n"
        "import petclinic.model.Person;\n"
        "@Entity @Table(name = \"vets\")\n"
        "public class Vet extends Person {\n"
        "    @ManyToMany(fetch = FetchType.EAGER)\n"
        "    @JoinTable(name = \"vet_specialties\",\n"
        "        joinColumns = @JoinColumn(name = \"vet_id\"),\n"
        "        inverseJoinColumns = @JoinColumn(name = \"specialty_id\"))\n"
        "    private Set<Specialty> specialties = new HashSet<>();\n"
        "    @XmlElement public Set<Specialty> getSpecialties() { return specialties; }\n"
        "}\n"
    ),
    "src/main/java/petclinic/vet/VetRepository.java": (
        "package petclinic.vet;\n"
        "import java.util.Collection;\n"
        "import org.springframework.cache.annotation.Cacheable;\n"
        "import org.springframework.dao.DataAccessException;\n"
        "import org.springframework.data.domain.Page;\n"
        "import org.springframework.data.domain.Pageable;\n"
        "import org.springframework.data.repository.Repository;\n"
        "import org.springframework.transaction.annotation.Transactional;\n"
        "public interface VetRepository extends Repository<Vet, Integer> {\n"
        "    @Transactional(readOnly = true) @Cacheable(\"vets\")\n"
        "    Collection<Vet> findAll() throws DataAccessException;\n"
        "    @Transactional(readOnly = true) @Cacheable(\"vets\")\n"
        "    Page<Vet> findAll(Pageable pageable) throws DataAccessException;\n"
        "}\n"
    ),
    "src/main/java/petclinic/vet/VetController.java": (
        "package petclinic.vet;\n"
        "import org.springframework.stereotype.Controller;\n"
        "import org.springframework.web.bind.annotation.GetMapping;\n"
        "import org.springframework.web.bind.annotation.ResponseBody;\n"
        "@Controller\n"
        "class VetController {\n"
        "    private final VetRepository vetRepository;\n"
        "    VetController(VetRepository vetRepository) { this.vetRepository = vetRepository; }\n"
        "    @GetMapping(\"/vets\") @ResponseBody\n"
        "    public java.util.Collection<Vet> showResourcesVetList() {\n"
        "        return vetRepository.findAll();\n"
        "    }\n"
        "}\n"
    ),
}

PETCLINIC_SHAPE_EDGES = [
    {
        "from": ["urn:ldp:staging:postgres:petclinic:owners#last_name"],
        "to": "service://spring-petclinic/petclinic.owner.OwnerController#findPaginated",
        "edgeType": "READS",
    },
    {
        "from": ["urn:ldp:staging:postgres:petclinic:vets#last_name"],
        "to": "service://spring-petclinic/petclinic.vet.VetController#showResourcesVetList",
        "edgeType": "READS",
    },
]


@_pytest.mark.skipif(
    _java_home_or_none() is None, reason="no JDK available; set LINEAGE_JAVA_HOME"
)
def test_the_real_petclinic_shape_compiles_and_is_corroborated():
    # `owners#last_name` and `vets#last_name` are declared on the @MappedSuperclass
    # `Person`, not on the entities themselves -- witnessing them requires collecting
    # inherited fields, and compiling them requires the superclass closure plus the
    # full JPA/MVC/data-commons stub surface the real repository imports.
    stage = _run_java_runtime_stage(
        sources=PETCLINIC_SHAPE_SOURCES,
        static_edges=PETCLINIC_SHAPE_EDGES,
        observed_at="2026-08-14T00:00:00Z",
        java_home=_java_home_or_none(),
    )

    assert stage.verdict == "CORROBORATED"
    assert stage.corroborated == 2
    assert stage.static_only == 0
