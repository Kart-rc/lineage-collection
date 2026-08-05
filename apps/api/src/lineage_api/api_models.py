from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
