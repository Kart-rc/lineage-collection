from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


_RFC3339 = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("date-time", raises=(TypeError, ValueError))
def _is_rfc3339_datetime(value: object) -> bool:
    if not isinstance(value, str) or _RFC3339.fullmatch(value) is None:
        return False
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.tzinfo is not None


@dataclass(frozen=True, slots=True)
class ContractError:
    path: str
    message: str


class ContractRegistry:
    """Loads and validates the versioned JSON contracts shared by the prototype."""

    def __init__(self, contract_directory: Path) -> None:
        self._schemas: dict[str, dict[str, Any]] = {}
        for path in sorted(contract_directory.glob("*.schema.json")):
            name = path.name.removesuffix(".schema.json")
            schema = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            self._schemas[name] = schema

    def names(self) -> set[str]:
        return set(self._schemas)

    def schema(self, name: str) -> dict[str, Any]:
        try:
            return self._schemas[name]
        except KeyError as error:
            raise KeyError(f"Unknown contract: {name}") from error

    def validate(self, name: str, payload: object) -> list[ContractError]:
        validator = Draft202012Validator(self.schema(name), format_checker=_FORMAT_CHECKER)
        errors: list[ContractError] = []
        for error in sorted(validator.iter_errors(payload), key=str):
            path = ".".join(str(part) for part in error.absolute_path)
            if error.validator == "required":
                for missing in error.validator_value:
                    if isinstance(error.instance, dict) and missing in error.instance:
                        continue
                    missing_path = ".".join(part for part in (path, missing) if part)
                    errors.append(ContractError(path=missing_path, message=f"'{missing}' is required"))
                continue
            errors.append(ContractError(path=path, message=error.message))
        if name == "coverage-manifest" and isinstance(payload, dict):
            errors.extend(self._validate_coverage_manifest(payload))
        if name == "runtime-window-manifest" and isinstance(payload, dict):
            errors.extend(self._validate_runtime_window_manifest(payload))
        return errors

    @staticmethod
    def _validate_coverage_manifest(payload: dict[str, Any]) -> list[ContractError]:
        scope_fields = (
            "completedScope",
            "reusedScope",
            "skippedScope",
            "unsupportedScope",
            "quarantinedScope",
            "failedScope",
        )
        expected = payload.get("expectedScope")
        accounted_values = [payload.get(field) for field in scope_fields]
        if not isinstance(expected, list) or not all(
            isinstance(values, list) for values in accounted_values
        ):
            return []

        accounted = [item for values in accounted_values for item in values]
        semantic_errors: list[ContractError] = []
        if payload.get("state") != "PLANNED" and Counter(expected) != Counter(accounted):
            semantic_errors.append(
                ContractError(
                    path="expectedScope",
                    message="every expected scope must be accounted exactly once",
                )
            )
        if payload.get("state") == "COMPLETE" and any(
            payload.get(field) for field in ("unsupportedScope", "quarantinedScope", "failedScope")
        ):
            semantic_errors.append(
                ContractError(
                    path="state",
                    message="COMPLETE coverage cannot contain unsupported, quarantined, or failed scope",
                )
            )
        return semantic_errors

    @staticmethod
    def _validate_runtime_window_manifest(payload: dict[str, Any]) -> list[ContractError]:
        counter_names = (
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
        counters = {
            name: payload.get(name)
            for name in counter_names
        }
        if not all(isinstance(value, int) for value in counters.values()):
            return []
        errors: list[ContractError] = []
        if counters["attempted"] != (
            counters["accepted"] + counters["rejected"] + counters["duplicates"]
        ):
            errors.append(
                ContractError(
                    path="attempted",
                    message="attempted must equal accepted plus rejected plus duplicates",
                )
            )
        if counters["drained"] > counters["accepted"]:
            errors.append(
                ContractError(path="drained", message="drained cannot exceed accepted")
            )
        emitter_counts = payload.get("emitterCounts")
        if isinstance(emitter_counts, dict) and all(
            isinstance(values, dict) for values in emitter_counts.values()
        ):
            for emitter, values in emitter_counts.items():
                if not all(isinstance(values.get(name), int) for name in counter_names):
                    continue
                if values["attempted"] != (
                    values["accepted"] + values["rejected"] + values["duplicates"]
                ):
                    errors.append(
                        ContractError(
                            path=f"emitterCounts.{emitter}.attempted",
                            message=(
                                "emitter attempted must equal accepted plus rejected "
                                "plus duplicates"
                            ),
                        )
                    )
                if values["drained"] > values["accepted"]:
                    errors.append(
                        ContractError(
                            path=f"emitterCounts.{emitter}.drained",
                            message="emitter drained cannot exceed accepted",
                        )
                    )
            totals = {
                name: sum(
                    int(values.get(name, 0))
                    for values in emitter_counts.values()
                    if isinstance(values.get(name, 0), int)
                )
                for name in counter_names
            }
            if totals != counters:
                errors.append(
                    ContractError(
                        path="emitterCounts",
                        message="emitter counters must sum exactly to aggregate counters",
                    )
                )
        reasons = payload.get("reasons")
        reason_counts = payload.get("reasonCounts")
        if isinstance(reasons, list) and isinstance(reason_counts, dict):
            if set(reasons) != set(reason_counts):
                errors.append(
                    ContractError(
                        path="reasonCounts",
                        message="reason count keys must match the distinct reasons",
                    )
                )
            if all(isinstance(count, int) for count in reason_counts.values()):
                classified_failures = (
                    counters["rejected"]
                    + counters["buffered"]
                    + counters["dropped"]
                    + counters["quarantined"]
                )
                if payload.get("outcome") in {"EXPIRED", "REVOKED", "DISABLED"}:
                    classified_failures += 1
                if sum(reason_counts.values()) != classified_failures:
                    errors.append(
                        ContractError(
                            path="reasonCounts",
                            message=(
                                "reason counts must exactly classify rejected, buffered, "
                                "dropped, quarantined and terminal outcome failures"
                            ),
                        )
                    )
        if payload.get("outcome") != "COMPLETE" and not reasons:
            errors.append(
                ContractError(
                    path="reasons",
                    message="non-complete runtime windows require a classified reason",
                )
            )
        if payload.get("outcome") != "COMPLETE":
            return errors
        if (
            counters["rejected"]
            or counters["buffered"]
            or counters["dropped"]
            or counters["quarantined"]
            or counters["accepted"] != counters["drained"]
            or bool(reasons)
            or bool(reason_counts)
        ):
            errors.append(
                ContractError(
                    path="outcome",
                    message="COMPLETE runtime window cannot contain loss and must drain every accepted observation",
                )
            )
        return errors
