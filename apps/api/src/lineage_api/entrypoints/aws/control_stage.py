from collections.abc import Mapping

from lineage_api.entrypoints.aws import sca_aggregate
from lineage_api.entrypoints.aws.common import create_handler

STAGE = "control-stage"
_stage_handler = create_handler(STAGE)


def handler(event: object, context: object):
    if isinstance(event, Mapping) and event.get("operation") == "BASELINE_SCA_AGGREGATE":
        return sca_aggregate.handler(event, context)
    return _stage_handler(event, context)
