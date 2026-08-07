from lineage_api.entrypoints.aws.common import create_handler

STAGE = "consolidation"
handler = create_handler(STAGE)
