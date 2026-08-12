"""Collect lineage from a real repository and score it on SCA and runtime alone.

`OpenLineage/OpenLineage` ships integration fixtures that carry both halves of the
evidence for one operation: the literal SQL the engine ran, and the columnLineage facet
it emitted while running. The SCA claim is derived by parsing that SQL; the runtime claim
is read from the facet. Neither was written to agree with the other.

Each fixture is treated as its own catalog snapshot, because these tables are reused with
different shapes across scenarios — `test.t1` appears with nine distinct schemas and
`test.xxx` with sixteen. A single merged catalog would let a statement resolve against
columns that do not exist in that scenario, so the schema facets of the event under
analysis are the snapshot for that event and nothing else.

One honesty note carried into the output: for an INSERT with an explicit column list, or
a CTAS with aliases, the SCA claim is fully independent of the event. For a *positional*
INSERT the target column order is not in the statement, so it comes from the same event
that supplies the runtime facet — shared context, flagged per row.

Run: uv run --project apps/api python scripts/verify_real_repo_confidence.py <estate-root>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from lineage_api.domain.confidence import derive_band
from lineage_api.domain.product_confidence import project_confidence
from lineage_api.services.consolidation import ConsolidationService
from lineage_api.services.resolver import (
    RawName,
    ResolveContext,
    ResolvedName,
    Resolver,
)
from lineage_api.services.sql_transformation_sca import (
    SqlTransformationSource,
    analyze_sql_sources,
    compile_derivation_edges,
)

RELATIVE = Path("OpenLineage/integration/hive/hive-openlineage-hook/integrations/container")
ENV, PLATFORM, SYSTEM = "staging", "hive", "warehouse"


def _rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def _catalog_for(event: dict) -> dict:
    """A snapshot built from the schema facets this event declares — and only this one."""
    datasets = []
    seen: set[str] = set()
    for side in ("inputs", "outputs"):
        for dataset in event.get(side) or []:
            name = str(dataset["name"])
            fields = dataset.get("facets", {}).get("schema", {}).get("fields", [])
            if name in seen or not fields:
                continue
            seen.add(name)
            datasets.append(
                {
                    "catalogRef": f"catalog://{SYSTEM}/{name}",
                    "env": ENV, "platform": PLATFORM, "system": SYSTEM,
                    "name": name, "aliases": [name.split(".", 1)[-1]], "kind": "DATASTORE",
                    "elements": [
                        {"name": str(f["name"]), "type": str(f.get("type", ""))}
                        for f in fields
                    ],
                }
            )
    return {
        "schemaVersion": "1.0.0", "snapshotId": "openlineage-event-v1",
        "resolverVersion": "1.0.0",
        "vocabulary": {"environments": [ENV], "platforms": [PLATFORM], "systems": [SYSTEM]},
        "datasets": datasets,
    }


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    directory = Path(sys.argv[1]).resolve() / RELATIVE
    if not directory.is_dir():
        raise SystemExit(f"OpenLineage checkout not found at {directory}")

    events = sorted(
        path for path in directory.glob("cll*.json") if "columnLineage" in path.read_text()
    )
    _rule(f"REAL EVENTS CARRYING BOTH SQL AND COLUMN LINEAGE  ({len(events)})")

    verified = unscored = 0
    residue_tally: dict[str, int] = {}
    shared_context = 0

    for path in events:
        event = json.loads(path.read_text())
        query = event.get("job", {}).get("facets", {}).get("sql", {}).get("query")
        outputs = event.get("outputs") or []
        if not query or not outputs or "columnLineage" not in outputs[0].get("facets", {}):
            continue

        resolver = Resolver(_catalog_for(event))
        context = ResolveContext(
            env=ENV, platform=PLATFORM, system=SYSTEM, repo="OpenLineage",
            digest="9" * 40, config={}, snapshot_id=resolver.snapshot_id,
        )
        service = ConsolidationService(database=None, resolver=resolver)  # type: ignore[arg-type]
        catalog = _catalog_for(event)

        def declared_columns(table: str) -> tuple[str, ...] | None:
            result = resolver.resolve(RawName("dataset", table, "SCA", ()), context)
            if not isinstance(result, ResolvedName):
                return None
            for dataset in catalog["datasets"]:
                if str(dataset["catalogRef"]) == str(result.catalog_ref):
                    return tuple(str(e["name"]) for e in dataset["elements"])
            return None

        positional = "insert into" in " ".join(query.split()).lower() and "(" not in (
            " ".join(query.split()).lower().split("select", 1)[0]
        )
        analysis = analyze_sql_sources(
            (SqlTransformationSource("query.sql", query.encode(), "hive"),),
            table_columns=declared_columns,
        )
        edges, compile_residue = compile_derivation_edges(
            analysis, resolver, context, repo="OpenLineage", digest="9" * 40,
            run_id="run", correlation_id="corr",
            ruleset_version="sql-transformation-rules-v1",
        )
        sca = {(e.from_urn, e.to_urn) for e in edges}

        runtime: set[tuple[str, str]] = set()
        try:
            for output in outputs:
                target = service._resolve_runtime_dataset(
                    f"{output['namespace']}/{output['name']}", ENV
                )
                for field, mapping in output["facets"]["columnLineage"]["fields"].items():
                    for item in mapping["inputFields"]:
                        source = service._resolve_runtime_dataset(
                            f"{item['namespace']}/{item['name']}", ENV
                        )
                        runtime.add((f"{source}#{item['field']}", f"{target}#{field}"))
        except ValueError:
            runtime = set()

        corroborated = sca & runtime
        codes = sorted({r.code for r in analysis.residue} | {r.code for r in compile_residue})
        for code in codes:
            residue_tally[code] = residue_tally.get(code, 0) + 1

        if not sca:
            unscored += 1
            print(f"\n  [ ] {path.name}")
            print(f"      {' '.join(query.split())[:66]}")
            print(f"      SCA 0 edges   residue={codes or '[]'}")
            continue

        mechanisms = {"SCA"} | ({"RUNTIME"} if corroborated else set())
        provenance = [{"mechanism": "SCA"}] + (
            [{"mechanism": "RUNTIME", "observedAt": "2026-08-12T10:00:00Z"}]
            if corroborated else []
        )
        band = derive_band(mechanisms)
        confidence = project_confidence(band, provenance)
        if band == "HIGH":
            verified += 1
        if positional:
            shared_context += 1

        print(f"\n  [x] {path.name}{'   (positional INSERT — column order shared)' if positional else ''}")
        print(f"      {' '.join(query.split())[:66]}")
        print(f"      SCA {len(sca)}   runtime {len(runtime)}   corroborated {len(corroborated)}"
              f"   -> {band} {confidence.display_band} ({confidence.percent}%)"
              f" signals={list(confidence.signals)}")
        for source, target in sorted(corroborated):
            print(f"          {source.split(':')[-1]:26s} -> {target.split(':')[-1]}")

    _rule("SUMMARY — SCA AND RUNTIME ONLY")
    print(f"  events with both halves of the evidence : {verified + unscored}")
    print(f"  reached HIGH / VERIFIED (92%)           : {verified}")
    print(f"  no static edge produced                 : {unscored}")
    print(f"  of the scored, relying on shared column order : {shared_context}")
    print(f"\n  residue blocking the rest: {dict(sorted(residue_tally.items()))}")
    print("\n  HIGH is the ceiling for SCA + runtime by construction: HIGHEST additionally")
    print("  requires an LLM assertion, which is excluded here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
