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
