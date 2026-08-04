from __future__ import annotations

from lineage_api.domain.errors import DomainError


BAND_ORDER = {
    "LOWEST": 0,
    "SINGLE": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "HIGHEST": 4,
}
DESTRUCTIVE_CHANGES = {"COLUMN_DROP", "COLUMN_TYPE_CHANGE", "DATASET_REMOVAL"}
NON_DESTRUCTIVE_CHANGES = {"COLUMN_RENAME", "TRANSFORM_CHANGE", "FINGERPRINT_DRIFT"}


def severity_for(
    change_type: str,
    band: str,
    correlation_id: str = "system-impact",
) -> str:
    if change_type not in DESTRUCTIVE_CHANGES | NON_DESTRUCTIVE_CHANGES:
        raise DomainError(
            "UNKNOWN_CHANGE_TYPE",
            f"Unknown impact change type: {change_type}",
            correlation_id,
            {"changeType": change_type},
        )
    if band not in BAND_ORDER:
        raise DomainError(
            "UNKNOWN_CONFIDENCE_BAND",
            f"Unknown confidence band: {band}",
            correlation_id,
            {"band": band},
        )

    confidence = BAND_ORDER[band]
    if change_type in DESTRUCTIVE_CHANGES and confidence >= BAND_ORDER["HIGH"]:
        return "BLOCK"
    if confidence >= BAND_ORDER["MEDIUM"]:
        return "WARN"
    return "INFO"
