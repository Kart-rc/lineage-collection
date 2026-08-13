"""apps/api/tests/application/test_java_runtime_stage.py"""
from lineage_api.application.java_runtime_stage import (
    element_observations,
    match_edges,
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
