from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from lineage_api.domain.evidence import ResidueEntry, ScaEdgeEvidence, ScaEvidenceFile
from lineage_api.services.resolver import (
    RawName,
    ResolveContext,
    ResolvedName,
    Resolver,
)


IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True, slots=True)
class _SourceBinding:
    raw_name: str
    elements: tuple[str, ...]
    resolved: ResolvedName


@dataclass(frozen=True, slots=True)
class _PendingResidue:
    file: str
    line: int
    span: str
    reason: str
    symbol: str
    ast_path: str


class ScaAnalyzer:
    def __init__(self, resolver: Resolver, ruleset_version: str) -> None:
        self._resolver = resolver
        self._ruleset_version = ruleset_version

    def analyze(
        self,
        repository_root: Path,
        repo: str,
        digest: str,
        scope_paths: tuple[str, ...],
        resolver_context: ResolveContext,
        run_id: str,
        correlation_id: str,
    ) -> ScaEvidenceFile:
        edges: list[ScaEdgeEvidence] = []
        pending_residue: list[_PendingResidue] = []
        dataset_urns: set[str] = set()
        quarantined_count = 0

        for relative_path in sorted(scope_paths):
            if Path(relative_path).suffix != ".py":
                continue
            source = (repository_root / relative_path).read_text(encoding="utf-8")
            module = ast.parse(source, filename=relative_path)
            for function_index, node in enumerate(module.body):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                bindings: dict[str, _SourceBinding] = {}
                for statement_index, statement in enumerate(node.body):
                    ast_path = f"Module.body[{function_index}].body[{statement_index}]"
                    if isinstance(statement, ast.Assign) and self._call_name(statement.value) == "read_dataset":
                        target = statement.targets[0]
                        if not isinstance(target, ast.Name):
                            continue
                        binding = self._read_binding(
                            statement.value,
                            resolver_context,
                            relative_path,
                            ast_path,
                            pending_residue,
                        )
                        if binding is None:
                            quarantined_count += 1
                        else:
                            bindings[target.id] = binding
                            dataset_urns.add(str(binding.resolved.urn))
                        continue

                    call = statement.value if isinstance(statement, ast.Expr) else None
                    if self._call_name(call) == "read_dataset":
                        self._record_dynamic_read(
                            call,
                            relative_path,
                            ast_path,
                            pending_residue,
                        )
                        continue

                    if self._call_name(call) != "write_dataset":
                        continue
                    emitted, target_urn, quarantined = self._write_edges(
                        call=call,
                        bindings=bindings,
                        resolver_context=resolver_context,
                        relative_path=relative_path,
                        ast_path=ast_path,
                        repo=repo,
                        digest=digest,
                        run_id=run_id,
                        correlation_id=correlation_id,
                    )
                    edges.extend(emitted)
                    quarantined_count += quarantined
                    if target_urn:
                        dataset_urns.add(target_urn)

        datasets_seen = tuple(sorted(dataset_urns))
        residue = tuple(
            ResidueEntry(
                file=item.file,
                line=item.line,
                span=item.span,
                reason=item.reason,
                symbol=item.symbol,
                ast_path=item.ast_path,
                nearby_facts=(),
                known_urns=datasets_seen,
            )
            for item in sorted(pending_residue, key=lambda item: (item.file, item.line, item.symbol))
        )
        sorted_edges = tuple(
            sorted(edges, key=lambda edge: (edge.to_urn, edge.from_urn, edge.transform))
        )
        return ScaEvidenceFile(
            schema_version="1.0.0",
            repo=repo,
            digest=digest,
            run_id=run_id,
            correlation_id=correlation_id,
            ruleset_version=self._ruleset_version,
            resolver_version=self._resolver.resolver_version,
            snapshot_id=self._resolver.snapshot_id,
            edges=sorted_edges,
            residue=residue,
            datasets_seen=datasets_seen,
            stats={
                "filesAnalyzed": len([path for path in scope_paths if Path(path).suffix == ".py"]),
                "edgesEmitted": len(sorted_edges),
                "residueCount": len(residue),
                "quarantinedCount": quarantined_count,
            },
        )

    def _read_binding(
        self,
        call: ast.Call,
        context: ResolveContext,
        relative_path: str,
        ast_path: str,
        residue: list[_PendingResidue],
    ) -> _SourceBinding | None:
        if not call.args or not isinstance(call.args[0], ast.Constant) or not isinstance(call.args[0].value, str):
            symbol = ast.unparse(call.args[0]) if call.args else "<missing>"
            residue.append(
                _PendingResidue(
                    relative_path,
                    call.lineno,
                    f"line {call.lineno}",
                    "dynamic-name",
                    symbol,
                    f"{ast_path}.value",
                )
            )
            return None
        elements = self._keyword_literal(call, "elements", default=[])
        raw_value = call.args[0].value
        result = self._resolver.resolve(
            RawName("dataset", raw_value, "SCA", tuple(str(item) for item in elements)),
            context,
        )
        if not isinstance(result, ResolvedName):
            residue.append(
                _PendingResidue(
                    relative_path,
                    call.lineno,
                    f"line {call.lineno}",
                    f"resolver-{result.reason.lower()}",
                    raw_value,
                    f"{ast_path}.value",
                )
            )
            return None
        return _SourceBinding(raw_value, tuple(elements), result)

    def _write_edges(
        self,
        call: ast.Call,
        bindings: dict[str, _SourceBinding],
        resolver_context: ResolveContext,
        relative_path: str,
        ast_path: str,
        repo: str,
        digest: str,
        run_id: str,
        correlation_id: str,
    ) -> tuple[list[ScaEdgeEvidence], str | None, int]:
        if not call.args or not isinstance(call.args[0], ast.Constant):
            return [], None, 1
        target_raw = str(call.args[0].value)
        mappings = self._keyword_literal(call, "mappings", default={})
        source_nodes = self._keyword_node(call, "sources")
        source_names = [node.id for node in source_nodes.elts if isinstance(node, ast.Name)] if isinstance(source_nodes, ast.List) else []
        if not source_names or source_names[0] not in bindings:
            return [], None, 1
        source = bindings[source_names[0]]
        target_result = self._resolver.resolve(
            RawName("dataset", target_raw, "SCA", tuple(str(key) for key in mappings)),
            resolver_context,
        )
        if not isinstance(target_result, ResolvedName):
            return [], None, 1

        source_elements = {urn.element: urn for urn in source.resolved.element_urns}
        target_elements = {urn.element: urn for urn in target_result.element_urns}
        emitted: list[ScaEdgeEvidence] = []
        for target_element, transform in mappings.items():
            source_element = next(
                (token for token in IDENTIFIER.findall(str(transform)) if token in source_elements),
                None,
            )
            if source_element is None or target_element not in target_elements:
                continue
            from_urn = str(source_elements[source_element])
            to_urn = str(target_elements[target_element])
            identity = "|".join(
                (
                    repo,
                    digest,
                    relative_path,
                    str(call.lineno),
                    from_urn,
                    to_urn,
                    str(transform),
                    self._ruleset_version,
                )
            )
            provenance_id = f"prov-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
            emitted.append(
                ScaEdgeEvidence(
                    provenance_id=provenance_id,
                    from_urn=from_urn,
                    to_urn=to_urn,
                    edge_type="DERIVES",
                    transform=str(transform),
                    mechanism="SCA",
                    exact=True,
                    file=relative_path,
                    line=call.lineno,
                    ast_path=f"{ast_path}.value.mappings[{target_element!r}]",
                    repo=repo,
                    digest=digest,
                    run_id=run_id,
                    correlation_id=correlation_id,
                    resolver_version=target_result.resolver_version,
                    snapshot_id=target_result.snapshot_id,
                )
            )
        return emitted, str(target_result.urn), 0

    @staticmethod
    def _record_dynamic_read(
        call: ast.Call,
        relative_path: str,
        ast_path: str,
        residue: list[_PendingResidue],
    ) -> None:
        if not call.args or isinstance(call.args[0], ast.Constant):
            return
        residue.append(
            _PendingResidue(
                file=relative_path,
                line=call.lineno,
                span=f"line {call.lineno}",
                reason="dynamic-name",
                symbol=ast.unparse(call.args[0]),
                ast_path=f"{ast_path}.value",
            )
        )

    @staticmethod
    def _call_name(node: ast.AST | None) -> str | None:
        if not isinstance(node, ast.Call):
            return None
        return node.func.id if isinstance(node.func, ast.Name) else None

    @staticmethod
    def _keyword_node(call: ast.Call, name: str) -> ast.AST | None:
        return next((keyword.value for keyword in call.keywords if keyword.arg == name), None)

    @classmethod
    def _keyword_literal(cls, call: ast.Call, name: str, default):
        node = cls._keyword_node(call, name)
        return ast.literal_eval(node) if node is not None else default
