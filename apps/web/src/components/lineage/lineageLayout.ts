import type { ConfidenceBand, LineageEdge, LineageResponse } from "../../api/types";
import { isRuntimeVerified } from "../review/reviewMeta";


export type NodeRole = "service" | "element" | "dataset";

/** Geometry constants ported from the Throughline Agentic prototype's geom(). */
export const GEOM = {
  X0: 30,
  COL_W: 356,
  NODE_W: 252,
  TOP: 52,
  GAP: 26,
  HEAD: 44,
  ROW: 30,
  PADB: 10,
  COLLAPSED: 44,
} as const;

const BAND_ORDER: Record<string, number> = {
  LOWEST: 0,
  SINGLE: 1,
  MEDIUM: 2,
  HIGH: 3,
  HIGHEST: 4,
};


export function nodeRole(urn: string): NodeRole {
  if (urn.startsWith("service://")) return "service";
  if (urn.includes("#")) return "element";
  return "dataset";
}


/** Short display name: last URN segment; service URNs keep Class#member. */
export function nodeTitle(urn: string): string {
  if (urn.startsWith("service://")) {
    const tail = urn.split("/").at(-1) ?? urn;
    return tail.split(".").at(-1) ?? tail;
  }
  return urn.split(":").at(-1) ?? urn;
}


/**
 * The card that carries a node, prototype-style: service methods group under
 * their class, dataset elements under their dataset; everything else stands alone.
 */
export function containerOf(urn: string): string {
  const hash = urn.indexOf("#");
  return hash === -1 ? urn : urn.slice(0, hash);
}


export function memberName(urn: string): string {
  const hash = urn.indexOf("#");
  if (hash !== -1) return urn.slice(hash + 1);
  return nodeTitle(urn);
}


export interface CanvasRow {
  urn: string;
  name: string;
  kind: "METHOD" | "ELEMENT";
  /** Weakest band across the row's incident edges — honest, derived, or null. */
  weakestBand: ConfidenceBand | null;
}

export interface CanvasCard {
  /** Container id: class URN for services, dataset URN for datasets. */
  id: string;
  title: string;
  system: string;
  role: "service" | "dataset";
  /** The URN the header itself represents when it is a graph node (datasets). */
  selfUrn: string | null;
  isSubjectCard: boolean;
  rows: CanvasRow[];
  x: number;
  y: number;
  w: number;
  h: number;
  expanded: boolean;
  /** y-center per member row urn, for edge anchoring. */
  rowY: Record<string, number>;
  /** Header/collapsed anchor y-center. */
  cy: number;
}

export interface CanvasEdgeGeom {
  edge: LineageEdge;
  path: string;
  /** Runtime-verified edges draw thick; static-only draw dashed amber. */
  verified: boolean;
  fromCard: string;
  toCard: string;
}

export interface CanvasModel {
  cards: CanvasCard[];
  edges: CanvasEdgeGeom[];
  width: number;
  height: number;
  columns: Array<{ x: number; label: string }>;
  subjectCardId: string | null;
  verifiedEdges: number;
  probableEdges: number;
}

type GraphNode = LineageResponse["nodes"][number];


function weakest(a: ConfidenceBand | null, b: ConfidenceBand): ConfidenceBand {
  if (a === null) return b;
  return (BAND_ORDER[b] ?? 0) < (BAND_ORDER[a] ?? 0) ? b : a;
}


/**
 * Build the prototype-style positioned canvas: rank-based columns of expandable
 * cards, with every edge anchored to the exact row it touches when expanded.
 */
export function buildCanvas(
  subject: string,
  nodes: LineageResponse["nodes"],
  edges: LineageEdge[],
  expandedState: Record<string, boolean>,
  defaultExpanded = true,
): CanvasModel {
  const g = GEOM;

  // Synthesize endpoints missing from the node list so no edge is dropped.
  const known = new Map<string, GraphNode>(nodes.map((node) => [node.urn, node]));
  for (const edge of edges) {
    for (const urn of [...edge.from, edge.to]) {
      if (!known.has(urn)) {
        known.set(urn, {
          urn,
          system: edge.system,
          kind: urn.includes("#") && !urn.startsWith("service://") ? "ELEMENT" : "DATASET",
        });
      }
    }
  }

  // Group nodes into cards by container.
  const cardMap = new Map<string, CanvasCard>();
  const ensureCard = (containerUrn: string, system: string): CanvasCard => {
    let card = cardMap.get(containerUrn);
    if (!card) {
      const role = containerUrn.startsWith("service://") ? "service" : "dataset";
      card = {
        id: containerUrn,
        title: nodeTitle(containerUrn),
        system,
        role,
        selfUrn: null,
        isSubjectCard: false,
        rows: [],
        x: 0,
        y: 0,
        w: g.NODE_W,
        h: 0,
        expanded: false,
        rowY: {},
        cy: 0,
      };
      cardMap.set(containerUrn, card);
    }
    return card;
  };

  const ordered = [...known.values()].sort((a, b) => a.urn.localeCompare(b.urn));
  for (const node of ordered) {
    const container = containerOf(node.urn);
    const card = ensureCard(container, node.system);
    if (node.urn === container) {
      card.selfUrn = node.urn;
      card.system = node.system;
    } else {
      card.rows.push({
        urn: node.urn,
        name: memberName(node.urn),
        kind: container.startsWith("service://") ? "METHOD" : "ELEMENT",
        weakestBand: null,
      });
    }
  }
  // A subject element implies its dataset card even if the dataset node is absent.
  const subjectCardId = cardMap.has(containerOf(subject)) ? containerOf(subject) : null;
  if (subjectCardId) cardMap.get(subjectCardId)!.isSubjectCard = true;

  // Weakest incident band per member row (and count edge tones).
  const orderedEdges = [...edges].sort((a, b) => a.edgeKey.localeCompare(b.edgeKey));
  const rowIndex = new Map<string, CanvasRow>();
  for (const card of cardMap.values()) {
    for (const row of card.rows) rowIndex.set(row.urn, row);
  }
  let verifiedEdges = 0;
  for (const edge of orderedEdges) {
    if (isRuntimeVerified(edge)) verifiedEdges += 1;
    for (const urn of [...edge.from, edge.to]) {
      const row = rowIndex.get(urn);
      if (row) row.weakestBand = weakest(row.weakestBand, edge.band);
    }
  }

  // Card-level condensed graph. Real service graphs contain cycles (a service
  // reads one dataset and writes another it also reads), so ranks come from
  // BFS hop distance around the subject card — upstream left, downstream right.
  const cardIds = [...cardMap.keys()];
  const adj = new Map<string, string[]>();
  const radj = new Map<string, string[]>();
  const indeg = new Map<string, number>();
  for (const id of cardIds) {
    adj.set(id, []);
    radj.set(id, []);
    indeg.set(id, 0);
  }
  const seenPair = new Set<string>();
  for (const edge of orderedEdges) {
    for (const fromUrn of edge.from) {
      const a = containerOf(fromUrn);
      const b = containerOf(edge.to);
      if (a === b || !cardMap.has(a) || !cardMap.has(b)) continue;
      const key = `${a}>${b}`;
      if (seenPair.has(key)) continue;
      seenPair.add(key);
      adj.get(a)!.push(b);
      radj.get(b)!.push(a);
      indeg.set(b, (indeg.get(b) ?? 0) + 1);
    }
  }

  const bfs = (start: string, edgesOf: Map<string, string[]>): Map<string, number> => {
    const dist = new Map<string, number>([[start, 0]]);
    const frontier = [start];
    while (frontier.length) {
      const current = frontier.shift()!;
      for (const next of edgesOf.get(current) ?? []) {
        if (!dist.has(next)) {
          dist.set(next, (dist.get(current) ?? 0) + 1);
          frontier.push(next);
        }
      }
    }
    return dist;
  };

  const rank = new Map<string, number>();
  if (subjectCardId) {
    const down = bfs(subjectCardId, adj);
    const up = bfs(subjectCardId, radj);
    for (const id of cardIds) {
      const d = down.get(id);
      const u = up.get(id);
      if (d !== undefined && u !== undefined) rank.set(id, d <= u ? d : -u);
      else if (d !== undefined) rank.set(id, d);
      else if (u !== undefined) rank.set(id, -u);
    }
    // Cards disconnected from the subject: settle next to any ranked neighbour.
    for (let pass = 0; pass < cardIds.length; pass++) {
      let changed = false;
      for (const id of cardIds) {
        if (rank.has(id)) continue;
        const before = (radj.get(id) ?? []).find((n) => rank.has(n));
        const after = (adj.get(id) ?? []).find((n) => rank.has(n));
        if (before !== undefined) rank.set(id, (rank.get(before) ?? 0) + 1);
        else if (after !== undefined) rank.set(id, (rank.get(after) ?? 0) - 1);
        else continue;
        changed = true;
      }
      if (!changed) break;
    }
    for (const id of cardIds) if (!rank.has(id)) rank.set(id, 0);
    // Normalize to 0-based columns.
    const minRank = Math.min(...rank.values());
    for (const [id, value] of rank) rank.set(id, value - minRank);
  } else {
    // No subject in the graph (synthetic proposal views) — Kahn's toposort.
    for (const id of cardIds) rank.set(id, 0);
    const queue = cardIds.filter((id) => (indeg.get(id) ?? 0) === 0);
    const remaining = new Map(indeg);
    while (queue.length) {
      const current = queue.shift()!;
      for (const next of adj.get(current) ?? []) {
        rank.set(next, Math.max(rank.get(next) ?? 0, (rank.get(current) ?? 0) + 1));
        const left = (remaining.get(next) ?? 0) - 1;
        remaining.set(next, left);
        if (left === 0) queue.push(next);
      }
    }
  }

  // Column stacks, vertically centered against the tallest column.
  const heightOf = (card: CanvasCard): number => {
    const expanded = expandedState[card.id] ?? defaultExpanded;
    if (!expanded || card.rows.length === 0) return g.COLLAPSED;
    return g.HEAD + card.rows.length * g.ROW + g.PADB;
  };
  const cols = new Map<number, CanvasCard[]>();
  for (const card of cardMap.values()) {
    const r = rank.get(card.id) ?? 0;
    if (!cols.has(r)) cols.set(r, []);
    cols.get(r)!.push(card);
  }
  for (const list of cols.values()) {
    list.sort((a, b) => a.title.localeCompare(b.title));
  }
  const maxRank = Math.max(0, ...cols.keys());
  let tallest = 0;
  const totals = new Map<number, number>();
  for (let r = 0; r <= maxRank; r++) {
    const list = cols.get(r) ?? [];
    const total =
      list.reduce((sum, card) => sum + heightOf(card), 0) + g.GAP * Math.max(0, list.length - 1);
    totals.set(r, total);
    tallest = Math.max(tallest, total);
  }
  for (let r = 0; r <= maxRank; r++) {
    const list = cols.get(r) ?? [];
    let y = g.TOP + (tallest - (totals.get(r) ?? 0)) / 2;
    const x = g.X0 + r * g.COL_W;
    for (const card of list) {
      const expanded = (expandedState[card.id] ?? defaultExpanded) && card.rows.length > 0;
      const h = heightOf(card);
      card.x = x;
      card.y = y;
      card.h = h;
      card.expanded = expanded;
      card.cy = expanded ? y + g.HEAD / 2 : y + h / 2;
      card.rowY = {};
      card.rows.forEach((row, index) => {
        card.rowY[row.urn] = y + g.HEAD + index * g.ROW + g.ROW / 2;
      });
      y += h + g.GAP;
    }
  }

  // Column labels relative to the subject's rank.
  const subjectRank = subjectCardId ? rank.get(subjectCardId) ?? null : null;
  const columns: Array<{ x: number; label: string }> = [];
  for (let r = 0; r <= maxRank; r++) {
    let label = "";
    if (subjectRank !== null) {
      if (r === subjectRank) label = "Subject";
      else if (r < subjectRank)
        label = `Upstream · ${subjectRank - r} hop${subjectRank - r > 1 ? "s" : ""}`;
      else label = `Downstream · ${r - subjectRank} hop${r - subjectRank > 1 ? "s" : ""}`;
    }
    columns.push({ x: g.X0 + r * g.COL_W, label });
  }

  // Edge geometry: anchor at the exact row when expanded, else at the card anchor.
  const anchorY = (card: CanvasCard, urn: string): number =>
    card.expanded && card.rowY[urn] !== undefined ? card.rowY[urn] : card.cy;
  const edgeGeoms: CanvasEdgeGeom[] = [];
  for (const edge of orderedEdges) {
    const fromUrn = edge.from[0];
    const fromCard = cardMap.get(containerOf(fromUrn));
    const toCard = cardMap.get(containerOf(edge.to));
    if (!fromCard || !toCard || fromCard.id === toCard.id) continue;
    const y1 = anchorY(fromCard, fromUrn);
    const y2 = anchorY(toCard, edge.to);
    const forward = toCard.x >= fromCard.x + fromCard.w;
    let path: string;
    if (forward) {
      const x1 = fromCard.x + fromCard.w;
      const x2 = toCard.x;
      const mid = x1 + Math.max(22, (x2 - x1) / 2);
      path = `M ${x1} ${y1} H ${mid} V ${y2} H ${x2}`;
    } else {
      // Back edge — curve around instead of crossing through cards.
      const x1 = fromCard.x + fromCard.w;
      const x2 = toCard.x + toCard.w;
      path = `M ${x1} ${y1} C ${x1 + 60} ${y1} ${x2 + 60} ${y2} ${x2} ${y2}`;
    }
    edgeGeoms.push({
      edge,
      path,
      verified: isRuntimeVerified(edge),
      fromCard: fromCard.id,
      toCard: toCard.id,
    });
  }

  const cards = [...cardMap.values()];
  const width = g.X0 + (maxRank + 1) * g.COL_W;
  const height = g.TOP + tallest + 40;
  return {
    cards,
    edges: edgeGeoms,
    width,
    height,
    columns,
    subjectCardId,
    verifiedEdges,
    probableEdges: orderedEdges.length - verifiedEdges,
  };
}
