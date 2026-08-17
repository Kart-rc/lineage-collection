"""Leave a REAL IN_REVIEW proposal in the floci state for the approval UI.

Runs a fresh baseline collection (intake + B1–B10) against the live floci
emulator with a uniquely-touched source archive (so its artifact digest cannot
collide with an already-registered package) and stops at ``AWAITING_APPROVAL``.
The proposal then shows up in the control room's Review queue; approving it
there drives the real ``review_proposal`` transaction and — via the product API
server's outbox drain — the real fenced publication.

Usage:
    ALLOW_LINEAGE_FLOCI_E2E=1 uv run --project apps/api python \
        scripts/floci/seed_pending_proposal.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration" / "floci"))

os.environ.setdefault("ALLOW_LINEAGE_FLOCI_E2E", "1")

from test_petclinic_floci_e2e import ACCEPTED_AT, JAVA_HOME, E2ESession  # noqa: E402
from java_stage_overrides import run_runtime_corroboration  # noqa: E402


def main() -> int:
    sess = E2ESession()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    command_id = f"cmd-seed-{stamp}"
    correlation_id = f"corr-seed-{stamp}"

    # Unique artifact digest: append a marker comment to one source file.
    base = zipfile.ZipFile(
        io.BytesIO(
            sess.s3.get_object(
                Bucket=sess.source_v1["reference"]["bucket"],
                Key=sess.source_v1["reference"]["key"],
            )["Body"].read()
        )
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as rebuilt:
        for info in base.infolist():
            if info.filename.endswith("/"):
                continue
            body = base.read(info.filename)
            if info.filename == sess.changed_java:
                body += f"\n// review-seed {stamp}\n".encode()
            rebuilt.writestr(info.filename, body)
    source = sess._upload_source(f"seed-{stamp}", out.getvalue())

    context = sess.intent_context(source, changed=[], closure=[])
    input_ref = sess.put_intent(command_id, context)
    sess.run_intake("BASELINE", command_id, correlation_id, input_ref)

    current = input_ref
    for stage_id in ("B1", "B2", "B3", "B4"):
        current = sess.execute_stage(
            "BASELINE", stage_id, command_id, correlation_id, current
        )["output"]
    inventory_doc = sess.artifacts.get(current)
    children = []
    for index, work_ref in enumerate(inventory_doc["workUnitRefs"]):
        child = sess.execute_stage(
            "BASELINE", "B5", command_id, correlation_id, work_ref,
            idempotency_key=f"{command_id}:B5:{index:05d}",
        )
        children.append({"outcome": "SUCCEEDED", "output": child["output"]})
    map_token = f"map-seed-{stamp}"
    prefix = f"workflow-results/{command_id}/B5/{map_token}/"
    manifest_ref = sess.artifacts.put(
        "map-manifest", f"{prefix}manifest.json", {"MapRunArn": map_token}, "1.0.0"
    )
    sess.artifacts.put("map-result-shard", f"{prefix}SUCCEEDED_0.json", children, "1.0.0")
    aggregate = sess.aggregation.execute({
        "schemaVersion": "1.0.0",
        "operation": "BASELINE_SCA_AGGREGATE",
        "workflowKind": "BASELINE",
        "workflowVersion": "1.0.0",
        "commandId": command_id,
        "correlationId": correlation_id,
        "causationId": f"{command_id}:B5A",
        "idempotencyKey": f"{command_id}:B5A",
        "determinantDigest": "sha256:" + "0" * 64,
        "workInventory": current,
        "mapResult": {
            "MapRunArn": f"arn:aws:states:us-east-1:000000000000:mapRun:lineage/Map:{map_token}",
            "ResultWriterDetails": {
                "Bucket": sess.config.evidence_bucket, "Key": manifest_ref["key"],
            },
        },
    })
    assert aggregate["outcome"] == "SUCCEEDED", aggregate
    aggregate_doc = sess.artifacts.get(aggregate["output"])
    sca_result = sess.artifacts.get(children[0]["output"])
    evidence_doc = sess.artifacts.get(sca_result["evidenceRef"])
    runtime = run_runtime_corroboration(
        artifacts=sess.artifacts,
        kinesis=sess.kinesis,
        sources=sess.read_java_sources(source["reference"]),
        static_edges=evidence_doc["edges"],
        command_id=command_id,
        work_unit_id=sca_result["workUnitId"],
        correlation_id=correlation_id,
        common=aggregate_doc["context"],
        observed_at=ACCEPTED_AT,
        java_home=os.environ.get("LINEAGE_JAVA_HOME", JAVA_HOME),
    )
    augmented = dict(aggregate_doc)
    augmented["context"] = {
        **aggregate_doc["context"],
        "runtimeManifestRefs": [runtime["manifestRef"]],
        "assertionRefs": [*aggregate_doc["context"]["assertionRefs"], runtime["assertionRef"]],
    }
    current = sess.artifacts.put(
        "sca-batch-result",
        f"commands/{command_id}/stages/B5A/runtime-augmented.json",
        augmented,
        "1.0.0",
    )
    proposal = None
    for stage_id in ("B6", "B7", "B8", "B9", "B10"):
        result = sess.execute_stage(
            "BASELINE", stage_id, command_id, correlation_id, current
        )
        current = result["output"]
        if stage_id == "B9":
            proposal = sess.artifacts.get(current)["proposal"]
        if stage_id == "B10":
            assert result.get("terminalOutcome") == "AWAITING_APPROVAL", result

    assert proposal is not None
    print(json.dumps({
        "proposalId": proposal["proposalId"],
        "state": proposal["state"],
        "commandId": command_id,
        "edges": len(proposal["diff"]["addedEdgeIds"]),
        "runtimeVerdict": runtime["verdict"],
        "next": "open the Review queue in the control room and approve it",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
