from lineage_api.entrypoints.aws.common import create_handler

STAGE = "runtime-validation"
handler = create_handler(STAGE)
