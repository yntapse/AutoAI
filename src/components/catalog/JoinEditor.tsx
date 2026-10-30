"use client";

import { useMemo, useState } from "react";
import type { CatalogColumnDetail } from "@/services/catalogService";
import {
  JOIN_TYPES,
  JoinCondition,
  JoinType,
  WorkspaceJoin,
  validateJoin,
} from "./relationshipGraph";

interface Props {
  /** Tables on the canvas, with their columns when loaded. */
  tables: { name: string; columns: CatalogColumnDetail[] | undefined }[];
  /** Pre-filled values: a new join from a declared key, or an existing join being edited. */
  initial?: Partial<Omit<WorkspaceJoin, "id">>;
  title?: string;
  onSave: (join: Omit<WorkspaceJoin, "id">) => void;
  onCancel: () => void;
}

const field =
  "w-full rounded-lg border border-white/10 bg-[#071127] px-2 py-1.5 text-[12px] text-slate-100 outline-none focus:border-cyan-400/40";

/**
 * Form for a workspace join. It only produces a definition for this workspace;
 * it never creates or changes a database constraint.
 */
export default function JoinEditor({ tables, initial, title, onSave, onCancel }: Props) {
  const [leftTable, setLeftTable] = useState(initial?.leftTable ?? tables[0]?.name ?? "");
  const [rightTable, setRightTable] = useState(
    initial?.rightTable ?? tables.find((t) => t.name !== (initial?.leftTable ?? tables[0]?.name))?.name ?? ""
  );
  const [joinType, setJoinType] = useState<JoinType>(initial?.joinType ?? "INNER");
  const [conditions, setConditions] = useState<JoinCondition[]>(
    initial?.conditions?.length ? initial.conditions.map((c) => ({ ...c })) : [{ leftColumn: "", rightColumn: "" }]
  );

  const columnsOf = (name: string) => tables.find((t) => t.name === name)?.columns;
  const leftCols = columnsOf(leftTable);
  const rightCols = columnsOf(rightTable);

  const known = useMemo(() => new Set(tables.map((t) => t.name)), [tables]);
  const lookup = useMemo(() => {
    const out: Record<string, CatalogColumnDetail[] | undefined> = {};
    tables.forEach((t) => {
      out[t.name] = t.columns;
    });
    return out;
  }, [tables]);
  const check = validateJoin({ leftTable, rightTable, joinType, conditions }, lookup, known);

  const setCondition = (i: number, patch: Partial<JoinCondition>) =>
    setConditions((prev) => prev.map((c, k) => (k === i ? { ...c, ...patch } : c)));

  const columnSelect = (cols: CatalogColumnDetail[] | undefined, value: string, onChange: (v: string) => void, label: string) => (
    <select className={field} value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
      <option value="">{cols ? "Column…" : "Loading…"}</option>
      {(cols ?? []).map((c) => (
        <option key={c.column_name} value={c.column_name}>
          {c.column_name} ({c.data_type})
        </option>
      ))}
    </select>
  );

  return (
    <div
      data-interactive
      data-nowheel
      className="absolute right-3 top-3 max-h-[92%] w-[340px] overflow-y-auto rounded-xl border border-amber-400/25 bg-[#0b1630]/95 p-4 text-[12px] text-slate-300 shadow-xl shadow-black/40"
    >
      <div className="flex items-start justify-between gap-2">
        <h4 className="text-[13px] font-semibold text-white">{title ?? "New workspace join"}</h4>
        <button className="text-slate-500 hover:text-white" onClick={onCancel} aria-label="Cancel">
          ✕
        </button>
      </div>
      <p className="mt-1 text-[11.5px] leading-relaxed text-slate-500">
        Defines how two tables should be joined in this workspace. It does not
        create or change any database constraint.
      </p>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <label className="space-y-1">
          <span className="text-[10.5px] uppercase tracking-wider text-slate-500">Left table</span>
          <select
            className={field}
            value={leftTable}
            onChange={(e) => {
              setLeftTable(e.target.value);
              setConditions([{ leftColumn: "", rightColumn: "" }]);
            }}
            aria-label="Left table"
          >
            {tables.map((t) => (
              <option key={t.name} value={t.name}>{t.name}</option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-[10.5px] uppercase tracking-wider text-slate-500">Right table</span>
          <select
            className={field}
            value={rightTable}
            onChange={(e) => {
              setRightTable(e.target.value);
              setConditions([{ leftColumn: "", rightColumn: "" }]);
            }}
            aria-label="Right table"
          >
            {tables.map((t) => (
              <option key={t.name} value={t.name}>{t.name}</option>
            ))}
          </select>
        </label>
      </div>

      <label className="mt-3 block space-y-1">
        <span className="text-[10.5px] uppercase tracking-wider text-slate-500">Join type</span>
        <select className={field} value={joinType} onChange={(e) => setJoinType(e.target.value as JoinType)} aria-label="Join type">
          {JOIN_TYPES.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
      </label>

      <div className="mt-3 space-y-2">
        <div className="text-[10.5px] uppercase tracking-wider text-slate-500">Match on</div>
        {conditions.map((c, i) => (
          <div key={i} className="flex items-center gap-1.5">
            <div className="min-w-0 flex-1">
              {columnSelect(leftCols, c.leftColumn, (v) => setCondition(i, { leftColumn: v }), `Left column ${i + 1}`)}
            </div>
            <span className="text-slate-500">=</span>
            <div className="min-w-0 flex-1">
              {columnSelect(rightCols, c.rightColumn, (v) => setCondition(i, { rightColumn: v }), `Right column ${i + 1}`)}
            </div>
            {conditions.length > 1 && (
              <button
                className="text-slate-500 hover:text-rose-300"
                onClick={() => setConditions((prev) => prev.filter((_, k) => k !== i))}
                aria-label={`Remove condition ${i + 1}`}
              >
                ✕
              </button>
            )}
          </div>
        ))}
        <button
          className="text-[11.5px] font-semibold text-cyan-300 hover:text-cyan-200"
          onClick={() => setConditions((prev) => [...prev, { leftColumn: "", rightColumn: "" }])}
        >
          + Add another condition (AND)
        </button>
      </div>

      {check.errors.length > 0 && (
        <ul className="mt-3 space-y-1 rounded-lg border border-rose-500/25 bg-rose-500/10 px-2.5 py-2 text-[11.5px] text-rose-300">
          {check.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      {check.warnings.length > 0 && (
        <ul className="mt-2 space-y-1 rounded-lg border border-amber-500/25 bg-amber-500/10 px-2.5 py-2 text-[11.5px] text-amber-200">
          {check.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}

      <div className="mt-4 flex justify-end gap-2">
        <button
          className="rounded-lg border border-white/10 px-3 py-1.5 text-[12px] font-semibold text-slate-300 hover:bg-white/[0.06]"
          onClick={onCancel}
        >
          Cancel
        </button>
        <button
          disabled={check.errors.length > 0}
          onClick={() => onSave({ leftTable, rightTable, joinType, conditions })}
          className="rounded-lg border border-cyan-400/40 bg-cyan-500/15 px-3 py-1.5 text-[12px] font-semibold text-cyan-100 hover:bg-cyan-500/25 disabled:opacity-40"
        >
          Save join
        </button>
      </div>
    </div>
  );
}
