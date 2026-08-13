"""apps/api/tests/application/test_runtime_emission.py"""
from lineage_api.application.runtime_emission import (
    sdk_payloads,
    session_scope,
    wire_dataset,
)

OBS = {
    "mechanism": "RUNTIME",
    "runtimeScope": "ELEMENT",
    "sessionComplete": True,
    "observedAt": "2026-08-12T10:00:00Z",
    "from": ["urn:ldp:staging:snowflake:payments:raw.transactions#amount"],
    "to": "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue",
    "edgeType": "DERIVES",
    "exact": False,
}


def test_wire_dataset_round_trips_platform_system_dataset():
    assert (
        wire_dataset("urn:ldp:staging:snowflake:payments:raw.transactions#amount")
        == "snowflake://payments/raw.transactions"
    )


def test_session_scope_is_sorted_unique_wire_identifiers():
    assert session_scope([OBS]) == (
        "snowflake://payments/analytics.daily_revenue",
        "snowflake://payments/raw.transactions",
    )


def test_sdk_payloads_conform_to_the_closed_sdk_schema():
    payloads = sdk_payloads([OBS], artifact_digest="a" * 40, run_id="run-1")
    assert payloads == [
        {
            "schemaVersion": "1.0.0",
            "observationId": "stage-run-1-0001",
            "sequence": 1,
            "artifactDigest": "a" * 40,
            "source": {
                "dataset": "snowflake://payments/raw.transactions",
                "field": "amount",
            },
            "target": {
                "dataset": "snowflake://payments/analytics.daily_revenue",
                "field": "gross_revenue",
            },
            "edgeType": "DERIVES",
            "transform": "",
            "observedAt": "2026-08-12T10:00:00Z",
        }
    ]


def test_multi_source_observation_produces_one_payload_per_source():
    multi = dict(OBS, **{"from": [OBS["from"][0],
        "urn:ldp:staging:snowflake:payments:raw.fx#rate"]})
    payloads = sdk_payloads([multi], artifact_digest="a" * 40, run_id="run-1")
    assert [p["source"]["field"] for p in payloads] == ["amount", "rate"]
    assert [p["sequence"] for p in payloads] == [1, 2]


# --- Task 6c: service-anchored element edges. A Java READS/WRITES edge anchors one end
# to a `service://repo/Type#method` endpoint rather than another dataset element; the
# SDK payload carries the element side as `source` and the service side as `endpoint`
# instead of `target`, and `session_scope` includes only the dataset side.

READS_OBS = {
    "mechanism": "RUNTIME",
    "runtimeScope": "ELEMENT",
    "sessionComplete": True,
    "observedAt": "2026-08-12T10:00:00Z",
    "from": ["urn:ldp:staging:mysql:petclinic:visits#pet_id"],
    "to": "service://spring-petclinic-microservices/"
    "org.springframework.samples.petclinic.visits.web.VisitResource#read",
    "edgeType": "READS",
    "exact": False,
}

WRITES_OBS = {
    "mechanism": "RUNTIME",
    "runtimeScope": "ELEMENT",
    "sessionComplete": True,
    "observedAt": "2026-08-12T10:00:00Z",
    "from": [
        "service://spring-petclinic-microservices/"
        "org.springframework.samples.petclinic.owners.web.OwnerResource#update"
    ],
    "to": "urn:ldp:staging:mysql:petclinic:owners#first_name",
    "edgeType": "WRITES",
    "exact": False,
}


def test_sdk_payloads_use_endpoint_form_for_a_reads_orientation_service_edge():
    payloads = sdk_payloads([READS_OBS], artifact_digest="a" * 40, run_id="run-1")
    assert payloads == [
        {
            "schemaVersion": "1.0.0",
            "observationId": "stage-run-1-0001",
            "sequence": 1,
            "artifactDigest": "a" * 40,
            "source": {
                "dataset": "mysql://petclinic/visits",
                "field": "pet_id",
            },
            "endpoint": {"service": READS_OBS["to"]},
            "edgeType": "READS",
            "transform": "",
            "observedAt": "2026-08-12T10:00:00Z",
        }
    ]


def test_sdk_payloads_use_endpoint_form_for_a_writes_orientation_service_edge():
    payloads = sdk_payloads([WRITES_OBS], artifact_digest="a" * 40, run_id="run-1")
    assert payloads == [
        {
            "schemaVersion": "1.0.0",
            "observationId": "stage-run-1-0001",
            "sequence": 1,
            "artifactDigest": "a" * 40,
            "source": {
                "dataset": "mysql://petclinic/owners",
                "field": "first_name",
            },
            "endpoint": {"service": WRITES_OBS["from"][0]},
            "edgeType": "WRITES",
            "transform": "",
            "observedAt": "2026-08-12T10:00:00Z",
        }
    ]


def test_session_scope_excludes_the_service_endpoint_and_includes_only_the_dataset_side():
    assert session_scope([READS_OBS]) == ("mysql://petclinic/visits",)
    assert session_scope([WRITES_OBS]) == ("mysql://petclinic/owners",)


def test_session_scope_mixes_dataset_only_and_service_anchored_observations():
    assert session_scope([OBS, READS_OBS]) == (
        "mysql://petclinic/visits",
        "snowflake://payments/analytics.daily_revenue",
        "snowflake://payments/raw.transactions",
    )
