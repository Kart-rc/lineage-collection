from __future__ import annotations

import pytest

from lineage_api.domain.errors import DomainError


def _severity_function():
    try:
        from lineage_api.domain.impact import severity_for
    except ModuleNotFoundError:
        pytest.fail("Impact severity mapping is not implemented")
    return severity_for


@pytest.mark.parametrize(
    ("change_type", "band", "expected"),
    [
        ("COLUMN_DROP", "HIGHEST", "BLOCK"),
        ("COLUMN_DROP", "HIGH", "BLOCK"),
        ("COLUMN_DROP", "MEDIUM", "WARN"),
        ("COLUMN_DROP", "SINGLE", "INFO"),
        ("COLUMN_DROP", "LOWEST", "INFO"),
        ("COLUMN_TYPE_CHANGE", "HIGH", "BLOCK"),
        ("DATASET_REMOVAL", "HIGH", "BLOCK"),
        ("COLUMN_RENAME", "HIGH", "WARN"),
        ("COLUMN_RENAME", "MEDIUM", "WARN"),
        ("COLUMN_RENAME", "SINGLE", "INFO"),
        ("TRANSFORM_CHANGE", "HIGHEST", "WARN"),
        ("FINGERPRINT_DRIFT", "HIGH", "WARN"),
        ("FINGERPRINT_DRIFT", "MEDIUM", "WARN"),
        ("FINGERPRINT_DRIFT", "LOWEST", "INFO"),
    ],
)
def test_impact_severity_matrix(change_type: str, band: str, expected: str) -> None:
    severity_for = _severity_function()

    assert severity_for(change_type, band) == expected


def test_unknown_change_type_is_rejected() -> None:
    severity_for = _severity_function()

    with pytest.raises(DomainError) as captured:
        severity_for("TABLE_TRUNCATE", "HIGH", correlation_id="corr-001")

    assert captured.value.code == "UNKNOWN_CHANGE_TYPE"
