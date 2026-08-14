from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from lineage_api.application.models import parse_utc


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PushRequest(ApiModel):
    payload: dict[str, Any]
    signature: str


class CollectionRequest(ApiModel):
    """Typed mirror of the environment-neutral collection submission contract.

    Field-level validation lives in ``lineage_api.application.collections`` so FastAPI,
    the neutral product API, and the AWS entry point cannot drift; this model exists to
    reject unknown fields and obvious shape errors before the request reaches it.
    """

    sourceType: Literal["LOCAL_CHECKOUT", "GIT"]
    origin: str = Field(min_length=1, max_length=2_048)
    repository: str = Field(min_length=1, max_length=128)
    revision: str = Field(min_length=40, max_length=40, pattern=r"^[0-9a-f]{40}$")
    environment: str = Field(min_length=1, max_length=128)
    platform: str = Field(min_length=1, max_length=128)
    system: str = Field(min_length=1, max_length=128)
    analyzerPack: str = Field(min_length=1, max_length=128)
    ruleset: str = Field(min_length=1, max_length=128)
    schemaProfile: str = Field(min_length=1, max_length=128)
    checkoutPath: str | None = Field(default=None, min_length=1, max_length=4_096)
    # StrictBool keeps parity with the neutral parser, which rejects "true"/1 coercions.
    runtimeVerification: StrictBool = False

    @model_validator(mode="after")
    def validate_source_mode(self) -> "CollectionRequest":
        if self.sourceType == "GIT" and self.checkoutPath is not None:
            raise ValueError("checkoutPath is not allowed for GIT sources")
        if self.sourceType == "LOCAL_CHECKOUT" and self.checkoutPath is None:
            raise ValueError("checkoutPath is required for LOCAL_CHECKOUT sources")
        return self

    def as_submission(self) -> dict[str, Any]:
        document = self.model_dump(exclude_none=True)
        return document


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
