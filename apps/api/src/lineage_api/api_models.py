from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lineage_api.application.models import parse_utc


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PushRequest(ApiModel):
    payload: dict[str, Any]
    signature: str


class ReviewRequest(ApiModel):
    version: int = Field(ge=1)
    actor: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    expectedLockVersion: int = Field(ge=1)


class CorrectionRequest(ReviewRequest):
    correctedEdges: list[dict[str, Any]] = Field(min_length=1)


class ImpactRequest(ApiModel):
    subject: str
    changeType: Literal[
        "COLUMN_DROP",
        "COLUMN_TYPE_CHANGE",
        "DATASET_REMOVAL",
        "COLUMN_RENAME",
        "TRANSFORM_CHANGE",
        "FINGERPRINT_DRIFT",
    ]
    depth: int = Field(default=5)
    version: str | None = None


class PRGateChangeRequest(ApiModel):
    changeType: Literal[
        "COLUMN_DROP",
        "COLUMN_TYPE_CHANGE",
        "DATASET_REMOVAL",
        "COLUMN_RENAME",
        "TRANSFORM_CHANGE",
        "FINGERPRINT_DRIFT",
    ]
    subject: str = Field(min_length=1)
    evidenceMechanisms: list[
        Literal["SCA", "NATIVE", "MANIFEST", "CACHE", "LLM"]
    ] = Field(min_length=1)


class PRGateEvaluationRequest(ApiModel):
    repo: str = Field(min_length=1)
    prNumber: int = Field(ge=1)
    headSha: str = Field(min_length=1)
    targetEnvironment: str = Field(min_length=1)
    policyVersion: str = Field(min_length=1)
    candidateArtifactDigest: str = Field(min_length=1)
    coverageComplete: bool
    changes: list[PRGateChangeRequest] = Field(min_length=1)
    depth: int = Field(default=5, ge=1, le=5)


class DeploymentEventPayloadRequest(ApiModel):
    schemaVersion: Literal["1.0.0"]
    eventId: str = Field(min_length=1)
    eventType: Literal["DEPLOYMENT", "ROLLBACK", "MERGE"]
    provider: str = Field(min_length=1)
    providerSequence: int = Field(ge=1)
    attempt: int = Field(ge=1)
    system: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    outcome: Literal["SUCCEEDED", "FAILED"]
    artifactDigest: str | None = Field(default=None, min_length=1)
    correlationId: str = Field(min_length=1)
    auditRef: str = Field(min_length=1)
    occurredAt: str = Field(min_length=1)

    @field_validator("occurredAt")
    @classmethod
    def validate_occurred_at(cls, value: str) -> str:
        parse_utc(value)
        return value

    @model_validator(mode="after")
    def validate_outcome_identity(self) -> "DeploymentEventPayloadRequest":
        if self.eventType == "ROLLBACK" and self.outcome != "SUCCEEDED":
            raise ValueError("rollback must be an explicit successful outcome")
        if (
            self.eventType in {"DEPLOYMENT", "ROLLBACK"}
            and self.outcome == "SUCCEEDED"
            and self.artifactDigest is None
        ):
            raise ValueError("successful deployment outcomes require an artifact digest")
        return self


class DeploymentOutcomeRequest(ApiModel):
    payload: DeploymentEventPayloadRequest
    signature: str = Field(min_length=1)
