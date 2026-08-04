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
  diff: { added: LineageEdge[]; removed: LineageEdge[]; bandChanged: LineageEdge[] };
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
  activeVersion: string;
  fencingToken: number;
  counts: { runs: number; inReview: number; quarantined: number };
  recentRuns: Run[];
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
    severity: Severity;
    confidenceBand: ConfidenceBand;
    pathLength: number;
    path: string[];
    owner: string;
  }>;
  summary: { block: number; warn: number; info: number };
}
