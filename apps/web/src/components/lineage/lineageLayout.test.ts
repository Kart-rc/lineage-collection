import type { LineageEdge, LineageResponse } from "../../api/types";
import { GEOM, buildCanvas, containerOf } from "./lineageLayout";


const OWNERS = "urn:ldp:staging:postgres:petclinic:owners";
const OWNERS_ID = `${OWNERS}#id`;
const CONTROLLER = "service://spring-petclinic/owner.OwnerController";
const SHOW = `${CONTROLLER}#showOwner`;
const FIND = `${CONTROLLER}#findOwner`;

const nodes: LineageResponse["nodes"] = [
  { urn: OWNERS, system: "petclinic", kind: "DATASET" },
  { urn: OWNERS_ID, system: "petclinic", kind: "ELEMENT" },
  { urn: SHOW, system: "petclinic", kind: "DATASET" },
  { urn: FIND, system: "petclinic", kind: "DATASET" },
];

const edges = [
  { edgeKey: "edge-1", from: [OWNERS], to: SHOW, system: "petclinic", band: "SINGLE", edgeType: "READS", provenance: [] },
  { edgeKey: "edge-2", from: [OWNERS_ID], to: FIND, system: "petclinic", band: "HIGH", edgeType: "READS", provenance: [] },
] as unknown as LineageEdge[];


test("containers group methods under their class and elements under their dataset", () => {
  expect(containerOf(SHOW)).toBe(CONTROLLER);
  expect(containerOf(OWNERS_ID)).toBe(OWNERS);
  expect(containerOf(OWNERS)).toBe(OWNERS);
});

test("the canvas groups member nodes into cards with honest weakest bands", () => {
  const model = buildCanvas(OWNERS, nodes, edges, {});

  const controller = model.cards.find((card) => card.id === CONTROLLER);
  const owners = model.cards.find((card) => card.id === OWNERS);
  expect(controller).toBeDefined();
  expect(owners).toBeDefined();
  expect(controller!.rows.map((row) => row.name).sort()).toEqual([
    "findOwner",
    "showOwner",
  ]);
  expect(owners!.rows.map((row) => row.name)).toEqual(["id"]);
  expect(owners!.isSubjectCard).toBe(true);
  expect(model.subjectCardId).toBe(OWNERS);

  // Weakest incident band per row — derived, never fabricated.
  const findRow = controller!.rows.find((row) => row.name === "findOwner");
  const showRow = controller!.rows.find((row) => row.name === "showOwner");
  expect(findRow!.weakestBand).toBe("HIGH");
  expect(showRow!.weakestBand).toBe("SINGLE");
  expect(model.verifiedEdges).toBe(1);
  expect(model.probableEdges).toBe(1);
});

test("cards rank into columns following data flow, labeled relative to the subject", () => {
  const model = buildCanvas(OWNERS, nodes, edges, {});
  const owners = model.cards.find((card) => card.id === OWNERS)!;
  const controller = model.cards.find((card) => card.id === CONTROLLER)!;

  // Data flows owners → controller, so the reader sits one column right.
  expect(controller.x).toBeGreaterThan(owners.x);
  expect(model.columns[0].label).toBe("Subject");
  expect(model.columns[1].label).toBe("Downstream · 1 hop");
});

test("edges anchor at the exact member row when expanded and at the card when collapsed", () => {
  const expandedModel = buildCanvas(OWNERS, nodes, edges, {}, true);
  const controller = expandedModel.cards.find((card) => card.id === CONTROLLER)!;
  const edge2 = expandedModel.edges.find((geom) => geom.edge.edgeKey === "edge-2")!;
  const findY = controller.rowY[FIND];
  expect(edge2.path.endsWith(`H ${controller.x}`)).toBe(true);
  expect(edge2.path).toContain(`V ${findY}`);

  const collapsedModel = buildCanvas(OWNERS, nodes, edges, {}, false);
  const collapsedController = collapsedModel.cards.find((card) => card.id === CONTROLLER)!;
  expect(collapsedController.expanded).toBe(false);
  expect(collapsedController.h).toBe(GEOM.COLLAPSED);
  const collapsedEdge = collapsedModel.edges.find((geom) => geom.edge.edgeKey === "edge-2")!;
  expect(collapsedEdge.path).toContain(`V ${collapsedController.cy}`);
});

test("runtime-verified edges are marked for thick strokes; static-only are not", () => {
  const model = buildCanvas(OWNERS, nodes, edges, {});
  const byKey = new Map(model.edges.map((geom) => [geom.edge.edgeKey, geom]));
  expect(byKey.get("edge-2")!.verified).toBe(true);
  expect(byKey.get("edge-1")!.verified).toBe(false);
});

test("a subject absent from the graph yields no subject card or column labels", () => {
  const model = buildCanvas("proposal-123", nodes, edges, {});
  expect(model.subjectCardId).toBeNull();
  expect(model.columns.every((column) => column.label === "")).toBe(true);
  // Every edge still renders between cards.
  expect(model.edges).toHaveLength(2);
});

test("missing edge endpoints are synthesized so no edge is dropped", () => {
  const extra = [
    ...edges,
    {
      edgeKey: "edge-3",
      from: [SHOW],
      to: "urn:ldp:staging:postgres:petclinic:audit_log",
      system: "petclinic",
      band: "SINGLE",
      edgeType: "WRITES",
      provenance: [],
    },
  ] as unknown as LineageEdge[];
  const model = buildCanvas(OWNERS, nodes, extra, {});
  expect(
    model.cards.some((card) => card.id === "urn:ldp:staging:postgres:petclinic:audit_log"),
  ).toBe(true);
  expect(model.edges).toHaveLength(3);
});
