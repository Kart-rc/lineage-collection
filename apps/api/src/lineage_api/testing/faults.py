from __future__ import annotations

from collections import Counter
from collections.abc import Mapping


class FailpointTriggered(RuntimeError):
    """Raised only at an explicitly named deterministic boundary."""

    def __init__(self, boundary: str, occurrence: int) -> None:
        self.boundary = boundary
        self.occurrence = occurrence
        super().__init__(f"injected fault at {boundary} occurrence {occurrence}")


class NamedFailpoints:
    """A deterministic failpoint schedule; it never sleeps or chooses randomly."""

    def __init__(self, scheduled_failures: Mapping[str, int]) -> None:
        if not scheduled_failures:
            raise ValueError("at least one named failpoint is required")
        if any(not name or count < 1 for name, count in scheduled_failures.items()):
            raise ValueError("failpoint names and positive occurrence counts are required")
        self._remaining = Counter(scheduled_failures)
        self._observed: Counter[str] = Counter()

    @classmethod
    def once(cls, *boundaries: str) -> "NamedFailpoints":
        if len(set(boundaries)) != len(boundaries):
            raise ValueError("once() boundaries must be unique")
        return cls({boundary: 1 for boundary in boundaries})

    @property
    def observed(self) -> dict[str, int]:
        return dict(self._observed)

    def hit(self, boundary: str) -> None:
        if not boundary:
            raise ValueError("fault boundary must not be empty")
        self._observed[boundary] += 1
        if self._remaining[boundary] < 1:
            return
        self._remaining[boundary] -= 1
        raise FailpointTriggered(boundary, self._observed[boundary])

    def assert_all_triggered(self) -> None:
        missing = sorted(name for name, remaining in self._remaining.items() if remaining)
        if missing:
            raise AssertionError(f"scheduled failpoints were not reached: {', '.join(missing)}")

