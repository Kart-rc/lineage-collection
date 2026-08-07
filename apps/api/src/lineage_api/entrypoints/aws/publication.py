from lineage_api.entrypoints.aws.common import create_handler

STAGE = "publication"
handler = create_handler(STAGE)
