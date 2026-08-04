from __future__ import annotations

import re


MECHANISMS = {"SCA", "LLM", "RUNTIME"}
SQL_KEYWORDS_AND_FUNCTIONS = {
    "AS",
    "AVG",
    "CASE",
    "CAST",
    "COUNT",
    "DATE",
    "ELSE",
    "END",
    "MAX",
    "MIN",
    "SUM",
    "THEN",
    "WHEN",
}
TRANSFORM_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[^\w\s]")


def derive_band(mechanisms: set[str]) -> str:
    if not mechanisms:
        raise ValueError("Confidence requires at least one element-level mechanism")
    unknown = mechanisms - MECHANISMS
    if unknown:
        raise ValueError(f"Unknown confidence mechanisms: {sorted(unknown)}")
    if mechanisms == {"LLM"}:
        return "LOWEST"
    if len(mechanisms) == 1:
        return "SINGLE"
    if mechanisms == {"SCA", "RUNTIME"}:
        return "HIGH"
    if mechanisms == MECHANISMS:
        return "HIGHEST"
    return "MEDIUM"


def normalize_transform(transform: str) -> str:
    tokens = TRANSFORM_TOKEN.findall(transform.strip())
    normalized = [
        token.lower() if token.upper() in SQL_KEYWORDS_AND_FUNCTIONS else token
        for token in tokens
    ]
    return "".join(normalized)
