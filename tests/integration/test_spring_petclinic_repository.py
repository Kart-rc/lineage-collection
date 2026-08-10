from __future__ import annotations

import json
import os
import hashlib
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath

import pytest

from lineage_api.application.repository_sources import (
    RepositoryCheckoutDescriptor,
    RepositorySourceLimits,
)
from lineage_api.infrastructure.local_git_source import LocalGitRepositorySource
from lineage_api.services.java_spring_sca import JavaSpringScaAnalyzer, JavaSpringSource


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_real_repository_acceptance.sh"
ORIGIN = "https://github.com/spring-projects/spring-petclinic"
REVISION = "88e37c15cf6fc8490b01bc3e8e2c800cec1ac272"
_SCHEMA_PATH = "src/main/resources/db/postgres/schema.sql"
_STAGES = [
    "QUEUED",
    "CLASSIFYING",
    "ANALYZING",
    "RESOLVING",
    "STORING_EVIDENCE",
    "MERGING",
    "PROPOSING",
    "IN_REVIEW",
]
_ENTITY_MAPPINGS = [
    {"entity": "Owner", "table": "owners"},
    {"entity": "Pet", "table": "pets"},
    {"entity": "PetType", "table": "types"},
    {"entity": "Specialty", "table": "specialties"},
    {"entity": "Vet", "table": "vets"},
    {"entity": "Visit", "table": "visits"},
]
_REPOSITORY_CHAINS = [
    {"entity": "Owner", "repository": "OwnerRepository", "table": "owners"},
    {"entity": "PetType", "repository": "PetTypeRepository", "table": "types"},
    {"entity": "Vet", "repository": "VetRepository", "table": "vets"},
]
_EDGE_ORACLE = sorted(
    [
        ("READS", "OwnerRepository", "Owner", "owners", "OwnerController#findOwner", "findById"),
        ("READS", "OwnerRepository", "Owner", "owners", "OwnerController#findPaginatedForOwnersLastName", "findByLastNameStartingWith"),
        ("WRITES", "OwnerRepository", "Owner", "owners", "OwnerController#processCreationForm", "save"),
        ("WRITES", "OwnerRepository", "Owner", "owners", "OwnerController#processUpdateOwnerForm", "save"),
        ("READS", "OwnerRepository", "Owner", "owners", "OwnerController#showOwner", "findById"),
        ("READS", "OwnerRepository", "Owner", "owners", "PetController#findOwner", "findById"),
        ("READS", "OwnerRepository", "Owner", "owners", "PetController#findPet", "findById"),
        ("READS", "PetTypeRepository", "PetType", "types", "PetController#populatePetTypes", "findPetTypes"),
        ("WRITES", "OwnerRepository", "Owner", "owners", "PetController#processCreationForm", "saveAndFlush"),
        ("WRITES", "OwnerRepository", "Owner", "owners", "PetController#updatePetDetails", "saveAndFlush"),
        ("READS", "PetTypeRepository", "PetType", "types", "PetTypeFormatter#parse", "findPetTypes"),
        ("READS", "OwnerRepository", "Owner", "owners", "VisitController#loadPetWithVisit", "findById"),
        ("WRITES", "OwnerRepository", "Owner", "owners", "VisitController#processNewVisitForm", "save"),
        ("READS", "VetRepository", "Vet", "vets", "VetController#findPaginated", "findAll"),
        ("READS", "VetRepository", "Vet", "vets", "VetController#showResourcesVetList", "findAll"),
    ]
)
_DATABASE_EFFECT_ALGORITHM = "sqlite-canonical-relational-snapshot-v1"
_DATABASE_EFFECT_SCHEMA_VERSION = "1.0.0"
_TIME_DERIVED_DIGEST_FIELDS = frozenset(
    {
        "evidence_objects.checksum[kind=manifest|proposal]",
        "stage_results.output_checksum",
        "stage_results.output_ref.checksum",
    }
)


class AcceptanceSourceError(ValueError):
    pass


class AcceptanceOracleError(ValueError):
    pass


def _canonical_bytes(document: dict[str, object]) -> bytes:
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_content_addressed_manifest(
    directory: Path, document: dict[str, object]
) -> Path:
    content = _canonical_bytes(document)
    digest = _sha256(content)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"sha256-{digest}.json"
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError("content-addressed manifest conflict")
        return path
    descriptor, temporary_name = tempfile.mkstemp(
        dir=directory, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise ValueError("content-addressed manifest conflict")
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def _verify_content_addressed_manifest(path: Path) -> dict[str, object]:
    match = re.fullmatch(r"sha256-([0-9a-f]{64})\.json", path.name)
    if match is None:
        raise ValueError("manifest filename is not content-addressed")
    content = path.read_bytes()
    if _sha256(content) != match.group(1):
        raise ValueError("manifest checksum mismatch")
    document = json.loads(content)
    if not isinstance(document, dict) or _canonical_bytes(document) != content:
        raise ValueError("manifest is not canonical JSON")
    return document


def _verified_evidence_bytes(path: Path, expected_checksum: str) -> bytes:
    content = path.read_bytes()
    if re.fullmatch(r"[0-9a-f]{64}", expected_checksum) is None:
        raise ValueError("evidence checksum is not exact")
    if _sha256(content) != expected_checksum:
        raise ValueError("evidence checksum mismatch")
    return content


def _collect_arguments(
    checkout: Path, *, origin: str = ORIGIN, revision: str = REVISION
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "lineage_api.cli",
        "collect-checkout",
        "--checkout",
        str(checkout),
        "--origin",
        origin,
        "--revision",
        revision,
        "--repository",
        "spring-petclinic",
        "--environment",
        "staging",
        "--platform",
        "postgres",
        "--system",
        "petclinic",
        "--analyzer-pack",
        "java-spring-data-jpa-v1",
        "--ruleset",
        "spring-data-rules-v1",
        "--profile",
        "postgres",
    ]


def _run_collect_checkout(
    checkout: Path,
    state_root: Path,
    *,
    origin: str = ORIGIN,
    revision: str = REVISION,
) -> dict[str, object]:
    state_root.mkdir(parents=True, exist_ok=True)
    environment = {
        "LANG": "C",
        "LC_ALL": "C",
        "LINEAGE_DATA_DIR": str(state_root),
        "LINEAGE_WEBHOOK_SECRET": "local-real-repository-acceptance",
        "PATH": os.defpath,
    }
    completed = subprocess.run(
        _collect_arguments(checkout, origin=origin, revision=revision),
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    output = completed.stdout
    if len(output.encode()) > 16 * 1024 or len(completed.stderr.encode()) > 16 * 1024:
        raise AcceptanceSourceError("PIPELINE_OUTPUT_LIMIT_EXCEEDED")
    if str(checkout) in output or "local-real-repository-acceptance" in output:
        raise AcceptanceSourceError("PIPELINE_OUTPUT_LEAKED_INPUT")
    try:
        result = json.loads(output)
    except json.JSONDecodeError as error:
        raise AcceptanceSourceError("PIPELINE_OUTPUT_INVALID") from error
    if not isinstance(result, dict):
        raise AcceptanceSourceError("PIPELINE_OUTPUT_INVALID")
    if completed.returncode != 0:
        code = str(result.get("errorCode") or result.get("reasonCode") or "PIPELINE_FAILED")
        if code in {
            "SOURCE_VALIDATION_FAILED",
            "INVALID_CHECKOUT_DESCRIPTOR",
            "SOURCE_DETERMINANT_MISMATCH",
        }:
            raise AcceptanceSourceError("SOURCE_VALIDATION_FAILED")
        raise AcceptanceOracleError(code)
    return result


@dataclass(frozen=True, slots=True)
class DatabaseEffectSnapshot:
    physical_bytes: bytes
    logical_digest: str
    table_names: tuple[str, ...]
    row_counts: dict[str, int]


def _quoted_identifier(identifier: str) -> str:
    if not identifier or "\x00" in identifier:
        raise ValueError("database identifier is invalid")
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _is_volatile_time_name(name: str) -> bool:
    return name.endswith("_at") or (name.endswith("At") and len(name) > 2)


def _normalize_json_volatility(
    value: object, *, table: str, column: str
) -> object:
    if isinstance(value, dict):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if item is not None and (
                _is_volatile_time_name(key_text)
                or key_text in {"leaseOwner", "leaseEpoch", "lease_owner", "lease_epoch"}
            ):
                normalized[key_text] = {"normalizedVolatile": "time-or-lease"}
            elif (
                table == "stage_results"
                and column == "output_ref"
                and key_text == "checksum"
            ):
                normalized[key_text] = {"normalizedVolatile": "time-derived-digest"}
            else:
                normalized[key_text] = _normalize_json_volatility(
                    item, table=table, column=column
                )
        return normalized
    if isinstance(value, list):
        return [
            _normalize_json_volatility(item, table=table, column=column)
            for item in value
        ]
    return value


def _encoded_sql_value(
    value: object,
    *,
    table: str,
    column: str,
    row: dict[str, object],
    logical: bool,
) -> dict[str, object]:
    if logical and value is not None and (
        _is_volatile_time_name(column) or column.startswith("lease_")
    ):
        return {"type": "normalized", "value": "time-or-lease"}
    if logical and table == "evidence_objects" and column == "checksum" and str(
        row.get("kind")
    ) in {"manifest", "proposal"}:
        return {"type": "normalized", "value": "time-derived-digest"}
    if logical and table == "stage_results" and column == "output_checksum":
        return {"type": "normalized", "value": "time-derived-digest"}
    if value is None:
        return {"type": "null"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "real", "value": value.hex()}
    if isinstance(value, bytes):
        return {"type": "blob", "length": len(value), "sha256": _sha256(value)}
    if not isinstance(value, str):
        raise ValueError("database value type is unsupported")
    if logical and (column.endswith("_json") or column in {"output_ref", "input_ref"}):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            pass
        else:
            return {
                "type": "json",
                "value": _normalize_json_volatility(parsed, table=table, column=column),
            }
    return {"type": "text", "value": value}


def _database_effect_snapshot(
    database: Path,
    *,
    max_tables: int = 128,
    max_rows: int = 500_000,
    max_bytes: int = 64 * 1024 * 1024,
) -> DatabaseEffectSnapshot:
    if min(max_tables, max_rows, max_bytes) < 1:
        raise ValueError("database snapshot bounds must be positive")
    uri = f"{database.resolve(strict=True).as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        table_names = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        )
        if len(table_names) > max_tables:
            raise ValueError("database table bound exceeded")
        physical_tables: list[dict[str, object]] = []
        logical_tables: list[dict[str, object]] = []
        row_counts: dict[str, int] = {}
        total_rows = 0
        encoded_bytes = 0
        for table in table_names:
            quoted = _quoted_identifier(table)
            column_rows = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
            columns = [str(row[1]) for row in column_rows]
            metadata = [
                {
                    "cid": int(row[0]),
                    "name": str(row[1]),
                    "declaredType": str(row[2]),
                    "notNull": bool(row[3]),
                    "default": row[4],
                    "primaryKeyOrder": int(row[5]),
                }
                for row in column_rows
            ]
            physical_rows: list[list[dict[str, object]]] = []
            logical_rows: list[list[dict[str, object]]] = []
            for values in connection.execute(f"SELECT * FROM {quoted}"):
                total_rows += 1
                if total_rows > max_rows:
                    raise ValueError("database row bound exceeded")
                row = dict(zip(columns, values, strict=True))
                physical_row = [
                    _encoded_sql_value(
                        value,
                        table=table,
                        column=column,
                        row=row,
                        logical=False,
                    )
                    for column, value in zip(columns, values, strict=True)
                ]
                logical_row = [
                    _encoded_sql_value(
                        value,
                        table=table,
                        column=column,
                        row=row,
                        logical=True,
                    )
                    for column, value in zip(columns, values, strict=True)
                ]
                encoded_bytes += len(_canonical_bytes({"row": physical_row}))
                encoded_bytes += len(_canonical_bytes({"row": logical_row}))
                if encoded_bytes > max_bytes:
                    raise ValueError("database byte bound exceeded")
                physical_rows.append(physical_row)
                logical_rows.append(logical_row)
            physical_rows.sort(key=lambda item: _canonical_bytes({"row": item}))
            logical_rows.sort(key=lambda item: _canonical_bytes({"row": item}))
            row_counts[table] = len(physical_rows)
            physical_tables.append(
                {"name": table, "columns": metadata, "rows": physical_rows}
            )
            logical_tables.append(
                {"name": table, "columns": metadata, "rows": logical_rows}
            )
        physical_bytes = _canonical_bytes(
            {
                "algorithm": _DATABASE_EFFECT_ALGORITHM,
                "schemaVersion": _DATABASE_EFFECT_SCHEMA_VERSION,
                "tables": physical_tables,
            }
        )
        logical_bytes = _canonical_bytes(
            {
                "algorithm": _DATABASE_EFFECT_ALGORITHM,
                "schemaVersion": _DATABASE_EFFECT_SCHEMA_VERSION,
                "normalization": {
                    "timeColumns": "non-null snake_case names ending _at",
                    "leaseColumns": "non-null names starting lease_",
                    "jsonTimeKeys": "non-null camelCase At or snake_case _at",
                    "jsonLeaseKeys": ["leaseOwner", "leaseEpoch", "lease_owner", "lease_epoch"],
                    "timeDerivedDigestFields": sorted(_TIME_DERIVED_DIGEST_FIELDS),
                },
                "tables": logical_tables,
            }
        )
        if len(physical_bytes) + len(logical_bytes) > max_bytes:
            raise ValueError("database byte bound exceeded")
        return DatabaseEffectSnapshot(
            physical_bytes=physical_bytes,
            logical_digest=f"sha256:{_sha256(logical_bytes)}",
            table_names=table_names,
            row_counts=row_counts,
        )


def _assert_no_database_effect(
    before: DatabaseEffectSnapshot, after: DatabaseEffectSnapshot
) -> None:
    if (
        before.physical_bytes != after.physical_bytes
        or before.table_names != after.table_names
        or before.row_counts != after.row_counts
        or before.logical_digest != after.logical_digest
    ):
        raise ValueError("duplicate changed durable database state")


def _one_json(connection: sqlite3.Connection, statement: str) -> dict[str, object]:
    row = connection.execute(statement).fetchone()
    if row is None:
        raise AcceptanceOracleError("required durable record is missing")
    document = json.loads(row[0])
    if not isinstance(document, dict):
        raise AcceptanceOracleError("durable record is not an object")
    return document


def _evidence_record(
    connection: sqlite3.Connection, kind: str
) -> tuple[str, str]:
    rows = connection.execute(
        "SELECT object_key, checksum FROM evidence_objects WHERE kind = ? ORDER BY object_key",
        (kind,),
    ).fetchall()
    if len(rows) != 1:
        raise AcceptanceOracleError(f"expected one {kind} evidence object")
    return str(rows[0][0]), str(rows[0][1])


def _evidence_path(state_root: Path, kind: str, object_key: str) -> Path:
    parts = PurePosixPath(object_key).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise AcceptanceOracleError("evidence key escapes its bounded store")
    path = state_root / "objects" / kind / Path(*parts[:-1]) / f"{parts[-1]}.json"
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to((state_root / "objects" / kind).resolve(strict=True)):
        raise AcceptanceOracleError("evidence key escapes its bounded store")
    return resolved


def _entity_mapping_inventory(checkout: Path) -> tuple[list[dict[str, str]], str]:
    descriptor = RepositoryCheckoutDescriptor(
        origin=ORIGIN,
        repository="spring-petclinic",
        revision=REVISION,
        checkout_root=checkout.resolve(strict=True),
        environment="staging",
        platform="postgres",
        system="petclinic",
        analyzer_pack="java-spring-data-jpa-v1",
        ruleset="spring-data-rules-v1",
    )
    snapshot = LocalGitRepositorySource(
        RepositorySourceLimits(
            max_files=10_000,
            max_file_bytes=4 * 1024 * 1024,
            max_total_bytes=128 * 1024 * 1024,
        )
    ).snapshot(descriptor)
    sources: list[JavaSpringSource] = []
    for path in snapshot.paths:
        posix = PurePosixPath(path)
        if path in {"pom.xml", "build.gradle", "build.gradle.kts"}:
            sources.append(JavaSpringSource(path, snapshot.read_bytes(path)))
        elif path.startswith("src/main/java/") and posix.suffix == ".java":
            sources.append(JavaSpringSource(path, snapshot.read_bytes(path)))
        elif path == _SCHEMA_PATH:
            sources.append(JavaSpringSource(path, snapshot.read_bytes(path), "postgres"))
    analysis = JavaSpringScaAnalyzer().analyze(tuple(sources))
    mappings: list[dict[str, str]] = []
    for fact in analysis.facts:
        if fact.kind != "spring.entity-table":
            continue
        attributes = dict(fact.attributes)
        mappings.append(
            {
                "entity": fact.subject.rsplit(".", 1)[-1],
                "table": str(attributes["table"]),
            }
        )
    return sorted(mappings, key=lambda item: (item["entity"], item["table"])), _sha256(
        snapshot.read_bytes(_SCHEMA_PATH)
    )


def _edge_summary(document: dict[str, object]) -> list[tuple[str, ...]]:
    edges = document.get("edges")
    if not isinstance(edges, list):
        raise AcceptanceOracleError("SCA evidence is missing edges")
    summary: list[tuple[str, ...]] = []
    for edge in edges:
        if not isinstance(edge, dict) or not isinstance(edge.get("evidence"), dict):
            raise AcceptanceOracleError("SCA edge evidence is malformed")
        evidence = edge["evidence"]
        invocations = evidence.get("invocations")
        if not isinstance(invocations, list) or len(invocations) != 1:
            raise AcceptanceOracleError("edge does not cite exactly one call site")
        invocation = invocations[0]
        if not isinstance(invocation, dict) or not isinstance(invocation.get("attributes"), list):
            raise AcceptanceOracleError("call-site citation is malformed")
        invocation_attributes = {
            str(item["name"]): str(item["value"])
            for item in invocation["attributes"]
            if isinstance(item, dict) and "name" in item and "value" in item
        }
        repository = evidence.get("repository")
        entity = evidence.get("entity")
        table = evidence.get("table")
        if not all(isinstance(value, dict) for value in (repository, entity, table)):
            raise AcceptanceOracleError("edge chain citation is malformed")
        edge_type = str(edge.get("edgeType"))
        service = edge.get("to") if edge_type == "READS" else (edge.get("from") or [None])[0]
        if not isinstance(service, str) or "/" not in service:
            raise AcceptanceOracleError("service identity is malformed")
        full_service = service.rsplit("/", 1)[-1]
        class_method = full_service.rsplit(".", 1)[-1]
        summary.append(
            (
                edge_type,
                str(repository["subject"]).rsplit(".", 1)[-1],
                str(entity["subject"]).rsplit(".", 1)[-1],
                str(table["subject"]),
                class_method,
                invocation_attributes["method"],
            )
        )
    return sorted(summary)


def _assert_equal(actual: object, expected: object, code: str) -> None:
    if actual != expected:
        raise AcceptanceOracleError(code)


def _build_manifest(
    checkout: Path,
    state_root: Path,
    first: dict[str, object],
    duplicate: dict[str, object],
    before_duplicate: DatabaseEffectSnapshot,
    after_duplicate: DatabaseEffectSnapshot,
) -> dict[str, object]:
    _assert_equal(first.get("outcome"), "ACCEPTED", "first delivery was not accepted")
    _assert_equal(first.get("commandStatus"), "COMPLETED", "command was not completed")
    _assert_equal(first.get("analysisStatus"), "COMPLETE", "analysis was incomplete")
    _assert_equal(first.get("runStatus"), "IN_REVIEW", "run did not stop in review")
    _assert_equal(first.get("proposalStatus"), "IN_REVIEW", "proposal did not stop in review")
    _assert_equal(first.get("runtimeStatus"), "NOT_PROVIDED", "runtime was fabricated")
    _assert_equal(first.get("stages"), _STAGES, "stage ledger differs from the oracle")
    _assert_equal(
        first.get("counts"),
        {"edges": 15, "reads": 10, "residue": 8, "unresolved": 0, "writes": 5},
        "lineage counts differ from the oracle",
    )
    coverage_summary = first.get("coverageManifest")
    if not isinstance(coverage_summary, dict):
        raise AcceptanceOracleError("coverage summary is missing")
    _assert_equal(
        coverage_summary.get("counts"),
        {"expected": 131, "completed": 33, "skipped": 98, "unsupported": 0, "failed": 0},
        "scope disposition differs from the oracle",
    )
    _assert_equal(coverage_summary.get("state"), "COMPLETE", "coverage is incomplete")
    _assert_equal(duplicate.get("outcome"), "DUPLICATE", "replay was not duplicate")
    for identity in ("commandId", "runId", "proposalId"):
        _assert_equal(duplicate.get(identity), first.get(identity), f"duplicate changed {identity}")
    duplicate_coverage = duplicate.get("coverageManifest")
    if not isinstance(duplicate_coverage, dict):
        raise AcceptanceOracleError("duplicate coverage is missing")
    _assert_equal(
        duplicate_coverage.get("manifestId"),
        coverage_summary.get("manifestId"),
        "duplicate changed coverage identity",
    )
    _assert_no_database_effect(before_duplicate, after_duplicate)

    database = state_root / "lineage.db"
    with sqlite3.connect(database) as connection:
        event = _one_json(connection, "SELECT payload_json FROM events")
        coverage = _one_json(connection, "SELECT payload_json FROM coverage_manifests")
        sca_key, sca_checksum = _evidence_record(connection, "sca")
    source = event.get("repositorySource")
    if not isinstance(source, dict):
        raise AcceptanceOracleError("exact repository determinant is missing")
    _assert_equal(source.get("origin"), ORIGIN, "source origin differs from the oracle")
    _assert_equal(source.get("revision"), REVISION, "source revision differs from the oracle")
    for key in ("scopeDigest", "scopeDispositionDigest"):
        if re.fullmatch(r"sha256:[0-9a-f]{64}", str(source.get(key))) is None:
            raise AcceptanceOracleError(f"{key} is not an exact digest")

    expected_scope = coverage.get("expectedScope")
    completed_scope = coverage.get("completedScope")
    skipped_scope = coverage.get("skippedScope")
    unsupported_scope = coverage.get("unsupportedScope")
    failed_scope = coverage.get("failedScope")
    dispositions = (completed_scope, skipped_scope, unsupported_scope, failed_scope)
    if not isinstance(expected_scope, list) or not all(isinstance(item, list) for item in dispositions):
        raise AcceptanceOracleError("coverage scope lists are malformed")
    _assert_equal(len(expected_scope), 131, "tracked scope changed")
    _assert_equal(sum(len(item) for item in dispositions), 131, "scope has silent loss")
    _assert_equal(
        set(expected_scope),
        set().union(*(set(item) for item in dispositions)),
        "scope has overlap or silent loss",
    )

    sca_path = _evidence_path(state_root, "sca", sca_key)
    sca_bytes = _verified_evidence_bytes(sca_path, sca_checksum)
    sca_document = json.loads(sca_bytes)
    if not isinstance(sca_document, dict):
        raise AcceptanceOracleError("SCA evidence is not an object")
    edges = _edge_summary(sca_document)
    _assert_equal(edges, _EDGE_ORACLE, "edge oracle mismatch or false edge present")
    mappings, schema_checksum = _entity_mapping_inventory(checkout)
    _assert_equal(mappings, _ENTITY_MAPPINGS, "entity mapping inventory changed")
    residue = sca_document.get("residue")
    if not isinstance(residue, list):
        raise AcceptanceOracleError("residue is missing")
    residue_codes = Counter(
        str(item.get("code")) for item in residue if isinstance(item, dict)
    )
    _assert_equal(residue_codes, Counter({"ignored-schema-statement": 8}), "residue oracle changed")
    analyzer_coverage = sca_document.get("coverage")
    if not isinstance(analyzer_coverage, dict):
        raise AcceptanceOracleError("analyzer coverage is missing")
    _assert_equal(analyzer_coverage.get("invocationsUnresolved"), 0, "unresolved calls remain")

    edge_records = [
        {
            "edgeType": edge_type,
            "repository": repository,
            "entity": entity,
            "table": table,
            "serviceCall": service,
            "repositoryMethod": method,
        }
        for edge_type, repository, entity, table, service, method in edges
    ]
    return {
        "schemaVersion": "1.0.0",
        "evidenceClass": "LOCAL_REAL_REPOSITORY_PASS",
        "finalOutcome": "PASS",
        "source": {"origin": ORIGIN, "repository": "spring-petclinic", "revision": REVISION},
        "determinants": {
            "analyzerPack": source["analyzerPack"],
            "ruleset": source["ruleset"],
            "schemaProfile": source["schemaProfile"],
            "scopeDigest": source["scopeDigest"],
            "scopeDispositionDigest": source["scopeDispositionDigest"],
            "determinantDigest": first["determinantDigest"],
            "resolverVersion": sca_document["resolverVersion"],
            "catalog": {
                "mode": "PINNED_PROFILE_SCHEMA",
                "profile": "postgres",
                "schemaDigest": f"sha256:{schema_checksum}",
            },
        },
        "identities": {
            "commandId": first["commandId"],
            "runId": first["runId"],
            "proposalId": first["proposalId"],
            "coverageManifestId": coverage_summary["manifestId"],
        },
        "stages": _STAGES,
        "counts": {"edges": 15, "reads": 10, "writes": 5, "residue": 8, "unresolved": 0},
        "coverage": {
            "state": "COMPLETE",
            "counts": coverage_summary["counts"],
            "analyzer": analyzer_coverage,
        },
        "oracle": {
            "entityMappings": mappings,
            "repositoryChains": _REPOSITORY_CHAINS,
            "serviceComponents": [
                "OwnerController",
                "PetController",
                "PetTypeFormatter",
                "VisitController",
                "VetController",
            ],
            "edges": edge_records,
            "falseEdges": 0,
        },
        "residue": {"count": 8, "codes": {"ignored-schema-statement": 8}},
        "evidenceDigests": {
            "sca": {"checksum": f"sha256:{sca_checksum}", "schemaVersion": "1.0.0"},
            "profileSchema": {"checksum": f"sha256:{schema_checksum}"},
        },
        "runtimeStatus": "NOT_PROVIDED",
        "awsStatus": "AWS_REQUIRED",
        "productionCollection": "OFF",
        "databaseEffectProof": {
            "algorithm": _DATABASE_EFFECT_ALGORITHM,
            "schemaVersion": _DATABASE_EFFECT_SCHEMA_VERSION,
            "normalization": {
                "timeColumns": "non-null snake_case names ending _at",
                "leaseColumns": "non-null names starting lease_",
                "jsonTimeKeys": "non-null camelCase At or snake_case _at",
                "jsonLeaseKeys": [
                    "leaseOwner",
                    "leaseEpoch",
                    "lease_owner",
                    "lease_epoch",
                ],
                "timeDerivedDigestFields": sorted(_TIME_DERIVED_DIGEST_FIELDS),
            },
            "tableCount": len(before_duplicate.table_names),
            "tableNames": list(before_duplicate.table_names),
            "logicalDigestBefore": before_duplicate.logical_digest,
            "logicalDigestAfter": after_duplicate.logical_digest,
            "physicalStateEqual": True,
            "rowCountsBefore": before_duplicate.row_counts,
            "rowCountsAfter": after_duplicate.row_counts,
        },
        "duplicate": {
            "outcome": "DUPLICATE",
            "sameIdentities": True,
            "noDurableEffects": True,
        },
    }


def _execute_acceptance(checkout: Path, output_root: Path) -> dict[str, object]:
    state_root = output_root / "state"
    if (state_root / "lineage.db").exists():
        raise AcceptanceOracleError("acceptance state must be fresh")
    first = _run_collect_checkout(checkout, state_root)
    before_duplicate = _database_effect_snapshot(state_root / "lineage.db")
    duplicate = _run_collect_checkout(checkout, state_root)
    after_duplicate = _database_effect_snapshot(state_root / "lineage.db")
    manifest = _build_manifest(
        checkout,
        state_root,
        first,
        duplicate,
        before_duplicate,
        after_duplicate,
    )
    manifest_path = _write_content_addressed_manifest(output_root / "java-spring", manifest)
    verified = _verify_content_addressed_manifest(manifest_path)
    content = manifest_path.read_bytes()
    return {
        "manifest": verified,
        "manifestBytes": content,
        "manifestChecksum": f"sha256:{_sha256(content)}",
        "manifestPath": manifest_path,
    }


def _safe_output_root() -> Path:
    run_id = os.environ.get("LINEAGE_ACCEPTANCE_RUN_ID", "local-real-repository")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", run_id) is None:
        raise AcceptanceSourceError("INVALID_ACCEPTANCE_RUN_ID")
    configured = os.environ.get("LINEAGE_ACCEPTANCE_OUTPUT")
    output = Path(configured) if configured else ROOT / "data" / "acceptance" / run_id
    expected_root = (ROOT / "data" / "acceptance").resolve()
    resolved = output.resolve()
    if not resolved.is_relative_to(expected_root):
        raise AcceptanceSourceError("INVALID_ACCEPTANCE_OUTPUT")
    return resolved


def _bounded_failure(outcome: str, reason_code: str) -> None:
    evidence_class = (
        "LOCAL_REAL_REPOSITORY_REQUIRED"
        if outcome == "INTEGRATION_REQUIRED"
        else "LOCAL_REAL_REPOSITORY_FAIL"
    )
    print(
        json.dumps(
            {"evidenceClass": evidence_class, "outcome": outcome, "reasonCode": reason_code},
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _main() -> int:
    checkout_value = os.environ.get("LINEAGE_REAL_REPOSITORY_CHECKOUT")
    if not checkout_value:
        _bounded_failure("INTEGRATION_REQUIRED", "LINEAGE_REAL_REPOSITORY_CHECKOUT_REQUIRED")
        return 2
    try:
        checkout = Path(checkout_value)
        try:
            if checkout.is_symlink() or not checkout.resolve(strict=True).is_dir():
                raise AcceptanceSourceError("SOURCE_VALIDATION_FAILED")
        except OSError as error:
            raise AcceptanceSourceError("SOURCE_VALIDATION_FAILED") from error
        output_root = _safe_output_root()
        with tempfile.TemporaryDirectory(prefix="lineage-real-repository-") as temporary:
            root = Path(temporary)
            first = _execute_acceptance(checkout, root / "first")
            second = _execute_acceptance(checkout, root / "second")
            _assert_equal(
                first["manifestChecksum"],
                second["manifestChecksum"],
                "fresh-state manifest checksums differ",
            )
            _assert_equal(
                first["manifestBytes"],
                second["manifestBytes"],
                "fresh-state manifest bytes differ",
            )
            manifest = first["manifest"]
            if not isinstance(manifest, dict):
                raise AcceptanceOracleError("manifest is not an object")
            retained = _write_content_addressed_manifest(output_root / "java-spring", manifest)
            _verify_content_addressed_manifest(retained)
            relative = retained.relative_to(ROOT).as_posix()
            summary = {
                "counts": manifest["counts"],
                "evidenceClass": "LOCAL_REAL_REPOSITORY_PASS",
                "manifestChecksum": first["manifestChecksum"],
                "manifestPath": relative,
                "outcome": "PASS",
                "runtimeStatus": "NOT_PROVIDED",
            }
            print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
            return 0
    except AcceptanceSourceError:
        _bounded_failure("INTEGRATION_REQUIRED", "SOURCE_VALIDATION_FAILED")
        return 2
    except (AcceptanceOracleError, OSError, ValueError, subprocess.SubprocessError):
        _bounded_failure("FAIL", "PIPELINE_OR_ORACLE_FAILED")
        return 2


def test_content_addressed_manifest_is_canonical_write_once_and_tamper_evident(
    tmp_path: Path,
) -> None:
    manifest = {
        "schemaVersion": "1.0.0",
        "evidenceClass": "LOCAL_REAL_REPOSITORY_PASS",
        "source": {"origin": ORIGIN, "revision": REVISION},
        "outcome": "PASS",
    }

    first = _write_content_addressed_manifest(tmp_path, manifest)
    second = _write_content_addressed_manifest(tmp_path, manifest)
    assert first == second
    assert _verify_content_addressed_manifest(first) == manifest
    assert first.name == f"sha256-{_sha256(_canonical_bytes(manifest))}.json"

    first.write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="checksum"):
        _verify_content_addressed_manifest(first)


def test_content_addressed_manifest_refuses_identity_conflict(tmp_path: Path) -> None:
    manifest = {
        "schemaVersion": "1.0.0",
        "evidenceClass": "LOCAL_REAL_REPOSITORY_PASS",
        "outcome": "PASS",
    }
    path = _write_content_addressed_manifest(tmp_path, manifest)
    path.write_bytes(_canonical_bytes({**manifest, "outcome": "FAIL"}))

    with pytest.raises(ValueError, match="conflict"):
        _write_content_addressed_manifest(tmp_path, manifest)


def test_retained_evidence_checksum_detects_tampering(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(b'{"status":"COMPLETE"}\n')
    checksum = _sha256(evidence.read_bytes())
    assert _verified_evidence_bytes(evidence, checksum) == evidence.read_bytes()

    evidence.write_bytes(b'{"status":"TAMPERED"}\n')
    with pytest.raises(ValueError, match="evidence checksum"):
        _verified_evidence_bytes(evidence, checksum)


def test_collect_checkout_rejects_wrong_origin_and_revision(tmp_path: Path) -> None:
    checkout = tmp_path / "wrong-source"
    checkout.mkdir()
    subprocess.run(["git", "init", "--quiet", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.email", "lineage@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.name", "Lineage Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "remote", "add", "origin", ORIGIN],
        check=True,
    )
    (checkout / "README.md").write_text("source boundary\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(checkout), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "commit", "--quiet", "-m", "fixture"],
        check=True,
    )
    revision = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    with pytest.raises(ValueError, match="SOURCE_VALIDATION_FAILED"):
        _run_collect_checkout(
            checkout,
            tmp_path / "wrong-origin-state",
            origin="https://example.com/wrong/repository",
            revision=revision,
        )
    with pytest.raises(ValueError, match="SOURCE_VALIDATION_FAILED"):
        _run_collect_checkout(
            checkout,
            tmp_path / "wrong-revision-state",
            origin=ORIGIN,
            revision="0" * 40,
        )


def _fake_uv(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "bin" / "uv"
    executable.parent.mkdir()
    executable.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    executable.chmod(0o755)
    return executable


def test_dedicated_runner_uses_offline_frozen_preprovisioned_environment(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    arguments = tmp_path / "uv-arguments"
    _fake_uv(
        tmp_path,
        "printf '%s\\n' \"$@\" > \"$UV_ARGS_FILE\"\n"
        "printf '%s\\n' '{\"evidenceClass\":\"LOCAL_REAL_REPOSITORY_PASS\",\"outcome\":\"PASS\"}'",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "LINEAGE_REAL_REPOSITORY_CHECKOUT": str(checkout),
            "PATH": f"{tmp_path / 'bin'}:{os.defpath}",
            "UV_ARGS_FILE": str(arguments),
        }
    )

    completed = subprocess.run(
        [str(SCRIPT)], cwd=ROOT, env=environment, capture_output=True, text=True, check=False
    )

    assert completed.returncode == 0
    assert arguments.read_text(encoding="utf-8").splitlines() == [
        "run",
        "--offline",
        "--frozen",
        "--no-sync",
        "--project",
        "apps/api",
        "--extra",
        "dev",
        "python",
        "tests/integration/test_spring_petclinic_repository.py",
    ]


def test_dedicated_runner_reports_missing_preprovisioned_environment_without_leak(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "secret-checkout"
    checkout.mkdir()
    _fake_uv(tmp_path, "echo \"dependency failure $LINEAGE_REAL_REPOSITORY_CHECKOUT\" >&2\nexit 1")
    environment = os.environ.copy()
    environment.update(
        {
            "LINEAGE_REAL_REPOSITORY_CHECKOUT": str(checkout),
            "PATH": f"{tmp_path / 'bin'}:{os.defpath}",
        }
    )

    completed = subprocess.run(
        [str(SCRIPT)], cwd=ROOT, env=environment, capture_output=True, text=True, check=False
    )

    assert completed.returncode == 2
    assert str(checkout) not in completed.stdout
    assert str(checkout) not in completed.stderr
    assert len(completed.stdout.encode()) < 1_024
    assert json.loads(completed.stdout) == {
        "evidenceClass": "LOCAL_REAL_REPOSITORY_REQUIRED",
        "outcome": "INTEGRATION_REQUIRED",
        "reasonCode": "RUNNER_ENVIRONMENT_UNAVAILABLE",
    }


def _effect_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            '''
            CREATE TABLE classification_decisions (
                decision_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                decided_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE audit_events (
                audit_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE "odd""table" (
                id INTEGER PRIMARY KEY,
                content BLOB NOT NULL
            );
            INSERT INTO classification_decisions VALUES
                ('decision-1', 'COMPLETE', '2026-01-01T00:00:00Z',
                 '{"createdAt":"2026-01-01T00:00:00Z","value":"stable"}');
            INSERT INTO audit_events VALUES
                ('audit-1', 'CREATE', '2026-01-01T00:00:00Z');
            INSERT INTO "odd""table" VALUES (1, X'000102');
            '''
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE classification_decisions SET status = 'CHANGED' WHERE decision_id = 'decision-1'",
        "INSERT INTO audit_events VALUES ('audit-2', 'CREATE', '2026-01-01T00:00:00Z')",
        "DELETE FROM audit_events WHERE audit_id = 'audit-1'",
        "CREATE TABLE newly_added (id INTEGER PRIMARY KEY)",
        'DROP TABLE "odd""table"',
    ],
)
def test_database_effect_proof_detects_every_table_and_mutation(
    tmp_path: Path, mutation: str
) -> None:
    database = tmp_path / "effects.db"
    _effect_database(database)
    before = _database_effect_snapshot(database)
    with sqlite3.connect(database) as connection:
        connection.execute(mutation)
    after = _database_effect_snapshot(database)

    assert "classification_decisions" in before.table_names
    assert 'odd"table' in before.table_names
    with pytest.raises(ValueError, match="duplicate changed durable database state"):
        _assert_no_database_effect(before, after)


def test_database_effect_logical_digest_normalizes_only_documented_volatility(
    tmp_path: Path,
) -> None:
    first_database = tmp_path / "first.db"
    second_database = tmp_path / "second.db"
    _effect_database(first_database)
    _effect_database(second_database)
    with sqlite3.connect(second_database) as connection:
        connection.execute(
            "UPDATE classification_decisions SET decided_at = ?, payload_json = ?",
            (
                "2027-02-02T00:00:00Z",
                '{"createdAt":"2027-02-02T00:00:00Z","value":"stable"}',
            ),
        )
        connection.execute(
            "UPDATE audit_events SET created_at = '2027-02-02T00:00:00Z'"
        )
    first = _database_effect_snapshot(first_database)
    second = _database_effect_snapshot(second_database)

    assert first.physical_bytes != second.physical_bytes
    assert first.logical_digest == second.logical_digest

    with sqlite3.connect(second_database) as connection:
        connection.execute(
            "UPDATE classification_decisions SET payload_json = ?",
            ('{"createdAt":"2027-02-02T00:00:00Z","value":"changed"}',),
        )
    changed = _database_effect_snapshot(second_database)
    assert changed.logical_digest != first.logical_digest


def test_database_effect_snapshot_enforces_table_row_and_byte_bounds(tmp_path: Path) -> None:
    database = tmp_path / "bounded.db"
    _effect_database(database)

    with pytest.raises(ValueError, match="table bound"):
        _database_effect_snapshot(database, max_tables=2)
    with pytest.raises(ValueError, match="row bound"):
        _database_effect_snapshot(database, max_rows=2)
    with pytest.raises(ValueError, match="byte bound"):
        _database_effect_snapshot(database, max_bytes=64)


def test_dedicated_runner_without_checkout_is_integration_required() -> None:
    environment = os.environ.copy()
    environment.pop("LINEAGE_REAL_REPOSITORY_CHECKOUT", None)
    completed = subprocess.run(
        [str(SCRIPT)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 2
    result = json.loads(completed.stdout)
    assert result == {
        "evidenceClass": "LOCAL_REAL_REPOSITORY_REQUIRED",
        "outcome": "INTEGRATION_REQUIRED",
        "reasonCode": "LINEAGE_REAL_REPOSITORY_CHECKOUT_REQUIRED",
    }


def test_dedicated_runner_bounds_failure_and_does_not_leak_checkout_path(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "secret-checkout-marker"
    environment = os.environ.copy()
    environment["LINEAGE_REAL_REPOSITORY_CHECKOUT"] = str(checkout)
    completed = subprocess.run(
        [str(SCRIPT)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 2
    assert len(completed.stdout.encode()) < 1_024
    assert str(checkout) not in completed.stdout
    result = json.loads(completed.stdout)
    assert result["outcome"] == "INTEGRATION_REQUIRED"
    assert result["reasonCode"] == "SOURCE_VALIDATION_FAILED"


@pytest.mark.skipif(
    not os.environ.get("LINEAGE_REAL_REPOSITORY_CHECKOUT"),
    reason="INTEGRATION_REQUIRED: set LINEAGE_REAL_REPOSITORY_CHECKOUT to the pinned official Spring Petclinic checkout",
)
def test_real_spring_petclinic_repository_acceptance(tmp_path: Path) -> None:
    checkout = Path(os.environ["LINEAGE_REAL_REPOSITORY_CHECKOUT"])
    first = _execute_acceptance(checkout, tmp_path / "first")
    second = _execute_acceptance(checkout, tmp_path / "second")

    assert first["manifestChecksum"] == second["manifestChecksum"]
    assert first["manifestBytes"] == second["manifestBytes"]
    manifest = first["manifest"]
    assert manifest["source"] == {
        "origin": ORIGIN,
        "repository": "spring-petclinic",
        "revision": REVISION,
    }
    assert manifest["counts"]["edges"] == 15
    assert manifest["counts"]["reads"] == 10
    assert manifest["counts"]["writes"] == 5
    assert manifest["counts"]["unresolved"] == 0
    assert manifest["coverage"]["counts"] == {
        "expected": 131,
        "completed": 33,
        "skipped": 98,
        "unsupported": 0,
        "failed": 0,
    }
    database_proof = manifest["databaseEffectProof"]
    assert database_proof["algorithm"] == "sqlite-canonical-relational-snapshot-v1"
    assert database_proof["schemaVersion"] == "1.0.0"
    assert database_proof["tableCount"] == 33
    assert len(database_proof["tableNames"]) == 33
    assert database_proof["logicalDigestBefore"] == database_proof["logicalDigestAfter"]
    assert database_proof["physicalStateEqual"] is True
    assert database_proof["rowCountsBefore"] == database_proof["rowCountsAfter"]
    assert manifest["runtimeStatus"] == "NOT_PROVIDED"
    assert manifest["finalOutcome"] == "PASS"


if __name__ == "__main__":
    raise SystemExit(_main())
