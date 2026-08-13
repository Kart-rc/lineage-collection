"""Close the lineage loop: instrument the analyzed code, generate tests from the static
claim, run them, and report whether execution corroborates what SCA derived.

The platform already consumes runtime evidence — `RuntimeLineageService` validates
sessions and observations, and `ConsolidationService.merge_runtime_observation`
corroborates existing edges without ever inventing one. What was missing is the step
that *produces* that evidence for a repository the analyzer has just read statically.

The loop implemented here is deliberately one-directional in trust:

    SCA edges ──► generated test plan ──► instrumented run ──► observations
                                                                    │
                          corroborated / static-only / runtime-only ◄┘

A generated test can only ever *target* an edge SCA already claimed. Observations that
match lift confidence; observations that do not appear are reported as uncorroborated
rather than silently dropped; observations with no static counterpart are reported as
runtime-only and never become lineage. That mirrors the merge rule downstream.

Executing code is a privilege, not a default. `run_test_plan` refuses to run unless the
caller passes `allow_execution=True`, and it is intended for instrumented non-production
verification of trusted sources. Executing untrusted repository code belongs in the
isolated worker the architecture already reserves for it, never in this process.
"""

from __future__ import annotations

import ast
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


MAX_TEST_CASES = 256
MAX_ARGUMENTS = 8
MAX_OBSERVATIONS = 4_096
DEFAULT_TIMEOUT_SECONDS = 5.0

# Deterministic argument values, chosen so a generated call is reproducible.
_ARGUMENT_LITERALS: dict[str, object] = {
    "str": "generated",
    "int": 0,
    "float": 0.0,
    "bool": False,
}


class RuntimeVerificationError(RuntimeError):
    """A bounded failure in test generation, execution, or verification."""


@dataclass(frozen=True, slots=True)
class StaticEdge:
    """The element-level claim SCA made, reduced to what runtime can confirm."""

    from_urn: str
    to_urn: str
    edge_type: str
    transform: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.from_urn, self.to_urn)


@dataclass(frozen=True, slots=True)
class RuntimeTestCase:
    name: str
    entry_point: str
    arguments: tuple[object, ...]

    def __post_init__(self) -> None:
        if not self.entry_point or len(self.arguments) > MAX_ARGUMENTS:
            raise RuntimeVerificationError("generated test case is outside its bounds")


@dataclass(frozen=True, slots=True)
class RuntimeTestPlan:
    module_path: str
    cases: tuple[RuntimeTestCase, ...]
    targeted_edges: tuple[StaticEdge, ...]

    def __post_init__(self) -> None:
        if len(self.cases) > MAX_TEST_CASES:
            raise RuntimeVerificationError("generated test plan exceeds its case bound")


@dataclass(frozen=True, slots=True)
class ObservedEdge:
    """One element-level edge actually witnessed while the code ran."""

    from_dataset: str
    from_element: str
    to_dataset: str
    to_element: str
    transform: str
    entry_point: str


@dataclass(frozen=True, slots=True)
class RuntimeRunResult:
    observations: tuple[ObservedEdge, ...]
    executed: tuple[str, ...]
    failures: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class RuntimeVerification:
    corroborated: tuple[StaticEdge, ...]
    static_only: tuple[StaticEdge, ...]
    runtime_only: tuple[ObservedEdge, ...]

    @property
    def verdict(self) -> str:
        if not self.corroborated and not self.static_only:
            return "NOT_PROVIDED"
        if self.static_only:
            return "PARTIALLY_CORROBORATED"
        return "CORROBORATED"

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "corroboratedCount": len(self.corroborated),
            "staticOnlyCount": len(self.static_only),
            "runtimeOnlyCount": len(self.runtime_only),
            "corroborated": [
                {"from": edge.from_urn, "to": edge.to_urn} for edge in self.corroborated
            ],
            "staticOnly": [
                {"from": edge.from_urn, "to": edge.to_urn} for edge in self.static_only
            ],
        }


# --------------------------------------------------------------------------------------
# 1. Generate a test plan from the static claim
# --------------------------------------------------------------------------------------


def generate_test_plan(
    module_path: str,
    module_source: str,
    static_edges: Sequence[StaticEdge],
) -> RuntimeTestPlan:
    """Derive a bounded, deterministic call plan for the module's entry points.

    Arguments are synthesized from annotations rather than guessed from names, so the
    plan is reproducible: the same source always yields the same plan.
    """
    try:
        tree = ast.parse(module_source)
    except SyntaxError as error:
        raise RuntimeVerificationError("generated plan source is not parseable") from error

    cases: list[RuntimeTestCase] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        signature = node.args
        if signature.vararg or signature.kwarg or signature.kwonlyargs:
            # An unbounded signature cannot be called deterministically.
            continue
        arguments: list[object] = []
        supported = True
        for argument in signature.args:
            annotation = argument.annotation
            name = annotation.id if isinstance(annotation, ast.Name) else None
            if name not in _ARGUMENT_LITERALS:
                supported = False
                break
            arguments.append(_ARGUMENT_LITERALS[name])
        if not supported or len(arguments) > MAX_ARGUMENTS:
            continue
        cases.append(
            RuntimeTestCase(
                name=f"runtime_verifies_{node.name}",
                entry_point=node.name,
                arguments=tuple(arguments),
            )
        )
        if len(cases) >= MAX_TEST_CASES:
            break

    return RuntimeTestPlan(
        module_path=module_path,
        cases=tuple(cases),
        targeted_edges=tuple(static_edges),
    )


# --------------------------------------------------------------------------------------
# 2. Instrumentation bound to the functions the analyzer read statically
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _DatasetHandle:
    dataset: str
    elements: tuple[str, ...]


@dataclass(slots=True)
class RecordingInstrumentation:
    """Supplies the dataset primitives and records what execution actually touched.

    The analyzed module declares `read_dataset` / `write_dataset` without defining them.
    Binding them here is the instrumentation: the same call sites SCA reads statically
    become the emission points at runtime.
    """

    entry_point: str = ""
    observations: list[ObservedEdge] = field(default_factory=list)

    def read_dataset(
        self, dataset: str, *, elements: Sequence[str] | None = None
    ) -> _DatasetHandle:
        if not isinstance(dataset, str) or not dataset:
            raise RuntimeVerificationError("instrumented read requires a dataset name")
        return _DatasetHandle(dataset, tuple(elements or ()))

    def write_dataset(
        self,
        dataset: str,
        *,
        sources: Sequence[_DatasetHandle] = (),
        mappings: Mapping[str, str] | None = None,
    ) -> None:
        if not isinstance(dataset, str) or not dataset:
            raise RuntimeVerificationError("instrumented write requires a dataset name")
        for target_element, transform in (mappings or {}).items():
            for handle in sources:
                if not isinstance(handle, _DatasetHandle):
                    continue
                for source_element in handle.elements:
                    # Only record a mapping the transform actually references, so an
                    # observation cannot claim a dependency the run never exercised.
                    if source_element not in transform:
                        continue
                    if len(self.observations) >= MAX_OBSERVATIONS:
                        raise RuntimeVerificationError("runtime observation bound exceeded")
                    self.observations.append(
                        ObservedEdge(
                            from_dataset=handle.dataset,
                            from_element=source_element,
                            to_dataset=dataset,
                            to_element=target_element,
                            transform=transform,
                            entry_point=self.entry_point,
                        )
                    )

    def namespace(self) -> dict[str, Any]:
        return {"read_dataset": self.read_dataset, "write_dataset": self.write_dataset}


# --------------------------------------------------------------------------------------
# 3. Run the plan under instrumentation
# --------------------------------------------------------------------------------------


def run_test_plan(
    plan: RuntimeTestPlan,
    module_source: str,
    *,
    allow_execution: bool = False,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> RuntimeRunResult:
    """Execute each generated case with instrumentation bound.

    `allow_execution` must be passed explicitly. Running code is a separate, opt-in
    phase from static collection, and this in-process runner is only for trusted
    sources; untrusted repository code belongs in the isolated worker.
    """
    if not allow_execution:
        raise RuntimeVerificationError(
            "runtime verification must be explicitly enabled before executing code"
        )
    if timeout_seconds <= 0:
        raise RuntimeVerificationError("runtime verification deadline must be positive")

    instrumentation = RecordingInstrumentation()
    namespace: dict[str, Any] = instrumentation.namespace()
    try:
        compiled = compile(module_source, plan.module_path, "exec")
    except SyntaxError as error:
        raise RuntimeVerificationError("instrumented module is not compilable") from error
    exec(compiled, namespace)  # noqa: S102 - trusted, instrumented, opt-in verification

    executed: list[str] = []
    failures: list[tuple[str, str]] = []
    deadline = time.monotonic() + timeout_seconds
    for case in plan.cases:
        if time.monotonic() > deadline:
            raise RuntimeVerificationError("runtime verification exceeded its deadline")
        target = namespace.get(case.entry_point)
        if not callable(target):
            failures.append((case.name, "ENTRY_POINT_MISSING"))
            continue
        instrumentation.entry_point = case.entry_point
        try:
            target(*case.arguments)
            executed.append(case.name)
        except Exception as error:  # noqa: BLE001 - a failing case is data, not a crash
            # A case that cannot run is recorded by class, never by message: the message
            # could carry values from the workload.
            failures.append((case.name, type(error).__name__))

    return RuntimeRunResult(
        observations=tuple(instrumentation.observations),
        executed=tuple(executed),
        failures=tuple(failures),
    )


# --------------------------------------------------------------------------------------
# 4. Verify the static claim against what ran
# --------------------------------------------------------------------------------------

ElementResolver = Callable[[str, str], str | None]


def verify_static_lineage(
    static_edges: Sequence[StaticEdge],
    observations: Sequence[ObservedEdge],
    resolve: ElementResolver,
) -> RuntimeVerification:
    """Compare the static claim with the observed run.

    Runtime evidence never invents an edge here, matching the downstream merge rule: an
    observation with no static counterpart is reported as runtime-only and goes no
    further.
    """
    observed_keys: dict[tuple[str, str], ObservedEdge] = {}
    unresolved: list[ObservedEdge] = []
    for observation in observations:
        from_urn = resolve(observation.from_dataset, observation.from_element)
        to_urn = resolve(observation.to_dataset, observation.to_element)
        if from_urn is None or to_urn is None:
            unresolved.append(observation)
            continue
        observed_keys[(from_urn, to_urn)] = observation

    corroborated: list[StaticEdge] = []
    static_only: list[StaticEdge] = []
    for edge in static_edges:
        if edge.key in observed_keys:
            corroborated.append(edge)
        else:
            static_only.append(edge)

    matched = {edge.key for edge in corroborated}
    runtime_only = [
        observation
        for key, observation in observed_keys.items()
        if key not in matched
    ] + unresolved

    return RuntimeVerification(
        corroborated=tuple(corroborated),
        static_only=tuple(static_only),
        runtime_only=tuple(runtime_only),
    )
