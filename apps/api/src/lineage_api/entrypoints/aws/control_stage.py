from lineage_api.entrypoints.aws.common import create_handler

STAGE = "control-stage"
handler = create_handler(STAGE)
