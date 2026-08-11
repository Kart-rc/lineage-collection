export type ConfidenceBand = "LOWEST" | "SINGLE" | "MEDIUM" | "HIGH" | "HIGHEST";
export type Severity = "INFO" | "WARN" | "BLOCK";

export interface ApiErrorShape {
  code: string;
  message: string;
  correlationId: string;
  details?: Record<string, unknown>;
}

export interface EvidenceRef {
  schemaVersion: string;
  kind: string;
  key: string;
  checksum: string;
}

export interface Provenance {
  provenanceId: string;
  from: string[];
  to: string;
  edgeType: string;
  mechanism: "SCA" | "LLM" | "RUNTIME";
  exact: boolean;
  evidenceRef: EvidenceRef;
  repo: string;
  runId: string;
  correlationId: string;
  transform?: string;
  citation?: { file: string; line: number; astPath: string };
  runtimeScope?: "DATASET" | "ELEMENT";
  sessionComplete: boolean;
}

export interface LineageEdge {
  schemaVersion: string;
  edgeKey: string;
  version: number;
  from: string[];
  to: string;
  edgeType: string;
  band: ConfidenceBand;
  corroboration: "NONE" | "DATASET" | "ELEMENT";
  status: string;
  transform?: string;
  provenance: Provenance[];
  autoPublishable: boolean;
  system: string;
  updatedAt: string;
}

export interface Proposal {
  schemaVersion: string;
  proposalId: string;
  version: number;
  system: string;
  state: string;
  expectedBaseVersion: string;
  diff: {
    added: LineageEdge[];
    removed: LineageEdge[];
    bandChanged: LineageEdge[];
    addedEdgeIds: string[];
    removedEdgeIds: string[];
    bandChangedEdgeIds: string[];
    edgeSetRef?: Record<string, unknown>;
  };
  correlationId: string;
  createdAt: string;
  updatedAt: string;
  lockVersion: number;
  decision?: {
    approvalId: string;
    actor: string;
    rationale: string;
    decision: string;
    decidedAt: string;
  };
}

export interface RunStage {
  sequence: number;
  stage: string;
  status: string;
  correlationId: string;
  detail: Record<string, unknown>;
  startedAt: string;
  completedAt: string;
}

export interface Run {
  runId: string;
  eventId: string;
  repo: string;
  digest: string;
  env: string;
  system: string;
  state: string;
  correlationId: string;
  failedStage: string | null;
  errorCode: string | null;
  createdAt: string;
  updatedAt: string;
  stages: RunStage[];
}

export interface Overview {
  environment: string;
  activeVersion: string | null;
  fencingToken: number;
  counts: { runs: number; inReview: number; quarantined: number };
  countsAreComplete: boolean;
  recentRuns: Run[];
  inReviewSample: Proposal[];
  resilience?: ResilienceSnapshot;
}

export interface Page<T> {
  items: T[];
  nextCursor: string | null;
}

export type OperationalStatus =
  | "HEALTHY"
  | "DEGRADED"
  | "OUT_OF_SYNC"
  | "COMPLETE"
  | "INCOMPLETE"
  | "CURRENT"
  | "STALE"
  | "IN_SYNC"
  | "NOT_AVAILABLE"
  | "NOT_CONFIGURED";

export interface ResilienceSnapshot {
  schemaVersion: string;
  capturedAt: string | null;
  status: OperationalStatus;
  correlation: {
    status: OperationalStatus;
    trackedCount: number;
    missingCount: number;
  };
  queue: {
    status: OperationalStatus;
    depth: number;
    oldestAgeSeconds: number | null;
    saturation: {
      status: OperationalStatus;
      observedDepth: number;
      capacity: number | null;
    };
    retryCount: number;
    deadLetterCount: number;
    leaseStealCount: number;
  };
  coverage: {
    status: OperationalStatus;
    incompleteCount: number;
    runtimeJoin: {
      status: OperationalStatus;
      joined: number;
      eligible: number;
      rate: number | null;
    };
    baseline: {
      status: OperationalStatus;
      ageSeconds: number | null;
      maxAgeSeconds: number;
    };
  };
  review: {
    status: OperationalStatus;
    oldestApprovalAgeSeconds: number | null;
  };
  publication: {
    status: OperationalStatus;
    publishLagSeconds: number | null;
    pointerPackage: {
      status: OperationalStatus;
      activeVersion: string | null;
      packageVersion: string | null;
    };
    watermark: {
      status: OperationalStatus;
      version: string | null;
      updatedAt: string | null;
      ageSeconds: number | null;
    };
  };
  productionSignals: {
    replication: { status: OperationalStatus; value: number | null };
    errorBudgetBurn: { status: OperationalStatus; value: number | null };
    unitCost: { status: OperationalStatus; value: number | null };
  };
}

export interface DemoReset {
  activeVersion: string;
  catalogDigest: string;
  catalogDatasetCount: number;
  demoDelivery: { payload: Record<string, unknown>; signature: string };
}

export interface CollectionResult {
  outcome: "ACCEPTED" | "DUPLICATE" | "QUARANTINED" | "BLOCKED";
  reason: string | null;
  eventId: string;
  run: Run | null;
  proposal: Proposal | null;
}

export type CollectionSourceType = "LOCAL_CHECKOUT" | "GIT";

export interface RepositoryCollectionRequest {
  readonly sourceType: CollectionSourceType;
  readonly origin: string;
  readonly repository: string;
  readonly revision: string;
  readonly environment: string;
  readonly platform: string;
  readonly system: string;
  readonly analyzerPack: string;
  readonly ruleset: string;
  readonly schemaProfile: string;
  /** Only ever sent for LOCAL_CHECKOUT, and only under the development policy. */
  readonly checkoutPath?: string;
}

export interface CollectionCounts {
  readonly edges: number;
  readonly reads: number;
  readonly writes: number;
  readonly residue: number;
  readonly unresolved: number;
}

export interface CollectionCoverage {
  readonly manifestId: string | null;
  readonly state: string | null;
  readonly counts: {
    readonly expected: number;
    readonly completed: number;
    readonly skipped: number;
    readonly unsupported: number;
    readonly failed: number;
  };
}

export interface RepositoryCollection {
  readonly commandId: string;
  readonly collectionId: string;
  readonly statusUrl: string;
  readonly sourceType: CollectionSourceType | "UNKNOWN";
  readonly origin: string;
  readonly repository: string;
  readonly revision: string;
  readonly environment: string;
  readonly system: string;
  readonly outcome: string;
  readonly reasonCode: string | null;
  readonly commandStatus: string;
  readonly terminal: boolean;
  readonly runId: string | null;
  readonly runStatus: string | null;
  readonly proposalId: string | null;
  readonly proposalStatus: string | null;
  readonly runtimeStatus: string;
  readonly analysisStatus: string | null;
  readonly statusReasons: readonly string[];
  readonly stages: readonly string[];
  readonly counts: CollectionCounts;
  readonly coverage: CollectionCoverage | null;
  readonly correlationId: string;
}

export interface LineageResponse {
  subject: string;
  direction: "up" | "down";
  namespaceVersion: string;
  depthSearched: number;
  truncated: boolean;
  nodes: Array<{ urn: string; system: string; kind: "ELEMENT" | "DATASET" }>;
  edges: LineageEdge[];
}

export interface ImpactResponse {
  subject: string;
  changeType: string;
  namespaceVersion: string;
  depthSearched: number;
  truncated: boolean;
  affected: Array<{
    urn: string;
    system: string;
    severity: Severity;
    band: ConfidenceBand;
    corroboration: "NONE" | "DATASET" | "ELEMENT";
    pathLength: number;
    viaEdges: string[];
    owner: string;
  }>;
  summary: { block: number; warn: number; info: number };
}
