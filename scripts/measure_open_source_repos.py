"""Run every analyzer cell against real open-source repositories and report the truth.

Fixtures are written to resolve. This script does the opposite job: it points each cell
at unmodified upstream code and records what actually comes out — including zeros, which
are the most informative result. A cell that finds nothing on real code is a coverage
finding, not a crash.

Run: uv run --project apps/api python scripts/measure_open_source_repos.py <estate-root>
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from lineage_api.services.java_interaction_sca import analyze_java_interactions
from lineage_api.services.kafka_binding_sca import (
    is_stream_configuration,
    read_stream_bindings,
    read_streams_topology,
)
from lineage_api.services.spark_sca import analyze_spark_sources

SKIP = ("/.git/", "/target/", "/build/", "/node_modules/", "/.gradle/")


def _files(root: Path, *suffixes: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for item in root.rglob("*"):
        if not item.is_file() or not item.name.endswith(suffixes):
            continue
        relative = item.relative_to(root).as_posix()
        if any(marker in f"/{relative}" for marker in SKIP):
            continue
        try:
            found[relative] = item.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
    return found


def _revision(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    return result.stdout.strip() or "?"


def _rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    estate = Path(sys.argv[1]).resolve()
    repos = sorted(p for p in estate.iterdir() if p.is_dir() and (p / ".git").exists())
    if not repos:
        raise SystemExit(f"no git repositories under {estate}")

    totals = {"bindings": 0, "topics": set(), "spark": 0, "inbound": 0, "outbound": 0}

    for repo in repos:
        _rule(f"{repo.name}  @ {_revision(repo)}")
        java = _files(repo, ".java")
        config = {
            path: text
            for path, text in _files(repo, ".yml", ".yaml", ".properties").items()
            if is_stream_configuration(path)
        }
        print(f"  {len(java)} java files, {len(config)} stream config files")

        # --- Kafka bindings -----------------------------------------------------------
        from_config = read_stream_bindings(config)
        from_code = read_streams_topology(java)
        bindings = type(from_config)(
            bindings=from_config.bindings + from_code.bindings,
            residue=from_config.residue + from_code.residue,
        )
        topics = sorted({b.topic for b in bindings.bindings})
        totals["bindings"] += len(bindings.bindings)
        totals["topics"].update(topics)
        print(f"\n  kafka bindings : {len(bindings.bindings)} across {len(topics)} topics"
              f"   residue={sorted({r.code for r in bindings.residue}) or '[]'}")
        for binding in bindings.bindings[:4]:
            print(f"      {binding.direction:6s} {binding.topic:32s} {binding.function}")
        if len(bindings.bindings) > 4:
            print(f"      … {len(bindings.bindings) - 4} more")

        # --- Spark --------------------------------------------------------------------
        spark = analyze_spark_sources(java)
        totals["spark"] += len(spark.jobs)
        print(f"\n  spark jobs     : {len(spark.jobs)}"
              f"   residue={sorted({r.code for r in spark.residue}) or '[]'}")
        for job in spark.jobs[:4]:
            columns = f", {len(job.mappings)} column mappings" if job.mappings else ""
            print(f"      {job.source[:38]:38s} -> {job.target[:34]}{columns}")
        if len(spark.jobs) > 4:
            print(f"      … {len(spark.jobs) - 4} more")

        # --- Interactions ---------------------------------------------------------------
        interactions = analyze_java_interactions(java, service=repo.name)
        totals["inbound"] += len(interactions.inbound)
        totals["outbound"] += len(interactions.outbound)
        channels = sorted({e.channel for e in interactions.inbound} |
                          {c.channel for c in interactions.outbound})
        print(f"\n  interactions   : {len(interactions.inbound)} inbound, "
              f"{len(interactions.outbound)} outbound   channels={channels or '[]'}"
              f"   residue={sorted({r.code for r in interactions.residue}) or '[]'}")
        for endpoint in interactions.inbound[:3]:
            print(f"      IN  {endpoint.channel:11s} {endpoint.operation[:44]:44s} {endpoint.handler[:28]}")
        for call in interactions.outbound[:3]:
            print(f"      OUT {call.channel:11s} {call.operation[:44]:44s} -> {call.to_service}")
        if len(interactions.inbound) > 3:
            print(f"      … {len(interactions.inbound) - 3} more inbound")

    _rule("TOTALS ACROSS ALL REAL REPOSITORIES")
    print(f"  kafka bindings      : {totals['bindings']} across {len(totals['topics'])} distinct topics")
    print(f"  spark jobs          : {totals['spark']}")
    print(f"  inbound endpoints   : {totals['inbound']}")
    print(f"  outbound calls      : {totals['outbound']}")
    print("\n  A zero is a coverage finding, not a failure. Every number above comes from")
    print("  unmodified upstream code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
