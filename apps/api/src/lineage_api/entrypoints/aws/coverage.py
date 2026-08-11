from lineage_api.entrypoints.aws.common import create_handler

STAGE = "coverage"
handler = create_handler(STAGE)
