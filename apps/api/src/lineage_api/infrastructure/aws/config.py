from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse


_PLACEHOLDERS = {"", "fixture-only", "local-synth", "notconfigured", "none", "undefined"}


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if value.lower() in _PLACEHOLDERS:
        suffix = " contains a placeholder" if value else " is required"
        raise RuntimeError(f"{name}{suffix}")
    return value


@dataclass(frozen=True, slots=True)
class AwsRuntimeConfig:
    region: str
    control_table: str
    ledger_table: str
    proposal_table: str
    pointer_table: str
    evidence_bucket: str
    package_bucket: str
    runtime_stream: str
    neptune_endpoint: str
    enterprise_endpoint: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "AwsRuntimeConfig":
        config = cls(
            control_table=_required(env, "LINEAGE_CONTROL_TABLE"),
            ledger_table=_required(env, "LINEAGE_LEDGER_TABLE"),
            proposal_table=_required(env, "LINEAGE_PROPOSAL_TABLE"),
            pointer_table=_required(env, "LINEAGE_POINTER_TABLE"),
            evidence_bucket=_required(env, "LINEAGE_EVIDENCE_BUCKET"),
            package_bucket=_required(env, "LINEAGE_PACKAGE_BUCKET"),
            runtime_stream=_required(env, "LINEAGE_RUNTIME_STREAM"),
            neptune_endpoint=_required(env, "LINEAGE_NEPTUNE_ENDPOINT"),
            enterprise_endpoint=_required(env, "LINEAGE_ENTERPRISE_ENDPOINT"),
            region=_required(env, "AWS_REGION"),
        )
        endpoint = urlparse(config.enterprise_endpoint)
        if endpoint.scheme != "https" or not endpoint.hostname:
            raise RuntimeError("LINEAGE_ENTERPRISE_ENDPOINT must be an HTTPS endpoint")
        if "://" in config.neptune_endpoint or "/" in config.neptune_endpoint:
            raise RuntimeError("LINEAGE_NEPTUNE_ENDPOINT must be a host name")
        return config
