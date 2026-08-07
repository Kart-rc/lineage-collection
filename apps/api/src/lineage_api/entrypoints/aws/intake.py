from lineage_api.entrypoints.aws.common import create_handler

STAGE = "intake"
handler = create_handler(STAGE)
