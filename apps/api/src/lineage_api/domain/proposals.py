from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lineage_api.domain.errors import DomainError
from lineage_api.domain.evidence import EvidenceRef


TRANSITIONS = {
    "DRAFT": {"IN_REVIEW"},
    "IN_REVIEW": {"APPROVED", "REJECTED", "SUPERSEDED"},
    "APPROVED": {"FINALIZED"},
    "REJECTED": set(),
    "SUPERSEDED": set(),
    "FINALIZED": set(),
}


def allowed_transitions(state: str) -> set[str]:
    try:
        return set(TRANSITIONS[state])
    except KeyError as error:
        raise ValueError(f"Unknown proposal state: {state}") from error


def assert_transition(current: str, requested: str, correlation_id: str) -> None:
    if requested not in allowed_transitions(current):
        raise DomainError(
            "INVALID_PROPOSAL_TRANSITION",
            f"Proposal cannot move from {current} to {requested}",
            correlation_id,
            {"current": current, "requested": requested},
        )


@dataclass(frozen=True, slots=True)
class Proposal:
    proposal_id: str
    version: int
    system: str
    state: str
    expected_base_version: str
    diff: dict[str, list[dict[str, Any]]]
    correlation_id: str
    created_at: str
    updated_at: str
    lock_version: int
    auto_publish_override: str | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    decision: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schemaVersion": "1.0.0",
            "proposalId": self.proposal_id,
            "version": self.version,
            "system": self.system,
            "state": self.state,
            "expectedBaseVersion": self.expected_base_version,
            "diff": self.diff,
            "correlationId": self.correlation_id,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "lockVersion": self.lock_version,
        }
        if self.auto_publish_override is not None:
            payload["autoPublishOverride"] = self.auto_publish_override
        if self.supersedes is not None:
            payload["supersedes"] = self.supersedes
        if self.superseded_by is not None:
            payload["supersededBy"] = self.superseded_by
        if self.decision is not None:
            payload["decision"] = self.decision
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Proposal":
        return cls(
            proposal_id=payload["proposalId"],
            version=int(payload["version"]),
            system=payload["system"],
            state=payload["state"],
            expected_base_version=payload["expectedBaseVersion"],
            diff=payload["diff"],
            correlation_id=payload["correlationId"],
            created_at=payload["createdAt"],
            updated_at=payload["updatedAt"],
            lock_version=int(payload["lockVersion"]),
            auto_publish_override=payload.get("autoPublishOverride"),
            supersedes=payload.get("supersedes"),
            superseded_by=payload.get("supersededBy"),
            decision=payload.get("decision"),
        )


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    approval_id: str
    proposal_id: str
    proposal_version: int
    decision: str
    actor: str
    rationale: str
    correlation_id: str
    decided_at: str
    reference: EvidenceRef


@dataclass(frozen=True, slots=True)
class ReviewDecisionResult:
    proposal: Proposal
    approval: ApprovalRecord
