"""Test-session defaults shared by both test trees (`apps/api/tests` and `tests`).

`Settings.from_environment()` fails closed when `LINEAGE_WEBHOOK_SECRET` is unset,
because that secret authenticates every signed delivery the platform accepts and a
silent fallback to the published demo value would make those deliveries forgeable.
A test session is development by definition, so declare development mode here rather
than making each test carry the environment.

This runs at conftest import, before any test module imports the application, and
uses `setdefault` so a test that needs production semantics can still set its own
value (several do, to prove the guard actually rejects weak configuration).
"""

from __future__ import annotations

import os

os.environ.setdefault("LINEAGE_DEV_MODE", "1")
