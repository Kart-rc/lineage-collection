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
    assert "new OwnerController(" in generated
    assert "Recorder.proxy(OwnerRepository.class, Owner.class)" in generated
    for method in ("find", "save"):
        assert f'invoke(site, "{method}")' in generated


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
