from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Protocol

from lineage_api.application.classification import ClassificationEvidence
from lineage_api.application.repository_sources import RepositorySnapshot
from lineage_api.services.java_spring_sca import (
    JavaSpringEvidenceCompiler,
    JavaSpringEvidenceContext,
    JavaSpringScaAnalyzer,
    JavaSpringSource,
)
from lineage_api.services.resolver import ResolveContext, Resolver
from lineage_api.services.sca import ScaAnalyzer
from lineage_api.services.sql_transformation_sca import (
    SqlTransformationSource,
    analyze_sql_sources,
    compile_derivation_edges,
)


_MAX_SELECTION_TEXT = 128


class AnalyzerSelectionError(ValueError):
    """A bounded, typed failure at the closed analyzer-selection boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message[:160])
        self.code = code


class AnalyzerSnapshot(Protocol):
    repository: str
    revision: str
    scope_digest: str
    environment: str
    platform: str
    system: str
    analyzer_pack: str
    ruleset: str
    paths: tuple[str, ...]

    def read_bytes(self, relative_path: str) -> bytes: ...


class AnalyzerSnapshotProvider(Protocol):
    def resolve(self, envelope: dict[str, Any]) -> AnalyzerSnapshot: ...


@dataclass(frozen=True, slots=True)
class AnalyzerSelection:
    analyzer_pack: str
    ruleset: str
    source_kind: str
    framework: str
    schema_profile: str

    def __post_init__(self) -> None:
        for name in (
            "analyzer_pack",
            "ruleset",
            "source_kind",
            "framework",
            "schema_profile",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or len(value) > _MAX_SELECTION_TEXT
                or any(ord(character) < 32 for character in value)
            ):
                raise AnalyzerSelectionError(
                    "INVALID_ANALYZER_SELECTION",
                    "analyzer selection contains an invalid bounded determinant",
                )

    @classmethod
    def from_envelope(cls, envelope: dict[str, Any]) -> "AnalyzerSelection":
        source = envelope.get("repositorySource")
        if source is None:
            return cls(
                "python-fixture-v1",
                "python-demo-v1",
                "fixture",
                "python-dataset-api",
                "snowflake",
            )
        if not isinstance(source, dict):
            raise AnalyzerSelectionError(
                "INVALID_SOURCE_DESCRIPTOR",
                "repository source descriptor must be an object",
            )
        try:
            return cls(
                analyzer_pack=source["analyzerPack"],
                ruleset=source["ruleset"],
                source_kind=source["sourceKind"],
                framework=source["framework"],
                schema_profile=source["schemaProfile"],
            )
        except (KeyError, TypeError):
            raise AnalyzerSelectionError(
                "INVALID_SOURCE_DESCRIPTOR",
                "repository source descriptor is missing a required determinant",
            ) from None


@dataclass(frozen=True, slots=True)
class AnalyzerSourceScope:
    expected_scope: tuple[str, ...]
    selected_scope: tuple[str, ...]
    skipped_scope: tuple[str, ...]
    unsupported_scope: tuple[str, ...]
    failed_scope: tuple[str, ...] = ()
    schema_version: str = "1.0.0"

    @property
    def disposition_digest(self) -> str:
        body = json.dumps(
            {
                "schemaVersion": self.schema_version,
                "expectedScope": list(self.expected_scope),
                "selectedScope": list(self.selected_scope),
                "skippedScope": list(self.skipped_scope),
                "unsupportedScope": list(self.unsupported_scope),
                "failedScope": list(self.failed_scope),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return f"sha256:{hashlib.sha256(body).hexdigest()}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "scopeDispositionDigest": self.disposition_digest,
            "expectedScope": list(self.expected_scope),
            "selectedScope": list(self.selected_scope),
            "recomputedScope": list(self.selected_scope),
            "skippedScope": list(self.skipped_scope),
            "unsupportedScope": list(self.unsupported_scope),
            "failedScope": list(self.failed_scope),
        }


@dataclass(frozen=True, slots=True)
class AnalyzerRunResult:
    document: dict[str, Any]
    status: str
    status_reasons: tuple[str, ...]
    edge_count: int
    read_count: int
    write_count: int
    residue_count: int
    unresolved_count: int


@dataclass(frozen=True, slots=True)
class AnalyzerDefinition:
    analyzer_pack: str
    ruleset: str
    source_kind: str
    framework: str
    schema_profiles: tuple[str, ...]
    resolver_version: str
    snapshot_id: str
    analyze: Callable[[AnalyzerSnapshot, str, str, str], AnalyzerRunResult] | None


class AnalyzerRegistry:
    def __init__(self, definitions: tuple[AnalyzerDefinition, ...]) -> None:
        packs = tuple(item.analyzer_pack for item in definitions)
        if len(packs) != len(set(packs)):
            raise ValueError("analyzer registry contains duplicate packs")
        self._definitions = tuple(sorted(definitions, key=lambda item: item.analyzer_pack))

    @classmethod
    def default(
        cls,
        python_analyzer: ScaAnalyzer | None = None,
        sql_resolver: Resolver | None = None,
    ) -> "AnalyzerRegistry":
        python_handler = (
            _PythonAnalyzerAdapter(python_analyzer).analyze
            if python_analyzer is not None
            else None
        )
        sql_handler = (
            _SqlTransformationAnalyzerAdapter(sql_resolver).analyze
            if sql_resolver is not None
            else None
        )
        return cls(
            (
                AnalyzerDefinition(
                    "python-fixture-v1",
                    "python-demo-v1",
                    "fixture",
                    "python-dataset-api",
                    ("snowflake",),
                    (
                        python_analyzer.resolver_version
                        if python_analyzer is not None
                        else "catalog-resolver-v1"
                    ),
                    (
                        python_analyzer.snapshot_id
                        if python_analyzer is not None
                        else "catalog-snapshot-v1"
                    ),
                    python_handler,
                ),
                AnalyzerDefinition(
                    "java-spring-data-jpa-v1",
                    "spring-data-rules-v1",
                    "git-checkout",
                    "spring-data-jpa",
                    ("h2", "mysql", "postgres"),
                    "schema-resolver-v1",
                    "repository-scope-v1",
                    _JavaSpringAnalyzerAdapter().analyze,
                ),
                AnalyzerDefinition(
                    "sql-transformation-v1",
                    "sql-transformation-rules-v1",
                    "git-checkout",
                    "sql-transformation",
                    ("postgres", "snowflake"),
                    (
                        sql_resolver.resolver_version
                        if sql_resolver is not None
                        else "catalog-resolver-v1"
                    ),
                    (
                        sql_resolver.snapshot_id
                        if sql_resolver is not None
                        else "catalog-snapshot-v1"
                    ),
                    sql_handler,
                ),
            )
        )

    def resolve(self, selection: AnalyzerSelection) -> AnalyzerDefinition:
        definition = next(
            (
                item
                for item in self._definitions
                if item.analyzer_pack == selection.analyzer_pack
            ),
            None,
        )
        if definition is None:
            raise AnalyzerSelectionError(
                "UNKNOWN_ANALYZER_PACK", "analyzer pack is outside the closed registry"
            )
        checks = (
            (
                selection.ruleset == definition.ruleset,
                "RULESET_MISMATCH",
                "ruleset does not match the selected analyzer pack",
            ),
            (
                selection.source_kind == definition.source_kind,
                "SOURCE_KIND_MISMATCH",
                "source kind does not match the selected analyzer pack",
            ),
            (
                selection.framework == definition.framework,
                "FRAMEWORK_MISMATCH",
                "framework does not match the selected analyzer pack",
            ),
            (
                selection.schema_profile in definition.schema_profiles,
                "PROFILE_MISMATCH",
                "schema profile is outside the analyzer compatibility cell",
            ),
        )
        for valid, code, message in checks:
            if not valid:
                raise AnalyzerSelectionError(code, message)
        return definition

    def analyze(
        self,
        snapshot: AnalyzerSnapshot,
        selection: AnalyzerSelection,
        run_id: str,
        correlation_id: str,
    ) -> AnalyzerRunResult:
        definition = self.resolve(selection)
        if snapshot.analyzer_pack != selection.analyzer_pack:
            raise AnalyzerSelectionError(
                "SOURCE_PACK_MISMATCH", "snapshot pack does not match durable selection"
            )
        if snapshot.ruleset != selection.ruleset:
            raise AnalyzerSelectionError(
                "SOURCE_RULESET_MISMATCH", "snapshot ruleset does not match durable selection"
            )
        if definition.analyze is None:
            raise AnalyzerSelectionError(
                "ANALYZER_NOT_CONFIGURED", "selected analyzer is not configured"
            )
        source_scope = self.source_scope(snapshot, selection)
        result = definition.analyze(
            _ScopedAnalyzerSnapshot(snapshot, source_scope.selected_scope),
            selection.schema_profile,
            run_id,
            correlation_id,
        )
        blocking_reasons = set(result.status_reasons)
        if source_scope.unsupported_scope:
            blocking_reasons.add("unsupported-source-scope")
        if source_scope.failed_scope:
            blocking_reasons.add("failed-source-scope")
        if not blocking_reasons:
            return result
        document = {
            **result.document,
            "status": "INTEGRATION_REQUIRED",
            "statusReasons": sorted(blocking_reasons),
            "sourceScopeDispositionDigest": source_scope.disposition_digest,
        }
        return AnalyzerRunResult(
            document=document,
            status="INTEGRATION_REQUIRED",
            status_reasons=tuple(sorted(blocking_reasons)),
            edge_count=result.edge_count,
            read_count=result.read_count,
            write_count=result.write_count,
            residue_count=result.residue_count,
            unresolved_count=result.unresolved_count,
        )

    def source_scope(
        self,
        snapshot: AnalyzerSnapshot,
        selection: AnalyzerSelection,
    ) -> AnalyzerSourceScope:
        self.resolve(selection)
        expected = tuple(sorted(snapshot.paths))
        if selection.analyzer_pack == "python-fixture-v1":
            selected = tuple(
                path for path in expected if PurePosixPath(path).suffix == ".py"
            )
            skipped = tuple(
                path
                for path in expected
                if PurePosixPath(path).suffix == ".md"
                or PurePosixPath(path).name
                in {"expected-lineage.json", "repository-evidence.json"}
            )
            unsupported = tuple(
                path
                for path in expected
                if path not in set(selected) and path not in set(skipped)
            )
        elif selection.analyzer_pack == "sql-transformation-v1":
            selected = tuple(
                path
                for path in expected
                if PurePosixPath(path).suffix == ".sql"
                and PurePosixPath(path).parts[:1] == ("sql",)
            )
            skipped = tuple(
                path
                for path in expected
                if PurePosixPath(path).suffix == ".md"
                or PurePosixPath(path).name == "expected-lineage.json"
            )
            unsupported = tuple(
                path
                for path in expected
                if path not in set(selected) and path not in set(skipped)
            )
        else:
            selected_paths: list[str] = []
            skipped_paths: list[str] = []
            unsupported_paths: list[str] = []
            for path in expected:
                disposition = _java_source_disposition(path, selection.schema_profile)
                if disposition == "selected":
                    selected_paths.append(path)
                elif disposition == "skipped":
                    skipped_paths.append(path)
                else:
                    unsupported_paths.append(path)
            selected = tuple(selected_paths)
            skipped = tuple(skipped_paths)
            unsupported = tuple(unsupported_paths)
        return AnalyzerSourceScope(
            expected_scope=expected,
            selected_scope=selected,
            skipped_scope=skipped,
            unsupported_scope=unsupported,
        )

    def classification_evidence(
        self, snapshot: AnalyzerSnapshot, selection: AnalyzerSelection
    ) -> list[ClassificationEvidence]:
        definition = self.resolve(selection)
        repository_class = (
            "APPLICATION_RUNTIME"
            if definition.framework == "spring-data-jpa"
            else "DATA_PIPELINE"
        )
        return [
            ClassificationEvidence(
                level=4,
                source="closed_analyzer_selection",
                repository_class=repository_class,
                ref=f"source-scope://{snapshot.scope_digest}",
            )
        ]

    def pins(
        self, snapshot: AnalyzerSnapshot, selection: AnalyzerSelection
    ) -> dict[str, str]:
        definition = self.resolve(selection)
        return {
            "catalogSnapshotId": (
                snapshot.scope_digest
                if definition.source_kind == "git-checkout"
                else definition.snapshot_id
            ),
            "resolverVersion": definition.resolver_version,
            "rulesetVersion": definition.ruleset,
        }

    def baseline_plan(
        self,
        snapshot: AnalyzerSnapshot,
        selection: AnalyzerSelection,
        *,
        max_fanout: int,
    ) -> dict[str, Any]:
        if max_fanout < 1:
            raise ValueError("max fanout must be positive")
        source_scope = self.source_scope(snapshot, selection)
        if len(source_scope.expected_scope) > max_fanout:
            raise ValueError("baseline repository scope exceeds the fanout limit")
        plan = source_scope.as_dict()
        return {
            **plan,
            "fanout": [
                {
                    "pack": selection.analyzer_pack,
                    "paths": plan["recomputedScope"],
                }
            ]
            if plan["recomputedScope"]
            else [],
        }


class _ScopedAnalyzerSnapshot:
    def __init__(self, snapshot: AnalyzerSnapshot, paths: tuple[str, ...]) -> None:
        self._snapshot = snapshot
        self.paths = paths

    def __getattr__(self, name: str) -> Any:
        return getattr(self._snapshot, name)

    def read_bytes(self, relative_path: str) -> bytes:
        if relative_path not in self.paths:
            raise AnalyzerSelectionError(
                "SOURCE_SCOPE_MISMATCH", "path is outside the selected analyzer scope"
            )
        return self._snapshot.read_bytes(relative_path)


@dataclass(frozen=True, slots=True)
class _FixtureSnapshot:
    repository: str
    revision: str
    scope_digest: str
    environment: str
    platform: str
    system: str
    analyzer_pack: str
    ruleset: str
    paths: tuple[str, ...]
    _root: Path

    def read_bytes(self, relative_path: str) -> bytes:
        if relative_path not in self.paths:
            raise AnalyzerSelectionError(
                "SOURCE_SCOPE_MISMATCH", "path is outside the fixture snapshot"
            )
        return (self._root / relative_path).read_bytes()


class FixtureSnapshotProvider:
    def __init__(self, fixture_root: Path) -> None:
        self._fixture_root = fixture_root

    def resolve(self, envelope: dict[str, Any]) -> AnalyzerSnapshot:
        if envelope.get("repositorySource") is not None:
            raise AnalyzerSelectionError(
                "SOURCE_KIND_MISMATCH", "exact checkout requires the configured source adapter"
            )
        root = self._fixture_root / "repositories" / str(envelope["repo"])
        paths = tuple(sorted(set(str(item) for item in envelope["changedFiles"])))
        if not paths and envelope.get("eventType") in {"baseline", "backfill"}:
            paths = tuple(
                sorted(
                    path.relative_to(root).as_posix()
                    for path in root.rglob("*")
                    if path.is_file()
                )
            )
        scope = hashlib.sha256(b"fixture-source-v1\0")
        for path in paths:
            body = (root / path).read_bytes()
            encoded = path.encode()
            scope.update(len(encoded).to_bytes(8, "big"))
            scope.update(encoded)
            scope.update(len(body).to_bytes(8, "big"))
            scope.update(body)
        return _FixtureSnapshot(
            repository=str(envelope["repo"]),
            revision=str(envelope["digest"]),
            scope_digest=f"sha256:{scope.hexdigest()}",
            environment=str(envelope["env"]),
            platform="snowflake",
            system=str(envelope["system"]),
            analyzer_pack="python-fixture-v1",
            ruleset="python-demo-v1",
            paths=paths,
            _root=root,
        )


class PinnedSnapshotProvider:
    def __init__(self, snapshot: RepositorySnapshot) -> None:
        self._snapshot = snapshot

    def resolve(self, envelope: dict[str, Any]) -> AnalyzerSnapshot:
        source = envelope.get("repositorySource")
        if not isinstance(source, dict):
            raise AnalyzerSelectionError(
                "INVALID_SOURCE_DESCRIPTOR", "exact checkout source descriptor is required"
            )
        expected = {
            "sourceKind": "git-checkout",
            "origin": self._snapshot.origin,
            "revision": self._snapshot.revision,
            "scopeDigest": self._snapshot.scope_digest,
            "scopeDispositionDigest": AnalyzerRegistry.default()
            .source_scope(
                self._snapshot,
                AnalyzerSelection.from_envelope(envelope),
            )
            .disposition_digest,
            "analyzerPack": self._snapshot.analyzer_pack,
            "ruleset": self._snapshot.ruleset,
            "framework": "spring-data-jpa",
            "schemaProfile": source.get("schemaProfile"),
            "platform": self._snapshot.platform,
        }
        if source != expected:
            raise AnalyzerSelectionError(
                "SOURCE_DETERMINANT_MISMATCH",
                "durable source determinant does not match the immutable snapshot",
            )
        if (
            str(envelope.get("repo")) != self._snapshot.repository
            or str(envelope.get("digest")) != self._snapshot.revision
            or str(envelope.get("env")) != self._snapshot.environment
            or str(envelope.get("system")) != self._snapshot.system
            or tuple(envelope.get("changedFiles", ())) != self._snapshot.paths
        ):
            raise AnalyzerSelectionError(
                "SOURCE_DETERMINANT_MISMATCH",
                "durable command does not match the immutable snapshot",
            )
        return self._snapshot


class _PythonAnalyzerAdapter:
    def __init__(self, analyzer: ScaAnalyzer) -> None:
        self._analyzer = analyzer

    def analyze(
        self,
        snapshot: AnalyzerSnapshot,
        schema_profile: str,
        run_id: str,
        correlation_id: str,
    ) -> AnalyzerRunResult:
        sca = self._analyzer.analyze(
            repository_root=None,
            repo=snapshot.repository,
            digest=snapshot.revision,
            scope_paths=snapshot.paths,
            resolver_context=ResolveContext(
                env=snapshot.environment,
                platform=schema_profile,
                system=snapshot.system,
                repo=snapshot.repository,
                digest=snapshot.revision,
                config={},
                snapshot_id=self._analyzer.snapshot_id,
            ),
            run_id=run_id,
            correlation_id=correlation_id,
            source_reader=snapshot.read_bytes,
        )
        return AnalyzerRunResult(
            sca.as_dict(),
            "COMPLETE",
            (),
            len(sca.edges),
            0,
            0,
            len(sca.residue),
            int(sca.stats.get("quarantinedCount", 0)),
        )


class _SqlTransformationAnalyzerAdapter:
    def __init__(self, resolver: Resolver) -> None:
        self._resolver = resolver

    def analyze(
        self,
        snapshot: AnalyzerSnapshot,
        schema_profile: str,
        run_id: str,
        correlation_id: str,
    ) -> AnalyzerRunResult:
        resolver = self._resolver
        sources = tuple(
            SqlTransformationSource(path, snapshot.read_bytes(path), schema_profile)
            for path in snapshot.paths
            if PurePosixPath(path).suffix == ".sql"
        )
        analysis = analyze_sql_sources(sources)
        edges, resolution_residue = compile_derivation_edges(
            analysis,
            resolver,
            ResolveContext(
                env=snapshot.environment,
                platform=schema_profile,
                system=snapshot.system,
                repo=snapshot.repository,
                digest=snapshot.revision,
                config={},
                snapshot_id=resolver.snapshot_id,
            ),
            repo=snapshot.repository,
            digest=snapshot.revision,
            run_id=run_id,
            correlation_id=correlation_id,
            ruleset_version=snapshot.ruleset,
        )
        residue = analysis.residue + resolution_residue
        status = "INTEGRATION_REQUIRED" if residue else "COMPLETE"
        status_reasons = tuple(sorted({item.code for item in residue}))
        document = {
            "schemaVersion": "1.0.0",
            "repo": snapshot.repository,
            "digest": snapshot.revision,
            "runId": run_id,
            "correlationId": correlation_id,
            "rulesetVersion": snapshot.ruleset,
            "resolverVersion": resolver.resolver_version,
            "snapshotId": resolver.snapshot_id,
            "status": status,
            "statusReasons": list(status_reasons),
            "edges": [
                {
                    "provenanceId": edge.provenance_id,
                    "from": [edge.from_urn],
                    "to": edge.to_urn,
                    "edgeType": edge.edge_type,
                    "transform": edge.transform,
                    "mechanism": edge.mechanism,
                    "exact": edge.exact,
                    "file": edge.file,
                    "line": edge.line,
                }
                for edge in edges
            ],
            "residue": [
                {
                    "code": item.code,
                    "location": {"path": item.path, "line": item.line},
                    "symbol": item.symbol,
                }
                for item in residue
            ],
            "datasetsSeen": sorted(
                {edge.to_urn.rsplit("#", 1)[0] for edge in edges}
                | {edge.from_urn.rsplit("#", 1)[0] for edge in edges}
            ),
            "stats": {
                "filesAnalyzed": analysis.files_analyzed,
                "edgesEmitted": len(edges),
                "residueCount": len(residue),
                "quarantinedCount": 0,
            },
        }
        return AnalyzerRunResult(
            document=document,
            status=status,
            status_reasons=status_reasons,
            edge_count=len(edges),
            read_count=0,
            write_count=0,
            residue_count=len(residue),
            unresolved_count=0,
        )


class _JavaSpringAnalyzerAdapter:
    def analyze(
        self,
        snapshot: AnalyzerSnapshot,
        schema_profile: str,
        run_id: str,
        correlation_id: str,
    ) -> AnalyzerRunResult:
        sources = []
        schema_candidates = tuple(
            path
            for path in snapshot.paths
            if _is_profile_schema_path(path, schema_profile)
        )
        schema_reason = (
            "missing-profile-schema"
            if not schema_candidates
            else "ambiguous-profile-schema"
            if len(schema_candidates) > 1
            else None
        )
        for path in snapshot.paths:
            suffix = PurePosixPath(path).suffix
            name = PurePosixPath(path).name
            if suffix == ".java" and not _is_main_java_path(path):
                continue
            if name in {"pom.xml", "build.gradle", "build.gradle.kts"} and len(
                PurePosixPath(path).parts
            ) != 1:
                continue
            if suffix not in {".java", ".sql"} and name not in {
                "pom.xml",
                "build.gradle",
                "build.gradle.kts",
            }:
                continue
            if suffix == ".sql" and (
                schema_reason is not None or path not in schema_candidates
            ):
                continue
            dialect = (
                schema_profile
                if suffix == ".sql"
                else None
            )
            sources.append(JavaSpringSource(path, snapshot.read_bytes(path), dialect))
        analysis = JavaSpringScaAnalyzer().analyze(tuple(sources))
        origin = getattr(snapshot, "origin", None)
        if not isinstance(origin, str):
            raise AnalyzerSelectionError(
                "SOURCE_KIND_MISMATCH", "Java Spring analysis requires an exact Git origin"
            )
        evidence = JavaSpringEvidenceCompiler().compile(
            analysis,
            JavaSpringEvidenceContext(
                origin=origin,
                repository=snapshot.repository,
                revision=snapshot.revision,
                scope_digest=snapshot.scope_digest,
                environment=snapshot.environment,
                platform=snapshot.platform,
                system=snapshot.system,
                analyzer_pack=snapshot.analyzer_pack,
                ruleset_version=snapshot.ruleset,
                resolver_version="schema-resolver-v1",
                schema_profile=schema_profile,
            ),
        )
        edge_payloads = []
        for edge in evidence.edges:
            edge_payloads.append(
                {
                    "provenanceId": edge.stable_id,
                    "from": [edge.from_urn],
                    "to": edge.to_urn,
                    "edgeType": edge.edge_type,
                    "transform": edge.transform,
                    "mechanism": "SCA",
                    "exact": edge.exact,
                    "executed": edge.executed,
                    "evidence": edge.evidence.as_dict(),
                    "repo": snapshot.repository,
                    "digest": snapshot.revision,
                    "runId": run_id,
                    "correlationId": correlation_id,
                    "resolverVersion": "schema-resolver-v1",
                    "snapshotId": snapshot.scope_digest,
                }
            )
        residue_payloads = [item.as_dict() for item in evidence.residue]
        status_reasons = set(evidence.status_reasons)
        if schema_reason is not None:
            status_reasons.add(schema_reason)
            residue_payloads.append(
                {
                    "code": schema_reason,
                    "location": {
                        "path": f"db/{schema_profile}/schema.sql",
                        "line": 1,
                        "startByte": 0,
                        "endByte": 0,
                        "astKind": "schema_profile",
                        "astPath": "schema_profile",
                    },
                    "factIds": [],
                }
            )
        final_status = "INTEGRATION_REQUIRED" if status_reasons else "COMPLETE"
        document = {
            "schemaVersion": "1.0.0",
            "repo": snapshot.repository,
            "digest": snapshot.revision,
            "runId": run_id,
            "correlationId": correlation_id,
            "rulesetVersion": snapshot.ruleset,
            "resolverVersion": "schema-resolver-v1",
            "snapshotId": snapshot.scope_digest,
            "status": final_status,
            "statusReasons": sorted(status_reasons),
            "edges": edge_payloads,
            "residue": sorted(
                residue_payloads,
                key=lambda item: (
                    str(item["location"]["path"]),
                    int(item["location"]["startByte"]),
                    str(item["code"]),
                ),
            ),
            "datasetsSeen": sorted({edge.dataset_urn for edge in evidence.edges}),
            "coverage": evidence.coverage.as_dict(),
            "stats": {
                "filesAnalyzed": evidence.coverage.supported_java_files,
                "edgesEmitted": len(evidence.edges),
                "residueCount": len(residue_payloads),
                "quarantinedCount": evidence.coverage.invocations_unresolved,
            },
        }
        return AnalyzerRunResult(
            document=document,
            status=final_status,
            status_reasons=tuple(sorted(status_reasons)),
            edge_count=len(evidence.edges),
            read_count=sum(edge.edge_type == "READS" for edge in evidence.edges),
            write_count=sum(edge.edge_type == "WRITES" for edge in evidence.edges),
            residue_count=len(residue_payloads),
            unresolved_count=evidence.coverage.invocations_unresolved,
        )


def canonical_source_metadata(
    snapshot: RepositorySnapshot, *, schema_profile: str
) -> dict[str, str]:
    selection = AnalyzerSelection(
        analyzer_pack=snapshot.analyzer_pack,
        ruleset=snapshot.ruleset,
        source_kind="git-checkout",
        framework="spring-data-jpa",
        schema_profile=schema_profile,
    )
    source_scope = AnalyzerRegistry.default().source_scope(snapshot, selection)
    return {
        "sourceKind": "git-checkout",
        "origin": snapshot.origin,
        "revision": snapshot.revision,
        "scopeDigest": snapshot.scope_digest,
        "scopeDispositionDigest": source_scope.disposition_digest,
        "analyzerPack": snapshot.analyzer_pack,
        "ruleset": snapshot.ruleset,
        "framework": "spring-data-jpa",
        "schemaProfile": schema_profile,
        "platform": snapshot.platform,
    }


def _is_profile_schema_path(path: str, schema_profile: str) -> bool:
    parts = PurePosixPath(path).parts
    return parts == (
        "src",
        "main",
        "resources",
        "db",
        schema_profile,
        "schema.sql",
    )


def _java_source_disposition(path: str, schema_profile: str) -> str:
    pure = PurePosixPath(path)
    name = pure.name
    suffix = pure.suffix.lower()
    if name in {"pom.xml", "build.gradle", "build.gradle.kts"}:
        return "selected" if len(pure.parts) == 1 else "skipped"
    if suffix == ".java":
        if _is_main_java_path(path):
            return "selected"
        return "skipped" if _is_test_path(path) else "unsupported"
    if suffix == ".sql":
        if _is_profile_schema_path(path, schema_profile):
            return "selected"
        return "skipped" if _is_policy_skipped_sql(path) else "unsupported"
    return "skipped"


def _is_test_path(path: str) -> bool:
    parts = tuple(part.lower() for part in PurePosixPath(path).parts)
    return "test" in parts or "tests" in parts or "fixtures" in parts


def _is_policy_skipped_sql(path: str) -> bool:
    pure = PurePosixPath(path)
    parts = tuple(part.lower() for part in pure.parts)
    if pure.name.lower() in {"data.sql", "user.sql"}:
        return True
    if any(
        part in {
            "fixture",
            "fixtures",
            "script",
            "scripts",
            "seed",
            "seeds",
            "setup",
            "test",
            "tests",
            "user",
            "users",
        }
        for part in parts
    ):
        return True
    return (
        len(parts) >= 3
        and parts[-3] == "db"
        and parts[-2] in {"h2", "mysql", "postgres"}
        and parts[-1] == "schema.sql"
    )


def _is_main_java_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return any(
        parts[index : index + 3] == ("src", "main", "java")
        for index in range(max(0, len(parts) - 2))
    )


def deterministic_checkout_event_id(
    metadata: dict[str, str], repository: str, environment: str, system: str
) -> str:
    body = json.dumps(
        {
            "environment": environment,
            "repository": repository,
            "source": metadata,
            "system": system,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"checkout-{hashlib.sha256(body).hexdigest()[:24]}"
