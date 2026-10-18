"use client";

/**
 * Bottom panel for the selected node: real preview rows, columns, the SQL that
 * produces them, and problems. Rows come from the database already limited, so
 * a large table is never loaded into the browser.
 */

import { useState } from "react";
import type { PreviewResult, SqlResult, StudioNodeResult } from "@/services/etlStudioService";
import { STATUS_LABELS, type DisplayStatus, type PipelineNode, type RunRecord } from "./pipelineModel";

export const PREVIEW_LIMITS = [10, 25, 50, 100] as const;

interface Props {
  node: PipelineNode | null;
  nodeName: string;
  result?: StudioNodeResult;
  status: DisplayStatus;
  outdated: boolean;
  run?: RunRecord;
  data?: PreviewResult;
  sql?: SqlResult;
  sqlError?: string | null;
  limit: number;
  onLimit: (n: number) => void;
  previewing: boolean;
  /** Why previewing is not possible right now, or null. */
  blockedReason: string | null;
  onPreview: () => void;
  onShowSql: () => void;
  /** Input nodes whose own output can be previewed (to see what goes in). */
  inputs: { port: string; nodeId: string; label: string }[];
  onPreviewInput: (nodeId: string) => void;
}

type Tab = "data" | "columns" | "sql" | "problems";

function cell(value: unknown): React.ReactNode {
  if (value === null || value === undefined) return <span className="italic text-slate-600">NULL</span>;
  const text = typeof value === "string" ? value : typeof value === "object" ? JSON.stringify(value) : String(value);
  return text.length > 120 ? `${text.slice(0, 120)}…` : text;
}

export default function PreviewPanel(props: Props) {
  const { node, result, data, run } = props;
  const [tab, setTab] = useState<Tab>("data");

  if (!node) {
    return (
      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4 text-[12.5px] text-slate-500">
        Select a node to see its columns, a preview of its real data, and the SQL behind it.
      </div>
    );
  }

  const issues = result?.issues ?? [];
  const tabs: [Tab, string][] = [
    ["data", "Data"],
    ["columns", `Columns${result?.columns ? ` (${result.columns.length})` : ""}`],
    ["sql", "SQL"],
    ["problems", `Problems${issues.length ? ` (${issues.length})` : ""}`],
  ];

  const complete = data && !data.has_more && data.rows.length < data.row_limit;

  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02]" data-testid="preview-panel">
      <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.06] px-4 py-2">
        <h3 className="text-[13px] font-semibold text-white">{props.nodeName}</h3>
        <span className="rounded-full border border-white/[0.1] px-2 py-[1px] text-[10.5px] text-slate-400">{STATUS_LABELS[props.status]}</span>
        {props.outdated && (
          <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-[1px] text-[10.5px] text-amber-300">
            Settings changed since this ran — preview again
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <label className="text-[11px] text-slate-500">
            Rows
            <select
              className="ml-1 rounded border border-white/[0.1] bg-[#0b1018] px-1 py-0.5 text-[11.5px] text-slate-200"
              value={props.limit}
              onChange={(e) => props.onLimit(Number(e.target.value))}
              aria-label="Preview rows"
            >
              {PREVIEW_LIMITS.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          {props.inputs.map((input) => (
            <button
              key={input.nodeId}
              className="rounded-md border border-white/[0.1] px-2 py-1 text-[11.5px] text-slate-300 hover:bg-white/[0.06] disabled:opacity-40"
              disabled={props.previewing}
              onClick={() => props.onPreviewInput(input.nodeId)}
              title="Show what flows into this step"
            >
              Preview input{props.inputs.length > 1 ? ` (${input.port})` : ""}
            </button>
          ))}
          <button
            className="rounded-md bg-cyan-500/90 px-3 py-1 text-[12px] font-medium text-[#04202a] hover:bg-cyan-400 disabled:opacity-40"
            disabled={props.previewing || props.blockedReason !== null}
            title={props.blockedReason ?? "Run a read-only query and show the first rows"}
            onClick={props.onPreview}
          >
            {props.previewing ? "Running…" : "Preview this step"}
          </button>
        </div>
      </div>

      <div className="flex gap-1 px-4 pt-2" role="tablist">
        {tabs.map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => {
              setTab(id);
              if (id === "sql" && !props.sql) props.onShowSql();
            }}
            className={`rounded-md px-2.5 py-1 text-[12px] ${tab === id ? "bg-white/[0.08] text-white" : "text-slate-500 hover:text-slate-200"}`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="p-4">
        {props.blockedReason && tab === "data" && <p className="mb-2 text-[12px] text-amber-300">{props.blockedReason}</p>}

        {tab === "data" && (
          <>
            {run?.state === "failed" && run.error && (
              <p role="alert" className="mb-2 rounded-lg border border-rose-500/25 bg-rose-500/10 px-3 py-2 text-[12px] text-rose-200">
                {run.error}
              </p>
            )}
            {!data && !run?.error && (
              <p className="text-[12.5px] text-slate-500">
                Nothing has been run for this step yet. Preview runs one read-only query and returns at most {props.limit} rows.
              </p>
            )}
            {data && (
              <>
                <p className="mb-2 text-[11.5px] text-slate-500" data-testid="preview-caption">
                  {complete
                    ? `The whole result fits: exactly ${data.rows.length} row${data.rows.length === 1 ? "" : "s"}.`
                    : `Showing the first ${data.rows.length} rows. More rows exist; the total is not counted.`}{" "}
                  Only these rows were fetched from the database.
                </p>
                <div className="max-h-72 overflow-auto rounded-lg border border-white/[0.08]">
                  <table className="min-w-full text-left text-[12px]">
                    <thead className="sticky top-0 bg-[#111826] text-slate-400">
                      <tr>
                        {data.columns.map((c) => (
                          <th key={c.name} className="whitespace-nowrap px-3 py-1.5 font-medium">
                            {c.name}
                            <span className="ml-1 text-[10px] text-slate-600">{c.data_type}</span>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {data.rows.map((row, i) => (
                        <tr key={i} className="border-t border-white/[0.05] text-slate-200">
                          {row.map((v, j) => (
                            <td key={j} className="whitespace-nowrap px-3 py-1">
                              {cell(v)}
                            </td>
                          ))}
                        </tr>
                      ))}
                      {data.rows.length === 0 && (
                        <tr>
                          <td className="px-3 py-3 text-slate-500" colSpan={Math.max(1, data.columns.length)}>
                            The query ran and returned no rows.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
                <p className="mt-2 text-[11px] text-slate-600">{data.execution_path}</p>
              </>
            )}
          </>
        )}

        {tab === "columns" && (
          <div className="max-h-72 overflow-auto rounded-lg border border-white/[0.08]">
            <table className="min-w-full text-left text-[12px]">
              <thead className="bg-[#111826] text-slate-400">
                <tr><th className="px-3 py-1.5">Column</th><th className="px-3 py-1.5">Type</th></tr>
              </thead>
              <tbody>
                {(result?.columns ?? []).map((c) => (
                  <tr key={c.name} className="border-t border-white/[0.05] text-slate-200">
                    <td className="px-3 py-1">{c.name}</td>
                    <td className="px-3 py-1 text-slate-400">{c.data_type}</td>
                  </tr>
                ))}
                {!result?.columns && (
                  <tr><td className="px-3 py-3 text-slate-500" colSpan={2}>Columns appear once this step is configured correctly.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {tab === "sql" && (
          <>
            {props.sqlError && <p className="mb-2 text-[12px] text-amber-300">{props.sqlError}</p>}
            {props.sql ? (
              <>
                <pre className="max-h-72 overflow-auto rounded-lg border border-white/[0.08] bg-[#0b1018] p-3 text-[11.5px] leading-relaxed text-slate-300" data-testid="sql-text">
                  {props.sql.sql}
                </pre>
                {Object.keys(props.sql.parameters).length > 0 && (
                  <p className="mt-2 text-[11.5px] text-slate-500">
                    Values are sent separately from the SQL as parameters:{" "}
                    {Object.entries(props.sql.parameters).map(([k, v]) => `${k} = ${JSON.stringify(v)}`).join(", ")}
                  </p>
                )}
                <p className="mt-1 text-[11px] text-slate-600">{props.sql.execution_path} Showing a 25-row preview of this step; this text is not editable and is never run from here.</p>
              </>
            ) : (
              !props.sqlError && <p className="text-[12.5px] text-slate-500">Loading…</p>
            )}
          </>
        )}

        {tab === "problems" &&
          (issues.length === 0 ? (
            <p className="text-[12.5px] text-slate-500">No problems found for this step.</p>
          ) : (
            <ul className="space-y-1">
              {issues.map((issue, i) => (
                <li key={i} className="rounded-lg border border-white/[0.08] px-3 py-1.5 text-[12px] text-slate-200">
                  <span className="mr-2 text-[10.5px] uppercase tracking-wide text-slate-500">{issue.kind}</span>
                  {issue.message}
                </li>
              ))}
            </ul>
          ))}
      </div>
    </div>
  );
}
