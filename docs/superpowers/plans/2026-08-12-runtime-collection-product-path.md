# Runtime Collection on the Product Path — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the existing runtime verification stage (Python + Java seams) onto the product collection path through the session plane, behind a per-collection opt-in, and prove SCA+runtime edges reach the HIGH band (VERIFIED 92%) on the payments-pipeline fixture and a real spring-petclinic-microservices checkout.

**Architecture:** `repository_collection.collect(runtime_execution=True)` adds a `runtimeExecution` flag to the signed push payload; intake copies it into the envelope; orchestration's existing `_incremental_runtime` stage (B6/I6), which already turns COMPLETE runtime sessions into VALIDATED evidence and already merges session observations through `merge_runtime_observation`, gains one new step — when the flag is set, it *produces* that session itself: run the stage against B5's SCA edges, map corroborated edges to SDK observations, and drive `grant_session → observe → close` on `RuntimeLineageService`. Everything downstream (consolidation, banding, confidence projection) is already built and stays untouched.

**Tech Stack:** Python 3.12 (uv workspace, `apps/api`), sqlite-backed services, subprocess `javac`/`java` for the Java harness (guarded by `LINEAGE_JAVA_HOME`), pytest.

**Spec:** `docs/superpowers/specs/2026-08-12-runtime-collection-product-path-design.md`

## Global Constraints

- Runtime failure NEVER fails the collection and NEVER touches SCA output; it degrades to `NOT_PROVIDED` plus a closed-vocabulary reason: `not-requested`, `session-denied`, `jvm-unavailable`, `execution-failed`, `observation-rejected`, `session-incomplete` (spec §4.5, §5).
- Runtime never invents an edge — only edges the stage corroborated are emitted (spec §3, existing rule in `runtime_verification.py` and `consolidation.py:238`).
- Only `sessionComplete=True` + `runtimeScope="ELEMENT"` assertions lift the band (`application/consolidation.py:39`); the Java seam must therefore emit element-scoped observations to reach HIGH.
- Default-off output must stay byte-identical to today (`runtimeStatus: NOT_PROVIDED`); the `runtimeExecution` key is only added to the payload when True so `eventId` determinism is preserved for existing callers.
- No secrets/query text through intake: observation payloads must pass `_assert_metadata_only` (`services/runtime.py:833`); runtime observation `transform` is always sent as `""`.
- Test command: `uv run --project apps/api python -m pytest apps/api/tests/... -q`. Scripts: `uv run --project apps/api python scripts/<name>.py`.
- Band ceiling for SCA+runtime is HIGH → VERIFIED 92% (`domain/confidence.py:25`, `domain/product_confidence.py:18`). HIGHEST is out of reach by definition — do not chase it.

## Key existing interfaces (read before starting any task)

- `application/runtime_stage.py:68` — `run_runtime_stage(*, module_path, module_source, static_edges, resolve, observed_at, allow_execution=False, timeout_seconds=None) -> RuntimeStageResult`. `RuntimeStageResult.observations` items look like `{"mechanism":"RUNTIME","runtimeScope":"ELEMENT","sessionComplete":True,"observedAt":...,"from":[from_urn],"to":to_urn,"edgeType":...,"exact":False}`.
- `services/runtime.py:62` — `grant_session(*, repo, environment, artifact_digest, datasets, ttl_seconds, actor)` → dict with `sessionId`, `token`. `:133` `observe(session_id, token, mechanism, payload)`. `:123` `mark_ready(session_id, token)`. `:159` `begin_drain(session_id, token)`. `:173` `close(session_id, token, *, expected_observations, drained, buffered=0, dropped=0)` → manifest with `outcome`.
- `services/runtime.py:450` `_parse_sdk` — SDK payload closed schema: `{schemaVersion, observationId, sequence, artifactDigest, source:{dataset,field}, target:{dataset,field}, edgeType, transform, observedAt}`. Datasets are wire identifiers `platform://system/dataset`.
- `services/orchestration.py:930` — `_incremental_runtime(envelope, run_id, sca)`; `sca["sca"]["edges"]` carries full edge dicts (`from`, `to`, `edgeType`, `transform`, `exact`, ...). If `completed_evidence(repo, env, digest)` is non-empty it becomes `{"status":"VALIDATED","source":"SESSION","sessionEvidence":...}` and `_incremental_consolidate` (`:1078-1089`) merges each session observation via `merge_runtime_observation`.
- `services/consolidation.py:343` `_runtime_dataset_urn` — parses `platform://system/dataset` back to a dataset URN; ELEMENT matching (`:262`) requires exact source/target element URN + edgeType equality against the SCA edge.
- `services/java_runtime_verification.py` — `generate_harness(sources) -> dict[relpath, source]` (`:317`), `parse_observations(stdout)` (`:337`, lines `OBSERVED\trepo\tmethod\ttable`), `classify_operation` (`:85`), `verify_java_lineage(static_access, observations)` (`:370`) at `(table, operation)` granularity. `JavaObservation(repository_type, method, table, operation)`.
- JVM probe pattern: `apps/api/tests/services/test_java_runtime_verification.py:47` (`LINEAGE_JAVA_HOME` → `javac` path, else `shutil.which`).
- `services/intake.py:111` — precedent for optional envelope fields (`runtimeObservation`).
- `application/repository_collection.py:306` — the hardcoded `runtimeStatus` read.
- `dependencies.py:171-209` — `resolver`, `runtime` service, `OrchestrationService(...)` construction; `:262` `process_repository_push` → `orchestration.process_push`.
- URN accessors: mirror `services/consolidation.py` usage (`LineageUrn.parse(x).dataset_urn`, `.with_element(field)`); confirm the element accessor name in `domain/urns.py` before Task 1.

---

### Task 1: Observation emission module

Map corroborated stage observations (element-URN form) to SDK-mechanism session payloads and a session dataset scope, purely and deterministically. Wire identity is derived from the URNs themselves so consolidation's `_runtime_dataset_urn` round-trips to the same URN by construction.

**Files:**
- Create: `apps/api/src/lineage_api/application/runtime_emission.py`
- Test: `apps/api/tests/application/test_runtime_emission.py`

**Interfaces:**
- Consumes: `RuntimeStageResult.observations` dicts (shape above); `LineageUrn` from `lineage_api.domain.urns`.
- Produces: `wire_dataset(urn_text: str) -> str`; `session_scope(observations: Sequence[dict]) -> tuple[str, ...]`; `sdk_payloads(observations: Sequence[dict], *, artifact_digest: str, run_id: str) -> list[dict]`. Task 3 calls all three.

- [ ] **Step 1: Confirm URN accessors.** Open `apps/api/src/lineage_api/domain/urns.py`; note the exact property names for the dataset URN (used as `.dataset_urn` in consolidation.py) and the element/fragment part of an element URN. Use those names below.

- [ ] **Step 2: Write the failing test**

```python
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
```

- [ ] **Step 3: Run to verify failure.** `uv run --project apps/api python -m pytest apps/api/tests/application/test_runtime_emission.py -q` — expect `ModuleNotFoundError`.

- [ ] **Step 4: Implement**

```python
"""apps/api/src/lineage_api/application/runtime_emission.py

Map corroborated runtime-stage observations to SDK-mechanism session payloads.

Wire identity is derived from the edge URNs, not from workload names, so the
consolidation join re-derives exactly the URN the analyzer produced. Transform
text is never forwarded: corroboration matches on URNs, and the intake plane is
metadata-only by contract.
"""
from __future__ import annotations

from typing import Sequence

from lineage_api.domain.urns import LineageUrn


def wire_dataset(urn_text: str) -> str:
    urn = LineageUrn.parse(urn_text)
    return f"{urn.platform}://{urn.system}/{urn.dataset}"


def _element(urn_text: str) -> str:
    # The fragment after '#'; prefer the LineageUrn accessor confirmed in Step 1.
    return urn_text.rsplit("#", 1)[1]


def session_scope(observations: Sequence[dict]) -> tuple[str, ...]:
    datasets = {
        wire_dataset(urn)
        for observation in observations
        for urn in (*observation["from"], observation["to"])
    }
    return tuple(sorted(datasets))


def sdk_payloads(
    observations: Sequence[dict], *, artifact_digest: str, run_id: str
) -> list[dict]:
    payloads: list[dict] = []
    sequence = 0
    for observation in observations:
        for from_urn in observation["from"]:
            sequence += 1
            payloads.append(
                {
                    "schemaVersion": "1.0.0",
                    "observationId": f"stage-{run_id}-{sequence:04d}",
                    "sequence": sequence,
                    "artifactDigest": artifact_digest,
                    "source": {
                        "dataset": wire_dataset(from_urn),
                        "field": _element(from_urn),
                    },
                    "target": {
                        "dataset": wire_dataset(observation["to"]),
                        "field": _element(observation["to"]),
                    },
                    "edgeType": str(observation["edgeType"]),
                    "transform": "",
                    "observedAt": str(observation["observedAt"]),
                }
            )
    return payloads
```

If Step 1 found a proper element accessor on `LineageUrn`, use it in `_element` instead of the string split.

- [ ] **Step 5: Run to verify pass.** Same command — expect all 4 tests PASS.

- [ ] **Step 6: Commit.** `git add apps/api/src/lineage_api/application/runtime_emission.py apps/api/tests/application/test_runtime_emission.py && git commit -m "feat: map corroborated stage observations to SDK session payloads"`

---

### Task 2: Element-scoped Java runtime seam

The Java harness currently observes `(repository_type, method, table)` — dataset scope, which can never lift a band (`application/consolidation.py:39`). Extend the recorder to also report the entity's field names (the fields actually carried by the proxied call), extend `parse_observations`, and add a Java stage runner that produces `RuntimeStageResult`-shaped element observations for SCA edges whose table+column the run witnessed.

**Files:**
- Modify: `apps/api/src/lineage_api/services/java_runtime_verification.py` (recorder source `:216`, `JavaObservation` `:72`, `parse_observations` `:337`)
- Create: `apps/api/src/lineage_api/application/java_runtime_stage.py`
- Test: `apps/api/tests/application/test_java_runtime_stage.py`

**Interfaces:**
- Consumes: `generate_harness`, `parse_observations`, `classify_operation`, `JavaObservation` from `services/java_runtime_verification.py`; SCA edge dicts (`from`, `to`, `edgeType`).
- Produces: `run_java_runtime_stage(*, sources: Mapping[str, str], static_edges: Sequence[dict], observed_at: str, java_home: str | None) -> RuntimeStageResult` (same `RuntimeStageResult` dataclass as the Python stage); `java_home_or_none() -> str | None` (the `LINEAGE_JAVA_HOME` probe). Task 3 calls both.

- [ ] **Step 1: Extend the recorder to report entity fields.** In `generate_recorder_source` (`:216`), where the proxy records `repositoryType \t method \t table`, append a fourth column: the entity class's declared field names, comma-joined, obtained via `entityType.getDeclaredFields()` (skip `static`/`synthetic` fields). The observation line becomes `OBSERVED\t<repo>\t<method>\t<table>\t<f1,f2,...>`. Read the current generated source first and keep its structure; only the record call changes.

- [ ] **Step 2: Extend `JavaObservation` and `parse_observations`.** Add `fields: tuple[str, ...] = ()` to `JavaObservation`. In `parse_observations`, accept 4 or 5 tab-separated parts; with 5, split the last on `","` into `fields`. 4-part lines stay valid (backward compatible — existing tests must keep passing).

- [ ] **Step 3: Write the failing test for the stage runner** (pure parts don't need a JVM; the subprocess path is covered by the integration proof in Task 6):

```python
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
```

Field-to-column matching rule (the honest, no-guess version): a Java field name matches a column when `field.lower()` equals `column.lower().replace("_", "")` or equals `column.lower()` — `firstName` matches `first_name`, `city` matches `city`. Anything else does not corroborate. This rule lives in one function so the petclinic iteration (Task 6) has a single place to refine, and every refinement needs a test here first.

- [ ] **Step 4: Run to verify failure**, then implement `application/java_runtime_stage.py`:

```python
"""Run the Java harness as a runtime stage and reduce it to element observations."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping, Sequence

from lineage_api.application.runtime_stage import RuntimeStageResult
from lineage_api.services.java_runtime_verification import (
    JavaObservation,
    generate_harness,
    parse_observations,
)
from lineage_api.services.liveness import derive_liveness


def java_home_or_none() -> str | None:
    configured = os.environ.get("LINEAGE_JAVA_HOME")
    if configured and (Path(configured) / "bin" / "javac").exists():
        return configured
    return None if shutil.which("javac") is None else ""


def _column_matches(field: str, column: str) -> bool:
    lowered = field.lower()
    return lowered == column.lower() or lowered == column.lower().replace("_", "")


def _edge_parts(edge: dict) -> tuple[str, str, str]:
    to_urn = str(edge["to"])
    dataset_part, _, element = to_urn.rpartition("#")
    table = dataset_part.rsplit(":", 1)[1]
    operation = "WRITE" if edge["edgeType"] in {"WRITE", "DERIVES"} else "READ"
    return table, element, operation


def match_edges(
    static_edges: Sequence[dict], observations: Sequence[JavaObservation]
) -> tuple[list[dict], list[dict]]:
    matched: list[dict] = []
    unmatched: list[dict] = []
    for edge in static_edges:
        table, element, operation = _edge_parts(edge)
        witnessed = any(
            observation.table == table
            and observation.operation == operation
            and any(_column_matches(field, element) for field in observation.fields)
            for observation in observations
            if observation.operation != "UNKNOWN"
        )
        (matched if witnessed else unmatched).append(edge)
    return matched, unmatched


def element_observations(edges: Sequence[dict], observed_at: str) -> list[dict]:
    return [
        {
            "mechanism": "RUNTIME",
            "runtimeScope": "ELEMENT",
            "sessionComplete": True,
            "observedAt": observed_at,
            "from": list(edge["from"]),
            "to": edge["to"],
            "edgeType": edge["edgeType"],
            "exact": False,
        }
        for edge in edges
    ]


def run_java_runtime_stage(
    *,
    sources: Mapping[str, str],
    static_edges: Sequence[dict],
    observed_at: str,
    java_home: str | None,
) -> RuntimeStageResult:
    if java_home is None:
        raise RuntimeError("jvm-unavailable")
    harness = generate_harness(dict(sources))
    prefix = (Path(java_home) / "bin") if java_home else Path("")
    javac = str(prefix / "javac") if java_home else "javac"
    java = str(prefix / "java") if java_home else "java"
    with tempfile.TemporaryDirectory() as workdir:
        root = Path(workdir)
        files = []
        for relative, text in {**dict(sources), **harness}.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            if relative.endswith(".java"):
                files.append(str(path))
        subprocess.run(
            [javac, "-d", str(root / "classes"), *files],
            check=True, capture_output=True, timeout=120,
        )
        completed = subprocess.run(
            [java, "-cp", str(root / "classes"), "harness.GeneratedRuntimeTest"],
            check=True, capture_output=True, text=True, timeout=120,
        )
    observations = parse_observations(completed.stdout)
    matched, unmatched = match_edges(static_edges, observations)
    stage_observations = element_observations(matched, observed_at)
    liveness = [
        derive_liveness(f"{e['from'][0]}->{e['to']}", 1, observed_at, session_complete=True)
        for e in matched
    ] + [
        derive_liveness(f"{e['from'][0]}->{e['to']}", 0, None, session_complete=True)
        for e in unmatched
    ]
    verdict = (
        "NOT_PROVIDED" if not matched and not unmatched
        else "PARTIALLY_CORROBORATED" if unmatched
        else "CORROBORATED"
    )
    return RuntimeStageResult(
        verdict=verdict,
        corroborated=len(matched),
        static_only=len(unmatched),
        runtime_only=len(observations) and 0,
        observations=tuple(stage_observations),
        liveness=tuple(liveness),
        executed=tuple(sorted({o.method for o in observations})),
    )
```

Note: the harness sources layout — mirror how `test_java_runtime_verification.py`'s JVM test writes and compiles the generated files (it already solved package dirs and classpath); copy its approach rather than inventing one. `runtime_only` is reported as 0 here because table-level observations without a static edge are not element evidence; leave a code comment saying exactly that.

- [ ] **Step 5: Run the new tests AND the existing Java verification tests.** `uv run --project apps/api python -m pytest apps/api/tests/application/test_java_runtime_stage.py apps/api/tests/services/test_java_runtime_verification.py -q` — all pass (the JVM-marked test may skip).

- [ ] **Step 6: Commit.** `git commit -m "feat: element-scoped Java runtime stage via entity-field recording"`

---

### Task 3: Session production inside `_incremental_runtime`

**Files:**
- Modify: `apps/api/src/lineage_api/services/orchestration.py` (`_incremental_runtime` `:930`; constructor to accept `resolver`)
- Modify: `apps/api/src/lineage_api/services/intake.py:111` (copy `runtimeExecution` flag)
- Modify: `apps/api/src/lineage_api/dependencies.py` (pass `resolver=resolver` to `OrchestrationService`)
- Test: `apps/api/tests/services/test_orchestration_runtime_execution.py`

**Interfaces:**
- Consumes: Task 1 (`sdk_payloads`, `session_scope`), Task 2 (`run_java_runtime_stage`, `java_home_or_none`), `run_runtime_stage` + `catalog_element_resolver` from `application/runtime_stage.py`, `RuntimeLineageService` session API.
- Produces: envelope key `runtimeExecution: bool`; `runtime` result dict gains `"verification"` (stage verdict) and `"reasons"` (closed vocabulary list). Task 4 surfaces both.

- [ ] **Step 1: intake.** After the `runtimeObservation` block at `services/intake.py:111-113`, add:

```python
        if payload.get("runtimeExecution") is True:
            envelope["runtimeExecution"] = True
```

- [ ] **Step 2: Write the failing orchestration test.** Mirror the existing orchestration test setup (find it: `grep -rl "OrchestrationService(" apps/api/tests`). Three cases minimum:

```python
def test_runtime_execution_flag_produces_a_complete_session_and_validated_status():
    # envelope for the payments-pipeline fixture with runtimeExecution=True;
    # process the push; assert result["runtimeStatus"] == "CORROBORATED",
    # runtime service holds a COMPLETE session for (repo, env, digest),
    # and at least one consolidated edge has band == "HIGH".

def test_runtime_execution_absent_is_byte_identical_to_today():
    # same envelope without the flag; assert runtimeStatus == "NOT_PROVIDED"
    # and no runtime session exists.

def test_runtime_failure_degrades_with_reason_and_leaves_sca_untouched():
    # env="production" in the grant path (or monkeypatch grant_session to raise
    # the real DomainError) → runtimeStatus NOT_PROVIDED,
    # reasons == ["session-denied"], analysis/edges identical to the no-flag run.
```

- [ ] **Step 3: Implement `_execute_runtime_session`.** In `OrchestrationService`: accept `resolver: Resolver | None = None` in `__init__`, store as `self._resolver`. At the top of `_incremental_runtime`, before the `evidence_status` lookup:

```python
        reasons: list[str] = []
        verification: str | None = None
        if envelope.get("runtimeExecution") is True:
            verification, reasons = self._execute_runtime_session(envelope, sca)
```

and implement:

```python
    def _execute_runtime_session(
        self, envelope: dict[str, Any], sca: dict[str, Any]
    ) -> tuple[str | None, list[str]]:
        """Produce runtime session evidence from the SCA claim. Fail-closed:
        every failure returns a closed-vocabulary reason and leaves SCA alone."""
        from lineage_api.application.java_runtime_stage import (
            java_home_or_none,
            run_java_runtime_stage,
        )
        from lineage_api.application.runtime_emission import sdk_payloads, session_scope
        from lineage_api.application.runtime_stage import (
            catalog_element_resolver,
            run_runtime_stage,
        )
        from lineage_api.services.resolver import ResolveContext

        edges = [
            edge for edge in sca["sca"]["edges"]
            if "#" in str(edge.get("to", "")) and edge.get("from")
        ]
        if not edges:
            return None, ["execution-failed"]
        snapshot = self._snapshot_provider.resolve(envelope)
        read = lambda path: snapshot.read(path).decode()  # confirm reader name on RepositorySnapshot
        python_paths = [p for p in snapshot.paths if p.endswith(".py")]
        java_paths = [p for p in snapshot.paths if p.endswith(".java")]
        observed_at = self._clock()
        observations: list[dict] = []
        verdicts: list[str] = []
        reasons: list[str] = []
        if python_paths and self._resolver is not None:
            try:
                stage = run_runtime_stage(
                    module_path=python_paths[0],
                    module_source=read(python_paths[0]),
                    static_edges=tuple(
                        StaticEdge(str(e["from"][0]), str(e["to"]),
                                   str(e["edgeType"]), str(e.get("transform", "")))
                        for e in edges
                    ),
                    resolve=catalog_element_resolver(
                        self._resolver,
                        ResolveContext(
                            env=envelope["env"], platform=envelope_platform,
                            system=envelope["system"], repo=envelope["repo"],
                            digest=envelope["digest"], config={},
                            snapshot_id=self._resolver.snapshot_id,
                        ),
                    ),
                    observed_at=observed_at,
                    allow_execution=True,
                )
                observations += list(stage.observations)
                verdicts.append(stage.verdict)
            except Exception:
                reasons.append("execution-failed")
        if java_paths:
            java_home = java_home_or_none()
            if java_home is None:
                reasons.append("jvm-unavailable")
            else:
                try:
                    stage = run_java_runtime_stage(
                        sources={p: read(p) for p in java_paths},
                        static_edges=edges,
                        observed_at=observed_at,
                        java_home=java_home,
                    )
                    observations += list(stage.observations)
                    verdicts.append(stage.verdict)
                except Exception:
                    reasons.append("execution-failed")
        if not observations:
            return _combined_verdict(verdicts), reasons or ["execution-failed"]
        payloads = sdk_payloads(
            observations, artifact_digest=envelope["digest"],
            run_id=str(envelope["eventId"]),
        )
        try:
            grant = self._runtime.grant_session(
                repo=envelope["repo"], environment=envelope["env"],
                artifact_digest=envelope["digest"],
                datasets=session_scope(observations),
                ttl_seconds=300, actor="collection-orchestrator",
            )
            session_id, token = str(grant["sessionId"]), str(grant["token"])
            self._runtime.mark_ready(session_id, token)
            for payload in payloads:
                self._runtime.observe(session_id, token, "SDK", payload)
            self._runtime.begin_drain(session_id, token)
            manifest = self._runtime.close(
                session_id, token,
                expected_observations=len(payloads), drained=True,
            )
        except DomainError as error:
            code = (
                "session-denied"
                if error.code in {"RUNTIME_PRODUCTION_DENIED", "RUNTIME_KILL_SWITCH"}
                else "observation-rejected"
            )
            return _combined_verdict(verdicts), [*reasons, code]
        if manifest.get("outcome") != "COMPLETE":
            return _combined_verdict(verdicts), [*reasons, "session-incomplete"]
        return _combined_verdict(verdicts), reasons
```

with a module-level helper:

```python
def _combined_verdict(verdicts: list[str]) -> str | None:
    real = [v for v in verdicts if v != "NOT_PROVIDED"]
    if not real:
        return None
    if all(v == "CORROBORATED" for v in real):
        return "CORROBORATED"
    return "PARTIALLY_CORROBORATED"
```

Adjustments to make while implementing (each is a fact-check, not a decision): the exact kill-switch `DomainError` code (grep `services/runtime_policy.py` for the code raised on kill switch; grant path may not raise it at all — then drop it from the set); `envelope_platform` — the envelope has no platform key, so derive it from the first edge URN (`LineageUrn.parse(edges[0]["to"]).platform`); the snapshot reader method name (check `RepositorySnapshot` — `_reader` is private, there will be a public `read`/`source` accessor; if only `_reader` exists, add a thin public `read()` to `RepositorySnapshot`); import `StaticEdge` from `lineage_api.services.runtime_verification` and `DomainError` from its actual module (grep existing orchestration imports).

- [ ] **Step 4: Thread verdict and reasons into the runtime result.** Still in `_incremental_runtime`, after the existing `runtime` dict is built, attach `runtime["reasons"] = reasons` (always a list) and `runtime["verification"] = verification` when not None. In `_run_incremental` (`:511`, `:536`) and `_run_baseline`'s finalize path, surface `"runtimeStatus": runtime-verification-or-existing-status`: the returned `runtimeStatus` becomes `i6["runtime"].get("verification") or i6["runtime"]["status"]`, and add `"runtimeReasons": i6["runtime"].get("reasons", [])` alongside every existing `"runtimeStatus"` key.

- [ ] **Step 5: dependencies.** In `dependencies.py`, add `resolver=resolver` to the `OrchestrationService(...)` construction (`:198-209`).

- [ ] **Step 6: Run the new tests.** Expect the three cases to pass. Then run the full orchestration test file(s) touched: `uv run --project apps/api python -m pytest apps/api/tests/services -q -k orchestration`.

- [ ] **Step 7: Commit.** `git commit -m "feat: produce runtime session evidence on the collection path behind runtimeExecution"`

---

### Task 4: Opt-in parameter and status surface on `repository_collection`

**Files:**
- Modify: `apps/api/src/lineage_api/application/repository_collection.py` (`collect` `:169`, payload `:175-190`, `_summarize` `:242-319`)
- Test: `apps/api/tests/application/test_repository_collection_runtime.py`

**Interfaces:**
- Consumes: Task 3's envelope flag and result keys (`runtimeStatus` verdict, `runtimeReasons`).
- Produces: `collect(descriptor, *, runtime_execution: bool = False)`; summary keys `runtimeStatus` (existing) and `runtimeReasons: list[str]` (new, `["not-requested"]` when the flag is off and the pipeline reported nothing).

- [ ] **Step 1: Write the failing test**

```python
def test_collect_defaults_to_not_requested(collection_service, descriptor):
    summary = collection_service.collect(descriptor)
    assert summary["runtimeStatus"] == "NOT_PROVIDED"
    assert summary["runtimeReasons"] == ["not-requested"]


def test_collect_with_runtime_execution_adds_the_payload_flag(monkeypatch, ...):
    # capture the canonical payload passed to process_push;
    # collect(descriptor, runtime_execution=True) must include "runtimeExecution": True
    # and the default call must NOT include the key at all (eventId stability).
```

Reuse the fixtures from the existing `test_repository_collection*.py` files (find them: `ls apps/api/tests/application/ | grep repository_collection`).

- [ ] **Step 2: Implement.** `collect(self, descriptor, *, runtime_execution: bool = False)`; after building `payload`, add `if runtime_execution: payload["runtimeExecution"] = True` **before** the `canonical`/HMAC lines. In `_summarize` (make it take the result dict as today):

```python
            "runtimeStatus": result.get("runtimeStatus", "NOT_PROVIDED"),
            "runtimeReasons": (
                list(result.get("runtimeReasons"))
                if isinstance(result.get("runtimeReasons"), list)
                and result.get("runtimeReasons")
                else ["not-requested"]
            ),
```

(`_summarize` is a staticmethod today; keep it one.)

- [ ] **Step 3: Run the tests**, then the whole application test dir: `uv run --project apps/api python -m pytest apps/api/tests/application -q`.

- [ ] **Step 4: Commit.** `git commit -m "feat: per-collection runtime_execution opt-in with closed runtimeReasons"`

---

### Task 5: Fixture acceptance proof — payments-pipeline reaches HIGH through the product path

**Files:**
- Test: `apps/api/tests/application/test_collection_runtime_acceptance.py`

**Interfaces:**
- Consumes: everything above, plus `build_services` from `dependencies.py` and the payments-pipeline fixture repo (`fixtures/repositories/payments-pipeline/`, datasets under `snowflake://payments/...`, catalog `fixtures/catalog/catalog-snapshot-v1.json`).

- [ ] **Step 1: Write the acceptance test.** Build the app the way existing end-to-end collection tests do (find one that drives `repository_collection.collect` against the fixture snapshot provider and copy its setup verbatim). Then:

```python
def test_payments_pipeline_reaches_high_through_a_real_session(services, descriptor):
    summary = services.repository_collection.collect(
        descriptor, runtime_execution=True
    )
    assert summary["runtimeStatus"] == "CORROBORATED"
    assert summary["runtimeReasons"] == []
    evidence = services.runtime.completed_evidence(
        repo=descriptor.repository.repository,
        environment=descriptor.repository.environment,
        artifact_digest=descriptor.repository.revision,
    )
    assert evidence and evidence[0]["manifest"]["outcome"] == "COMPLETE"
    # Band proof: at least one consolidated edge carries SCA + ELEMENT runtime → HIGH.
    edges = <query the edge ledger the way existing consolidation tests do>
    high = [e for e in edges if e["band"] == "HIGH"]
    assert high, [e["band"] for e in edges]
    mechanisms = {p["mechanism"] for p in high[0]["provenance"]}
    assert mechanisms == {"SCA", "RUNTIME"}
```

Replace the one angle-bracket line with the actual ledger read used by `apps/api/tests/services/test_consolidation*.py` (e.g. `ConsolidationService._latest_edges` via the service, or the query service) — copy, don't invent. Also assert `project_confidence` display: `VERIFIED`, `92` for that edge if the query surface exposes it; if it does not, assert via `derive_band`/`project_confidence` on the mechanisms directly.

- [ ] **Step 2: Run it.** Expect FAIL initially only if wiring bugs exist; fix forward until green. This test is the spec's acceptance bar #1 — do not weaken its assertions to make it pass; fix the pipeline instead.

- [ ] **Step 3: Regression sweep.** `uv run --project apps/api python -m pytest apps/api/tests -q` (full suite), then:
  - `uv run --project apps/api python scripts/verify_prototype_alignment.py` → still 18/18.
  - `uv run --project apps/api python scripts/verify_real_repo_confidence.py /private/tmp/lineage-estate` → still 20 of 24 (skip gracefully if the estate checkout is absent; note it in the commit message).

- [ ] **Step 4: Commit.** `git commit -m "test: payments-pipeline reaches HIGH via a real runtime session on the product path"`

---

### Task 6: Petclinic proof — clone, collect, iterate to HIGH

This is the goal's iterate-loop. The petclinic checkout is real and unmodified; every mismatch discovered here is fixed in the *cells or seams* (with a unit test added in the matching task's test file), never by editing the checkout.

**Files:**
- Create: `scripts/verify_petclinic_runtime_confidence.py`
- Test: `apps/api/tests/application/test_petclinic_runtime_acceptance.py` (skips without checkout/JVM)

- [ ] **Step 1: Clone the repo** (pinned, shallow) into the scratch estate dir:

```bash
git clone --depth 1 https://github.com/spring-petclinic/spring-petclinic-microservices \
  /private/tmp/lineage-estate/spring-petclinic-microservices 2>/dev/null || true
```

Reuse `/private/tmp/lineage-estate` because `verify_real_repo_confidence.py` already treats it as the estate root. Record the resolved HEAD digest in the script output.

- [ ] **Step 2: Write the script.** Start from `scripts/measure_real_petclinic.py` (it already builds the snapshot descriptor, system=`petclinic`, origin, and drives the Java cell against the checkout — reuse its snapshot construction verbatim). Then instead of calling the cell directly, drive the product path the way Task 5's test does: build services pinned to that snapshot (`build_services(settings, repository_snapshot=...)` — mirror how `dependencies.py:262` composes for pinned snapshots), call `repository_collection.collect(descriptor, runtime_execution=True)`, and print per-edge: URN pair, band, corroboration, runtimeStatus, runtimeReasons, and the display band/percent from `project_confidence`. Exit non-zero if no edge reached HIGH so the iteration loop has a machine-readable failure.

- [ ] **Step 3: Run and iterate.** `uv run --project apps/api python scripts/verify_petclinic_runtime_confidence.py /private/tmp/lineage-estate/spring-petclinic-microservices`. Expected first-run failures, in likely order, and where the fix goes:
  1. **Analyzer selection** — petclinic needs the `java-spring-data-jpa-v1` pack; the descriptor's `AnalyzerIdentity` must match what `measure_real_petclinic.py` uses. Fix in the script.
  2. **Field/column mismatches** — extend `_column_matches` in `application/java_runtime_stage.py` (Task 2 file) with a test per new rule; never loosen to substring matching.
  3. **Wire-identity round-trip** — if `merge_runtime_observation` fails to match, print both the SCA edge URN and `_runtime_dataset_urn(wire, env)` and reconcile in `runtime_emission.wire_dataset` (the URN side is authoritative; never touch the SCA URN).
  4. **Harness compile failures on real sources** — the harness needs only entity/repository/injection-site files, not the whole service; filter `java_paths` to files matching the regexes in `java_runtime_verification.py` (`@Entity`, `extends JpaRepository`, constructor-injection sites) before generating.
  Iterate until at least one petclinic edge prints `band=HIGH display=VERIFIED 92` — that is the SCA+runtime ceiling, and the goal's stop condition.
- [ ] **Step 4: Freeze it as a skipping test.** `test_petclinic_runtime_acceptance.py` runs the same assertions, `pytest.mark.skipif` when the checkout or `java_home_or_none()` is missing (mirror `test_real_repo_corroboration.py:41-43`).

- [ ] **Step 5: Commit.** `git commit -m "feat: petclinic runtime corroboration to HIGH through the product path"`

---

### Task 7: Regression locks and documentation

**Files:**
- Modify: `docs/prototype-coverage.md` (L06 row), `README.md` (runtime section `:85`), `docs/superpowers/plans/2026-08-12-runtime-on-the-collection-path.md` (mark Task 4 done)
- Test: `apps/api/tests/application/test_collection_default_off_golden.py`

- [ ] **Step 1: Golden default-off test.** Collect the payments fixture twice in one test — once with no flag, once with `runtime_execution=False` explicitly — and assert both summaries are equal and `runtimeStatus == "NOT_PROVIDED"`, `runtimeReasons == ["not-requested"]`, and that `services.runtime.evidence_status(...)["status"] == "NOT_PROVIDED"` (no session was created).
- [ ] **Step 2: Full sweep.** `uv run --project apps/api python -m pytest apps/api/tests -q` and both scripts from Task 5 Step 3 — all green.
- [ ] **Step 3: Update docs.** L06 row: local plane now carries real stage-produced sessions on the collection path (opt-in, in-process; isolation still a stated limit). README: replace "Until a runtime session is joined, runtimeStatus remains NOT_PROVIDED" with the opt-in description. Mark the old plan's Task 4 complete with a pointer to this plan.
- [ ] **Step 4: Commit.** `git commit -m "docs: runtime collection is on the product path; lock default-off behaviour"`

---

## Self-review notes (already applied)

- Spec §3 flow → Tasks 3+4; §3.1/§3.2 invariant → Task 1 (URN-derived wire identity makes the round-trip structural) and Task 3 (single `self._resolver`); §4 components map 1:1 except that the orchestration block lives in `OrchestrationService._incremental_runtime` rather than `repository_collection` — the spec's approval Q&A placed session ownership in the application layer, but the SCA edges and the `RuntimeLineageService` only meet inside the pipeline that `process_push` runs; `repository_collection` still owns the opt-in and the status surface. This is a deliberate, documented deviation, not drift.
- Spec §5 error table → Task 3 Step 3 reason mapping + Task 4 `not-requested` + Task 7 golden test.
- Spec §6 tests → Tasks 1-2 (unit), 5 (fixture acceptance), 6 (petclinic acceptance), 7 (regression locks). The spec's "resolver invariant fails loudly" test is subsumed: wire identity is derived from URNs, so a mismatched snapshot manifests as a failed corroboration match in Task 5's acceptance test rather than a silent pass.
- Type consistency: `RuntimeStageResult` is the single stage result type across both seams; `sdk_payloads`/`session_scope` consume the stage observation dict shape defined at `runtime_stage.py:100-111`; reason strings appear only from the closed vocabulary.

---

## Addendum (controller ruling after Task 6 BLOCKED): Tasks 6b–6d — element-grounded Java edges to HIGH

Task 6 proved the wall: `JavaSpringEvidenceCompiler.compile()` (services/java_spring_sca.py:~806)
always emits bare dataset URNs (READS: `dataset -> service://repo/Type#method`; WRITES reversed),
while the residue-guarded `spring.query-element` facts (`_emit_query_elements`, :2472 — per-method
provable columns from derived-query names and JPQL, mapped through proven entity-field facts) are
grouped in `by_kind` and never wired into edges. The goal (a real Java repo edge at HIGH) requires
wiring them through. Honesty boundaries unchanged: element edges only from residue-guarded facts;
runtime corroboration only from what the proxy recorder genuinely witnesses (the repository's own
entity table + declared fields), so cross-entity JPQL projections (e.g. petclinic
`PetRepository.findPetTypeById` -> `types.id`) will remain static-only — expected and correct.
The reachable petclinic HIGH candidate is `VisitRepository.findByPetId(In)` -> `visits.pet_id`
(same-entity derived predicate, witnessed via `_column_matches("petId","pet_id")`).

### Task 6b: Element-scoped Java SCA edges from query-element facts

**Files:** Modify `apps/api/src/lineage_api/services/java_spring_sca.py` (compile candidate loop
~:806 and `_collapse_java_spring_candidates` if needed); Test `apps/api/tests/services/test_java_spring_sca.py`.

In `compile()`: build `elements_by_method` = {fact.subject: [(table, column), ...]} from
`by_kind["spring.query-element"]`. In the candidate loop, after `dataset_urn` is built, when the
invoked `repository#method` has query-element facts whose table equals the resolved `table_name`,
ADDITIONALLY append one candidate per column with the dataset side element-scoped:
`str(LineageUrn(env, platform, system, table_name).with_element(column))`, same from/to orientation
(READS: element-dataset -> service; WRITES: service -> element-dataset), transform suffixed with
`#column`. The bare dataset edge is kept (existing consumers/tests unchanged). TDD: a test proving
a repository with a derived predicate method yields both the dataset edge and the element edge;
a JPQL projection onto a different entity yields the element edge for that other table only when
the table resolves (else residue, existing behavior). Full suite green; petclinic script now
prints element-scoped edges > 0 (still 0 HIGH until 6c). Commit: `feat: element-ground Java SCA
edges from provable query elements`.

### Task 6c: Runtime corroboration for service-anchored element edges

**Files:** Modify `apps/api/src/lineage_api/services/orchestration.py` (`_execute_runtime_session`
Java edge selection), `apps/api/src/lineage_api/application/java_runtime_stage.py` (`_edge_parts`/
`match_edges` both orientations), `apps/api/src/lineage_api/application/runtime_emission.py`
(endpoint payload form), `apps/api/src/lineage_api/services/runtime.py` (`_parse_sdk` optional
`endpoint`), `packages/contracts/runtime-observation.schema.json` (additive optional `endpoint`),
`apps/api/src/lineage_api/services/consolidation.py` (`merge_runtime_observation` endpoint branch).
Tests in the matching existing test files per component.

1. Selection: a Java-eligible edge is one where EITHER endpoint parses as `urn:ldp:` with
   non-None `.element` (reuse `_is_element_scoped_dataset_urn` on both ends). Python selection
   unchanged (`to` element-scoped).
2. `_edge_parts`: if `to` is an element ldp URN -> (table, element from `to`, op WRITE);
   elif `from[0]` is an element ldp URN -> (table, element from `from[0]`, op READ). edgeType
   mapping unchanged ("READS"->READ else WRITE for {WRITE,WRITES,DERIVES}).
3. Emission: when a corroborated edge has a `service://` endpoint, emit the SDK payload with
   `source: {dataset, field}` from the element side and `endpoint: {service: "<service-urn>"}`
   INSTEAD of `target`; other keys unchanged. `session_scope` includes only the dataset side.
4. `_parse_sdk`: allow `endpoint` as alternative to `target` (exactly one of the two);
   endpoint form normalizes to `{granularity: "ELEMENT", sourceDatasets: [ds], sourceFields: [f],
   targetDataset: ds, targetField: f, endpoint: service_urn, edgeType, transform, exact: True,
   observedAt}` (targetDataset/-Field mirror the source so scope/idempotency/sequence logic is
   untouched; `endpoint` carries the service identity for the merge).
5. `merge_runtime_observation`: when the observation carries `endpoint`, ELEMENT candidates are
   edges where either (edge_type READS-like: `tuple(edge.from_urns) == (ds#f,)` and
   `edge.to_urn == endpoint`) or (WRITES-like: `edge.from_urns == (endpoint,)` and
   `edge.to_urn == ds#f`), exact string equality on the service side, catalog-resolved URN on the
   dataset side, `runtime_scope="ELEMENT"`. Never parse the service URN with `LineageUrn`.
   No change to band rules — HIGH arises from the existing `derive_consolidation` check.
Commit: `feat: corroborate service-anchored element edges through the session plane`.

### Task 6d: Petclinic to HIGH, re-frozen

Re-run `scripts/verify_petclinic_runtime_confidence.py` and iterate within the existing
boundaries until >= 1 edge prints band=HIGH display=VERIFIED 92 (expected: the
`visits.pet_id -> VisitResource#...` READ edge). Update the frozen
`test_petclinic_runtime_acceptance.py` to assert the HIGH state (skips unchanged). Update
`measure_real_petclinic.py` expectations only if edge counts changed (additive element edges).
Regression locks: full suite, 18/18 alignment, 20/24 real-repo. Commit: `feat: petclinic runtime
corroboration to HIGH through the product path`.
