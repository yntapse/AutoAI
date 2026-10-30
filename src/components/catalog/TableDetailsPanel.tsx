"use client";

import { useState } from "react";
import {
  CatalogColumnDetail,
  CatalogTableSummary,
  DataSourceSummary,
  TablePreviewPage,
  isSessionExpiredError,
  previewTable,
} from "@/services/catalogService";
import type { MapEdge, WorkspaceJoin } from "./relationshipGraph";

export const SAMPLE_ROWS = 5;

interface Props {
  table: CatalogTableSummary;
  /** Column metadata: undefined while loading, "error" if it failed. */
  columns: CatalogColumnDetail[] | "error" | undefined;
  source: DataSourceSummary;
  declared: MapEdge[];
  joins: WorkspaceJoin[];
  /** Related tables (declared FKs) that are not on the canvas. */
  offCanvas: string[];
  onAddTable: (name: string) => void;
  onSelectEdge: (id: string) => void;
  onSelectJoin: (id: string) => void;
  onSessionExpired: () => void;
  onClose: () => void;
}

const label = "text-[10.5px] uppercase tracking-wider text-slate-500";

function rowCountText(table: CatalogTableSummary): string {
  if (table.row_count === null) return "Not available";
  const n = table.row_count.toLocaleString();
  if (table.row_count_is_estimate === true) return `~${n} (estimate from database statistics)`;
  if (table.row_count_is_estimate === false) return `${n} (exact count at the last scan)`;
  return `${n} (accuracy not recorded)`;
}

/**
 * Details for the table selected on the map. Everything here is catalog
 * metadata. The only live query is the optional sample, which asks the existing
 * preview API for a handful of rows and only when the user presses the button.
 */
export default function TableDetailsPanel({
  table,
  columns,
  source,
  declared,
  joins,
  offCanvas,
  onAddTable,
  onSelectEdge,
  onSelectJoin,
  onSessionExpired,
  onClose,
}: Props) {
  const [sample, setSample] = useState<TablePreviewPage | null>(null);
  const [sampleLoading, setSampleLoading] = useState(false);
  const [sampleError, setSampleError] = useState<string | null>(null);

  async function loadSample() {
    setSampleLoading(true);
    setSampleError(null);
    try {
      setSample(await previewTable(table.id, source, { limit: SAMPLE_ROWS }));
    } catch (err) {
      if (isSessionExpiredError(err)) {
        onSessionExpired();
        setSampleError("Your database session expired. Reconnect above, then try again.");
      } else {
        setSampleError(err instanceof Error ? err.message : "Could not load sample rows.");
      }
    } finally {
      setSampleLoading(false);
    }
  }

  const touching = declared.filter(
    (e) => e.childTable === table.table_name || e.parentTable === table.table_name
  );
  const myJoins = joins.filter(
    (j) => j.leftTable === table.table_name || j.rightTable === table.table_name
  );

  return (
    <div
      data-interactive
      data-nowheel
      className="absolute right-3 top-3 max-h-[92%] w-[340px] overflow-y-auto rounded-xl border border-white/10 bg-[#0b1630]/95 p-4 text-[12px] text-slate-300 shadow-xl shadow-black/40"
    >
      <div className="flex items-start justify-between gap-2">
        <h4 className="text-[13px] font-semibold text-white">Table details</h4>
        <button className="text-slate-500 hover:text-white" onClick={onClose} aria-label="Close details">
          ✕
        </button>
      </div>

      <div className="mt-3 space-y-2.5">
        <div>
          <div className={label}>Table</div>
          <div className="mt-0.5 break-all font-mono text-[12px] text-slate-100">
            {table.schema_name}.{table.table_name}
          </div>
        </div>
        <div>
          <div className={label}>Rows</div>
          <div className="mt-0.5 text-slate-100">{rowCountText(table)}</div>
        </div>
        <div>
          <div className={label}>Description</div>
          <div className="mt-0.5 text-slate-100">
            {table.business_description || <span className="text-slate-600">Not available</span>}
          </div>
        </div>
        <div>
          <div className={label}>Profiling</div>
          <div className="mt-0.5 text-slate-600">
            Null counts, distinct counts and other statistics are not available yet.
          </div>
        </div>
      </div>

      <div className="mt-4">
        <div className={label}>Columns ({table.column_count})</div>
        {columns === undefined && <p className="mt-1 text-slate-500">Loading columns…</p>}
        {columns === "error" && <p className="mt-1 text-rose-300">Could not load columns.</p>}
        {Array.isArray(columns) && (
          <div className="mt-1 overflow-x-auto">
            <table className="w-full text-[11.5px]">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wider text-slate-600">
                  <th className="py-1 pr-2 font-medium">Name</th>
                  <th className="py-1 pr-2 font-medium">Type</th>
                  <th className="py-1 pr-2 font-medium">Null</th>
                  <th className="py-1 font-medium">Key</th>
                </tr>
              </thead>
              <tbody>
                {columns.map((c) => (
                  <tr key={c.column_name} className="border-t border-white/[0.05]" title={c.business_description ?? undefined}>
                    <td className="py-1 pr-2 font-mono text-slate-100">{c.column_name}</td>
                    <td className="py-1 pr-2 font-mono text-slate-500">{c.data_type}</td>
                    <td className="py-1 pr-2 text-slate-500">{c.is_nullable ? "yes" : "no"}</td>
                    <td className="py-1">
                      {c.is_primary_key && (
                        <span className="rounded border border-cyan-400/30 bg-cyan-500/10 px-1 text-[9.5px] font-semibold text-cyan-300">PK</span>
                      )}
                      {c.is_foreign_key && (
                        <span className="ml-1 rounded border border-violet-400/30 bg-violet-500/10 px-1 text-[9.5px] font-semibold text-violet-300">FK</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="mt-4">
        <div className={label}>Declared relationships</div>
        <ul className="mt-1 space-y-1">
          {touching.map((e) => (
            <li key={e.id}>
              <button
                className="w-full rounded-md px-2 py-1 text-left font-mono text-[11px] hover:bg-white/[0.06]"
                onClick={() => onSelectEdge(e.id)}
              >
                {e.childTable}.{e.childColumn} → {e.parentTable}.{e.parentColumn}
              </button>
            </li>
          ))}
          {touching.length === 0 && <li className="text-slate-600">None declared.</li>}
        </ul>
        {offCanvas.length > 0 && (
          <div className="mt-2 rounded-lg border border-amber-500/25 bg-amber-500/10 px-2.5 py-2 text-[11.5px] text-amber-200">
            Related tables not on the canvas:
            <ul className="mt-1 space-y-1">
              {offCanvas.map((name) => (
                <li key={name} className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono">{name}</span>
                  <button
                    className="shrink-0 rounded border border-amber-400/30 px-1.5 py-0.5 text-[10.5px] font-semibold hover:bg-amber-500/20"
                    onClick={() => onAddTable(name)}
                  >
                    Add to canvas
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {myJoins.length > 0 && (
        <div className="mt-4">
          <div className={label}>Workspace joins (not database keys)</div>
          <ul className="mt-1 space-y-1">
            {myJoins.map((j) => (
              <li key={j.id}>
                <button
                  className="w-full rounded-md px-2 py-1 text-left font-mono text-[11px] text-amber-200 hover:bg-white/[0.06]"
                  onClick={() => onSelectJoin(j.id)}
                >
                  {j.joinType} {j.leftTable} ⟶ {j.rightTable}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-4">
        <div className={label}>Sample records</div>
        {!sample && (
          <button
            onClick={() => void loadSample()}
            disabled={sampleLoading}
            className="mt-1 rounded-lg border border-white/10 px-3 py-1.5 text-[11.5px] font-semibold text-slate-200 hover:bg-white/[0.06] disabled:opacity-50"
          >
            {sampleLoading ? "Loading…" : `Show ${SAMPLE_ROWS} sample rows`}
          </button>
        )}
        {sampleError && (
          <p className="mt-2 rounded-lg border border-rose-500/25 bg-rose-500/10 px-2.5 py-2 text-[11.5px] text-rose-300">
            {sampleError}
          </p>
        )}
        {sample && (
          <div className="mt-1 overflow-x-auto">
            {sample.rows.length === 0 ? (
              <p className="text-slate-600">The table returned no rows.</p>
            ) : (
              <table className="w-full whitespace-nowrap text-[11px]">
                <thead>
                  <tr className="text-left text-slate-500">
                    {sample.column_names.map((n) => (
                      <th key={n} className="pr-3 py-1 font-mono font-medium">{n}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sample.rows.map((row, i) => (
                    <tr key={i} className="border-t border-white/[0.05]">
                      {sample.column_names.map((n) => (
                        <td key={n} className="max-w-[140px] truncate pr-3 py-1 font-mono text-slate-200">
                          {row[n] === null || row[n] === undefined ? (
                            <span className="text-slate-600">NULL</span>
                          ) : (
                            String(row[n])
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="mt-1 text-[10.5px] text-slate-600">
              First {sample.rows.length} rows only, read live from the database.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
