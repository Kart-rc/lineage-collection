from __future__ import annotations

from typing import Mapping, Protocol, Sequence

from lineage_api.runtime.models import EmitResult, FieldMapping


class Producer(Protocol):
    def enqueue(self, payload: dict[str, object]) -> EmitResult: ...


class ConfigurableRuntimeSDK:
    """Thin, fail-open metadata builder; catalog resolution stays downstream."""

    def __init__(
        self,
        *,
        producer: Producer,
        enabled: bool = True,
        max_datasets: int = 64,
        max_aliases: int = 32,
        max_field_mappings: int = 256,
        max_string_bytes: int = 2048,
    ) -> None:
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 1
            for value in (max_datasets, max_aliases, max_field_mappings, max_string_bytes)
        ):
            raise ValueError("runtime SDK metadata limits must be positive integers")
        self._producer = producer
        self._enabled = enabled
        self._max_datasets = max_datasets
        self._max_aliases = max_aliases
        self._max_field_mappings = max_field_mappings
        self._max_string_bytes = max_string_bytes

    def read(
        self,
        dataset: str,
        *,
        aliases: Mapping[str, str] | None = None,
    ) -> EmitResult:
        self._require_dataset(dataset)
        return self._emit("READ", (dataset,), (), aliases=aliases)

    def write(
        self,
        dataset: str,
        *,
        aliases: Mapping[str, str] | None = None,
    ) -> EmitResult:
        self._require_dataset(dataset)
        return self._emit("WRITE", (), (dataset,), aliases=aliases)

    def derive(
        self,
        source_datasets: Sequence[str],
        target_dataset: str,
        *,
        aliases: Mapping[str, str] | None = None,
        field_mappings: Sequence[FieldMapping] = (),
    ) -> EmitResult:
        if not source_datasets:
            raise ValueError("runtime derive requires at least one source dataset")
        if len(source_datasets) + 1 > self._max_datasets:
            raise ValueError("runtime SDK dataset limit exceeded")
        for dataset in source_datasets:
            self._require_dataset(dataset)
        self._require_dataset(target_dataset)
        return self._emit(
            "DERIVE",
            source_datasets,
            (target_dataset,),
            aliases=aliases,
            field_mappings=field_mappings,
        )

    def connect(
        self,
        source_dataset: str,
        target_dataset: str,
        *,
        aliases: Mapping[str, str] | None = None,
    ) -> EmitResult:
        self._require_dataset(source_dataset)
        self._require_dataset(target_dataset)
        return self._emit(
            "CONNECT",
            (source_dataset,),
            (target_dataset,),
            aliases=aliases,
        )

    def _emit(
        self,
        operation: str,
        sources: Sequence[str],
        targets: Sequence[str],
        *,
        aliases: Mapping[str, str] | None,
        field_mappings: Sequence[FieldMapping] = (),
    ) -> EmitResult:
        if not self._enabled:
            return EmitResult(False, None, "SDK_DISABLED")
        if len(sources) + len(targets) > self._max_datasets:
            raise ValueError("runtime SDK dataset limit exceeded")
        if aliases is not None and len(aliases) > self._max_aliases:
            raise ValueError("runtime SDK alias limit exceeded")
        if len(field_mappings) > self._max_field_mappings:
            raise ValueError("runtime SDK field mapping limit exceeded")
        alias_payload = dict(sorted((aliases or {}).items()))
        if not all(
            isinstance(key, str) and key and isinstance(value, str) and value
            for key, value in alias_payload.items()
        ):
            raise ValueError("runtime aliases must contain non-empty metadata strings")
        for key, value in alias_payload.items():
            self._require_string_limit(key)
            self._require_string_limit(value)
        for mapping in field_mappings:
            for value in (
                mapping.source_dataset,
                mapping.source_field,
                mapping.target_dataset,
                mapping.target_field,
            ):
                self._require_string_limit(value)
        mapping_payload = [mapping.as_payload() for mapping in field_mappings]
        payload: dict[str, object] = {
            "operation": operation,
            "sourceDatasets": sorted(set(sources)),
            "targetDatasets": sorted(set(targets)),
            "aliases": alias_payload,
            "fieldMappings": sorted(
                mapping_payload,
                key=lambda item: (
                    item["sourceDataset"],
                    item["sourceField"],
                    item["targetDataset"],
                    item["targetField"],
                ),
            ),
        }
        try:
            return self._producer.enqueue(payload)
        except Exception:
            return EmitResult(False, None, "PRODUCER_INTERNAL_ERROR")

    def _require_dataset(self, dataset: str) -> None:
        if not isinstance(dataset, str) or not dataset:
            raise ValueError("runtime dataset metadata must be non-empty")
        self._require_string_limit(dataset)

    def _require_string_limit(self, value: str) -> None:
        if len(value) > self._max_string_bytes or len(value.encode()) > self._max_string_bytes:
            raise ValueError("runtime SDK string limit exceeded")
