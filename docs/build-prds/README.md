# Build-ready lineage platform PRDs

These documents turn the normative L01-L16 domain requirements into owned, deployable artifacts.
They do not create one service per PRD: several build units share the versioned Python package and
are separated through ports, handlers, IAM, scaling and release configuration.

The executable acceptance policy is [normative](../acceptance/lineage-platform-acceptance.md).
Unknown enterprise `CTX-*` values remain Definition-of-Ready seams and are never fixture defaults
for production.

## Build order

1. Foundations: [B01](B01-contracts-and-correlation.md), [B02](B02-catalog-and-resolver.md),
   [B03](B03-evidence-and-control-stores.md).
2. Durable flow: [B04](B04-event-intake-and-lanes.md),
   [B05](B05-classifier-and-orchestrator.md).
3. Evidence engines: [B06](B06-sca-worker-and-rule-packs.md),
   [B07](B07-llm-inference-gateway.md), [B08](B08-runtime-session-and-ingestion.md),
   [B09](B09-runtime-emitters-and-integrations.md).
4. Trust: [B10](B10-consolidation-and-confidence.md),
   [B11](B11-proposal-review-and-policy.md),
   [B12](B12-publisher-and-projection-manager.md).
5. Product and operations: [B13](B13-query-impact-and-pr-gate.md),
   [B14](B14-review-and-operations-ui.md),
   [B15](B15-operations-telemetry-and-recovery.md).
6. Delivery: [B16](B16-platform-iac-and-delivery.md).

No downstream build unit is Ready until its upstream contracts, fixtures, test oracle and rollback
boundary are versioned. `AWS_REQUIRED` and `NOT_CONFIGURED` are visible outcomes, never passes.
