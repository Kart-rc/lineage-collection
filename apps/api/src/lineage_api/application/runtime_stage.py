"""Runtime verification as a collection stage.

`services/runtime_verification.py` proved the loop works but nothing called it, so
`runtimeStatus` was structurally `NOT_PROVIDED` for every real collection. This module
is the seam that puts it on the collection path: it takes the edges SCA just proposed,
generates and runs a bounded plan against the analysed module, and returns runtime
assertions plus per-edge liveness.

Two rules carry over unchanged from the verification library and must not be relaxed
here. Runtime never invents an edge — an observation with no static counterpart is
counted as runtime-only and produces no assertion. And executing code stays an explicit,
opt-in phase: `allow_execution` is threaded straight through rather than defaulted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from lineage_api.services.liveness import EdgeLiveness, derive_liveness
from lineage_api.services.resolver import (
    RawName,
    ResolveContext,
    ResolvedName,
    Resolver,
)
from lineage_api.services.runtime_verification import (
    StaticEdge,
    generate_test_plan,
    run_test_plan,
    verify_static_lineage,
)

ElementResolver = Callable[[str, str], str | None]


@dataclass(frozen=True, slots=True)
class RuntimeStageResult:
    verdict: str
    corroborated: int
    static_only: int
    runtime_only: int
    observations: tuple[dict, ...]
    liveness: tuple[EdgeLiveness, ...]
    executed: tuple[str, ...]


def catalog_element_resolver(
    resolver: Resolver, context: ResolveContext
) -> ElementResolver:
    """Resolve an observed (dataset, element) pair through the pinned catalog.

    An unknown name resolves to None rather than a guessed URN, which is what makes
    the observation runtime-only downstream instead of silently inventing an edge.
    """

    def resolve(dataset: str, element: str) -> str | None:
        result = resolver.resolve(
            RawName("dataset", dataset, "RUNTIME", (element,)), context
        )
        if not isinstance(result, ResolvedName) or not result.element_urns:
            return None
        return str(result.element_urns[0])

    return resolve


def run_runtime_stage(
    *,
    module_path: str,
    module_source: str,
    static_edges: Sequence[StaticEdge],
    resolve: ElementResolver,
    observed_at: str,
    allow_execution: bool = False,
    timeout_seconds: float | None = None,
) -> RuntimeStageResult:
    plan = generate_test_plan(module_path, module_source, static_edges)
    run_kwargs: dict[str, object] = {"allow_execution": allow_execution}
    if timeout_seconds is not None:
        run_kwargs["timeout_seconds"] = timeout_seconds
    run = run_test_plan(plan, module_source, **run_kwargs)
    verification = verify_static_lineage(static_edges, run.observations, resolve)

    # Count how many times each corroborated edge was actually witnessed, so liveness
    # reflects the run rather than a flat "observed at least once".
    witnessed: dict[tuple[str, str], int] = {}
    for observation in run.observations:
        from_urn = resolve(observation.from_dataset, observation.from_element)
        to_urn = resolve(observation.to_dataset, observation.to_element)
        if from_urn is None or to_urn is None:
            continue
        witnessed[(from_urn, to_urn)] = witnessed.get((from_urn, to_urn), 0) + 1

    observations: list[dict] = []
    liveness: list[EdgeLiveness] = []

    for edge in verification.corroborated:
        count = witnessed.get(edge.key, 1)
        observations.append(
            {
                "mechanism": "RUNTIME",
                "runtimeScope": "ELEMENT",
                "sessionComplete": True,
                "observedAt": observed_at,
                "from": [edge.from_urn],
                "to": edge.to_urn,
                "edgeType": edge.edge_type,
                "exact": False,
            }
        )
        liveness.append(
            derive_liveness(_edge_key(edge), count, observed_at, session_complete=True)
        )

    # A complete session that never witnessed a claimed edge is the only evidence that
    # justifies calling it UNOBSERVED. The edge itself is untouched.
    for edge in verification.static_only:
        liveness.append(
            derive_liveness(_edge_key(edge), 0, None, session_complete=True)
        )

    return RuntimeStageResult(
        verdict=verification.verdict,
        corroborated=len(verification.corroborated),
        static_only=len(verification.static_only),
        runtime_only=len(verification.runtime_only),
        observations=tuple(observations),
        liveness=tuple(liveness),
        executed=run.executed,
    )


def _edge_key(edge: StaticEdge) -> str:
    return f"{edge.from_urn}->{edge.to_urn}"
