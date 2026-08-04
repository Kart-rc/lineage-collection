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
