"use client";

/**
 * Relationships passed from the Data Catalog with "Build Dataset".
 *
 * This list only reports what the Catalog knows. A relationship is shown as
 * "Declared" only when the database itself declares it as a foreign key.
 * A join the user drew on the Relationship Map is shown as "Defined by you"
 * and is NOT treated as confirmed. Nothing here creates or runs anything until
 * the user presses "Create join", and even then only a join node is added to the
 * canvas; it is checked and measured like any other join.
 */

import type { HandoffRelationship } from "@/components/catalog/datasetHandoff";

interface Props {
  relationships: HandoffRelationship[];
  tableName: (tableId: string) => string;
  /** Join node id already on the canvas for this relationship, if any. */
  existingJoin: (rel: HandoffRelationship) => string | null;
  /** Both tables are on the canvas. */
  canCreate: (rel: HandoffRelationship) => boolean;
  onCreate: (rel: HandoffRelationship) => void;
  onClose: () => void;
}

export default function HandoffRelationshipsPanel(props: Props) {
  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4" data-testid="handoff-relationships">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-[14px] font-semibold text-white">Relationships from the Catalog</h3>
          <p className="mt-0.5 text-[11.5px] text-slate-500">
            For the tables you chose. Nothing has been joined yet. &ldquo;Create join&rdquo; adds a join step to the canvas for you to check, measure
            on real data and edit.
          </p>
        </div>
        <button className="text-[12px] text-slate-500 hover:text-slate-200" onClick={props.onClose} aria-label="Hide Catalog relationships">
          ✕
        </button>
      </div>
      <ul className="mt-3 space-y-2">
        {props.relationships.map((r) => {
          const existing = props.existingJoin(r);
          const ready = props.canCreate(r);
          return (
            <li key={r.id} className="rounded-xl border border-white/[0.08] p-3" data-testid="handoff-relationship" data-kind={r.kind}>
              <span
                className={`rounded-full border px-2 py-[1px] text-[10.5px] ${
                  r.kind === "declared"
                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                    : "border-amber-500/30 bg-amber-500/10 text-amber-300"
                }`}
              >
                {r.kind === "declared" ? "Declared in the database" : "Defined by you on the map"}
              </span>
              <div className="mt-1.5 space-y-0.5 text-[12.5px] text-white">
                {r.conditions.map((c) => (
                  <div key={`${c.leftColumn}|${c.rightColumn}`}>
                    {props.tableName(r.leftTableId)}.{c.leftColumn} <span className="text-slate-500">=</span> {props.tableName(r.rightTableId)}.
                    {c.rightColumn}
                  </div>
                ))}
              </div>
              <p className="mt-1 text-[11.5px] text-slate-400">
                {r.kind === "declared"
                  ? `Foreign key${r.constraintName ? ` ${r.constraintName}` : ""}: ${props.tableName(r.leftTableId)} holds the key. The database enforces it; it does not say how many rows will match.`
                  : `Not a database constraint and not yet checked against data. Join type chosen on the map: ${r.joinType ?? "INNER"}.`}
              </p>
              <div className="mt-2">
                {existing ? (
                  <span className="text-[11.5px] text-emerald-300">Join {existing} is on the canvas</span>
                ) : (
                  <button
                    className="rounded-md bg-cyan-500/90 px-2.5 py-1 text-[11.5px] font-medium text-[#04202a] hover:bg-cyan-400 disabled:opacity-40"
                    disabled={!ready}
                    title={ready ? undefined : "Both tables need to be on the canvas"}
                    onClick={() => props.onCreate(r)}
                  >
                    Create join
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
