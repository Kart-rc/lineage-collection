from lineage_api.entrypoints.aws.common import create_handler


STAGE = "classification"
handler = create_handler(STAGE)
