import type { LineageEdge, LineageResponse, Provenance } from "../../api/types";
import { Link } from "../../routing";
import { bandDisplay, isRuntimeVerified, urnShort } from "../review/reviewMeta";
import { nodeRole, nodeTitle } from "./lineageLayout";


const RECEIPT_KIND: Record<Provenance["mechanism"], { label: string; mod: string }> = {
  RUNTIME: { label: "RUNTIME", mod: "runtime" },
  SCA: { label: "STATIC-SCA", mod: "sca" },
  LLM: { label: "LLM", mod: "llm" },
};


function shortChecksum(value: string): string {
  return value.length > 14 ? `${value.slice(0, 14)}…` : value;
}


/** Live payloads carry sha256; fixtures carry checksum. Accept either. */
function evidenceDigest(evidenceRef: Provenance["evidenceRef"]): string {
  const raw = evidenceRef as unknown as Record<string, unknown>;
  const digest = raw.checksum ?? raw.sha256;
  return typeof digest === "string" ? digest : "digest unavailable";
}


function shortName(urn: string): string {
  const tail = urn.split(":").at(-1) ?? urn;
  return tail.split("/").at(-1) ?? tail;
}


function nodeCategory(node: LineageResponse["nodes"][number]): "service" | "element" | "dataset" {
  if (node.urn.startsWith("service://")) return "service";
  if (node.kind === "ELEMENT") return "element";
  return "dataset";
}


function trustLine(edge: LineageEdge): string {
  if (isRuntimeVerified(edge)) {
    const scope = edge.corroboration === "ELEMENT" ? "element" : "dataset";
    return `Static analysis and the runtime harness agree at ${scope} level.`;
  }
  return "Static evidence only — runtime corroboration pending.";
}


function receiptTitle(item: Provenance): string {
  if (item.citation) return `${item.citation.file} · ${item.citation.line}`;
  if (item.runtimeScope) return `runtime · ${item.runtimeScope}`;
  return item.evidenceRef.key;
}


type EdgeInspectorProps = {
  data?: LineageResponse;
  edge: LineageEdge | null;
  /** A selected node (service method, dataset or element) — prototype model. */
  nodeUrn?: string | null;
  collapsed: boolean;
  onToggle: () => void;
  onRoot: (urn: string) => void;
  onSelectEdge?: (edge: LineageEdge) => void;
};


/** Inspector state for a selected node: identity + its incident edges. */
function NodeSummary({
  data,
  urn,
  onToggle,
  onRoot,
  onSelectEdge,
}: {
  data?: LineageResponse;
  urn: string;
  onToggle: () => void;
  onRoot: (urn: string) => void;
  onSelectEdge?: (edge: LineageEdge) => void;
}) {
  const role = nodeRole(urn);
  const node = data?.nodes.find((item) => item.urn === urn) ?? null;
  const incident = (data?.edges ?? []).filter((edge) =>
    [...edge.from, edge.to].includes(urn),
  );
  return (
    <>
      <RailHead eyebrow="Selected node" title={nodeTitle(urn)} onToggle={onToggle} />
      <div className="rail-subject">
        <strong>
          {role === "service" ? "service" : role === "element" ? "element" : "dataset"}
          {node ? ` · ${node.system}` : ""}
        </strong>
        <code>{urn}</code>
      </div>
      <p className="eyebrow">
        Incident edges · {incident.length}
      </p>
      {incident.length ? (
        <div className="rail-node-edges">
          {incident.map((edge) => {
            const projection = bandDisplay(edge.band);
            const other = edge.to === urn ? edge.from[0] : edge.to;
            return (
              <button
                key={edge.edgeKey}
                type="button"
                className="rail-node-edge"
                onClick={() => onSelectEdge?.(edge)}
                title={`${edge.from.map(urnShort).join(", ")} → ${urnShort(edge.to)}`}
              >
                <span className="rail-node-edge__verb">{edge.edgeType}</span>
                <span className="rail-node-edge__other">{nodeTitle(other)}</span>
                <span
                  className={`rail-node-edge__band rail-node-edge__band--${projection.tone}`}
                >
                  {projection.label}
                </span>
              </button>
            );
          })}
        </div>
      ) : (
        <p className="rail-hint">No edges touch this node within the walk.</p>
      )}
      <div className="lineage-rail__actions">
        <button
          type="button"
          className="lineage-rail__bundle"
          onClick={() => onRoot(urn)}
          title={`Re-root the walk on ${urn}`}
        >
          <span aria-hidden="true">↺</span> Walk from this node
        </button>
      </div>
      <p className="rail-hint">Select an incident edge for its evidence · Esc clears.</p>
    </>
  );
}


function WalkSummary({ data, onToggle }: { data?: LineageResponse; onToggle: () => void }) {
  if (!data) {
    return (
      <>
        <RailHead eyebrow="Selection" title="Walk summary" onToggle={onToggle} />
        <p className="rail-hint">Resolving the active projection…</p>
      </>
    );
  }
  const counts = { dataset: 0, element: 0, service: 0 };
  for (const node of data.nodes) counts[nodeCategory(node)] += 1;
  const verified = data.edges.filter(isRuntimeVerified).length;
  const staticOnly = data.edges.length - verified;
  const total = data.edges.length;
  return (
    <>
      <RailHead eyebrow="Selection" title="Walk summary" onToggle={onToggle} />
      <div className="rail-subject">
        <strong>{shortName(data.subject)}</strong>
        <code>{data.subject}</code>
      </div>
      <dl>
        <div>
          <dt>Projection</dt>
          <dd>{data.namespaceVersion}</dd>
        </div>
        <div>
          <dt>Traversal</dt>
          <dd>
            {data.direction} · depth {data.depthSearched}
          </dd>
        </div>
        <div>
          <dt>Nodes</dt>
          <dd>
            {counts.dataset} datasets · {counts.element} elements · {counts.service} services
          </dd>
        </div>
      </dl>
      <p className="eyebrow">Edge confidence · {total}</p>
      {total > 0 ? (
        <>
          <div className="rail-band-bar" aria-hidden="true">
            {verified > 0 && (
              <span
                className="rail-band-bar__verified"
                style={{ width: `${(verified / total) * 100}%` }}
              />
            )}
            {staticOnly > 0 && (
              <span
                className="rail-band-bar__static"
                style={{ width: `${(staticOnly / total) * 100}%` }}
              />
            )}
          </div>
          <ul className="rail-band-counts">
            <li data-band="verified">
              <span className="rail-band-dot" aria-hidden="true" />
              {verified} runtime-verified · {bandDisplay("HIGH").label}
            </li>
            <li data-band="static">
              <span className="rail-band-dot" aria-hidden="true" />
              {staticOnly} static-only · {bandDisplay("SINGLE").label}
            </li>
          </ul>
        </>
      ) : (
        <p className="rail-hint">No edges within this walk.</p>
      )}
      {data.truncated && (
        <p className="rail-truncated">Walk truncated — counts cover the visible neighborhood only.</p>
      )}
      <p className="rail-hint">Select an edge for its evidence.</p>
    </>
  );
}


function RailHead({
  eyebrow,
  title,
  onToggle,
}: {
  eyebrow: string;
  title: string;
  onToggle: () => void;
}) {
  return (
    <div className="edge-inspector__head">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h2>{title}</h2>
      </div>
      <CollapseButton onToggle={onToggle} />
    </div>
  );
}


function CollapseButton({ onToggle }: { onToggle: () => void }) {
  return (
    <button
      type="button"
      className="edge-inspector__toggle"
      aria-expanded="true"
      aria-label="Collapse evidence rail"
      onClick={onToggle}
    >
      <span aria-hidden="true">»</span>
    </button>
  );
}


export function EdgeInspector({
  data,
  edge,
  nodeUrn = null,
  collapsed,
  onToggle,
  onRoot,
  onSelectEdge,
}: EdgeInspectorProps) {
  if (collapsed) {
    return (
      <aside className="edge-inspector edge-inspector--collapsed" aria-label="Evidence rail">
        <button
          type="button"
          className="edge-inspector__expand"
          aria-expanded="false"
          aria-label="Expand evidence rail"
          onClick={onToggle}
        >
          <span aria-hidden="true">«</span>
          <span className="edge-inspector__vertical">Evidence</span>
          {edge && <span className="edge-inspector__badge-count">1</span>}
        </button>
      </aside>
    );
  }

  if (!edge && nodeUrn) {
    return (
      <aside className="edge-inspector edge-inspector--empty" aria-label="Evidence rail">
        <NodeSummary
          data={data}
          urn={nodeUrn}
          onToggle={onToggle}
          onRoot={onRoot}
          onSelectEdge={onSelectEdge}
        />
      </aside>
    );
  }

  if (!edge) {
    return (
      <aside className="edge-inspector edge-inspector--empty" aria-label="Evidence rail">
        <WalkSummary data={data} onToggle={onToggle} />
      </aside>
    );
  }

  const projection = bandDisplay(edge.band);
  const score = projection.percent;
  const bundleRunId = edge.provenance[0]?.runId ?? null;
  const endpoints = [...edge.from.filter((urn) => urn !== edge.to), edge.to];
  const receiptCount = edge.provenance.length;

  return (
    <aside className="edge-inspector" aria-label="Evidence rail">
      <div className="edge-inspector__head">
        <div>
          <p className="eyebrow">Selected edge</p>
          <h2 className="lineage-rail__flow">
            {edge.from.map(nodeTitle).join(" + ")}{" "}
            <span className="lineage-rail__type">→ {edge.edgeType.toLowerCase()} →</span>{" "}
            {nodeTitle(edge.to)}
          </h2>
        </div>
        <CollapseButton onToggle={onToggle} />
      </div>
      <p className="lineage-rail__facts">
        {edge.edgeKey} · {edge.status} · v{edge.version} · {edge.system}
      </p>

      <div className={`lineage-conf lineage-conf--${projection.tone}`}>
        <div className="lineage-conf__head">
          <strong>{projection.label}</strong>
          <span>
            {projection.tone === "verified" ? "runtime + static agree" : "static-only signal"}
          </span>
        </div>
        <div className="lineage-conf__track" aria-hidden="true">
          <span className="lineage-conf__fill" style={{ width: `${score}%` }} />
        </div>
        <p>{trustLine(edge)}</p>
      </div>

      {edge.transform && (
        <div>
          <p className="eyebrow">Transform</p>
          <code className="lineage-rail__transform">{edge.transform}</code>
        </div>
      )}

      <div className="lineage-rail__evidence">
        <p className="eyebrow">
          Evidence · {receiptCount} {receiptCount === 1 ? "receipt" : "receipts"}
        </p>
        <div className="lineage-receipts">
          {edge.provenance.map((item) => (
            <article key={item.provenanceId} className="lineage-receipt">
              <div className="lineage-receipt__head">
                <span
                  className={`lineage-receipt__kind lineage-receipt__kind--${RECEIPT_KIND[item.mechanism]?.mod ?? "llm"}`}
                >
                  {RECEIPT_KIND[item.mechanism]?.label ?? item.mechanism}
                </span>
                <span
                  className="lineage-receipt__cite"
                  title={item.citation?.astPath ?? item.evidenceRef.key}
                >
                  {receiptTitle(item)}
                </span>
              </div>
              <span className="lineage-receipt__meta">
                {item.exact ? "exact" : "observed"} ·{" "}
                {item.sessionComplete ? "complete session" : "partial session"} ·{" "}
                {shortChecksum(evidenceDigest(item.evidenceRef))}
              </span>
              <div className="lineage-receipt__origin">
                <Link className="lineage-receipt__run" to={`/runs/${item.runId}`}>
                  Run {item.runId} →
                </Link>
                <code>{item.correlationId}</code>
              </div>
            </article>
          ))}
        </div>
      </div>

      <div className="lineage-rail__actions">
        {bundleRunId && (
          <Link className="lineage-rail__bundle" to={`/runs/${bundleRunId}`}>
            Open evidence bundle
          </Link>
        )}
        <div className="lineage-rail__reroot">
          {endpoints.map((urn) => (
            <button
              key={urn}
              type="button"
              onClick={() => onRoot(urn)}
              title={`Re-root the walk on ${urn}`}
            >
              <span aria-hidden="true">↺</span> Re-root · {nodeTitle(urn)}
            </button>
          ))}
        </div>
      </div>
    </aside>
  );
}
