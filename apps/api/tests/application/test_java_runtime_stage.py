"""apps/api/tests/application/test_java_runtime_stage.py"""
from lineage_api.application.java_runtime_stage import (
    element_observations,
    match_edges,
    select_relevant_java_sources,
)
from lineage_api.services.java_runtime_verification import JavaObservation

EDGE = {
    "from": ["urn:ldp:staging:postgres:petclinic:owners#first_name"],
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


def test_to_element_scope_takes_precedence_when_both_ends_are_element_scoped():
    # EDGE has both `to` and `from[0]` element-scoped (the synthetic WRITE fixture
    # above); `to` wins, matching the existing WRITE-orientation behaviour exactly.
    matched, unmatched = match_edges([EDGE], [OBS])
    assert matched == [EDGE] and unmatched == []


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
