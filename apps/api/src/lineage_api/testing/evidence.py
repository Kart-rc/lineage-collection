from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

from jsonschema import Draft202012Validator


Outcome = Literal["PASS", "FAIL", "WAIVED", "AWS_REQUIRED", "NOT_CONFIGURED"]
_OUTCOMES = frozenset({"PASS", "FAIL", "WAIVED", "AWS_REQUIRED", "NOT_CONFIGURED"})
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_BUILD = re.compile(r"^B(0[1-9]|1[0-6])$")
_ACCEPTANCE = re.compile(r"^B(0[1-9]|1[0-6])-AC-[0-9]{3}$")
_SCENARIO = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
_IGNORED_PARTS = frozenset(
    {".git", ".pytest_cache", ".venv", "__pycache__", "data", "dist", "node_modules", "output"}
)
_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_SCHEMA_PATH = _PROJECT_ROOT / "packages/contracts/acceptance-evidence-manifest.schema.json"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def digest_tree(root: Path, included_paths: Sequence[str]) -> str:
    """Digest the exact named source set, including paths and bytes in stable order."""
    root = root.resolve()
    files: list[Path] = []
    for relative in included_paths:
        candidate = (root / relative).resolve()
        if root not in candidate.parents and candidate != root:
            raise ValueError(f"included path escapes artifact root: {relative}")
        if not candidate.exists():
            raise ValueError(f"included artifact path does not exist: {relative}")
        if candidate.is_file():
            files.append(candidate)
            continue
        files.extend(
            path
            for path in candidate.rglob("*")
            if path.is_file() and not (_IGNORED_PARTS & set(path.relative_to(root).parts))
        )
    digest = hashlib.sha256()
    for path in sorted(set(files), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        body = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return f"sha256:{digest.hexdigest()}"


def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))


def _write_immutable(path: Path, encoded: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise RuntimeError(f"refusing to replace immutable acceptance evidence: {path}")
        return
    path.write_bytes(encoded)


class ScenarioEvidence:
    def __init__(
        self,
        writer: "AcceptanceEvidenceWriter",
        *,
        build_id: str,
        acceptance_id: str,
        scenario_id: str,
        environment: str,
        thresholds: Mapping[str, object] | None,
        fault_point: str | None,
        correlation_id: str | None,
    ) -> None:
        self.writer = writer
        self.build_id = build_id
        self.acceptance_id = acceptance_id
        self.scenario_id = scenario_id
        self.environment = environment
        self.thresholds = dict(thresholds or {})
        self.fault_point = fault_point
        self.correlation_id = correlation_id
        self.metrics: dict[str, object] = {}
        self.checksums: dict[str, str] = {}
        self.evidence_refs: list[str] = []
        self.manifest: dict[str, Any] | None = None

    def metric(self, name: str, value: object) -> None:
        if not name:
            raise ValueError("metric name must not be empty")
        self.metrics[name] = value

    def checksum(self, name: str, digest: str) -> None:
        if not name or _DIGEST.fullmatch(digest) is None:
            raise ValueError("evidence checksums must be named sha256 digests")
        self.checksums[name] = digest

    def reference(self, reference: str) -> None:
        if not reference:
            raise ValueError("evidence reference must not be empty")
        self.evidence_refs.append(reference)

    def __enter__(self) -> "ScenarioEvidence":
        return self

    def __exit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        outcome: Outcome = "PASS" if error_type is None else "FAIL"
        self.manifest = self.writer.record(
            build_id=self.build_id,
            acceptance_id=self.acceptance_id,
            scenario_id=self.scenario_id,
            outcome=outcome,
            environment=self.environment,
            metrics=self.metrics,
            checksums=self.checksums,
            thresholds=self.thresholds,
            fault_point=self.fault_point,
            correlation_id=self.correlation_id,
            evidence_refs=self.evidence_refs,
            error_type=None if error is None else type(error).__name__,
        )
        return False


class AcceptanceEvidenceWriter:
    def __init__(self, output_root: Path, *, artifact_digest: str) -> None:
        if _DIGEST.fullmatch(artifact_digest) is None:
            raise ValueError("artifact digest must be an exact sha256:<64 lowercase hex> value")
        self.output_root = output_root.resolve()
        self.artifact_digest = artifact_digest
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._validator = _validator()

    def scenario(
        self,
        *,
        build_id: str,
        acceptance_id: str,
        scenario_id: str,
        environment: str,
        thresholds: Mapping[str, object] | None = None,
        fault_point: str | None = None,
        correlation_id: str | None = None,
    ) -> ScenarioEvidence:
        return ScenarioEvidence(
            self,
            build_id=build_id,
            acceptance_id=acceptance_id,
            scenario_id=scenario_id,
            environment=environment,
            thresholds=thresholds,
            fault_point=fault_point,
            correlation_id=correlation_id,
        )

    def record(
        self,
        *,
        build_id: str,
        acceptance_id: str,
        scenario_id: str,
        outcome: Outcome,
        environment: str,
        metrics: Mapping[str, object] | None = None,
        checksums: Mapping[str, str] | None = None,
        thresholds: Mapping[str, object] | None = None,
        fault_point: str | None = None,
        correlation_id: str | None = None,
        evidence_refs: Iterable[str] = (),
        error_type: str | None = None,
    ) -> dict[str, Any]:
        if outcome not in _OUTCOMES:
            raise ValueError(f"unsupported acceptance outcome: {outcome}")
        if _BUILD.fullmatch(build_id) is None:
            raise ValueError("build ID must match B01 through B16")
        if _ACCEPTANCE.fullmatch(acceptance_id) is None:
            raise ValueError("acceptance ID must be a stable Bxx-AC-nnn identifier")
        if acceptance_id.rsplit("-AC-", 1)[0] != build_id:
            raise ValueError("acceptance ID must belong to its build ID")
        if _SCENARIO.fullmatch(scenario_id) is None:
            raise ValueError("scenario ID must be a lowercase, bounded slug")
        if not environment:
            raise ValueError("acceptance environment must not be empty")
        checksum_values = dict(checksums or {})
        if any(_DIGEST.fullmatch(value) is None for value in checksum_values.values()):
            raise ValueError("all acceptance checksums must be exact sha256 digests")
        detail = {
            "schemaVersion": "1.0.0",
            "buildId": build_id,
            "acceptanceId": acceptance_id,
            "scenarioId": scenario_id,
            "artifactDigest": self.artifact_digest,
            "outcome": outcome,
            "environment": environment,
            "metrics": dict(metrics or {}),
            "checksums": checksum_values,
            "thresholds": dict(thresholds or {}),
            "faultPoint": fault_point,
            "correlationId": correlation_id,
            "errorType": error_type,
        }
        detail_body = _canonical(detail) + b"\n"
        detail_digest = hashlib.sha256(detail_body).hexdigest()
        relative = Path(build_id) / acceptance_id / f"{scenario_id}-{detail_digest[:16]}.evidence.json"
        _write_immutable(self.output_root / relative, detail_body)
        references = [
            *sorted(set(evidence_refs)),
            f"acceptance:{relative.as_posix()}#sha256:{detail_digest}",
        ]
        manifest = {
            "schemaVersion": "1.0.0",
            "buildId": build_id,
            "acceptanceId": acceptance_id,
            "artifactDigest": self.artifact_digest,
            "outcome": outcome,
            "evidenceRefs": references,
        }
        self._validator.validate(manifest)
        manifest_body = _canonical(manifest) + b"\n"
        manifest_digest = hashlib.sha256(manifest_body).hexdigest()
        manifest_path = (
            self.output_root
            / build_id
            / acceptance_id
            / f"{scenario_id}-{manifest_digest[:16]}.manifest.json"
        )
        _write_immutable(manifest_path, manifest_body)
        return manifest


def load_manifests(root: Path) -> list[dict[str, Any]]:
    validator = _validator()
    manifests = []
    for path in sorted(root.rglob("*.manifest.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        validator.validate(document)
        manifests.append(document)
    return manifests


def evidence_exit_code(root: Path) -> int:
    manifests = load_manifests(root)
    if not manifests or any(manifest["outcome"] == "FAIL" for manifest in manifests):
        return 1
    return 0


def summarize(root: Path) -> int:
    manifests = load_manifests(root)
    counts = Counter(manifest["outcome"] for manifest in manifests)
    print(
        json.dumps(
            {"manifestCount": len(manifests), "outcomes": dict(sorted(counts.items()))},
            sort_keys=True,
        )
    )
    return evidence_exit_code(root)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and summarize acceptance evidence")
    parser.add_argument("command", choices=("summarize",))
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    return summarize(args.root)


if __name__ == "__main__":
    raise SystemExit(main())
