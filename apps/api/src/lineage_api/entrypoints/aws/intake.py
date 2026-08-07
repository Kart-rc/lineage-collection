from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from lineage_api.entrypoints.aws.common import create_handler


STAGE = "intake"
_single_handler = create_handler(STAGE)


def handler(event: object, context: object) -> dict[str, Any]:
    if not isinstance(event, Mapping) or not isinstance(event.get("Records"), list):
        return _single_handler(event, context)
    failures: list[dict[str, str]] = []
    for record in event["Records"]:
        message_id = str(record.get("messageId", "unknown"))
        try:
            body = json.loads(record["body"])
            result = _single_handler(body, context)
            if result["outcome"] == "REDRIVE_REQUIRED":
                failures.append({"itemIdentifier": message_id})
        except Exception:
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}
