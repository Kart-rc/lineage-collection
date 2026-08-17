import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "../api/client";
import type {
  InboundInteraction,
  InteractionFieldDoc,
  OutboundInteraction,
  SystemInteractions,
} from "../api/types";
import { Link } from "../routing";
import "../styles/pages/interactions.css";


/** Channel glyphs and tones, ported from the Agentic prototype's channelMeta(). */
const CHANNEL_META: Record<string, { glyph: string; label: string; tone: string }> = {
  REST: { glyph: "⇄", label: "REST", tone: "rest" },
  GRPC: { glyph: "⇉", label: "gRPC", tone: "grpc" },
  GRAPHQL: { glyph: "◆", label: "GraphQL", tone: "graphql" },
  ASYNC_EVENT: { glyph: "≋", label: "Event", tone: "event" },
};

/** The caller row for statically-declared inbound endpoints — callers unknown. */
const DECLARED = "__declared__";


interface CellOp {
  readonly kind: "inbound" | "outbound";
  readonly channel: string;
  readonly operation: string;
  readonly handler: string;
  readonly citation: { file: string; line: number };
  readonly requestFields: InteractionFieldDoc[];
  readonly responseFields: InteractionFieldDoc[];
  readonly system: string;
  readonly commandId: string;
  readonly revision: string;
}

interface MatrixModel {
  readonly callers: string[];
  readonly callees: string[];
  readonly cells: Map<string, CellOp[]>;
}


function cellKey(caller: string, callee: string): string {
  return `${caller}→${callee}`;
}


function buildMatrix(items: SystemInteractions[]): MatrixModel {
  const callers = new Set<string>();
  const callees = new Set<string>();
  const cells = new Map<string, CellOp[]>();
  const push = (caller: string, callee: string, op: CellOp) => {
    const key = cellKey(caller, callee);
    if (!cells.has(key)) cells.set(key, []);
    cells.get(key)!.push(op);
  };
  for (const item of items) {
    if (item.inbound.length) {
      callers.add(DECLARED);
      callees.add(item.service);
      for (const endpoint of item.inbound) {
        push(DECLARED, item.service, {
          kind: "inbound",
          channel: endpoint.channel,
          operation: endpoint.operation,
          handler: endpoint.handler,
          citation: endpoint.citation,
          requestFields: endpoint.requestFields,
          responseFields: endpoint.responseFields,
          system: item.system,
          commandId: item.commandId,
          revision: item.revision,
        });
      }
    }
    for (const call of item.outbound) {
      callers.add(call.fromService);
      callees.add(call.toService);
      push(call.fromService, call.toService, {
        kind: "outbound",
        channel: call.channel,
        operation: call.operation,
        handler: call.handler,
        citation: call.citation,
        requestFields: [],
        responseFields: [],
        system: item.system,
        commandId: item.commandId,
        revision: item.revision,
      });
    }
  }
  return {
    callers: [...callers].sort((a, b) =>
      a === DECLARED ? 1 : b === DECLARED ? -1 : a.localeCompare(b),
    ),
    callees: [...callees].sort(),
    cells,
  };
}


function callerLabel(caller: string): string {
  return caller === DECLARED ? "declared surface" : caller;
}


function FieldList({
  label,
  fields,
}: {
  label: string;
  fields: InteractionFieldDoc[];
}) {
  if (!fields.length) return null;
  return (
    <div className="ix-fields">
      <p className="ix-fields__label">{label}</p>
      <ul>
        {fields.map((field) => (
          <li key={field.name}>
            <code>{field.name}</code>
            <span className="ix-fields__type">{field.type || "—"}</span>
            {field.classification !== "NONE" && (
              <span
                className={`ix-fields__class ix-fields__class--${field.classification.toLowerCase()}`}
              >
                {field.classification}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}


export function InteractionsPage() {
  const interactions = useQuery({
    queryKey: ["interactions"],
    queryFn: ({ signal }) => api.interactions(signal),
  });
  const [selected, setSelected] = useState<{ caller: string; callee: string } | null>(
    null,
  );

  const items = interactions.data?.items ?? [];
  const matrix = useMemo(() => buildMatrix(items), [items]);
  const selectedOps = selected
    ? matrix.cells.get(cellKey(selected.caller, selected.callee)) ?? []
    : [];
  const residueTotal = items.reduce((sum, item) => sum + item.residue.length, 0);

  return (
    <div className="page ix-page">
      <header className="ix-head">
        <div>
          <p className="eyebrow">Interactions</p>
          <h1>Service-to-service traffic — the second lineage plane</h1>
          <p className="lede">
            What crosses an API boundary never comes to rest in a dataset, so the
            lineage walk cannot see it. Every collection also derives this plane
            statically — metadata only, never a value.
          </p>
        </div>
        <div className="ix-head__facts">
          <span className="ix-mechanism" title="Derived by static analysis of route literals — routes built from expressions are residue, never guessed">
            STATIC-SCA
          </span>
          {residueTotal > 0 && (
            <span
              className="ix-residue"
              title="Mappings whose route or target is not a string literal — recorded as residue, never guessed"
            >
              {residueTotal} residue
            </span>
          )}
        </div>
      </header>

      {interactions.isPending && (
        <p className="empty-state">Reading the interactions projection…</p>
      )}
      {interactions.isError && (
        <p className="inline-error" role="alert">
          The interactions projection is unavailable on this deployment.
        </p>
      )}
      {interactions.isSuccess && !items.length && (
        <p className="empty-state">
          No interactions collected yet — the interactions plane rides every
          collection submission. Onboard or re-submit a repository to populate it.
        </p>
      )}

      {items.length > 0 && (
        <div className="ix-body">
          <div className="ix-main">
            <section className="ix-matrix-card" aria-label="Interaction matrix">
              <div className="ix-matrix-scroll">
                <table className="ix-matrix">
                  <caption className="explorer-sr">
                    Callers by rows, called services by columns
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col" className="ix-matrix__corner">
                        caller ↓ · callee →
                      </th>
                      {matrix.callees.map((callee) => (
                        <th key={callee} scope="col">
                          {callee}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {matrix.callers.map((caller) => (
                      <tr key={caller}>
                        <th scope="row">
                          <span className="ix-matrix__caller">{callerLabel(caller)}</span>
                          {caller === DECLARED && (
                            <span className="ix-matrix__caller-hint">
                              inbound endpoints — callers unknown
                            </span>
                          )}
                        </th>
                        {matrix.callees.map((callee) => {
                          const ops = matrix.cells.get(cellKey(caller, callee)) ?? [];
                          const channels = [...new Set(ops.map((op) => op.channel))];
                          const isSelected =
                            selected?.caller === caller && selected?.callee === callee;
                          return (
                            <td key={callee}>
                              {ops.length ? (
                                <button
                                  type="button"
                                  className={`ix-cell${isSelected ? " is-selected" : ""}`}
                                  aria-pressed={isSelected}
                                  aria-label={`${ops.length} operations from ${callerLabel(caller)} to ${callee}`}
                                  onClick={() => setSelected({ caller, callee })}
                                >
                                  <span className="ix-cell__count">{ops.length}</span>
                                  <span className="ix-cell__chips">
                                    {channels.map((channel) => {
                                      const meta = CHANNEL_META[channel] ?? {
                                        glyph: "·",
                                        label: channel,
                                        tone: "rest",
                                      };
                                      return (
                                        <span
                                          key={channel}
                                          className={`ix-chip ix-chip--${meta.tone}`}
                                        >
                                          <span aria-hidden="true">{meta.glyph}</span>{" "}
                                          {meta.label}
                                        </span>
                                      );
                                    })}
                                  </span>
                                </button>
                              ) : (
                                <span className="ix-cell ix-cell--empty" aria-hidden="true">
                                  —
                                </span>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="ix-systems" aria-label="Collected systems">
              <p className="eyebrow">Collected systems · {items.length}</p>
              {items.map((item) => (
                <div key={item.system} className="ix-system">
                  <strong>{item.system}</strong>
                  <span className="ix-system__meta">
                    {item.service} · rev {item.revision.slice(0, 12)} ·{" "}
                    {item.inbound.length} inbound · {item.outbound.length} outbound ·{" "}
                    {item.residue.length} residue
                  </span>
                  <Link to={`/runs/${item.commandId}`}>Collected by {item.commandId} →</Link>
                </div>
              ))}
            </section>
          </div>

          <aside className="ix-rail" aria-label="Interaction detail">
            {selected ? (
              <>
                <div className="ix-rail__head">
                  <p className="eyebrow">Selected cell</p>
                  <h2>
                    {callerLabel(selected.caller)}{" "}
                    <span className="ix-rail__arrow">→</span> {selected.callee}
                  </h2>
                  <p className="ix-rail__count">
                    {selectedOps.length}{" "}
                    {selectedOps.length === 1 ? "operation" : "operations"} · statically
                    derived
                  </p>
                </div>
                <div className="ix-ops">
                  {selectedOps.map((op) => {
                    const meta = CHANNEL_META[op.channel] ?? {
                      glyph: "·",
                      label: op.channel,
                      tone: "rest",
                    };
                    return (
                      <article
                        key={`${op.operation}·${op.handler}`}
                        className="ix-op"
                      >
                        <div className="ix-op__head">
                          <span className={`ix-chip ix-chip--${meta.tone}`}>
                            <span aria-hidden="true">{meta.glyph}</span> {meta.label}
                          </span>
                          <code className="ix-op__operation">{op.operation}</code>
                        </div>
                        <p className="ix-op__meta">
                          {op.handler} · {op.citation.file.split("/").pop()} ·{" "}
                          {op.citation.line}
                        </p>
                        <FieldList label="Request" fields={op.requestFields} />
                        <FieldList label="Response" fields={op.responseFields} />
                        <div className="ix-op__origin">
                          <Link to={`/runs/${op.commandId}`}>Run {op.commandId} →</Link>
                          <span>rev {op.revision.slice(0, 12)}</span>
                        </div>
                      </article>
                    );
                  })}
                </div>
              </>
            ) : (
              <>
                <div className="ix-rail__head">
                  <p className="eyebrow">Selection</p>
                  <h2>Interaction matrix</h2>
                </div>
                <p className="rail-hint">
                  Rows are callers, columns are the services they call. Inbound
                  endpoints sit on the <strong>declared surface</strong> row — the
                  analyzer sees what a service exposes, not who calls it. Select a
                  cell for its operations, schemas and receipts.
                </p>
              </>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}
