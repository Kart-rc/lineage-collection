from __future__ import annotations

import hashlib
import hmac
import json
import shutil
from pathlib import Path

from lineage_api.config import Settings


PROJECT_ROOT = Path(__file__).parents[4]
SECRET = "test-lineage-secret"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=PROJECT_ROOT,
        data_directory=tmp_path,
        fixture_directory=PROJECT_ROOT / "fixtures",
        database_path=tmp_path / "lineage.db",
        object_directory=tmp_path / "objects",
        webhook_secret=SECRET,
    )


def _delivery(
    event_id: str = "delivery-001",
    *,
    digest: str = "demo-digest-v2",
    env: str = "staging",
    runtime_execution: bool = False,
):
    from lineage_api.services.intake import PushDelivery

    payload = {
        "eventId": event_id,
        "eventType": "repo.push",
        "repo": "payments-pipeline",
        "digest": digest,
        "env": env,
        "system": "payments",
        "changedFiles": ["pipeline.py"],
        "receivedAt": "2026-08-04T16:00:00Z",
    }
    if runtime_execution:
        payload["runtimeExecution"] = True
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return PushDelivery(payload, f"sha256={signature}")


def test_runtime_execution_flag_produces_a_complete_session_and_validated_status(
    tmp_path,
) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()

    collected = services.orchestration.process_push(
        _delivery("delivery-flag-on", runtime_execution=True)
    )

    assert collected["outcome"] == "ACCEPTED"
    assert collected["runtimeStatus"] == "CORROBORATED"
    assert collected["runtimeReasons"] == []

    with services.database.connection() as connection:
        row = connection.execute(
            "SELECT outcome FROM runtime_sessions WHERE repo = ? AND environment = ? "
            "AND artifact_digest = ?",
            ("payments-pipeline", "staging", "demo-digest-v2"),
        ).fetchone()
    assert row is not None
    assert row["outcome"] == "COMPLETE"

    bands = {edge["band"] for edge in collected["evidenceManifest"]["edges"]}
    assert "HIGH" in bands


def test_runtime_execution_absent_is_byte_identical_to_today(tmp_path) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()

    collected = services.orchestration.process_push(
        _delivery("delivery-flag-off", runtime_execution=False)
    )

    assert collected["outcome"] == "ACCEPTED"
    assert collected["runtimeStatus"] == "NOT_PROVIDED"
    assert "runtimeReasons" not in collected
    assert collected["evidenceManifest"]["runtime"] == {"status": "NOT_PROVIDED"}

    with services.database.connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM runtime_sessions WHERE repo = ?",
            ("payments-pipeline",),
        ).fetchone()[0]
    assert count == 0


def test_runtime_failure_degrades_with_reason_and_leaves_sca_untouched(tmp_path) -> None:
    from lineage_api.dependencies import build_services
    from lineage_api.domain.errors import DomainError

    services = build_services(_settings(tmp_path))
    services.reset()

    baseline = services.orchestration.process_push(
        _delivery("delivery-baseline-compare", runtime_execution=False)
    )

    def _deny_production(**kwargs):
        raise DomainError(
            "RUNTIME_PRODUCTION_DENIED",
            "Runtime collection sessions are denied for production targets",
            "runtime-grant",
        )

    services.orchestration._runtime.grant_session = _deny_production

    collected = services.orchestration.process_push(
        _delivery("delivery-flag-denied", digest="demo-digest-v3", runtime_execution=True)
    )

    assert collected["outcome"] == "ACCEPTED"
    assert collected["runtimeStatus"] == "NOT_PROVIDED"
    assert collected["runtimeReasons"] == ["session-denied"]

    with services.database.connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM runtime_sessions WHERE repo = ?",
            ("payments-pipeline",),
        ).fetchone()[0]
    assert count == 0

    baseline_edges = [
        {k: v for k, v in edge.items() if k != "version"}
        for edge in baseline["evidenceManifest"]["edges"]
    ]
    denied_edges = [
        {k: v for k, v in edge.items() if k != "version"}
        for edge in collected["evidenceManifest"]["edges"]
    ]
    assert denied_edges == baseline_edges
    assert baseline["analysis"]["status"] == collected["analysis"]["status"]
    assert baseline["analysis"]["edgeCount"] == collected["analysis"]["edgeCount"]


# --- Critical 1: a mid-session observation rejection must degrade to NOT_PROVIDED
# (never leak the internal INCOMPLETE evidence status) and must not leave a dangling,
# non-terminal self-granted session that contaminates a later collection.


def test_mid_session_observation_rejection_degrades_to_not_provided_and_revokes_session(
    tmp_path,
) -> None:
    from lineage_api.dependencies import build_services
    from lineage_api.domain.errors import DomainError

    services = build_services(_settings(tmp_path))
    services.reset()

    def _reject_observation(*args, **kwargs):
        raise DomainError(
            "RUNTIME_SCOPE_VIOLATION",
            "Runtime observation references a dataset outside the signed session scope",
            "runtime-test",
        )

    services.orchestration._runtime.observe = _reject_observation

    collected = services.orchestration.process_push(
        _delivery(
            "delivery-mid-session-rejection",
            digest="demo-digest-mid-session-rejection",
            runtime_execution=True,
        )
    )

    assert collected["outcome"] == "ACCEPTED"
    assert collected["runtimeStatus"] == "NOT_PROVIDED"
    assert collected["runtimeReasons"] == ["observation-rejected"]

    with services.database.connection() as connection:
        rows = connection.execute(
            "SELECT state, outcome, actor FROM runtime_sessions WHERE repo = ? "
            "AND environment = ? AND artifact_digest = ?",
            ("payments-pipeline", "staging", "demo-digest-mid-session-rejection"),
        ).fetchall()
    # Exactly one session was self-granted for this attempt, and it was driven to a
    # terminal CLOSED/REVOKED state -- never left dangling in READY/OBSERVING with
    # outcome=None.
    assert len(rows) == 1
    assert rows[0]["state"] == "CLOSED"
    assert rows[0]["outcome"] == "REVOKED"
    assert rows[0]["actor"] == "collection-orchestrator"


def test_subsequent_default_off_collection_of_same_digest_is_not_provided(tmp_path) -> None:
    from lineage_api.application.repository_collection import RepositoryCollectionService
    from lineage_api.dependencies import build_services
    from lineage_api.domain.errors import DomainError

    services = build_services(_settings(tmp_path))
    services.reset()

    def _reject_observation(*args, **kwargs):
        raise DomainError(
            "RUNTIME_SCOPE_VIOLATION",
            "Runtime observation references a dataset outside the signed session scope",
            "runtime-test",
        )

    services.orchestration._runtime.observe = _reject_observation
    services.orchestration.process_push(
        _delivery(
            "delivery-mid-session-rejection-2",
            digest="demo-digest-later-default-off",
            runtime_execution=True,
        )
    )
    # Restore normal observation handling before the later, unrelated default-off
    # collection -- only the earlier self-granted session's REVOKED outcome should be
    # in play here.
    del services.orchestration._runtime.observe

    later = services.orchestration.process_push(
        _delivery(
            "delivery-later-default-off",
            digest="demo-digest-later-default-off",
            runtime_execution=False,
        )
    )

    assert later["outcome"] == "ACCEPTED"
    assert later["runtimeStatus"] == "NOT_PROVIDED"
    # `RepositoryCollectionService._summarize` is what actually stamps the
    # ["not-requested"] default onto the wire result when `runtimeReasons` is absent
    # (see `test_runtime_execution_absent_is_byte_identical_to_today` for the raw,
    # key-absent orchestration contract this is built on).
    summarized = RepositoryCollectionService._summarize(
        later, "irrelevant-scope-digest", "irrelevant-revision"
    )
    assert summarized["runtimeReasons"] == ["not-requested"]

    evidence = services.runtime.evidence_status(
        repo="payments-pipeline",
        environment="staging",
        artifact_digest="demo-digest-later-default-off",
    )
    assert evidence == {"status": "NOT_PROVIDED", "sessionIds": []}


def test_partial_seam_failure_keeps_verdict_when_the_other_seam_completes_a_session(
    tmp_path, monkeypatch
) -> None:
    from lineage_api.dependencies import build_services
    from lineage_api.services.intake import PushDelivery

    # A private copy of the fixture tree so a dummy .java path can be added without
    # touching the checked-in fixtures other tests (e.g. baseline scope) depend on.
    fixture_root = tmp_path / "fixtures"
    shutil.copytree(PROJECT_ROOT / "fixtures", fixture_root)
    (fixture_root / "repositories" / "payments-pipeline" / "Dummy.java").write_text(
        "// dummy source; java_home_or_none is patched unavailable before this compiles\n"
    )

    settings = Settings(
        project_root=PROJECT_ROOT,
        data_directory=tmp_path / "data",
        fixture_directory=fixture_root,
        database_path=tmp_path / "data" / "lineage.db",
        object_directory=tmp_path / "data" / "objects",
        webhook_secret=SECRET,
    )
    services = build_services(settings)
    services.reset()

    import lineage_api.application.java_runtime_stage as java_runtime_stage

    monkeypatch.setattr(java_runtime_stage, "java_home_or_none", lambda: None)
    # This test's own concern is reason-aggregation (python succeeds, java seam is
    # jvm-unavailable) -- not Java edge *selection*, which Critical 2's dedicated
    # tests cover. `pipeline.py`'s SCA edges are Python-shape (both ends ldp, no
    # service anchor) and are correctly excluded from java_edges by that fix, so the
    # java branch here is kept reachable by loosening eligibility for this test alone.
    monkeypatch.setattr(java_runtime_stage, "is_java_service_anchored_edge", lambda edge: True)

    payload = {
        "eventId": "delivery-mixed-seam",
        "eventType": "repo.push",
        "repo": "payments-pipeline",
        "digest": "demo-digest-mixed-seam",
        "env": "staging",
        "system": "payments",
        "changedFiles": ["pipeline.py", "Dummy.java"],
        "receivedAt": "2026-08-04T16:00:00Z",
        "runtimeExecution": True,
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()

    collected = services.orchestration.process_push(
        PushDelivery(payload, f"sha256={signature}")
    )

    assert collected["runtimeStatus"] == "CORROBORATED"
    assert collected["runtimeReasons"] == ["jvm-unavailable"]

    with services.database.connection() as connection:
        row = connection.execute(
            "SELECT outcome FROM runtime_sessions WHERE repo = ? AND environment = ? "
            "AND artifact_digest = ?",
            ("payments-pipeline", "staging", "demo-digest-mixed-seam"),
        ).fetchone()
    assert row is not None
    assert row["outcome"] == "COMPLETE"


# --- Important 3: a runtime stage exception is a bounded, closed-vocabulary
# "execution-failed" on the wire, but the exception type + a truncated message are
# still captured somewhere an operator can see them.


def test_execution_failed_stage_exception_is_captured_as_diagnostic_detail(
    tmp_path, monkeypatch
) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()

    def _boom(*args, **kwargs):
        raise ValueError("synthetic python stage failure for diagnostics coverage")

    monkeypatch.setattr("lineage_api.application.runtime_stage.run_runtime_stage", _boom)

    collected = services.orchestration.process_push(
        _delivery(
            "delivery-execution-failed",
            digest="demo-digest-execution-failed",
            runtime_execution=True,
        )
    )

    assert collected["outcome"] == "ACCEPTED"
    assert collected["runtimeStatus"] == "NOT_PROVIDED"
    # The wire-facing reason stays inside the closed vocabulary -- no exception text.
    assert collected["runtimeReasons"] == ["execution-failed"]

    storing_evidence = next(
        stage for stage in collected["run"]["stages"] if stage["stage"] == "STORING_EVIDENCE"
    )
    assert storing_evidence["detail"]["runtimeDiagnostics"] == [
        {
            "stage": "python",
            "exceptionType": "ValueError",
            "message": "synthetic python stage failure for diagnostics coverage",
        }
    ]


# --- Task 6c: `_execute_runtime_session`'s Java edge selection accepts an
# element-scoped `urn:ldp:` URN on EITHER end (READS: element in `from`, service
# endpoint in `to`; WRITES: reversed); the Python selection stays `to`-only, unchanged.

READS_EDGE = {
    "from": ["urn:ldp:staging:mysql:petclinic:visits#pet_id"],
    "to": "service://spring-petclinic-microservices/"
    "org.springframework.samples.petclinic.visits.web.VisitResource#read",
    "edgeType": "READS",
}
WRITES_EDGE = {
    "from": [
        "service://spring-petclinic-microservices/"
        "org.springframework.samples.petclinic.owners.web.OwnerResource#update"
    ],
    "to": "urn:ldp:staging:mysql:petclinic:owners#first_name",
    "edgeType": "WRITES",
}
DATASET_SCOPE_EDGE = {
    "from": ["urn:ldp:staging:mysql:petclinic:vets"],
    "to": "service://spring-petclinic-microservices/"
    "org.springframework.samples.petclinic.vets.web.VetResource#list",
    "edgeType": "READS",
}
PYTHON_EDGE = {
    "from": ["urn:ldp:staging:snowflake:payments:raw.transactions#amount"],
    "to": "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue",
    "edgeType": "DERIVES",
    "transform": "SUM(amount)",
}


class _StubSnapshot:
    """Duck-types the pieces of `RepositorySnapshot` `_execute_runtime_session` reads:
    `.paths` (to decide which stage(s) are eligible to run) and `.read_bytes` (source
    text). Real compilation is never reached -- `run_java_runtime_stage` is monkeypatched
    below purely to capture what `static_edges` selection handed it."""

    def __init__(self, paths: tuple[str, ...]) -> None:
        self.paths = paths

    def read_bytes(self, path: str) -> bytes:
        return b"// stub source, never actually parsed or compiled\n"


class _StubSnapshotProvider:
    def __init__(self, snapshot: _StubSnapshot) -> None:
        self._snapshot = snapshot

    def resolve(self, envelope):
        return self._snapshot


def _sca(edges: list[dict]) -> dict:
    return {"sca": {"edges": edges}}


def test_java_selection_accepts_an_element_scoped_urn_on_either_end(
    tmp_path, monkeypatch
) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()
    services.orchestration._snapshot_provider = _StubSnapshotProvider(
        _StubSnapshot(paths=("Repo.java",))
    )

    import lineage_api.application.java_runtime_stage as java_runtime_stage

    captured: dict[str, object] = {}

    def _fake_java_stage(*, sources, static_edges, observed_at, java_home):
        captured["static_edges"] = list(static_edges)
        from lineage_api.application.runtime_stage import RuntimeStageResult

        return RuntimeStageResult(
            verdict="NOT_PROVIDED",
            corroborated=0,
            static_only=0,
            runtime_only=0,
            observations=(),
            liveness=(),
            executed=(),
        )

    monkeypatch.setattr(java_runtime_stage, "java_home_or_none", lambda: "/fake/java-home")
    monkeypatch.setattr(java_runtime_stage, "run_java_runtime_stage", _fake_java_stage)

    envelope = {
        "env": "staging",
        "system": "petclinic",
        "repo": "spring-petclinic-microservices",
        "digest": "demo-digest-java-selection",
        "eventId": "delivery-java-selection",
    }
    verification, reasons, diagnostics = services.orchestration._execute_runtime_session(
        envelope, _sca([READS_EDGE, WRITES_EDGE, DATASET_SCOPE_EDGE, PYTHON_EDGE])
    )

    # Both orientations were selected and handed to the Java stage; the dataset-scoped
    # edge (neither end an element-scoped ldp URN) was not, and neither was the
    # both-ends-ldp Python-shape DERIVES edge -- Critical 2: nothing in the Java seam
    # witnesses a dataset-to-dataset transform, so a table-write observation must never
    # be able to "corroborate" it.
    selected = captured["static_edges"]
    assert READS_EDGE in selected
    assert WRITES_EDGE in selected
    assert DATASET_SCOPE_EDGE not in selected
    assert PYTHON_EDGE not in selected
    # No observations from the stub stage -> execution-failed, but the point already
    # proven above is that selection reached the stage with the right edges.
    assert verification is None
    assert reasons == ["execution-failed"]
    assert diagnostics == []


def test_python_selection_stays_to_only_and_is_unaffected_by_java_widening(
    tmp_path, monkeypatch
) -> None:
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()
    services.orchestration._snapshot_provider = _StubSnapshotProvider(
        _StubSnapshot(paths=("pipeline.py",))
    )
    assert services.orchestration._resolver is not None

    import lineage_api.application.runtime_stage as runtime_stage

    captured: dict[str, object] = {}
    original = runtime_stage.run_runtime_stage

    def _capturing_python_stage(*, static_edges, **kwargs):
        captured["static_edges"] = list(static_edges)
        return original(static_edges=static_edges, **kwargs)

    monkeypatch.setattr(
        "lineage_api.application.runtime_stage.run_runtime_stage", _capturing_python_stage
    )

    envelope = {
        "env": "staging",
        "system": "payments",
        "repo": "payments-pipeline",
        "digest": "demo-digest-python-selection",
        "eventId": "delivery-python-selection",
    }
    services.orchestration._execute_runtime_session(
        envelope, _sca([READS_EDGE, WRITES_EDGE, PYTHON_EDGE])
    )

    # The Python selection is the exact same test as before 6c: eligible purely by
    # whether `to` parses as an element-scoped ldp URN, regardless of what `from` looks
    # like. `READS_EDGE`'s `to` is a service endpoint (not element-scoped) so it is
    # excluded; `WRITES_EDGE`'s `to` happens to be an element-scoped ldp URN (a Java
    # WRITES edge always anchors its dataset side on `to`) so it is included exactly as
    # it always would have been -- 6c widened the JAVA selection to also look at `from`,
    # it did not narrow or change what `to`-only already accepted.
    selected = [
        (edge.from_urn, edge.to_urn, edge.edge_type) for edge in captured["static_edges"]
    ]
    assert (PYTHON_EDGE["from"][0], PYTHON_EDGE["to"], PYTHON_EDGE["edgeType"]) in selected
    assert (WRITES_EDGE["from"][0], WRITES_EDGE["to"], WRITES_EDGE["edgeType"]) in selected
    assert (READS_EDGE["from"][0], READS_EDGE["to"], READS_EDGE["edgeType"]) not in selected
    assert len(selected) == 2


def test_no_groundable_edge_is_reported_as_its_own_reason_not_execution_failed(
    tmp_path,
) -> None:
    # Most real repositories that end INTEGRATION_REQUIRED carry zero element-scoped
    # or service-anchored edges -- nothing either runtime seam could ground. That is
    # not a failed execution: nothing was executable. The closed vocabulary must say
    # so, or every such collection reads as a runtime crash to the operator.
    from lineage_api.dependencies import build_services

    services = build_services(_settings(tmp_path))
    services.reset()

    envelope = {
        "env": "staging",
        "system": "petclinic",
        "repo": "spring-petclinic-microservices",
        "digest": "demo-digest-no-groundable",
        "eventId": "delivery-no-groundable",
    }
    verification, reasons, diagnostics = services.orchestration._execute_runtime_session(
        envelope, _sca([DATASET_SCOPE_EDGE])
    )

    assert verification is None
    assert reasons == ["no-groundable-edges"]
    assert diagnostics == []


def test_called_process_error_diagnostic_carries_the_stderr_tail() -> None:
    # `str(CalledProcessError)` is only the command line -- for a harness `javac`
    # failure the compiler's own errors live in `stderr`, and without them the
    # diagnostic is undebuggable. The bounded message must include the stderr tail.
    import subprocess

    from lineage_api.services.orchestration import _runtime_stage_diagnostic

    error = subprocess.CalledProcessError(
        1,
        ["javac", "-d", "classes", "Owner.java"],
        output=b"",
        stderr=b"Owner.java:3: error: package org.example.missing does not exist\n",
    )

    diagnostic = _runtime_stage_diagnostic("java", error)

    assert diagnostic["stage"] == "java"
    assert diagnostic["exceptionType"] == "CalledProcessError"
    assert "package org.example.missing does not exist" in diagnostic["message"]
    assert len(diagnostic["message"]) <= 480
