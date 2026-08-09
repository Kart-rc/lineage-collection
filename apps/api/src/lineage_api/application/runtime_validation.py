from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from typing import Any, Mapping


_COUNTERS = (
    "attempted",
    "accepted",
    "rejected",
    "duplicates",
    "retried",
    "buffered",
    "dropped",
    "quarantined",
    "drained",
)
_REQUIRED_FIELDS = frozenset(
    {
        "schemaVersion",
        "manifestId",
        "windowId",
        "leaseId",
        "profileId",
        "profileVersion",
        "workloadId",
        "repo",
        "environment",
        "artifactDigest",
        "mechanism",
        "outcome",
        *_COUNTERS,
        "sourceChecksum",
        "observationChecksum",
        "reasons",
        "reasonCounts",
        "emitterCounts",
        "closedAt",
    }
)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_MECHANISMS = frozenset({"OPENLINEAGE", "SDK", "OTEL", "DASK"})
_OUTCOMES = frozenset({"COMPLETE", "INCOMPLETE", "EXPIRED", "REVOKED", "DISABLED"})


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value.encode()) <= 2_048


def _counter_set(value: object) -> dict[str, int] | None:
    if not isinstance(value, Mapping) or set(value) != set(_COUNTERS):
        return None
    if not all(
        isinstance(value[name], int)
        and not isinstance(value[name], bool)
        and value[name] >= 0
        for name in _COUNTERS
    ):
        return None
    return {name: int(value[name]) for name in _COUNTERS}


def runtime_window_manifest_errors(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, Mapping) or set(payload) != _REQUIRED_FIELDS:
        return ("SCHEMA_FIELDS",)
    errors: list[str] = []
    for name in (
        "manifestId",
        "windowId",
        "leaseId",
        "profileId",
        "profileVersion",
        "workloadId",
        "repo",
        "environment",
    ):
        if not _text(payload[name]):
            errors.append(f"INVALID_{name.upper()}")
    if payload["schemaVersion"] != "1.0.0":
        errors.append("SCHEMA_VERSION")
    for name in ("artifactDigest", "sourceChecksum", "observationChecksum"):
        if not isinstance(payload[name], str) or _DIGEST.fullmatch(payload[name]) is None:
            errors.append(f"INVALID_{name.upper()}")
    if payload["mechanism"] not in _MECHANISMS:
        errors.append("INVALID_MECHANISM")
    if payload["outcome"] not in _OUTCOMES:
        errors.append("INVALID_OUTCOME")
    try:
        closed = datetime.fromisoformat(str(payload["closedAt"]).replace("Z", "+00:00"))
        if closed.tzinfo is None:
            raise ValueError
    except ValueError:
        errors.append("INVALID_CLOSED_AT")

    counters = _counter_set({name: payload[name] for name in _COUNTERS})
    if counters is None:
        errors.append("INVALID_COUNTERS")
        return tuple(sorted(set(errors)))
    if counters["attempted"] != (
        counters["accepted"] + counters["rejected"] + counters["duplicates"]
    ):
        errors.append("ATTEMPTED_ARITHMETIC")
    if counters["drained"] > counters["accepted"]:
        errors.append("DRAINED_ARITHMETIC")

    emitters = payload["emitterCounts"]
    if not isinstance(emitters, Mapping) or not emitters or len(emitters) > 128:
        errors.append("INVALID_EMITTER_COUNTS")
    else:
        totals = Counter({name: 0 for name in _COUNTERS})
        for emitter, value in emitters.items():
            emitter_counters = _counter_set(value)
            if not _text(emitter) or emitter_counters is None:
                errors.append("INVALID_EMITTER_COUNTS")
                continue
            if emitter_counters["attempted"] != (
                emitter_counters["accepted"]
                + emitter_counters["rejected"]
                + emitter_counters["duplicates"]
            ) or emitter_counters["drained"] > emitter_counters["accepted"]:
                errors.append("INVALID_EMITTER_ARITHMETIC")
            totals.update(emitter_counters)
        if any(totals[name] != counters[name] for name in _COUNTERS):
            errors.append("EMITTER_TOTAL_MISMATCH")

    reasons = payload["reasons"]
    reason_counts = payload["reasonCounts"]
    if (
        not isinstance(reasons, list)
        or len(reasons) > 64
        or not all(_text(reason) for reason in reasons)
        or len(set(reasons)) != len(reasons)
        or not isinstance(reason_counts, Mapping)
        or set(reason_counts) != set(reasons)
        or not all(
            isinstance(count, int) and not isinstance(count, bool) and count >= 1
            for count in reason_counts.values()
        )
    ):
        errors.append("INVALID_REASON_COUNTS")
    else:
        classified = (
            counters["rejected"]
            + counters["buffered"]
            + counters["dropped"]
            + counters["quarantined"]
        )
        if payload["outcome"] in {"EXPIRED", "REVOKED", "DISABLED"}:
            classified += 1
        if sum(reason_counts.values()) != classified:
            errors.append("REASON_TOTAL_MISMATCH")

    if payload["outcome"] == "COMPLETE":
        if (
            counters["rejected"]
            or counters["buffered"]
            or counters["dropped"]
            or counters["quarantined"]
            or counters["accepted"] != counters["drained"]
            or reasons
            or reason_counts
        ):
            errors.append("COMPLETE_CONTAINS_LOSS")
    elif not reasons:
        errors.append("INCOMPLETE_WITHOUT_REASON")
    return tuple(sorted(set(errors)))


__all__ = ["runtime_window_manifest_errors"]
