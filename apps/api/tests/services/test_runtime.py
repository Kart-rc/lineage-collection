from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lineage_api.db import Database
from lineage_api.domain.errors import DomainError


PROJECT_ROOT = Path(__file__).parents[4]
RUNTIME_FIXTURES = PROJECT_ROOT / "fixtures" / "runtime"
ARTIFACT = "sha256:runtime-artifact-v1"
SOURCE_DATASET = "snowflake://payments/raw.transactions"
TARGET_DATASET = "snowflake://payments/analytics.daily_revenue"


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


def _runtime_service_type():
    try:
        from lineage_api.services.runtime import RuntimeLineageService
    except ModuleNotFoundError:
        pytest.fail("RuntimeLineageService is not implemented")
    return RuntimeLineageService


@pytest.fixture
def runtime(tmp_path: Path):
    clock = MutableClock()
    database = Database(tmp_path / "lineage.db")
    database.initialize()
    service = _runtime_service_type()(
        database,
        signing_secret="runtime-test-secret",
        clock=clock,
        approved_otel_parsers={"otel-sql-parser-v1"},
    )
    return service, database, clock


def _grant_ready(service, *, environment: str = "staging", ttl_seconds: int = 300):
    grant = service.grant_session(
        repo="payments-pipeline",
        environment=environment,
        artifact_digest=ARTIFACT,
        datasets=(SOURCE_DATASET, TARGET_DATASET),
        ttl_seconds=ttl_seconds,
        actor="runtime-test",
    )
    ready = service.mark_ready(grant["sessionId"], grant["token"])
    assert ready["state"] == "READY"
    return grant


def _fixture(name: str) -> dict[str, object]:
    return json.loads((RUNTIME_FIXTURES / name).read_text(encoding="utf-8"))


def test_signed_scope_rejects_tampering_artifact_mismatch_and_production(runtime) -> None:
    service, database, _ = runtime
    grant = _grant_ready(service)

    with pytest.raises(DomainError) as bad_signature:
        service.observe(
            grant["sessionId"], grant["token"] + "tampered", "SDK", _fixture("sdk-field-mapping.json")
        )
    assert bad_signature.value.code == "RUNTIME_SIGNATURE_INVALID"

    mismatched = {**_fixture("sdk-field-mapping.json"), "artifactDigest": "sha256:other"}
    with pytest.raises(DomainError) as wrong_artifact:
        service.observe(grant["sessionId"], grant["token"], "SDK", mismatched)
    assert wrong_artifact.value.code == "RUNTIME_ARTIFACT_MISMATCH"

    with pytest.raises(DomainError) as production:
        service.grant_session(
            repo="payments-pipeline",
            environment="production-canary",
            artifact_digest=ARTIFACT,
            datasets=(SOURCE_DATASET,),
            ttl_seconds=300,
            actor="runtime-test",
        )
    assert production.value.code == "RUNTIME_PRODUCTION_DENIED"
    with database.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM runtime_sessions WHERE environment LIKE 'prod%'"
        ).fetchone()[0] == 0


def test_expired_and_revoked_sessions_close_without_accepting_more_observations(runtime) -> None:
    service, database, clock = runtime
    expired = _grant_ready(service, ttl_seconds=5)
    clock.advance(6)

    with pytest.raises(DomainError) as expiration:
        service.observe(
            expired["sessionId"], expired["token"], "SDK", _fixture("sdk-field-mapping.json")
        )
    assert expiration.value.code == "RUNTIME_SESSION_EXPIRED"
    assert service.session_manifest(expired["sessionId"])["outcome"] == "EXPIRED"

    active = _grant_ready(service)
    revoked = service.revoke(active["sessionId"], actor="security", reason="scope withdrawn")
    assert revoked["outcome"] == "REVOKED"
    assert set(revoked) == {
        "schemaVersion",
        "sessionId",
        "repo",
        "environment",
        "artifactDigest",
        "outcome",
        "attempted",
        "accepted",
        "rejected",
        "duplicates",
        "buffered",
        "dropped",
        "drained",
        "observationChecksum",
        "closedAt",
    }
    with database.connection() as connection:
        audit = connection.execute(
            "SELECT actor, action, correlation_id FROM audit_events WHERE resource_id = ?",
            (active["sessionId"],),
        ).fetchone()
    assert dict(audit) == {
        "actor": "security",
        "action": "RUNTIME_SESSION_REVOKED",
        "correlation_id": active["sessionId"],
    }
    with pytest.raises(DomainError) as revocation:
        service.observe(
            active["sessionId"], active["token"], "SDK", _fixture("sdk-field-mapping.json")
        )
    assert revocation.value.code == "RUNTIME_SESSION_REVOKED"


@pytest.mark.parametrize(
    "mutation, expected_code",
    [
        (lambda body: {**body, "unknown": "field"}, "RUNTIME_UNKNOWN_FIELD"),
        (lambda body: {**body, "literalValue": "4111111111111111"}, "RUNTIME_PROHIBITED_FIELD"),
        (
            lambda body: {**body, "secret": "token-value"},
            "RUNTIME_PROHIBITED_FIELD",
        ),
    ],
)
def test_closed_schema_rejects_unknown_values_and_secrets(runtime, mutation, expected_code) -> None:
    service, _, _ = runtime
    grant = _grant_ready(service)

    with pytest.raises(DomainError) as rejected:
        service.observe(
            grant["sessionId"],
            grant["token"],
            "SDK",
            mutation(_fixture("sdk-field-mapping.json")),
        )

    assert rejected.value.code == expected_code


def test_dataset_sequence_and_duplicate_observation_are_idempotent(runtime) -> None:
    service, database, _ = runtime
    grant = _grant_ready(service)
    payload = _fixture("sdk-field-mapping.json")

    first = service.observe(grant["sessionId"], grant["token"], "SDK", payload)
    duplicate = service.observe(grant["sessionId"], grant["token"], "SDK", payload)

    assert duplicate == {**first, "duplicate": True}
    out_of_order = {**payload, "observationId": "sdk-observation-old", "sequence": 0}
    with pytest.raises(DomainError) as sequence:
        service.observe(grant["sessionId"], grant["token"], "SDK", out_of_order)
    assert sequence.value.code == "RUNTIME_SEQUENCE_CONFLICT"
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM runtime_observations").fetchone()[0] == 1


def test_incomplete_drain_records_attempted_accepted_rejected_and_duplicate_counts(runtime) -> None:
    service, _, _ = runtime
    grant = _grant_ready(service)
    payload = _fixture("sdk-field-mapping.json")
    service.observe(grant["sessionId"], grant["token"], "SDK", payload)
    service.observe(grant["sessionId"], grant["token"], "SDK", payload)
    with pytest.raises(DomainError):
        service.observe(
            grant["sessionId"], grant["token"], "SDK", {**payload, "secret": "not-allowed"}
        )

    assert service.begin_drain(grant["sessionId"], grant["token"])["state"] == "DRAIN"
    manifest = service.close(
        grant["sessionId"],
        grant["token"],
        expected_observations=2,
        drained=False,
        buffered=1,
        dropped=1,
    )

    assert manifest["outcome"] == "INCOMPLETE"
    assert manifest["attempted"] == 3
    assert manifest["accepted"] == 1
    assert manifest["rejected"] == 1
    assert manifest["duplicates"] == 1
    assert manifest["buffered"] == 1
    assert manifest["dropped"] == 1
    assert manifest["drained"] == 0


def test_complete_close_is_idempotent_and_exposes_only_complete_artifact_bound_evidence(
    runtime,
) -> None:
    service, _, _ = runtime
    grant = _grant_ready(service)
    service.observe(
        grant["sessionId"], grant["token"], "SDK", _fixture("sdk-field-mapping.json")
    )
    service.begin_drain(grant["sessionId"], grant["token"])

    first = service.close(
        grant["sessionId"], grant["token"], expected_observations=1, drained=True
    )
    replay = service.close(
        grant["sessionId"], grant["token"], expected_observations=1, drained=True
    )

    assert replay == first
    assert first["outcome"] == "COMPLETE"
    assert first["drained"] == 1
    evidence = service.completed_evidence(
        repo="payments-pipeline",
        environment="staging",
        artifact_digest=ARTIFACT,
    )
    assert len(evidence) == 1
    assert evidence[0]["manifest"] == first
    assert len(evidence[0]["observations"]) == 1


def test_openlineage_sdk_and_otel_retain_mechanism_and_supported_granularity(runtime) -> None:
    service, _, _ = runtime
    grant = _grant_ready(service)

    openlineage = service.observe(
        grant["sessionId"], grant["token"], "OPENLINEAGE", _fixture("openlineage-column-lineage.json")
    )
    sdk_payload = {**_fixture("sdk-field-mapping.json"), "observationId": "sdk-observation-2", "sequence": 2}
    sdk = service.observe(grant["sessionId"], grant["token"], "SDK", sdk_payload)
    otel_payload = {**_fixture("otel-db-span.json"), "sequence": 3}
    otel = service.observe(grant["sessionId"], grant["token"], "OTEL", otel_payload)

    assert (openlineage["mechanism"], openlineage["granularity"], openlineage["exact"]) == (
        "OPENLINEAGE",
        "ELEMENT",
        True,
    )
    assert (sdk["mechanism"], sdk["granularity"], sdk["exact"]) == ("SDK", "ELEMENT", True)
    assert (otel["mechanism"], otel["granularity"], otel["exact"]) == (
        "OTEL",
        "CONNECTIVITY",
        False,
    )


def test_multi_output_openlineage_event_is_stored_atomically_and_replay_is_idempotent(
    runtime,
) -> None:
    service, database, _ = runtime
    payload = json.loads(
        (RUNTIME_FIXTURES / "openlineage" / "multi-output.json").read_text(
            encoding="utf-8"
        )
    )
    datasets = (
        SOURCE_DATASET,
        TARGET_DATASET,
        "snowflake://payments/raw.refunds",
        "snowflake://payments/analytics.refund_summary",
    )
    grant = service.grant_session(
        repo="payments-pipeline",
        environment="staging",
        artifact_digest=ARTIFACT,
        datasets=datasets,
        ttl_seconds=300,
        actor="runtime-test",
    )
    service.mark_ready(grant["sessionId"], grant["token"])

    first = service.observe(grant["sessionId"], grant["token"], "OPENLINEAGE", payload)
    replay = service.observe(grant["sessionId"], grant["token"], "OPENLINEAGE", payload)

    assert len(first["observations"]) == 2
    assert [item["granularity"] for item in first["observations"]] == ["ELEMENT", "DATASET"]
    assert replay["duplicate"] is True
    assert all(item["duplicate"] is True for item in replay["observations"])
    with database.connection() as connection:
        stored = connection.execute(
            "SELECT COUNT(*) FROM runtime_observations WHERE session_id = ?",
            (grant["sessionId"],),
        ).fetchone()[0]
        counters = connection.execute(
            "SELECT attempted, accepted, duplicates FROM runtime_sessions WHERE session_id = ?",
            (grant["sessionId"],),
        ).fetchone()
    assert stored == 2
    assert dict(counters) == {"attempted": 2, "accepted": 2, "duplicates": 2}


def test_openlineage_unknown_connector_is_rejected_as_unsupported_coverage(runtime) -> None:
    service, _, _ = runtime
    payload = json.loads(
        (RUNTIME_FIXTURES / "openlineage" / "multi-output.json").read_text(
            encoding="utf-8"
        )
    )
    payload["producer"] = (
        "https://github.com/OpenLineage/OpenLineage/tree/9.0.0/integration/spark"
    )
    grant = service.grant_session(
        repo="payments-pipeline",
        environment="staging",
        artifact_digest=ARTIFACT,
        datasets=(
            SOURCE_DATASET,
            TARGET_DATASET,
            "snowflake://payments/raw.refunds",
            "snowflake://payments/analytics.refund_summary",
        ),
        ttl_seconds=300,
        actor="runtime-test",
    )
    service.mark_ready(grant["sessionId"], grant["token"])

    with pytest.raises(DomainError) as rejected:
        service.observe(grant["sessionId"], grant["token"], "OPENLINEAGE", payload)

    assert rejected.value.code == "RUNTIME_COVERAGE_UNSUPPORTED"
    assert rejected.value.details == {"code": "OPENLINEAGE_CONNECTOR_UNSUPPORTED"}


def test_otel_requires_a_separately_approved_parser_contract_for_exact_columns(runtime) -> None:
    service, _, _ = runtime
    grant = _grant_ready(service)
    base = _fixture("otel-db-span.json")
    unapproved = {
        **base,
        "parserContract": "unapproved-parser",
        "fieldMapping": {"sourceField": "amount", "targetField": "gross_revenue"},
    }

    with pytest.raises(DomainError) as parser:
        service.observe(grant["sessionId"], grant["token"], "OTEL", unapproved)
    assert parser.value.code == "RUNTIME_PARSER_NOT_APPROVED"

    approved = {
        **base,
        "observationId": "otel-observation-approved",
        "sequence": 2,
        "parserContract": "otel-sql-parser-v1",
        "fieldMapping": {"sourceField": "amount", "targetField": "gross_revenue"},
    }
    observation = service.observe(grant["sessionId"], grant["token"], "OTEL", approved)
    assert observation["granularity"] == "ELEMENT"
    assert observation["exact"] is True
    assert observation["parserContract"] == "otel-sql-parser-v1"
