from __future__ import annotations

from pathlib import Path

import pytest

from lineage_api.db import Database


FROM_URN = "urn:ldp:staging:snowflake:payments:raw.transactions#amount"
TO_URN = "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue"


def _consolidation_types():
    try:
        from lineage_api.services.consolidation import (
            ConsolidationService,
            MechanismAssertion,
        )
    except ModuleNotFoundError:
        pytest.fail("Consolidation is not implemented")
    return ConsolidationService, MechanismAssertion


def _service(tmp_path: Path, name: str = "lineage.db"):
    consolidation_service, _ = _consolidation_types()
    database = Database(tmp_path / name)
    database.initialize()
    return consolidation_service(database, clock=lambda: "2026-08-04T16:00:00Z")


def _assertion(
    mechanism: str,
    provenance_id: str,
    *,
    transform: str | None = "SUM(amount)",
    exact: bool = True,
    runtime_scope: str | None = None,
    session_complete: bool = True,
):
    _, mechanism_assertion = _consolidation_types()
    return mechanism_assertion(
        provenance_id=provenance_id,
        from_urns=(FROM_URN,),
        to_urn=TO_URN,
        edge_type="DERIVES",
        transform=transform,
        mechanism=mechanism,
        exact=exact,
        evidence_ref={
            "schemaVersion": "1.0.0",
            "kind": mechanism.lower(),
            "key": provenance_id,
            "checksum": "a" * 64,
        },
        repo="payments-pipeline",
        run_id="run-001",
        correlation_id="corr-001",
        runtime_scope=runtime_scope,
        session_complete=session_complete,
    )


def test_merge_is_idempotent_by_provenance_id(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assertion = _assertion("SCA", "prov-sca")

    first = service.merge(assertion)
    replay = service.merge(assertion)

    assert replay == first
    assert first.version == 1
    assert first.band == "SINGLE"
    assert len(first.provenance) == 1
    assert service.version_count(first.edge_key) == 1


def test_engine_arrival_order_is_commutative(tmp_path: Path) -> None:
    sca = _assertion("SCA", "prov-sca")
    llm = _assertion("LLM", "prov-llm", exact=False)
    runtime = _assertion(
        "RUNTIME",
        "prov-runtime",
        transform=None,
        exact=False,
        runtime_scope="ELEMENT",
    )
    forward = _service(tmp_path, "forward.db")
    reverse = _service(tmp_path, "reverse.db")

    forward_result = forward.merge_many([sca, llm, runtime])
    reverse_result = reverse.merge_many([runtime, llm, sca])

    assert forward_result.as_dict() == reverse_result.as_dict()
    assert forward_result.band == "HIGHEST"
    assert forward_result.corroboration == "ELEMENT"
    assert [item.provenance_id for item in forward_result.provenance] == [
        "prov-llm",
        "prov-runtime",
        "prov-sca",
    ]


def test_dataset_runtime_corroboration_never_changes_the_band(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.merge_many(
        [
            _assertion("SCA", "prov-sca"),
            _assertion(
                "RUNTIME",
                "prov-runtime-dataset",
                transform=None,
                exact=False,
                runtime_scope="DATASET",
            ),
        ]
    )

    assert result.band == "SINGLE"
    assert result.corroboration == "DATASET"


def test_incomplete_runtime_session_is_retained_but_never_promotes(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.merge_many(
        [
            _assertion("SCA", "prov-sca"),
            _assertion(
                "RUNTIME",
                "prov-runtime-incomplete",
                transform=None,
                exact=False,
                runtime_scope="ELEMENT",
                session_complete=False,
            ),
        ]
    )

    assert result.band == "SINGLE"
    assert result.corroboration == "NONE"
    assert len(result.provenance) == 2


def test_different_exact_or_probable_transforms_conflict_without_averaging(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.merge_many(
        [
            _assertion("SCA", "prov-sca", transform="SUM(amount)"),
            _assertion("LLM", "prov-llm", transform="amount * 2", exact=False),
        ]
    )

    assert result.status == "CONFLICTING"
    assert result.band == "MEDIUM"
    assert result.transform is None
    assert {item.transform for item in result.provenance} == {"SUM(amount)", "amount * 2"}
    assert result.auto_publishable is False


def test_equivalent_normalized_transforms_do_not_conflict(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.merge_many(
        [
            _assertion("SCA", "prov-sca", transform="SUM( amount )"),
            _assertion("LLM", "prov-llm", transform="sum(amount)", exact=False),
        ]
    )

    assert result.status == "PROPOSED"
    assert result.transform == "SUM( amount )"
    assert result.auto_publishable is True


def test_complete_dataset_runtime_observation_corroborates_without_raising_band(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    static = service.merge(_assertion("SCA", "prov-sca"))
    manifest = {
        "sessionId": "runtime-session-dataset",
        "outcome": "COMPLETE",
        "observationChecksum": "sha256:runtime",
    }
    observation = {
        "observationId": "runtime-dataset-1",
        "sessionId": "runtime-session-dataset",
        "mechanism": "OPENLINEAGE",
        "granularity": "DATASET",
        "sourceDatasets": ["snowflake://payments/raw.transactions"],
        "targetDataset": "snowflake://payments/analytics.daily_revenue",
        "edgeType": "DERIVES",
        "exact": True,
    }

    merged = service.merge_runtime_observation(
        observation,
        manifest,
        environment="staging",
        repo="payments-pipeline",
        correlation_id="corr-runtime-dataset",
    )
    replay = service.merge_runtime_observation(
        observation,
        manifest,
        environment="staging",
        repo="payments-pipeline",
        correlation_id="corr-runtime-dataset",
    )

    assert len(merged) == 1
    assert replay == merged
    assert merged[0].edge_key == static.edge_key
    assert merged[0].corroboration == "DATASET"
    assert merged[0].band == "SINGLE"
    assert service.version_count(static.edge_key) == 2


# --- Task 6c: `merge_runtime_observation` endpoint branch. A service-anchored Java
# element edge names one end with a `service://repo/Type#method` endpoint URN rather
# than another dataset element. Corroborating it means exact string equality on the
# service side and a catalog-resolved URN on the dataset side -- never `LineageUrn.parse`
# on the service URN itself.

VISITS_ELEMENT_URN = "urn:ldp:staging:mysql:petclinic:visits#pet_id"
OWNERS_ELEMENT_URN = "urn:ldp:staging:mysql:petclinic:owners#first_name"
VISIT_SERVICE_URN = (
    "service://spring-petclinic-microservices/"
    "org.springframework.samples.petclinic.visits.web.VisitResource#read"
)
OWNER_SERVICE_URN = (
    "service://spring-petclinic-microservices/"
    "org.springframework.samples.petclinic.owners.web.OwnerResource#update"
)


def _sca_assertion(provenance_id: str, *, from_urns: tuple[str, ...], to_urn: str, edge_type: str):
    _, mechanism_assertion = _consolidation_types()
    return mechanism_assertion(
        provenance_id=provenance_id,
        from_urns=from_urns,
        to_urn=to_urn,
        edge_type=edge_type,
        transform=None,
        mechanism="SCA",
        exact=True,
        evidence_ref={
            "schemaVersion": "1.0.0",
            "kind": "sca",
            "key": provenance_id,
            "checksum": "b" * 64,
        },
        repo="spring-petclinic-microservices",
        run_id="run-java-001",
        correlation_id="corr-java-001",
    )


def _endpoint_manifest(session_id: str = "runtime-session-endpoint"):
    return {
        "sessionId": session_id,
        "outcome": "COMPLETE",
        "observationChecksum": "sha256:runtime-endpoint",
    }


def test_endpoint_observation_corroborates_a_reads_orientation_edge(tmp_path: Path) -> None:
    service = _service(tmp_path)
    static = service.merge(
        _sca_assertion(
            "prov-sca-reads",
            from_urns=(VISITS_ELEMENT_URN,),
            to_urn=VISIT_SERVICE_URN,
            edge_type="READS",
        )
    )
    observation = {
        "observationId": "runtime-endpoint-reads-1",
        "granularity": "ELEMENT",
        "sourceDatasets": ["mysql://petclinic/visits"],
        "targetDataset": "mysql://petclinic/visits",
        "sourceFields": ["pet_id"],
        "targetField": "pet_id",
        "endpoint": VISIT_SERVICE_URN,
        "edgeType": "READS",
        "exact": True,
    }

    merged = service.merge_runtime_observation(
        observation,
        _endpoint_manifest(),
        environment="staging",
        repo="spring-petclinic-microservices",
        correlation_id="corr-runtime-endpoint-reads",
    )

    assert len(merged) == 1
    assert merged[0].edge_key == static.edge_key
    assert merged[0].corroboration == "ELEMENT"
    assert merged[0].band == "HIGH"
    assert {p.mechanism for p in merged[0].provenance} == {"SCA", "RUNTIME"}


def test_endpoint_observation_corroborates_a_writes_orientation_edge(tmp_path: Path) -> None:
    service = _service(tmp_path)
    static = service.merge(
        _sca_assertion(
            "prov-sca-writes",
            from_urns=(OWNER_SERVICE_URN,),
            to_urn=OWNERS_ELEMENT_URN,
            edge_type="WRITES",
        )
    )
    observation = {
        "observationId": "runtime-endpoint-writes-1",
        "granularity": "ELEMENT",
        "sourceDatasets": ["mysql://petclinic/owners"],
        "targetDataset": "mysql://petclinic/owners",
        "sourceFields": ["first_name"],
        "targetField": "first_name",
        "endpoint": OWNER_SERVICE_URN,
        "edgeType": "WRITES",
        "exact": True,
    }

    merged = service.merge_runtime_observation(
        observation,
        _endpoint_manifest("runtime-session-endpoint-writes"),
        environment="staging",
        repo="spring-petclinic-microservices",
        correlation_id="corr-runtime-endpoint-writes",
    )

    assert len(merged) == 1
    assert merged[0].edge_key == static.edge_key
    assert merged[0].corroboration == "ELEMENT"
    assert merged[0].band == "HIGH"


def test_endpoint_observation_never_corroborates_a_different_service(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.merge(
        _sca_assertion(
            "prov-sca-reads-2",
            from_urns=(VISITS_ELEMENT_URN,),
            to_urn=VISIT_SERVICE_URN,
            edge_type="READS",
        )
    )
    observation = {
        "observationId": "runtime-endpoint-reads-mismatch",
        "granularity": "ELEMENT",
        "sourceDatasets": ["mysql://petclinic/visits"],
        "targetDataset": "mysql://petclinic/visits",
        "sourceFields": ["pet_id"],
        "targetField": "pet_id",
        # A same-shaped but different service URN must never match by fuzzy or partial
        # comparison -- only exact string equality.
        "endpoint": VISIT_SERVICE_URN + "AllByOwnerId",
        "edgeType": "READS",
        "exact": True,
    }

    merged = service.merge_runtime_observation(
        observation,
        _endpoint_manifest(),
        environment="staging",
        repo="spring-petclinic-microservices",
        correlation_id="corr-runtime-endpoint-mismatch",
    )

    assert merged == []


def test_endpoint_observation_never_corroborates_the_wrong_orientation(tmp_path: Path) -> None:
    # The SCA edge is WRITES-shaped (service -> owners#first_name); an observation whose
    # endpoint/element pairing matches the READS orientation instead must not corroborate
    # it -- orientation is part of the claim, not incidental.
    service = _service(tmp_path)
    service.merge(
        _sca_assertion(
            "prov-sca-writes-2",
            from_urns=(OWNER_SERVICE_URN,),
            to_urn=OWNERS_ELEMENT_URN,
            edge_type="WRITES",
        )
    )
    observation = {
        "observationId": "runtime-endpoint-writes-mismatch",
        "granularity": "ELEMENT",
        "sourceDatasets": ["mysql://petclinic/owners"],
        "targetDataset": "mysql://petclinic/owners",
        "sourceFields": ["first_name"],
        "targetField": "first_name",
        "endpoint": OWNER_SERVICE_URN,
        "edgeType": "READS",
        "exact": True,
    }

    merged = service.merge_runtime_observation(
        observation,
        _endpoint_manifest(),
        environment="staging",
        repo="spring-petclinic-microservices",
        correlation_id="corr-runtime-endpoint-writes-mismatch",
    )

    assert merged == []


def test_endpoint_observation_replay_is_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.merge(
        _sca_assertion(
            "prov-sca-reads-3",
            from_urns=(VISITS_ELEMENT_URN,),
            to_urn=VISIT_SERVICE_URN,
            edge_type="READS",
        )
    )
    observation = {
        "observationId": "runtime-endpoint-reads-replay",
        "granularity": "ELEMENT",
        "sourceDatasets": ["mysql://petclinic/visits"],
        "targetDataset": "mysql://petclinic/visits",
        "sourceFields": ["pet_id"],
        "targetField": "pet_id",
        "endpoint": VISIT_SERVICE_URN,
        "edgeType": "READS",
        "exact": True,
    }
    manifest = _endpoint_manifest("runtime-session-endpoint-replay")

    first = service.merge_runtime_observation(
        observation,
        manifest,
        environment="staging",
        repo="spring-petclinic-microservices",
        correlation_id="corr-runtime-endpoint-replay",
    )
    replay = service.merge_runtime_observation(
        observation,
        manifest,
        environment="staging",
        repo="spring-petclinic-microservices",
        correlation_id="corr-runtime-endpoint-replay",
    )

    assert replay == first
