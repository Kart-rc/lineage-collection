from __future__ import annotations

import pytest


def _confidence_functions():
    try:
        from lineage_api.domain.confidence import derive_band, normalize_transform
    except ModuleNotFoundError:
        pytest.fail("Confidence rules are not implemented")
    return derive_band, normalize_transform


@pytest.mark.parametrize(
    ("mechanisms", "band"),
    [
        ({"LLM"}, "LOWEST"),
        ({"SCA"}, "SINGLE"),
        ({"RUNTIME"}, "SINGLE"),
        ({"SCA", "LLM"}, "MEDIUM"),
        ({"RUNTIME", "LLM"}, "MEDIUM"),
        ({"SCA", "RUNTIME"}, "HIGH"),
        ({"SCA", "RUNTIME", "LLM"}, "HIGHEST"),
    ],
)
def test_ordinal_band_matrix(mechanisms: set[str], band: str) -> None:
    derive_band, _ = _confidence_functions()

    assert derive_band(mechanisms) == band


def test_empty_mechanism_set_is_invalid() -> None:
    derive_band, _ = _confidence_functions()

    with pytest.raises(ValueError, match="at least one element-level mechanism"):
        derive_band(set())


def test_transform_normalization_folds_keywords_but_not_identifiers() -> None:
    _, normalize_transform = _confidence_functions()

    assert normalize_transform(" SUM( amount ) ") == normalize_transform("sum(amount)")
    assert normalize_transform("Customer_ID") != normalize_transform("customer_id")
