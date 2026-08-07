# B07 — LLM inference gateway

Normative requirements: [L05 inference](../component-prds/05-llm-inference-gateway.md) and
[L12 security/operations](../component-prds/12-security-observability-operations.md).

## Ownership

Track C owns residue eligibility, prompt/model registry, structured-output validation, guardrails,
cache/budget policy and rollout. The enterprise model-gateway owner supplies approved endpoints.

## Boundary

Process only explicitly eligible SCA residue under a pinned prompt/model/policy, return strict
candidate evidence or skip/reject records, and never publish or manufacture certainty.

## Contracts

Inputs reference bounded residue and determinants; outputs reference validated candidates, reject
reason, token/cost metadata and guardrail decisions. Raw free-form model text is not a lineage edge.

## State and failure model

Deterministic cache keys bind all determinants. Budget exhaustion, outage, invalid structure,
guardrail failure and low confidence become skip/reject-with-record. Retries are bounded and do not
block deterministic SCA completion.

## Data ownership

B07 owns prompt/model versions, response/reject evidence, cache keys, budget ledger and evaluation
corpus. It does not own source, credentials, proposals or published truth.

## Infrastructure bill of materials

Private Lambda/ECS gateway client, approved Bedrock/enterprise endpoint, DynamoDB budget/cache index,
S3 response/reject evidence, KMS, egress controls, secrets references, alarms and kill switch.

## Local adapter

Deterministic fixture responses exercise schema, cache, tier, budget and guardrail behavior. A local
fixture never proves model quality, availability or production cost.

## Security and privacy

Minimize/redact prompts, prohibit secrets/sampled data, restrict endpoint/model actions, retain
auditable prompt/model identity and run adversarial prompt-injection/exfiltration negatives.

## SLOs

Zero guardrail bypass or fabricated edge is mandatory. Quality, latency, spend and cache targets
remain `NOT_CONFIGURED`/`AWS_REQUIRED` until the approved gateway and evaluation owner are present.

## Observability

Measure eligible/skipped/rejected/accepted residue, cache hit, tokens, unit cost, budget shedding,
guardrail reason, model/prompt version, latency and correlation without logging protected prompts.

## Acceptance criteria

| Acceptance ID | Requirement | Scenario and evidence | Gate |
|---|---|---|---|
| B07-AC-001 | L05 guardrails and graceful degradation | Adversarial/invalid/outage/budget fixtures never create an edge; valid structured fixtures retain exact prompt/model/policy identity | Every PR; live quality gate separately |

## Deployment and rollback

Shadow then canary prompt/model/policy versions with budget ceilings. Rollback disables the alias or
returns to the prior determinants; cached records remain immutable and version-scoped.

## Dependencies

B01, B03, B05, B06 residue and B10 candidate consumption. Gateway, model, prompt approval, cost caps
and data policy are CTX seams.

## Definition of Ready

Eligible residue, strict output schema, adversarial corpus, model/prompt IDs, privacy review, budget
owner and kill-switch runbook are defined.

## Definition of Done

All guardrail/failure branches have evidence; fixture cache/budget behavior is deterministic;
production-only claims remain explicit; `B07-AC-001` is retained.
