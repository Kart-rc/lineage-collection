from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping

from lineage_api.runtime.adapters import AdapterIssue, AdapterResult, canonical, checksum


_MAX_DATASETS = 256
_MAX_COLUMN_MAPPINGS = 1_024
_MAX_INPUT_FIELDS = 256
_MAX_SOURCE_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class OpenLineageControl:
    schema_url: str
    lease_id: str
    profile_id: str
    profile_version: str
    artifact_digest: str

    def as_mapping(self) -> dict[str, str]:
        return {
            "_schemaURL": self.schema_url,
            "leaseId": self.lease_id,
            "profileId": self.profile_id,
            "profileVersion": self.profile_version,
            "artifactDigest": self.artifact_digest,
        }


@dataclass(frozen=True, slots=True)
class OpenLineageProfile:
    supported_schema_urls: frozenset[str]
    supported_facet_schema_urls: frozenset[str]
    permitted_granularity: frozenset[str]
    control: OpenLineageControl | None
    supported_producer_prefixes: frozenset[str] = frozenset()


class OpenLineageAdapter:
    """Deterministic OpenLineage event normalizer with explicit unsupported coverage."""

    def normalize(
        self, payload: dict[str, object], profile: OpenLineageProfile
    ) -> AdapterResult:
        unsupported: list[AdapterIssue] = []
        quarantined: list[AdapterIssue] = []
        if len(canonical(payload).encode()) > _MAX_SOURCE_BYTES:
            quarantined.append(
                AdapterIssue("OPENLINEAGE_SOURCE_LIMIT_EXCEEDED", "payload")
            )
            return AdapterResult.build(
                observations=(), unsupported=(), quarantined=quarantined, source=payload
            )
        schema_url = str(payload.get("schemaURL", ""))
        if schema_url not in profile.supported_schema_urls:
            unsupported.append(AdapterIssue("OPENLINEAGE_SCHEMA_UNSUPPORTED", "schemaURL"))
            return AdapterResult.build(
                observations=(), unsupported=unsupported, quarantined=(), source=payload
            )
        producer = str(payload.get("producer", ""))
        connector_required = bool(schema_url)
        connector_supported = any(
            producer.startswith(prefix) for prefix in profile.supported_producer_prefixes
        )
        if profile.supported_producer_prefixes and (
            (connector_required and not producer) or (producer and not connector_supported)
        ):
            unsupported.append(AdapterIssue("OPENLINEAGE_CONNECTOR_UNSUPPORTED", "producer"))
            return AdapterResult.build(
                observations=(), unsupported=unsupported, quarantined=(), source=payload
            )
        event_type = payload.get("eventType")
        if event_type not in {"START", "RUNNING", "COMPLETE", "FAIL"}:
            unsupported.append(AdapterIssue("OPENLINEAGE_EVENT_TYPE_UNSUPPORTED", "eventType"))
            return AdapterResult.build(
                observations=(), unsupported=unsupported, quarantined=(), source=payload
            )
        run = payload.get("run")
        job = payload.get("job")
        if not isinstance(run, dict) or not isinstance(job, dict):
            quarantined.append(AdapterIssue("OPENLINEAGE_SHAPE_INVALID", "run"))
            return AdapterResult.build(
                observations=(), unsupported=(), quarantined=quarantined, source=payload
            )
        run_facets = run.get("facets") if isinstance(run.get("facets"), dict) else {}
        if profile.control is not None:
            supplied = run_facets.get("lineageControl")
            if not isinstance(supplied, dict) or supplied != profile.control.as_mapping():
                quarantined.append(
                    AdapterIssue("OPENLINEAGE_CONTROL_MISMATCH", "run.facets.lineageControl")
                )
                return AdapterResult.build(
                    observations=(), unsupported=(), quarantined=quarantined, source=payload
                )
        parent_run_id: str | None = None
        parent = run_facets.get("parent")
        if isinstance(parent, dict):
            parent_schema = parent.get("_schemaURL")
            if parent_schema not in profile.supported_facet_schema_urls:
                unsupported.append(
                    AdapterIssue("OPENLINEAGE_FACET_UNSUPPORTED", "run.facets.parent")
                )
            else:
                parent_run = parent.get("run")
                if isinstance(parent_run, dict) and parent_run.get("runId"):
                    parent_run_id = str(parent_run["runId"])
        inputs = payload.get("inputs")
        outputs = payload.get("outputs")
        if not isinstance(inputs, list) or not isinstance(outputs, list):
            quarantined.append(AdapterIssue("OPENLINEAGE_SHAPE_INVALID", "datasets"))
            return AdapterResult.build(
                observations=(), unsupported=unsupported, quarantined=quarantined, source=payload
            )
        if len(inputs) > _MAX_DATASETS or len(outputs) > _MAX_DATASETS:
            quarantined.append(
                AdapterIssue("OPENLINEAGE_SOURCE_LIMIT_EXCEEDED", "datasets")
            )
            return AdapterResult.build(
                observations=(), unsupported=unsupported, quarantined=quarantined, source=payload
            )
        if (
            not inputs
            or not outputs
            or not all(isinstance(item, dict) for item in inputs)
            or not all(isinstance(item, dict) for item in outputs)
        ):
            quarantined.append(AdapterIssue("OPENLINEAGE_SHAPE_INVALID", "datasets"))
            return AdapterResult.build(
                observations=(), unsupported=unsupported, quarantined=quarantined, source=payload
            )
        input_datasets = sorted(
            {self._dataset(item) for item in inputs if isinstance(item, dict)}
        )
        if not input_datasets:
            quarantined.append(AdapterIssue("OPENLINEAGE_SHAPE_INVALID", "inputs"))
            return AdapterResult.build(
                observations=(), unsupported=unsupported, quarantined=quarantined, source=payload
            )
        source_checksum = checksum(payload)
        observations: list[dict[str, object]] = []
        for output_index, output in enumerate(
            sorted(
                (item for item in outputs if isinstance(item, dict)),
                key=self._dataset,
            )
        ):
            target_dataset = self._dataset(output)
            facets = output.get("facets") if isinstance(output.get("facets"), dict) else {}
            column = facets.get("columnLineage")
            column_supported = False
            if isinstance(column, dict):
                column_schema = column.get("_schemaURL")
                if column_schema is not None and column_schema not in profile.supported_facet_schema_urls:
                    unsupported.append(
                        AdapterIssue(
                            "OPENLINEAGE_FACET_UNSUPPORTED",
                            f"outputs[{output_index}].facets.columnLineage",
                        )
                    )
                else:
                    column_supported = "ELEMENT" in profile.permitted_granularity
            fields = column.get("fields") if column_supported and isinstance(column, dict) else None
            if isinstance(fields, dict) and fields:
                if len(fields) > _MAX_COLUMN_MAPPINGS:
                    quarantined.append(
                        AdapterIssue(
                            "OPENLINEAGE_SOURCE_LIMIT_EXCEEDED",
                            f"outputs[{output_index}].facets.columnLineage.fields",
                        )
                    )
                    return AdapterResult.build(
                        observations=(),
                        unsupported=unsupported,
                        quarantined=quarantined,
                        source=payload,
                    )
                for target_field, raw_mapping in sorted(fields.items()):
                    if not isinstance(raw_mapping, dict):
                        quarantined.append(
                            AdapterIssue("OPENLINEAGE_SHAPE_INVALID", "columnLineage.fields")
                        )
                        continue
                    raw_inputs = raw_mapping.get("inputFields")
                    if not isinstance(raw_inputs, list) or not raw_inputs:
                        quarantined.append(
                            AdapterIssue("OPENLINEAGE_SHAPE_INVALID", "columnLineage.inputFields")
                        )
                        continue
                    if len(raw_inputs) > _MAX_INPUT_FIELDS:
                        quarantined.append(
                            AdapterIssue(
                                "OPENLINEAGE_SOURCE_LIMIT_EXCEEDED",
                                "columnLineage.inputFields",
                            )
                        )
                        return AdapterResult.build(
                            observations=(),
                            unsupported=unsupported,
                            quarantined=quarantined,
                            source=payload,
                        )
                    ordered_inputs = sorted(
                        (item for item in raw_inputs if isinstance(item, dict)),
                        key=lambda item: (self._dataset(item), str(item.get("field", ""))),
                    )
                    body: dict[str, object] = {
                        "granularity": "ELEMENT",
                        "sourceDatasets": [self._dataset(item) for item in ordered_inputs],
                        "targetDataset": target_dataset,
                        "sourceFields": [str(item.get("field", "")) for item in ordered_inputs],
                        "targetField": str(target_field),
                        "edgeType": "DERIVES",
                        "transform": str(
                            raw_mapping.get("transformationDescription", "runtime mapping")
                        ),
                        "exact": True,
                    }
                    observations.append(
                        self._observation(
                            payload,
                            job,
                            body,
                            parent_run_id=parent_run_id,
                            source_checksum=source_checksum,
                        )
                    )
            elif "DATASET" in profile.permitted_granularity:
                body = {
                    "granularity": "DATASET",
                    "sourceDatasets": input_datasets,
                    "targetDataset": target_dataset,
                    "edgeType": "DERIVES",
                    "exact": True,
                }
                observations.append(
                    self._observation(
                        payload,
                        job,
                        body,
                        parent_run_id=parent_run_id,
                        source_checksum=source_checksum,
                    )
                )
            else:
                unsupported.append(
                    AdapterIssue(
                        "OPENLINEAGE_GRANULARITY_UNSUPPORTED",
                        f"outputs[{output_index}]",
                    )
                )
        return AdapterResult.build(
            observations=() if quarantined else observations,
            unsupported=unsupported,
            quarantined=quarantined,
            source=payload,
        )

    @staticmethod
    def _dataset(value: Mapping[str, object]) -> str:
        return f"{str(value.get('namespace', '')).rstrip('/')}/{value.get('name', '')}"

    @staticmethod
    def _observation(
        payload: Mapping[str, object],
        job: Mapping[str, object],
        body: dict[str, object],
        *,
        parent_run_id: str | None,
        source_checksum: str,
    ) -> dict[str, object]:
        run = payload["run"]
        run_id = str(run.get("runId", "")) if isinstance(run, dict) else ""
        identity = {
            "event": payload.get("observationId", run_id),
            "runState": payload.get("eventType"),
            "body": body,
        }
        observation: dict[str, object] = {
            "schemaVersion": "1.0.0",
            "observationId": "openlineage-"
            + hashlib.sha256(canonical(identity).encode()).hexdigest(),
            "mechanism": "OPENLINEAGE",
            "runState": str(payload["eventType"]),
            "runId": run_id,
            "jobNamespace": str(job.get("namespace", "")),
            "jobName": str(job.get("name", "")),
            "producer": str(payload.get("producer", "")),
            "observedAt": str(payload.get("eventTime", "")),
            "sourceChecksum": source_checksum,
            **body,
        }
        if parent_run_id is not None:
            observation["parentRunId"] = parent_run_id
        return observation
