from __future__ import annotations

from dataclasses import dataclass

from lineage_api.application.models import CoverageManifest, CoverageState


class CoverageIncompleteError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CoverageVerification:
    ready: bool
    expected_scope: int
    accounted_scope: int
    errors: tuple[str, ...]


class CoverageVerifier:
    def verify(self, manifest: CoverageManifest) -> CoverageVerification:
        errors = list(manifest.accounting_errors())
        if manifest.state is not CoverageState.COMPLETE:
            errors.insert(0, f"coverage state is not COMPLETE: {manifest.state.value}")
        accounted = (
            manifest.completed_scope
            + manifest.reused_scope
            + manifest.skipped_scope
            + manifest.unsupported_scope
            + manifest.quarantined_scope
            + manifest.failed_scope
        )
        return CoverageVerification(
            ready=not errors,
            expected_scope=len(manifest.expected_scope),
            accounted_scope=len(accounted),
            errors=tuple(errors),
        )

    def require_complete(self, manifest: CoverageManifest) -> CoverageVerification:
        result = self.verify(manifest)
        if not result.ready:
            raise CoverageIncompleteError("; ".join(result.errors))
        return result
