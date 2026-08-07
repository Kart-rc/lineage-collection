from lineage_api.entrypoints.aws.common import create_handler

STAGE = "deployment"
handler = create_handler(STAGE)
