import type { LineageEdge, LineageResponse } from "../../api/types";
import { layoutGraph } from "./lineageLayout";


const nodes: LineageResponse["nodes"] = [
  { urn: "urn:ldp:staging:snowflake:payments:raw.transactions#amount", system: "payments", kind: "ELEMENT" },
  { urn: "urn:ldp:staging:snowflake:payments:analytics.daily_revenue#gross_revenue", system: "payments", kind: "ELEMENT" },
  { urn: "urn:ldp:staging:snowflake:payments:risk.customer_features#lifetime_value", system: "payments", kind: "ELEMENT" },
];

const edges = [
  { edgeKey: "edge-1", from: [nodes[0].urn], to: nodes[1].urn },
  { edgeKey: "edge-2", from: [nodes[1].urn], to: nodes[2].urn },
] as LineageEdge[];

test("layout is stable and places downstream nodes in directional layers", () => {
  const first = layoutGraph(nodes, edges, "down");
  const second = layoutGraph([...nodes].reverse(), [...edges].reverse(), "down");

  expect(second).toEqual(first);
  expect(first.nodes[nodes[0].urn].x).toBeLessThan(first.nodes[nodes[1].urn].x);
  expect(first.nodes[nodes[1].urn].x).toBeLessThan(first.nodes[nodes[2].urn].x);
  expect(first.width).toBeGreaterThan(600);
  expect(first.height).toBeGreaterThan(250);
});

test("upstream mode mirrors directional layers without changing stable vertical order", () => {
  const down = layoutGraph(nodes, edges, "down");
  const up = layoutGraph(nodes, edges, "up");

  expect(up.nodes[nodes[0].urn].x).toBeGreaterThan(up.nodes[nodes[2].urn].x);
  expect(up.nodes[nodes[0].urn].y).toBe(down.nodes[nodes[0].urn].y);
});
