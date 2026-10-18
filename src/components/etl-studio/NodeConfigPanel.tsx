"use client";

/**
 * Settings for the selected node. Every control only edits the node's
 * configuration in the browser. The server checks the result when the pipeline
 * is validated; nothing here runs against a database.
 */

import { JOIN_TYPE_HELP } from "./joinSuggestions";
import type { CatalogTableSummary } from "@/services/catalogService";
import type { DiagnosticsResult, StudioColumn, StudioIssue, StudioNodeResult } from "@/services/etlStudioService";
import {
  NODE_META,
  OPERATOR_LABELS,
  connectionProblem,
  formatRowCount,
  inputPorts,
  inputsOf,
  operatorNeedsValue,
  operatorsFor,
  type Config,
  type PipelineEdge,
  type PipelineNode,
} from "./pipelineModel";

const JOIN_TYPES = ["INNER", "LEFT", "RIGHT", "FULL"] as const;
const JOIN_HELP: Record<string, string> = JOIN_TYPE_HELP;
const MAX_JOIN_CONDITIONS = 5;
const MAX_FILTER_CONDITIONS = 10;

interface Props {
  node: PipelineNode;
  nodes: PipelineNode[];
  edges: PipelineEdge[];
  results: Record<string, StudioNodeResult | undefined>;
  tables: CatalogTableSummary[];
  /** Tables of other data sources already shown in the Builder (only used to name a Source step's table). */
  otherTables?: CatalogTableSummary[];
  /** Why this join cannot be measured even with a connection (it reads from a non-main source). */
  measureBlockedReason?: string | null;
  nodeLabel: (id: string) => string;
  onConfig: (config: Config) => void;
  onConnect: (source: string, target: string, port: string) => string | null;
  onDisconnect: (edge: PipelineEdge) => void;
  onRemove: () => void;
  diagnostics?: DiagnosticsResult;
  /** The pipeline changed after these measurements were taken. */
  diagnosticsOutdated?: boolean;
  diagnosticsError?: string | null;
  measuring: boolean;
  canMeasure: boolean;
  onMeasure: () => void;
}

const field = "w-full rounded-lg border border-white/[0.1] bg-[#0b1018] px-2 py-1.5 text-[12.5px] text-slate-100 outline-none focus:border-cyan-400/60";
const smallBtn = "rounded-md border border-white/[0.1] px-2 py-1 text-[11.5px] text-slate-300 hover:bg-white/[0.06] disabled:opacity-40";

function Label({ children }: { children: React.ReactNode }) {
  return <div className="mb-1 mt-3 text-[11px] font-medium uppercase tracking-wide text-slate-500">{children}</div>;
}

function asList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function asRows(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((v): v is Record<string, unknown> => typeof v === "object" && v !== null) : [];
}

function ColumnSelect({
  columns,
  value,
  onChange,
  label,
}: {
  columns: StudioColumn[];
  value: string;
  onChange: (v: string) => void;
  label: string;
}) {
  return (
    <select aria-label={label} className={field} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Choose a column…</option>
      {value && !columns.some((c) => c.name === value) && <option value={value}>{value} (not available)</option>}
      {columns.map((c) => (
        <option key={c.name} value={c.name}>
          {c.name} ({c.data_type})
        </option>
      ))}
    </select>
  );
}

function ColumnChecklist({
  columns,
  selected,
  onChange,
  label,
}: {
  columns: StudioColumn[];
  selected: string[];
  onChange: (next: string[]) => void;
  label: string;
}) {
  const missing = selected.filter((s) => !columns.some((c) => c.name === s));
  return (
    <div aria-label={label} className="max-h-40 overflow-auto rounded-lg border border-white/[0.08] p-1.5">
      {columns.length === 0 && <p className="px-1 text-[12px] text-slate-600">Connect an input first to see its columns.</p>}
      {columns.map((c) => (
        <label key={c.name} className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-[12px] text-slate-300 hover:bg-white/[0.04]">
          <input
            type="checkbox"
            checked={selected.includes(c.name)}
            onChange={(e) => onChange(e.target.checked ? [...selected, c.name] : selected.filter((s) => s !== c.name))}
          />
          <span className="truncate">{c.name}</span>
          <span className="ml-auto shrink-0 text-[10.5px] text-slate-600">{c.data_type}</span>
        </label>
      ))}
      {missing.map((m) => (
        <div key={m} className="px-1 text-[12px] text-rose-300">
          {m} is no longer available
        </div>
      ))}
    </div>
  );
}

function Issues({ issues }: { issues: StudioIssue[] }) {
  if (issues.length === 0) return null;
  const tone: Record<string, string> = {
    invalid: "border-rose-500/25 bg-rose-500/10 text-rose-200",
    incomplete: "border-slate-500/25 bg-slate-500/10 text-slate-300",
    blocked: "border-amber-500/25 bg-amber-500/10 text-amber-200",
    warning: "border-amber-500/25 bg-amber-500/10 text-amber-200",
  };
  return (
    <ul className="mt-3 space-y-1" aria-label="Problems with this step">
      {issues.map((issue, i) => (
        <li key={`${issue.message}-${i}`} className={`rounded-lg border px-2 py-1.5 text-[12px] ${tone[issue.kind] ?? tone.incomplete}`}>
          <span className="mr-1 font-semibold capitalize">{issue.kind === "incomplete" ? "To do" : issue.kind}:</span>
          {issue.message}
        </li>
      ))}
    </ul>
  );
}

export default function NodeConfigPanel(props: Props) {
  const { node, nodes, edges, results, tables, nodeLabel, onConfig, onConnect, onDisconnect, onRemove } = props;
  const meta = NODE_META[node.type];
  const result = results[node.id];
  const ports = inputPorts(node.type);
  const wired = inputsOf(edges, node.id);
  const cfg = node.config;
  const columnsOf = (id: string | undefined): StudioColumn[] => (id ? results[id]?.columns ?? [] : []);
  const inputColumns = columnsOf(wired.in ?? wired.left);
  const issues = result?.issues ?? [];

  const set = (patch: Config) => onConfig({ ...cfg, ...patch });

  function connectionControls() {
    if (ports.length === 0) return null;
    return (
      <>
        <Label>Inputs</Label>
        <div className="space-y-2">
          {ports.map((port) => {
            const current = wired[port] ?? "";
            const choices = nodes.filter(
              (n) => n.id !== node.id && (n.id === current || connectionProblem(nodes, edges, n.id, node.id, port) === null)
            );
            return (
              <div key={port}>
                {ports.length > 1 && <div className="mb-0.5 text-[11px] text-slate-500">{port} input</div>}
                <select
                  className={field}
                  aria-label={`${port} input`}
                  value={current}
                  onChange={(e) => {
                    const next = e.target.value;
                    if (current) onDisconnect({ source: current, target: node.id, port });
                    if (next) onConnect(next, node.id, port);
                  }}
                >
                  <option value="">Not connected</option>
                  {choices.map((n) => (
                    <option key={n.id} value={n.id}>
                      {nodeLabel(n.id)}
                    </option>
                  ))}
                </select>
              </div>
            );
          })}
        </div>
      </>
    );
  }

  function sourceForm() {
    const tableId = typeof cfg.table_id === "string" ? cfg.table_id : "";
    const table = tables.find((t) => t.id === tableId);
    const elsewhere = table ? undefined : props.otherTables?.find((t) => t.id === tableId);
    return (
      <>
        <Label>Table</Label>
        {/* Choosing a table here always means a table of the main source, so any recorded source is dropped. */}
        <select
          className={field}
          aria-label="Table"
          value={tableId}
          onChange={(e) => set({ table_id: e.target.value || null, source_id: undefined })}
        >
          <option value="">Choose a table…</option>
          {tableId && !table && elsewhere && (
            <option value={tableId}>
              {elsewhere.schema_name}.{elsewhere.table_name} (another data source)
            </option>
          )}
          {tableId && !table && !elsewhere && <option value={tableId}>Unknown table (not in the Catalog)</option>}
          {tables.map((t) => (
            <option key={t.id} value={t.id}>
              {t.schema_name}.{t.table_name}
            </option>
          ))}
        </select>
        {table && (
          <p className="mt-2 text-[12px] text-slate-400">
            {formatRowCount(table.row_count, table.row_count_is_estimate)} · {table.column_count} columns
          </p>
        )}
        <Label>Columns</Label>
        <ul className="max-h-48 overflow-auto rounded-lg border border-white/[0.08] p-1.5 text-[12px]">
          {(result?.columns ?? []).map((c) => (
            <li key={c.name} className="flex gap-2 px-1 py-0.5 text-slate-300">
              <span className="truncate">{c.name}</span>
              <span className="ml-auto shrink-0 text-[10.5px] text-slate-600">{c.data_type}</span>
            </li>
          ))}
          {(result?.columns ?? []).length === 0 && <li className="px-1 text-slate-600">Choose a table to see its columns.</li>}
        </ul>
      </>
    );
  }

  function joinForm() {
    const left = columnsOf(wired.left);
    const right = columnsOf(wired.right);
    const conditions = asRows(cfg.conditions);
    const update = (next: Record<string, unknown>[]) => set({ conditions: next });
    const d = props.diagnostics?.summary;
    return (
      <>
        <Label>Join type</Label>
        <div className="grid grid-cols-4 gap-1">
          {JOIN_TYPES.map((jt) => (
            <button
              key={jt}
              type="button"
              aria-pressed={cfg.join_type === jt}
              className={`rounded-md border px-1 py-1 text-[11.5px] ${
                cfg.join_type === jt ? "border-cyan-400/60 bg-cyan-500/10 text-white" : "border-white/[0.1] text-slate-400 hover:bg-white/[0.05]"
              }`}
              onClick={() => set({ join_type: jt })}
            >
              {jt}
            </button>
          ))}
        </div>
        <p className="mt-1 text-[11.5px] text-slate-500">{JOIN_HELP[String(cfg.join_type)] ?? ""}</p>

        <Label>Matching columns</Label>
        <div className="space-y-2">
          {conditions.map((c, i) => (
            <div key={i} className="grid grid-cols-[1fr_auto_1fr_auto] items-center gap-1.5">
              <ColumnSelect
                label={`Left column ${i + 1}`}
                columns={left}
                value={String(c.left_column ?? "")}
                onChange={(v) => update(conditions.map((x, j) => (j === i ? { ...x, left_column: v } : x)))}
              />
              <span className="text-slate-500">=</span>
              <ColumnSelect
                label={`Right column ${i + 1}`}
                columns={right}
                value={String(c.right_column ?? "")}
                onChange={(v) => update(conditions.map((x, j) => (j === i ? { ...x, right_column: v } : x)))}
              />
              <button className={smallBtn} aria-label={`Remove condition ${i + 1}`} onClick={() => update(conditions.filter((_, j) => j !== i))}>
                ×
              </button>
            </div>
          ))}
        </div>
        <button
          className={`${smallBtn} mt-2`}
          disabled={conditions.length >= MAX_JOIN_CONDITIONS}
          onClick={() => update([...conditions, { left_column: "", right_column: "" }])}
        >
          + Add matching columns
        </button>
        {(!wired.left || !wired.right) && (
          <p className="mt-2 text-[12px] text-amber-300">Connect both a left and a right input to choose columns.</p>
        )}

        <Label>Measure on the real data</Label>
        <button className={smallBtn} disabled={props.measuring || !props.canMeasure || result?.status !== "ready"} onClick={props.onMeasure}>
          {props.measuring ? "Measuring…" : "Measure this join"}
        </button>
        {!props.canMeasure && (
          <p className="mt-1 text-[11.5px] text-slate-500" data-testid="measure-blocked">
            {props.measureBlockedReason ?? "Connect to the database (above) to measure."}
          </p>
        )}
        {props.diagnosticsError && <p className="mt-2 text-[12px] text-rose-300">{props.diagnosticsError}</p>}
        {d && props.diagnosticsOutdated && (
          <p className="mt-2 rounded-lg border border-amber-500/25 bg-amber-500/10 px-2 py-1 text-[12px] text-amber-200" data-testid="measurement-outdated">
            Measurement outdated: the pipeline changed after this was measured. The numbers below describe the earlier settings. Press &ldquo;Measure this join&rdquo; again.
          </p>
        )}
        {d && (
          <dl className={`mt-2 grid grid-cols-2 gap-x-3 gap-y-1 rounded-lg border border-white/[0.08] p-2 text-[12px] ${props.diagnosticsOutdated ? "opacity-50" : ""}`} aria-label="Join measurements">
            <dt className="text-slate-500">Left rows</dt><dd className="text-right text-slate-200">{d.left_rows.toLocaleString("en-US")}</dd>
            <dt className="text-slate-500">Right rows</dt><dd className="text-right text-slate-200">{d.right_rows.toLocaleString("en-US")}</dd>
            <dt className="text-slate-500">Matching pairs</dt><dd className="text-right text-slate-200">{d.matched_pairs.toLocaleString("en-US")}</dd>
            <dt className="text-slate-500">Result rows ({d.join_type})</dt><dd className="text-right text-slate-200">{d.output_rows.toLocaleString("en-US")}</dd>
            <dt className="text-slate-500">Left rows with no match</dt><dd className="text-right text-slate-200">{d.unmatched_left_rows.toLocaleString("en-US")}</dd>
            <dt className="text-slate-500">Right rows with no match</dt><dd className="text-right text-slate-200">{d.unmatched_right_rows.toLocaleString("en-US")}</dd>
            <dt className="text-slate-500">Empty keys (left / right)</dt><dd className="text-right text-slate-200">{d.left_null_keys} / {d.right_null_keys}</dd>
            <dt className="text-slate-500">Largest repeat (left / right)</dt><dd className="text-right text-slate-200">{d.largest_left_key_group} / {d.largest_right_key_group}</dd>
            {d.multiplies_main_side && (
              <dd className="col-span-2 mt-1 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-amber-200">
                This join repeats rows of the {d.main_side} table: {d.extra_rows_from_multiplication.toLocaleString("en-US")} extra rows
                appear because some keys match several rows on the other side.
              </dd>
            )}
          </dl>
        )}
      </>
    );
  }

  function filterForm() {
    const conditions = asRows(cfg.conditions);
    const update = (next: Record<string, unknown>[]) => set({ conditions: next });
    return (
      <>
        <Label>Keep rows where</Label>
        <select
          className={`${field} mb-2`}
          aria-label="Combine conditions"
          value={String(cfg.combine ?? "AND")}
          onChange={(e) => set({ combine: e.target.value })}
        >
          <option value="AND">all conditions are true (AND)</option>
          <option value="OR">any condition is true (OR)</option>
        </select>
        <div className="space-y-2">
          {conditions.map((c, i) => {
            const col = inputColumns.find((x) => x.name === c.column);
            const ops = operatorsFor(col?.category ?? "other");
            const op = String(c.operator ?? "");
            const patch = (p: Record<string, unknown>) => update(conditions.map((x, j) => (j === i ? { ...x, ...p } : x)));
            return (
              <div key={i} className="rounded-lg border border-white/[0.08] p-1.5">
                <div className="grid grid-cols-[1fr_auto] gap-1.5">
                  <ColumnSelect
                    label={`Filter column ${i + 1}`}
                    columns={inputColumns}
                    value={String(c.column ?? "")}
                    onChange={(v) => {
                      const nextCol = inputColumns.find((x) => x.name === v);
                      const allowed = operatorsFor(nextCol?.category ?? "other");
                      patch({ column: v, operator: allowed.includes(op) ? op : "" });
                    }}
                  />
                  <button className={smallBtn} aria-label={`Remove filter ${i + 1}`} onClick={() => update(conditions.filter((_, j) => j !== i))}>
                    ×
                  </button>
                </div>
                <select
                  className={`${field} mt-1.5`}
                  aria-label={`Filter operator ${i + 1}`}
                  value={op}
                  onChange={(e) => patch({ operator: e.target.value })}
                >
                  <option value="">Choose a test…</option>
                  {ops.map((o) => (
                    <option key={o} value={o}>
                      {OPERATOR_LABELS[o] ?? o}
                    </option>
                  ))}
                </select>
                {op && operatorNeedsValue(op) && (
                  <input
                    className={`${field} mt-1.5`}
                    aria-label={`Filter value ${i + 1}`}
                    placeholder={col?.category === "datetime" ? "YYYY-MM-DD" : "Value"}
                    value={String(c.value ?? "")}
                    onChange={(e) => patch({ value: e.target.value })}
                  />
                )}
              </div>
            );
          })}
        </div>
        <button
          className={`${smallBtn} mt-2`}
          disabled={conditions.length >= MAX_FILTER_CONDITIONS}
          onClick={() => update([...conditions, { column: "", operator: "", value: "" }])}
        >
          + Add condition
        </button>
      </>
    );
  }

  function dropForm() {
    return (
      <>
        <Label>Columns to remove</Label>
        <ColumnChecklist label="Columns to remove" columns={inputColumns} selected={asList(cfg.columns)} onChange={(columns) => set({ columns })} />
        <p className="mt-1 text-[11.5px] text-slate-500">At least one column must remain.</p>
      </>
    );
  }

  function renameForm() {
    const mappings = asRows(cfg.mappings);
    const update = (next: Record<string, unknown>[]) => set({ mappings: next });
    return (
      <>
        <Label>Renames</Label>
        <div className="space-y-2">
          {mappings.map((m, i) => (
            <div key={i} className="grid grid-cols-[1fr_auto_1fr_auto] items-center gap-1.5">
              <ColumnSelect
                label={`Column to rename ${i + 1}`}
                columns={inputColumns}
                value={String(m.from ?? "")}
                onChange={(v) => update(mappings.map((x, j) => (j === i ? { ...x, from: v } : x)))}
              />
              <span className="text-slate-500">→</span>
              <input
                className={field}
                aria-label={`New name ${i + 1}`}
                placeholder="New name"
                value={String(m.to ?? "")}
                onChange={(e) => update(mappings.map((x, j) => (j === i ? { ...x, to: e.target.value } : x)))}
              />
              <button className={smallBtn} aria-label={`Remove rename ${i + 1}`} onClick={() => update(mappings.filter((_, j) => j !== i))}>
                ×
              </button>
            </div>
          ))}
        </div>
        <button className={`${smallBtn} mt-2`} onClick={() => update([...mappings, { from: "", to: "" }])}>
          + Add rename
        </button>
      </>
    );
  }

  function dedupeForm() {
    const chosen = asList(cfg.columns);
    return (
      <>
        <Label>Rows count as duplicates when</Label>
        <div className="space-y-1 text-[12px] text-slate-300">
          <label className="flex items-center gap-2">
            <input type="radio" name={`dedupe-${node.id}`} checked={chosen.length === 0} onChange={() => set({ columns: [] })} />
            every column is identical
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name={`dedupe-${node.id}`}
              checked={chosen.length > 0}
              onChange={() => set({ columns: inputColumns.slice(0, 1).map((c) => c.name) })}
            />
            these columns are identical:
          </label>
        </div>
        {chosen.length > 0 && (
          <div className="mt-2">
            <ColumnChecklist label="Key columns" columns={inputColumns} selected={chosen} onChange={(columns) => set({ columns })} />
            <p className="mt-1 text-[11.5px] text-amber-300">
              When rows share these values, which one is kept is not defined, because no sort order is applied.
            </p>
          </div>
        )}
      </>
    );
  }

  function missingForm() {
    const strategy = String(cfg.strategy ?? "drop_rows");
    return (
      <>
        <Label>What to do</Label>
        <select className={field} aria-label="Strategy" value={strategy} onChange={(e) => set({ strategy: e.target.value })}>
          <option value="drop_rows">Remove rows with an empty value</option>
          <option value="fixed">Fill empty values with a fixed value</option>
          <option value="mean">Fill empty values with the column mean</option>
        </select>
        <p className="mt-1 text-[11.5px] text-slate-500">Median and most-common-value fills are not available yet.</p>
        <Label>Columns</Label>
        <ColumnChecklist label="Columns to check" columns={inputColumns} selected={asList(cfg.columns)} onChange={(columns) => set({ columns })} />
        {strategy === "fixed" && (
          <>
            <Label>Fixed value</Label>
            <input className={field} aria-label="Fixed value" value={String(cfg.value ?? "")} onChange={(e) => set({ value: e.target.value })} />
          </>
        )}
      </>
    );
  }

  function aggregateForm() {
    const measures = asRows(cfg.measures);
    const update = (next: Record<string, unknown>[]) => set({ measures: next });
    return (
      <>
        <Label>Group by (optional)</Label>
        <ColumnChecklist label="Group-by columns" columns={inputColumns} selected={asList(cfg.group_by)} onChange={(group_by) => set({ group_by })} />
        <p className="mt-1 text-[11.5px] text-slate-500">Leave empty to get one row for the whole table. Columns you do not choose are dropped.</p>
        <Label>Measures</Label>
        <div className="space-y-2">
          {measures.map((m, i) => {
            const fn = String(m.function ?? "");
            const patch = (p: Record<string, unknown>) => update(measures.map((x, j) => (j === i ? { ...x, ...p } : x)));
            return (
              <div key={i} className="rounded-lg border border-white/[0.08] p-1.5">
                <div className="grid grid-cols-[1fr_auto] gap-1.5">
                  <select className={field} aria-label={`Measure function ${i + 1}`} value={fn} onChange={(e) => patch({ function: e.target.value })}>
                    <option value="">Choose a calculation…</option>
                    <option value="sum">Sum</option>
                    <option value="avg">Average</option>
                    <option value="min">Smallest</option>
                    <option value="max">Largest</option>
                    <option value="count">Count</option>
                  </select>
                  <button className={smallBtn} aria-label={`Remove measure ${i + 1}`} onClick={() => update(measures.filter((_, j) => j !== i))}>
                    ×
                  </button>
                </div>
                <div className="mt-1.5">
                  <ColumnSelect label={`Measure column ${i + 1}`} columns={inputColumns} value={String(m.column ?? "")} onChange={(v) => patch({ column: v })} />
                  {fn === "count" && <p className="mt-1 text-[11.5px] text-slate-500">Leave the column empty to count rows.</p>}
                </div>
                <input
                  className={`${field} mt-1.5`}
                  aria-label={`Measure name ${i + 1}`}
                  placeholder="Name of the new column"
                  value={String(m.alias ?? "")}
                  onChange={(e) => patch({ alias: e.target.value })}
                />
              </div>
            );
          })}
        </div>
        <button className={`${smallBtn} mt-2`} onClick={() => update([...measures, { function: "", column: "", alias: "" }])}>
          + Add measure
        </button>
      </>
    );
  }

  function changeTypeForm() {
    const mappings = asRows(cfg.mappings);
    const update = (next: Record<string, unknown>[]) => set({ mappings: next });
    return (
      <>
        <Label>Conversions</Label>
        <div className="space-y-2">
          {mappings.map((m, i) => (
            <div key={i} className="grid grid-cols-[1fr_auto_1fr_auto] items-center gap-1.5">
              <ColumnSelect
                label={`Column to convert ${i + 1}`}
                columns={inputColumns}
                value={String(m.column ?? "")}
                onChange={(v) => update(mappings.map((x, j) => (j === i ? { ...x, column: v } : x)))}
              />
              <span className="text-slate-500">→</span>
              <select
                className={field}
                aria-label={`New type ${i + 1}`}
                value={String(m.to ?? "")}
                onChange={(e) => update(mappings.map((x, j) => (j === i ? { ...x, to: e.target.value } : x)))}
              >
                <option value="">Choose a type…</option>
                <option value="integer">Whole number</option>
                <option value="decimal">Decimal number</option>
                <option value="text">Text</option>
                <option value="date">Date</option>
              </select>
              <button className={smallBtn} aria-label={`Remove conversion ${i + 1}`} onClick={() => update(mappings.filter((_, j) => j !== i))}>
                ×
              </button>
            </div>
          ))}
        </div>
        <button className={`${smallBtn} mt-2`} onClick={() => update([...mappings, { column: "", to: "" }])}>
          + Add conversion
        </button>
        <p className="mt-2 text-[11.5px] text-amber-300">
          Values that cannot be converted stop the run with a database error. Decimal to whole number is not available yet.
        </p>
      </>
    );
  }

  function deriveForm() {
    const operand = (side: "left" | "right") => {
      const raw = cfg[side];
      const spec = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
      const isColumn = "column" in spec;
      const setSpec = (next: Record<string, unknown>) => set({ [side]: next });
      return (
        <div className="rounded-lg border border-white/[0.08] p-1.5">
          <select
            className={field}
            aria-label={`${side === "left" ? "First" : "Second"} value type`}
            value={isColumn ? "column" : "value"}
            onChange={(e) => setSpec(e.target.value === "column" ? { column: "" } : { value: "" })}
          >
            <option value="column">A column</option>
            <option value="value">A number</option>
          </select>
          <div className="mt-1.5">
            {isColumn ? (
              <ColumnSelect
                label={`${side === "left" ? "First" : "Second"} column`}
                columns={inputColumns}
                value={String(spec.column ?? "")}
                onChange={(v) => setSpec({ column: v })}
              />
            ) : (
              <input
                className={field}
                aria-label={`${side === "left" ? "First" : "Second"} number`}
                placeholder="Number"
                value={String(spec.value ?? "")}
                onChange={(e) => setSpec({ value: e.target.value })}
              />
            )}
          </div>
        </div>
      );
    };
    return (
      <>
        <Label>New column name</Label>
        <input className={field} aria-label="New column name" placeholder="For example: total_price" value={String(cfg.name ?? "")} onChange={(e) => set({ name: e.target.value })} />
        <Label>Calculation</Label>
        <div className="space-y-2">
          {operand("left")}
          <select className={field} aria-label="Operation" value={String(cfg.operation ?? "")} onChange={(e) => set({ operation: e.target.value })}>
            <option value="add">plus (+)</option>
            <option value="subtract">minus (−)</option>
            <option value="multiply">times (×)</option>
            <option value="divide">divided by (÷)</option>
          </select>
          {operand("right")}
        </div>
        <p className="mt-1 text-[11.5px] text-slate-500">
          At least one side must be a column. An empty value gives an empty result. Dividing by zero gives an empty result, not an error.
        </p>
      </>
    );
  }

  function outputForm() {
    return (
      <>
        <Label>Dataset name</Label>
        <input className={field} aria-label="Dataset name" placeholder="Optional" value={String(cfg.name ?? "")} onChange={(e) => set({ name: e.target.value })} />
        <Label>Columns</Label>
        <ul className="max-h-48 overflow-auto rounded-lg border border-white/[0.08] p-1.5 text-[12px]">
          {inputColumns.map((c) => (
            <li key={c.name} className="flex gap-2 px-1 py-0.5 text-slate-300">
              <span className="truncate">{c.name}</span>
              <span className="ml-auto shrink-0 text-[10.5px] text-slate-600">{c.data_type}</span>
            </li>
          ))}
          {inputColumns.length === 0 && <li className="px-1 text-slate-600">Connect the last step of your pipeline.</li>}
        </ul>
        <Label>Publish</Label>
        <button className={smallBtn} disabled title="Publishing is not available yet">
          Publish dataset
        </button>
        <p className="mt-1 text-[11.5px] text-slate-500">
          Publishing is not available in this version. The existing build step requires a reconciliation check against the source
          that this builder cannot produce yet, so nothing can be saved as a dataset or sent to AutoML from here.
        </p>
      </>
    );
  }

  const forms: Record<string, () => React.ReactNode> = {
    source: sourceForm,
    join: joinForm,
    filter: filterForm,
    drop_columns: dropForm,
    rename_columns: renameForm,
    remove_duplicates: dedupeForm,
    handle_missing: missingForm,
    aggregate: aggregateForm,
    change_type: changeTypeForm,
    derive_column: deriveForm,
    output: outputForm,
  };

  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4" data-testid="node-config">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-[14px] font-semibold text-white">{meta.label}</h3>
          <p className="text-[11.5px] text-slate-500">
            {nodeLabel(node.id)} · {meta.hint}
          </p>
        </div>
        <button
          className={`${smallBtn} border-rose-500/30 text-rose-300`}
          title="Removes this node and its connections from the canvas only. No data is changed."
          onClick={onRemove}
        >
          Remove node
        </button>
      </div>
      {connectionControls()}
      {forms[node.type]?.()}
      {result?.notes.length ? (
        <ul className="mt-3 space-y-1 text-[11.5px] text-slate-500">
          {result.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
      <Issues issues={issues} />
    </div>
  );
}
