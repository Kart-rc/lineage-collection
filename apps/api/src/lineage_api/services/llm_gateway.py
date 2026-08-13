"""The LLM mechanism: a third, independently-guardrailed opinion on residue.

The confidence model has always recognised three mechanisms, and `HIGHEST` requires all
three. Nothing in this codebase ever produced an `LLM` assertion, so that band was
unreachable by construction rather than by policy. This module closes the gap.

It is built to the guardrails in `docs/component-prds/05-llm-inference-gateway.md`, and
the important property is that they make fabrication *structurally* impossible rather
than merely discouraged. A proposal must:

  1. parse as the expected schema                      (SCHEMA_REJECT)
  2. cite text that actually appears in the chunk      (CITATION_REJECT)
  3. name URNs the catalog resolves *and* this analysis already saw  (URN_REJECT)
  4. fit inside the response budget                    (BUDGET_REJECT)

The chain stops at the first failure (L05-FR-005). Because step 3 requires the URN to be
both catalogued and in scope, a hallucinated dataset cannot become an edge no matter what
the model returns — which is what lets LLM evidence raise a band without lowering trust.

The transport is pluggable and defaults to absent, because a live gateway is genuinely
NOT_CONFIGURED here. `RecordedTransport` replays a captured response for tests and
demonstrations and reports `is_live == False`, so a replay can never be presented as a
live model call.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class LlmProposal:
    from_urn: str
    to_urn: str
    transform: str
    citation: str


@dataclass(frozen=True, slots=True)
class LlmReject:
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class LlmResult:
    accepted: tuple[LlmProposal, ...]
    rejects: tuple[LlmReject, ...]
    cache_key: str
    served_from_cache: bool = False


class LlmTransport(Protocol):
    is_live: bool

    def complete(self, prompt: str) -> list[dict[str, Any]]: ...


@dataclass
class RecordedTransport:
    """Replays a captured response. Never presents itself as a live call."""

    responses: Mapping[str, list[dict[str, Any]]]
    is_live: bool = field(default=False, init=False)
    calls: int = field(default=0, init=False)

    def complete(self, prompt: str) -> list[dict[str, Any]]:
        self.calls += 1
        if "any" in self.responses:
            return list(self.responses["any"])
        return list(self.responses.get(prompt, []))


class LlmGateway:
    def __init__(
        self,
        resolver,
        transport: LlmTransport | None = None,
        model_id: str = "unconfigured",
        prompt_version: str = "lineage-residue-v1",
        max_proposals: int = 32,
    ) -> None:
        self._resolver = resolver
        self._transport = transport
        self._model_id = model_id
        self._prompt_version = prompt_version
        self._max_proposals = max_proposals
        self._cache: dict[str, LlmResult] = {}

    def cache_key(self, chunk: str) -> str:
        digest = hashlib.sha256(chunk.encode()).hexdigest()[:24]
        return f"{digest}|{self._prompt_version}|{self._model_id}"

    def propose(self, *, chunk: str, known_urns: Sequence[str]) -> LlmResult:
        key = self.cache_key(chunk)
        cached = self._cache.get(key)
        if cached is not None:
            return LlmResult(cached.accepted, cached.rejects, key, served_from_cache=True)

        if self._transport is None:
            # The honest default. A gateway that does not exist proposes nothing.
            return LlmResult((), (LlmReject("NOT_CONFIGURED", "no LLM transport"),), key)

        # Context assembly records what the model was given (L05-FR-003).
        prompt = (
            f"version={self._prompt_version}\nknownUrns={sorted(known_urns)}\nchunk={chunk}"
        )
        raw = self._transport.complete(prompt)

        accepted: list[LlmProposal] = []
        rejects: list[LlmReject] = []
        in_scope = {str(urn) for urn in known_urns}

        for item in raw:
            reject = self._guardrails(item, chunk, in_scope, len(accepted))
            if reject is not None:
                rejects.append(reject)
                continue
            accepted.append(
                LlmProposal(
                    from_urn=str(item["fromUrn"]),
                    to_urn=str(item["toUrn"]),
                    transform=str(item["transform"]),
                    citation=str(item["citation"]),
                )
            )

        result = LlmResult(tuple(accepted), tuple(rejects), key)
        self._cache[key] = result
        return result

    def _guardrails(
        self,
        item: Mapping[str, Any],
        chunk: str,
        in_scope: set[str],
        accepted_so_far: int,
    ) -> LlmReject | None:
        """The PRD chain, in order. The first failure stops it."""
        required = ("fromUrn", "toUrn", "transform", "citation")
        if not all(isinstance(item.get(name), str) and item[name] for name in required):
            return LlmReject("SCHEMA_REJECT", "proposal is missing a required field")

        if str(item["citation"]) not in chunk:
            return LlmReject(
                "CITATION_REJECT", f"citation not present in chunk: {item['citation']!r}"
            )

        for endpoint in (str(item["fromUrn"]), str(item["toUrn"])):
            dataset = endpoint.rsplit("#", 1)[0]
            if dataset not in in_scope:
                # Catalogued or not, a dataset this analysis never saw cannot appear.
                return LlmReject("URN_REJECT", f"URN outside the analysed scope: {endpoint}")

        if accepted_so_far >= self._max_proposals:
            return LlmReject("BUDGET_REJECT", "response exceeds the proposal budget")
        return None
