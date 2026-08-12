from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from lineage_api.domain.urns import LineageUrn


CONFIG_PATTERN = re.compile(r"\$\{(?P<key>[A-Za-z_][A-Za-z0-9_]*)\}")

# The product model groups and colours lineage by the kind of system a dataset lives in.
# Kind is catalog-owned: it is declared, never inferred from a name or a platform.
DATASET_KINDS = frozenset(
    {"DATASTORE", "STREAM", "LAKE_LANDING", "LAKE_FILE", "CACHE", "SEARCH", "UNKNOWN"}
)


@dataclass(frozen=True, slots=True)
class RawName:
    kind: str
    value: str
    source: str
    elements: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolveContext:
    env: str
    platform: str
    system: str
    repo: str
    digest: str
    config: dict[str, str]
    snapshot_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedName:
    status: Literal["RESOLVED"]
    urn: LineageUrn
    element_urns: tuple[LineageUrn, ...]
    catalog_ref: str
    rules_applied: tuple[str, ...]
    resolver_version: str
    snapshot_id: str
    kind: str = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class QuarantinedName:
    status: Literal["QUARANTINED"]
    raw: RawName
    reason: str
    candidates: tuple[str, ...]
    gap_report: dict[str, Any]
    resolver_version: str
    snapshot_id: str


ResolutionResult = ResolvedName | QuarantinedName


@dataclass(frozen=True, slots=True)
class _NormalizedName:
    platform: str
    system: str
    dataset: str
    sanitized_raw: RawName
    rules: tuple[str, ...]


class Resolver:
    def __init__(self, catalog: dict[str, Any]) -> None:
        self._catalog = catalog
        self.snapshot_id = str(catalog["snapshotId"])
        self.resolver_version = str(catalog["resolverVersion"])
        self._datasets = tuple(catalog["datasets"])
        for dataset in self._datasets:
            kind = dataset.get("kind", "UNKNOWN")
            if kind not in DATASET_KINDS:
                raise ValueError(f"unrecognised dataset kind: {kind!r}")

    @classmethod
    def from_path(cls, path: Path) -> "Resolver":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def resolve(self, raw: RawName, context: ResolveContext) -> ResolutionResult:
        if context.snapshot_id is not None and context.snapshot_id != self.snapshot_id:
            return self._quarantine(
                raw,
                "SNAPSHOT_MISMATCH",
                (),
                {"requestedSnapshot": context.snapshot_id},
            )

        normalized = self._normalize(raw, context)
        if isinstance(normalized, QuarantinedName):
            return normalized

        vocabulary = self._catalog["vocabulary"]
        if (
            context.env not in vocabulary["environments"]
            or normalized.platform not in vocabulary["platforms"]
            or normalized.system not in vocabulary["systems"]
        ):
            return self._quarantine(
                normalized.sanitized_raw,
                "VOCAB_VIOLATION",
                (),
                {
                    "env": context.env,
                    "platform": normalized.platform,
                    "system": normalized.system,
                },
            )

        matches = self._catalog_matches(normalized)
        if not matches:
            return self._quarantine(
                normalized.sanitized_raw,
                "UNKNOWN_DATASET",
                (),
                {"normalizedValue": normalized.dataset},
            )
        if len(matches) > 1:
            candidates = tuple(sorted(str(dataset["catalogRef"]) for dataset in matches))
            return self._quarantine(
                normalized.sanitized_raw,
                "AMBIGUOUS",
                candidates,
                {"normalizedValue": normalized.dataset},
            )

        dataset = matches[0]
        catalog_elements = {str(element["name"]) for element in dataset.get("elements", [])}
        unknown_elements = [element for element in raw.elements if element not in catalog_elements]
        if unknown_elements:
            return self._quarantine(
                normalized.sanitized_raw,
                "UNKNOWN_ELEMENT",
                (),
                {"normalizedValue": normalized.dataset, "elements": unknown_elements},
            )

        urn = LineageUrn(
            env=context.env,
            platform=str(dataset["platform"]),
            system=str(dataset["system"]),
            dataset=str(dataset["name"]),
        )
        return ResolvedName(
            status="RESOLVED",
            urn=urn,
            element_urns=tuple(urn.with_element(element) for element in raw.elements),
            catalog_ref=str(dataset["catalogRef"]),
            rules_applied=(*normalized.rules, "catalog-match"),
            resolver_version=self.resolver_version,
            snapshot_id=self.snapshot_id,
            kind=str(dataset.get("kind", "UNKNOWN")),
        )

    def resolve_batch(
        self, raw_names: list[RawName], context: ResolveContext
    ) -> list[ResolutionResult]:
        pinned_context = replace(context, snapshot_id=self.snapshot_id)
        return [self.resolve(raw_name, pinned_context) for raw_name in raw_names]

    def _normalize(
        self, raw: RawName, context: ResolveContext
    ) -> _NormalizedName | QuarantinedName:
        sanitized_value = self._strip_credentials(raw.value.strip())
        sanitized_raw = replace(raw, value=sanitized_value)
        value = unquote(sanitized_value)
        rules: list[str] = []

        missing_keys = [
            match.group("key")
            for match in CONFIG_PATTERN.finditer(value)
            if match.group("key") not in context.config
        ]
        if missing_keys:
            return self._quarantine(
                sanitized_raw,
                "UNRESOLVED_CONFIG",
                (),
                {"missingKeys": sorted(set(missing_keys))},
            )
        if CONFIG_PATTERN.search(value):
            value = CONFIG_PATTERN.sub(lambda match: context.config[match.group("key")], value)
            rules.append("config-substitution")

        platform = context.platform
        system = context.system
        decoded_value = value
        is_jdbc = value.lower().startswith("jdbc:")
        url_value = value[5:] if is_jdbc else value
        parsed = urlsplit(url_value)
        if parsed.scheme and parsed.netloc:
            rules.insert(0, "decode")
            platform = parsed.scheme.lower()
            if not is_jdbc and parsed.hostname:
                system = parsed.hostname.lower()
            decoded_value = parsed.path.lstrip("/")

        if decoded_value.startswith("catalog://"):
            dataset_value = decoded_value
        else:
            dataset_value = decoded_value.strip("/")
            if "/" in dataset_value:
                dataset_value = dataset_value.rsplit("/", 1)[-1]
            if "." not in dataset_value and context.config.get("defaultSchema"):
                dataset_value = f"{context.config['defaultSchema']}.{dataset_value}"
                rules.append("default-schema")

        case_folded = dataset_value.lower()
        if case_folded != dataset_value or platform != context.platform or system != context.system:
            if "case-fold" not in rules:
                rules.append("case-fold")

        return _NormalizedName(
            platform=platform.lower(),
            system=system.lower(),
            dataset=case_folded,
            sanitized_raw=sanitized_raw,
            rules=tuple(rules),
        )

    def _catalog_matches(self, normalized: _NormalizedName) -> list[dict[str, Any]]:
        exact_catalog = [
            dataset
            for dataset in self._datasets
            if str(dataset["catalogRef"]).lower() == normalized.dataset
        ]
        if exact_catalog:
            return exact_catalog

        alias_matches = [
            dataset
            for dataset in self._datasets
            if normalized.dataset
            in {self._alias_key(str(alias)) for alias in dataset.get("aliases", [])}
        ]
        if alias_matches:
            return alias_matches

        return [
            dataset
            for dataset in self._datasets
            if str(dataset["name"]).lower() == normalized.dataset
            and str(dataset["platform"]).lower() == normalized.platform
            and str(dataset["system"]).lower() == normalized.system
        ]

    @staticmethod
    def _alias_key(value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme and parsed.netloc:
            return parsed.path.lstrip("/").lower()
        return value.lower()

    @staticmethod
    def _strip_credentials(value: str) -> str:
        is_jdbc = value.lower().startswith("jdbc:")
        url_value = value[5:] if is_jdbc else value
        parsed = urlsplit(url_value)
        if not parsed.scheme or not parsed.netloc:
            return value
        hostname = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port is not None else ""
        prefix = "jdbc:" if is_jdbc else ""
        return f"{prefix}{parsed.scheme.lower()}://{hostname}{port}{parsed.path}"

    def _quarantine(
        self,
        raw: RawName,
        reason: str,
        candidates: tuple[str, ...],
        gap_report: dict[str, Any],
    ) -> QuarantinedName:
        sanitized_raw = replace(raw, value=self._strip_credentials(raw.value))
        return QuarantinedName(
            status="QUARANTINED",
            raw=sanitized_raw,
            reason=reason,
            candidates=candidates,
            gap_report=gap_report,
            resolver_version=self.resolver_version,
            snapshot_id=self.snapshot_id,
        )
