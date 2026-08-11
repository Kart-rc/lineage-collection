from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from lineage_api.entrypoints.aws.common import create_handler

STAGE = "publication"
_single_handler = create_handler(STAGE)


def handler(event: object, context: object) -> dict[str, Any]:
    if not isinstance(event, Mapping) or not isinstance(event.get("Records"), list):
        return _single_handler(event, context)
    failures: list[dict[str, str]] = []
    for record in event["Records"]:
        event_id = str(record.get("eventID", "unknown"))
        try:
            image = record["dynamodb"]["NewImage"]
            if image.get("topic", {}).get("S") != "PROPOSAL_APPROVED":
                raise ValueError("unexpected publication stream record")
            envelope = json.loads(image["payload"]["S"])
            result = _single_handler(envelope, context)
            if result["outcome"] == "REDRIVE_REQUIRED":
                failures.append({"itemIdentifier": event_id})
        except Exception:
            failures.append({"itemIdentifier": event_id})
    return {"batchItemFailures": failures}
