"""The single identity rule for object-store datasets.

An S3 object has to be named identically by the analyzer that reads the code and by the
runtime that observes the job, or an edge can never be corroborated by its own
observation. This module exists because those two paths had diverged: the resolver kept
only the last path segment while the runtime path kept the whole key, so
`s3://bucket/raw/events/2024/` was `2024` on one side and `raw/events/2024` on the
other, and `merge_runtime_observation` — which requires exact URN equality — could never
match them.

Both paths now call this. Two implementations of an identity rule is not a style
problem; it is a correctness one.
"""

from __future__ import annotations

import re

# Scheme variants that address the same store. `s3a://` and `s3://` are the same bucket,
# so they must not produce different URNs and split the graph in half.
OBJECT_STORE_SCHEMES = {
    "s3": "s3",
    "s3a": "s3",
    "s3n": "s3",
    "gs": "gs",
    "gcs": "gs",
    "abfss": "abfs",
    "abfs": "abfs",
}

# Hive-style partition segment: `dt=2024-01-01`, `year=2024`.
_PARTITION_SEGMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")


def is_object_store(scheme: str) -> bool:
    return scheme.lower() in OBJECT_STORE_SCHEMES


def canonical_scheme(scheme: str) -> str:
    return OBJECT_STORE_SCHEMES[scheme.lower()]


def canonical_object_key(path: str) -> str:
    """The dataset component for an object-store path.

    The full hierarchical key is preserved — two prefixes ending in the same segment are
    different datasets. Trailing Hive-style partition segments are stripped, because a
    partition is one physical slice of a table rather than a dataset of its own; an
    inner segment that happens to contain `=` is part of the name and is left alone.
    """
    segments = [segment for segment in path.split("/") if segment]
    while segments and _PARTITION_SEGMENT.fullmatch(segments[-1]):
        segments.pop()
    if not segments:
        raise ValueError(f"object key resolves to nothing: {path!r}")
    return "/".join(segments)
