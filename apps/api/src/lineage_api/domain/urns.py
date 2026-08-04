from __future__ import annotations

import re
from dataclasses import dataclass


URN_PATTERN = re.compile(
    r"^urn:ldp:(?P<env>[^:#]+):(?P<platform>[^:#]+):(?P<system>[^:#]+):"
    r"(?P<dataset>[^#]+?)(?:#(?P<element>[^#]+))?$"
)


@dataclass(frozen=True, slots=True)
class LineageUrn:
    env: str
    platform: str
    system: str
    dataset: str
    element: str | None = None

    def __post_init__(self) -> None:
        values = (self.env, self.platform, self.system, self.dataset)
        if any(not value or ":" in value or "#" in value for value in values):
            raise ValueError("Invalid lineage URN components")
        if self.element is not None and (not self.element or "#" in self.element):
            raise ValueError("Invalid lineage URN element")

    @classmethod
    def parse(cls, value: str) -> "LineageUrn":
        match = URN_PATTERN.fullmatch(value)
        if not match:
            raise ValueError(f"Invalid lineage URN: {value!r}")
        try:
            return cls(**match.groupdict())
        except ValueError as error:
            raise ValueError(f"Invalid lineage URN: {value!r}") from error

    def __str__(self) -> str:
        value = self.dataset_urn
        return f"{value}#{self.element}" if self.element is not None else value

    @property
    def dataset_urn(self) -> str:
        return f"urn:ldp:{self.env}:{self.platform}:{self.system}:{self.dataset}"

    def with_element(self, element: str) -> "LineageUrn":
        return LineageUrn(
            env=self.env,
            platform=self.platform,
            system=self.system,
            dataset=self.dataset,
            element=element,
        )

    def same_asset_as(self, other: "LineageUrn") -> bool:
        return (
            self.platform,
            self.system,
            self.dataset,
            self.element,
        ) == (
            other.platform,
            other.system,
            other.dataset,
            other.element,
        )
