from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


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
        validator = Draft202012Validator(self.schema(name))
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
