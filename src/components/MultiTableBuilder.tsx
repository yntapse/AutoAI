"use client";

/**
 * MultiTableBuilder - Sprint 1 multi-table dataset builder + Join Advisor.
 *
 * Rendered INSIDE the existing New Project page, in the Database branch of
 * Step 2. It is not a page and owns no routing.
 *
 * Flow:
 *   select up to 3 tables -> pick the base table -> load columns
 *   -> define relationships by hand -> analyse each one against the real data
 *   -> approve a join type -> preview -> hand the result upward
 *
 * The parent keeps ownership of the connection fields and of everything
 * Step 3 needs. This component reports upward through onDatasetReady()
 * (a valid preview arrived) and onReset() (the selection changed, so any
 * previous preview is stale).
 *
 * Sprint 1 scope: manual joins only. No relationship detection, no saved
 * models, no scheduling. The advisor RECOMMENDS; it never applies a join
 * type on the user's behalf.
 */

import { useCallback, useEffect, useState } from "react";
import {
  DataModelColumnMeta,
  DataModelPreviewResponse,
  DataModelRelationship,
  DataModelSpec,
  RelationshipProfileResponse,
  analyzeRelationship,
  fetchDataModelColumns,
  previewDataModel,
} from "@/services/projectService";

const MAX_TABLES = 3;

export interface MultiTableBuilderProps {
  dbType: string;
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
  availableTables: string[];
  /** A valid preview arrived: the parent stores the spec and fills Step 3. */
  onDatasetReady: (
    spec: DataModelSpec,
    preview: DataModelPreviewResponse,
  ) => void;
  /** The selection changed: any previous preview is now stale. */
  onReset: () => void;
}

/* ─── shared class strings, matching the rest of the page ──────────────── */
const INPUT_CLS =
  "w-full rounded-xl border border-white/[0.08] bg-white/[0.04] px-3 py-2 text-[13px] text-slate-100 outline-none transition-all focus:border-cyan-400/40 focus:bg-white/[0.06] focus:ring-2 focus:ring-cyan-400/20";
const LABEL_CLS = "mb-2 block text-[13px] font-semibold text-slate-300";
const PANEL_CLS = "rounded-xl border border-white/[0.08] bg-white/[0.02] p-4";

export default function MultiTableBuilder({
  dbType,
  host,
  port,
  database,
  username,
  password,
  availableTables,
  onDatasetReady,
  onReset,
}: MultiTableBuilderProps) {
  const [selectedTables, setSelectedTables] = useState<string[]>([]);
  const [baseTable, setBaseTable] = useState("");
  const [columnsByTable, setColumnsByTable] = useState<
    Record<string, DataModelColumnMeta[]>
  >({});
  const [selectedColumns, setSelectedColumns] = useState<
    Record<string, string[]>
  >({});
  const [relationships, setRelationships] = useState<DataModelRelationship[]>(
    [],
  );
  const [preview, setPreview] = useState<DataModelPreviewResponse | null>(null);
  const [loading, setLoading] = useState<"idle" | "columns" | "preview">(
    "idle",
  );
  const [error, setError] = useState<string | null>(null);
  const [showSql, setShowSql] = useState(false);

  const connection = { dbType, host, port, database, username, password };

  /* ─── Join Advisor state ──────────────────────────────────────────────
   * Analyses are keyed by the relationship's COLUMN PAIR, not by its array
   * index. Two consequences, both wanted:
   *   - changing a table or a column changes the key, so the old evidence
   *     stops being found and the panel disappears (it is now stale);
   *   - changing only join_type leaves the key alone, so the evidence stays.
   * Removing a relationship cannot mis-attach evidence to its neighbour the
   * way an index-keyed map would.
   */
  const [analyses, setAnalyses] = useState<
    Record<string, RelationshipProfileResponse>
  >({});
  const [analyzingKey, setAnalyzingKey] = useState<string | null>(null);
  const [analysisErrors, setAnalysisErrors] = useState<Record<string, string>>(
    {},
  );

  /** Identity of a relationship's evidence. join_type is deliberately absent. */
  const relationshipKey = (rel: DataModelRelationship) =>
    `${rel.left_table}.${rel.left_column}=${rel.right_table}.${rel.right_column}`;

  /** Unordered table pair, so A-B and B-A count as the same relationship. */
  const pairKey = (a: string, b: string) => [a, b].sort().join("::");

  const usedPairs = new Set(
    relationships.map((r) => pairKey(r.left_table, r.right_table)),
  );

  /** The first table pair that has no relationship yet, or null if none. */
  const firstFreePair = (): [string, string] | null => {
    for (let i = 0; i < selectedTables.length; i += 1) {
      for (let j = i + 1; j < selectedTables.length; j += 1) {
        if (!usedPairs.has(pairKey(selectedTables[i], selectedTables[j]))) {
          return [selectedTables[i], selectedTables[j]];
        }
      }
    }
    return null;
  };

  /* Which side of this edge is the BASE side?
   *
   * The advisor phrases everything in terms of "base records", so the side it
   * calls `base` must be the side the join accumulates onto - the same side
   * sql_builder._plan_join_order puts on the left. Sending baseTable with the
   * other side's column would reference a column that does not exist there.
   */
  const resolveAdvisorSides = (rel: DataModelRelationship) => {
    const leftIsBase =
      rel.left_table === baseTable ||
      (rel.right_table !== baseTable &&
        relationships.some(
          (other) =>
            other !== rel &&
            (other.left_table === baseTable ||
              other.right_table === baseTable) &&
            (other.left_table === rel.left_table ||
              other.right_table === rel.left_table),
        ));

    return leftIsBase
      ? {
          baseTable: rel.left_table,
          baseColumn: rel.left_column,
          relatedTable: rel.right_table,
          relatedColumn: rel.right_column,
        }
      : {
          baseTable: rel.right_table,
          baseColumn: rel.right_column,
          relatedTable: rel.left_table,
          relatedColumn: rel.left_column,
        };
  };

  /* ─── any structural change invalidates the preview ───────────────────── */
  const invalidatePreview = useCallback(() => {
    setPreview(null);
    setShowSql(false);
    onReset();
  }, [onReset]);

  /* ─── table selection ─────────────────────────────────────────────────── */
  const toggleTable = (table: string) => {
    setError(null);
    invalidatePreview();

    setSelectedTables((current) => {
      const isSelected = current.includes(table);

      if (!isSelected && current.length >= MAX_TABLES) {
        setError(
          `You can combine at most ${MAX_TABLES} tables in a single dataset.`,
        );
        return current;
      }

      const next = isSelected
        ? current.filter((t) => t !== table)
        : [...current, table];

      if (isSelected) {
        // Drop every relationship that referenced the removed table.
        setRelationships((rels) =>
          rels.filter((r) => r.left_table !== table && r.right_table !== table),
        );
        setSelectedColumns((cols) => {
          const copy = { ...cols };
          delete copy[table];
          return copy;
        });
      }

      // Keep the base table valid: first selected table by default.
      setBaseTable((currentBase) => {
        if (next.length === 0) return "";
        if (!currentBase || !next.includes(currentBase)) return next[0];
        return currentBase;
      });

      return next;
    });
  };

  /* ─── load column metadata whenever the table set changes ─────────────── */
  const tableKey = selectedTables.join("|");

  useEffect(() => {
    if (selectedTables.length === 0) {
      setColumnsByTable({});
      return;
    }

    let cancelled = false;
    setLoading("columns");
    setError(null);

    fetchDataModelColumns({ ...connection, tables: selectedTables })
      .then((data) => {
        if (cancelled) return;

        const next: Record<string, DataModelColumnMeta[]> = {};
        for (const entry of data.tables) {
          next[entry.table] = entry.columns;
        }
        setColumnsByTable(next);

        // Default to every column of every table.
        setSelectedColumns((current) => {
          const merged: Record<string, string[]> = {};
          for (const table of selectedTables) {
            merged[table] =
              current[table] ?? (next[table] ?? []).map((c) => c.name);
          }
          return merged;
        });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setColumnsByTable({});
        setError(
          err instanceof Error ? err.message : "Could not load columns.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading("idle");
      });

    return () => {
      cancelled = true;
    };
    // connection fields are stable for the lifetime of this panel
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tableKey]);

  /* ─── column selection ────────────────────────────────────────────────── */
  const toggleColumn = (table: string, column: string) => {
    invalidatePreview();
    setSelectedColumns((current) => {
      const existing = current[table] ?? [];
      const next = existing.includes(column)
        ? existing.filter((c) => c !== column)
        : [...existing, column];
      return { ...current, [table]: next };
    });
  };

  const setAllColumns = (table: string, all: boolean) => {
    invalidatePreview();
    setSelectedColumns((current) => ({
      ...current,
      [table]: all ? (columnsByTable[table] ?? []).map((c) => c.name) : [],
    }));
  };

  /* ─── relationships ───────────────────────────────────────────────────── */
  const addRelationship = () => {
    if (selectedTables.length < 2) return;

    // Never more relationships than it takes to connect every table, and
    // never a second relationship between a pair that already has one.
    if (relationships.length >= requiredRelationships) {
      setError(
        `${selectedTables.length} tables need only ` +
          `${requiredRelationships} relationship` +
          `${requiredRelationships === 1 ? "" : "s"}.`,
      );
      return;
    }

    const pair = firstFreePair();
    if (!pair) {
      setError("Every pair of selected tables already has a relationship.");
      return;
    }

    const [left, right] = pair;
    setError(null);
    invalidatePreview();

    setRelationships((current) => [
      ...current,
      {
        left_table: left,
        left_column: columnsByTable[left]?.[0]?.name ?? "",
        right_table: right,
        right_column: columnsByTable[right]?.[0]?.name ?? "",
        join_type: "inner",
      },
    ]);
  };

  const updateRelationship = (
    index: number,
    patch: Partial<DataModelRelationship>,
  ) => {
    const current = relationships[index];
    if (!current) return;

    const candidate = { ...current, ...patch };

    // A table change can produce a self-join or a duplicate of another row.
    // Reject it rather than letting the backend fail later.
    if (patch.left_table || patch.right_table) {
      if (candidate.left_table === candidate.right_table) {
        setError(
          `'${candidate.left_table}' cannot be joined to itself. ` +
            `Pick a different table.`,
        );
        return;
      }

      const newPair = pairKey(candidate.left_table, candidate.right_table);
      const clashes = relationships.some(
        (rel, i) =>
          i !== index && pairKey(rel.left_table, rel.right_table) === newPair,
      );
      if (clashes) {
        setError(
          `${candidate.left_table} and ${candidate.right_table} already have ` +
            `a relationship. Edit that one instead.`,
        );
        return;
      }
    }

    setError(null);

    // Every change - including join_type - alters the compiled SQL, so the
    // dataset preview is always invalidated. The Join Advisor analysis is a
    // separate concern: relationshipKey() ignores join_type, so the evidence
    // survives an INNER/LEFT switch and disappears on a structural change.
    invalidatePreview();

    setRelationships((list) =>
      list.map((rel, i) => {
        if (i !== index) return rel;
        const next = { ...rel, ...patch };

        // Changing a side's table invalidates that side's column.
        if (patch.left_table && patch.left_table !== rel.left_table) {
          next.left_column = columnsByTable[patch.left_table]?.[0]?.name ?? "";
        }
        if (patch.right_table && patch.right_table !== rel.right_table) {
          next.right_column =
            columnsByTable[patch.right_table]?.[0]?.name ?? "";
        }
        return next;
      }),
    );
  };

  const removeRelationship = (index: number) => {
    invalidatePreview();
    setRelationships((current) => current.filter((_, i) => i !== index));
  };

  /* ─── Join Advisor ────────────────────────────────────────────────────── */
  const runAnalysis = async (rel: DataModelRelationship) => {
    const key = relationshipKey(rel);
    if (!rel.left_column || !rel.right_column) {
      setAnalysisErrors((prev) => ({
        ...prev,
        [key]: "Choose a column on both sides first.",
      }));
      return;
    }

    setAnalyzingKey(key);
    setAnalysisErrors((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });

    try {
      const result = await analyzeRelationship({
        ...connection,
        ...resolveAdvisorSides(rel),
      });
      setAnalyses((prev) => ({ ...prev, [key]: result }));
    } catch (err: unknown) {
      setAnalysisErrors((prev) => ({
        ...prev,
        [key]:
          err instanceof Error
            ? err.message
            : "Could not analyse this relationship.",
      }));
    } finally {
      setAnalyzingKey(null);
    }
  };

  /* ─── preview ─────────────────────────────────────────────────────────── */
  const buildSpec = (): DataModelSpec => ({
    tables: selectedTables,
    baseTable,
    relationships,
    selectedColumns,
  });

  const runPreview = async () => {
    setLoading("preview");
    setError(null);
    setPreview(null);
    onReset();

    const spec = buildSpec();

    try {
      const data = await previewDataModel({ ...connection, ...spec });
      setPreview(data);

      // Only a usable dataset is handed upward. An oversized join previews
      // fine on 20 rows but would fail at extract, so it is not "ready".
      if (!data.exceeds_limit) {
        onDatasetReady(spec, data);
      }
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Could not preview the dataset.",
      );
    } finally {
      setLoading("idle");
    }
  };

  /* ─── derived ─────────────────────────────────────────────────────────── */
  const requiredRelationships = Math.max(0, selectedTables.length - 1);
  const canPreview =
    selectedTables.length > 0 &&
    baseTable !== "" &&
    relationships.length >= requiredRelationships &&
    relationships.every((r) => r.left_column !== "" && r.right_column !== "") &&
    selectedTables.every((t) => (selectedColumns[t] ?? []).length > 0) &&
    loading === "idle";

  /* ─── render ──────────────────────────────────────────────────────────── */
  return (
    <div className="space-y-4">
      {/* ── 1. Tables ──────────────────────────────────────────────────── */}
      <div className={PANEL_CLS}>
        <label className={LABEL_CLS}>
          Select tables{" "}
          <span className="font-normal text-slate-500">
            (up to {MAX_TABLES})
          </span>
        </label>

        {availableTables.length === 0 ? (
          <p className="text-[13px] text-slate-500">
            No tables found in this schema.
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {availableTables.map((table) => {
              const isSelected = selectedTables.includes(table);
              const isFull =
                !isSelected && selectedTables.length >= MAX_TABLES;
              return (
                <button
                  key={table}
                  type="button"
                  disabled={isFull}
                  onClick={() => toggleTable(table)}
                  className={`rounded-xl border px-3 py-2 text-[13px] font-medium transition-all ${
                    isSelected
                      ? "border-cyan-400/50 bg-cyan-500/15 text-cyan-300"
                      : isFull
                        ? "cursor-not-allowed border-white/[0.06] bg-white/[0.02] text-slate-600"
                        : "border-white/[0.08] bg-white/[0.04] text-slate-300 hover:border-white/[0.15] hover:bg-white/[0.08]"
                  }`}
                >
                  {table}
                </button>
              );
            })}
          </div>
        )}

        {selectedTables.length > 0 && (
          <div className="mt-4">
            <label className={LABEL_CLS}>
              Base table{" "}
              <span className="font-normal text-slate-500">
                (its rows become the dataset rows)
              </span>
            </label>
            <div className="flex flex-wrap gap-2">
              {selectedTables.map((table) => (
                <button
                  key={table}
                  type="button"
                  onClick={() => {
                    invalidatePreview();
                    setBaseTable(table);
                  }}
                  className={`rounded-lg border px-3 py-1.5 text-[12px] transition-all ${
                    baseTable === table
                      ? "border-violet-400/50 bg-violet-500/15 text-violet-300"
                      : "border-white/[0.08] bg-white/[0.04] text-slate-400 hover:bg-white/[0.08]"
                  }`}
                >
                  {table}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── 2. Columns ─────────────────────────────────────────────────── */}
      {selectedTables.length > 0 && loading !== "columns" && (
        <div className={PANEL_CLS}>
          <label className={LABEL_CLS}>Columns to include</label>
          <div className="space-y-3">
            {selectedTables.map((table) => {
              const cols = columnsByTable[table] ?? [];
              const chosen = selectedColumns[table] ?? [];
              return (
                <div key={table}>
                  <div className="mb-1.5 flex items-center gap-3">
                    <span className="text-[12px] font-semibold text-slate-300">
                      {table}
                    </span>
                    <span className="text-[11px] text-slate-500">
                      {chosen.length}/{cols.length}
                    </span>
                    <button
                      type="button"
                      onClick={() => setAllColumns(table, true)}
                      className="text-[11px] text-cyan-400 hover:text-cyan-300"
                    >
                      all
                    </button>
                    <button
                      type="button"
                      onClick={() => setAllColumns(table, false)}
                      className="text-[11px] text-slate-500 hover:text-slate-300"
                    >
                      none
                    </button>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {cols.map((col) => {
                      const isOn = chosen.includes(col.name);
                      return (
                        <button
                          key={col.name}
                          type="button"
                          onClick={() => toggleColumn(table, col.name)}
                          title={col.type}
                          className={`rounded-md border px-2 py-1 text-[11px] transition-all ${
                            isOn
                              ? "border-cyan-400/40 bg-cyan-500/10 text-cyan-300"
                              : "border-white/[0.06] bg-white/[0.02] text-slate-500 hover:text-slate-300"
                          }`}
                        >
                          {col.name}
                          {col.primary_key && (
                            <span className="ml-1 text-[9px] text-violet-400">
                              PK
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {loading === "columns" && (
        <p className="text-[13px] text-slate-500">Loading columns…</p>
      )}

      {/* ── 3. Relationships ───────────────────────────────────────────── */}
      {selectedTables.length >= 2 && (
        <div className={PANEL_CLS}>
          <div className="mb-3 flex items-center justify-between">
            <label className="text-[13px] font-semibold text-slate-300">
              Relationships
            </label>
            <span className="text-[11px] text-slate-500">
              {relationships.length}/{requiredRelationships} required
            </span>
          </div>

          <div className="space-y-2">
            {relationships.map((rel, index) => (
              <div
                key={index}
                className="space-y-3 rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    value={rel.left_table}
                    onChange={(e) =>
                      updateRelationship(index, { left_table: e.target.value })
                    }
                    className={`${INPUT_CLS} w-auto min-w-[120px]`}
                  >
                    {selectedTables.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>

                  <select
                    value={rel.left_column}
                    onChange={(e) =>
                      updateRelationship(index, { left_column: e.target.value })
                    }
                    className={`${INPUT_CLS} w-auto min-w-[130px]`}
                  >
                    {/* Without this, an empty value makes the browser display
                        the first option while the state is still "" - the row
                        looks complete but the analyse button stays disabled. */}
                    {!rel.left_column && (
                      <option value="">Select column…</option>
                    )}
                    {(columnsByTable[rel.left_table] ?? []).map((c) => (
                      <option key={c.name} value={c.name}>
                        {c.name}
                      </option>
                    ))}
                  </select>

                  <span className="text-[13px] text-slate-500">=</span>

                  <select
                    value={rel.right_table}
                    onChange={(e) =>
                      updateRelationship(index, { right_table: e.target.value })
                    }
                    className={`${INPUT_CLS} w-auto min-w-[120px]`}
                  >
                    {selectedTables.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>

                  <select
                    value={rel.right_column}
                    onChange={(e) =>
                      updateRelationship(index, {
                        right_column: e.target.value,
                      })
                    }
                    className={`${INPUT_CLS} w-auto min-w-[130px]`}
                  >
                    {!rel.right_column && (
                      <option value="">Select column…</option>
                    )}
                    {(columnsByTable[rel.right_table] ?? []).map((c) => (
                      <option key={c.name} value={c.name}>
                        {c.name}
                      </option>
                    ))}
                  </select>

                  <button
                    type="button"
                    onClick={() => removeRelationship(index)}
                    className="ml-auto rounded-lg border border-rose-500/25 px-2 py-1 text-[11px] text-rose-300 hover:bg-rose-500/10"
                  >
                    Remove
                  </button>
                </div>

                {/* ── Join Advisor ─────────────────────────────────────── */}
                {(() => {
                  const key = relationshipKey(rel);
                  const analysis = analyses[key];
                  const analysisError = analysisErrors[key];
                  const busy = analyzingKey === key;
                  const p = analysis?.profile;
                  const rec = analysis?.recommendation;

                  const tone =
                    rec?.severity === "danger"
                      ? "border-rose-500/30 bg-rose-500/10 text-rose-300"
                      : rec?.severity === "warning"
                        ? "border-amber-400/25 bg-amber-500/10 text-amber-200/90"
                        : "border-cyan-400/30 bg-cyan-500/10 text-cyan-300";

                  const recLabel =
                    rec?.recommended === "inner"
                      ? "Recommended: INNER JOIN"
                      : rec?.recommended === "left"
                        ? "Recommended: LEFT JOIN"
                        : "No suitable join recommended";

                  // The cards describe THIS relationship's base side, which is
                  // not always the dataset's base table: in a 3-table chain the
                  // second edge accumulates onto the middle table. Prefer what
                  // the backend actually profiled; fall back to the same rule
                  // the analyse call uses, so the wording matches before and
                  // after an analysis.
                  const rowBaseTable =
                    analysis?.base_table ?? resolveAdvisorSides(rel).baseTable;

                  const cards = [
                    {
                      type: "inner" as const,
                      title: "INNER JOIN",
                      blurb: `Keep only ${rowBaseTable || "base-table"} records that have a matching record.`,
                      rows: p?.inner_result_rows,
                    },
                    {
                      type: "left" as const,
                      title: "LEFT JOIN",
                      blurb: `Keep every ${rowBaseTable || "base-table"} record, even without a match.`,
                      rows: p?.left_result_rows,
                    },
                  ];

                  return (
                    <div className="space-y-3">
                      <button
                        type="button"
                        disabled={busy || !rel.left_column || !rel.right_column}
                        onClick={() => runAnalysis(rel)}
                        className="flex items-center gap-2 rounded-lg border border-violet-400/30 bg-violet-500/10 px-3 py-1.5 text-[12px] font-medium text-violet-300 transition-all hover:bg-violet-500/15 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {busy ? (
                          <>
                            <svg
                              className="h-3.5 w-3.5 animate-spin"
                              fill="none"
                              viewBox="0 0 24 24"
                            >
                              <circle
                                className="opacity-25"
                                cx="12"
                                cy="12"
                                r="10"
                                stroke="currentColor"
                                strokeWidth="4"
                              />
                              <path
                                className="opacity-75"
                                fill="currentColor"
                                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                              />
                            </svg>
                            Analyzing…
                          </>
                        ) : analysis ? (
                          "Re-analyze relationship"
                        ) : (
                          "Analyze relationship"
                        )}
                      </button>

                      {analysisError && (
                        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2">
                          <p className="text-[12px] text-rose-300">
                            {analysisError}
                          </p>
                        </div>
                      )}

                      {rec && p && analysis && (
                        <div className={`rounded-lg border px-3 py-2.5 ${tone}`}>
                          <p className="text-[12.5px] font-semibold">
                            {recLabel}
                          </p>
                          <p className="mt-0.5 text-[12px] opacity-90">
                            {rec.headline}
                          </p>

                          <ul className="mt-2 space-y-1">
                            {rec.reasons.map((reason, i) => (
                              <li key={i} className="text-[12px] opacity-90">
                                · {reason}
                              </li>
                            ))}
                          </ul>

                          {rec.recommended === null && (
                            <p className="mt-2 text-[12px] font-medium">
                              No join type is suggested. You can still choose
                              one below if you want to continue anyway.
                            </p>
                          )}

                          <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1 border-t border-white/[0.08] pt-2 text-[11px] text-slate-400">
                            <span>
                              {analysis.base_table}:{" "}
                              {p.base_rows.toLocaleString()} rows
                            </span>
                            <span>
                              {analysis.related_table}:{" "}
                              {p.related_rows.toLocaleString()} rows
                            </span>
                            <span>
                              matched: {p.base_matched_rows.toLocaleString()}
                            </span>
                            <span>
                              unmatched: {p.base_unmatched_rows.toLocaleString()}
                            </span>
                            <span>match: {p.match_percentage}%</span>
                            <span>{p.cardinality.replace(/_/g, "-")}</span>
                            <span>
                              INNER → {p.inner_result_rows.toLocaleString()}
                            </span>
                            <span>
                              LEFT → {p.left_result_rows.toLocaleString()}
                            </span>
                          </div>

                          {p.row_multiplication && (
                            <p className="mt-2 text-[12px] font-medium text-amber-200/90">
                              Row multiplication: one {analysis.base_table}{" "}
                              record matches up to{" "}
                              {p.max_rows_per_key.toLocaleString()}{" "}
                              {analysis.related_table} records, so records will
                              repeat.
                            </p>
                          )}
                        </div>
                      )}

                      {/* ── Join type / record handling ─────────────────── */}
                      <div>
                        <p className="mb-1.5 text-[12px] font-semibold text-slate-300">
                          Join type / record handling
                        </p>
                        <div className="grid gap-2 sm:grid-cols-2">
                          {cards.map((card) => {
                            const active = rel.join_type === card.type;
                            const advised = rec?.recommended === card.type;
                            return (
                              <button
                                key={card.type}
                                type="button"
                                onClick={() =>
                                  updateRelationship(index, {
                                    join_type: card.type,
                                  })
                                }
                                className={`rounded-lg border px-3 py-2 text-left transition-all ${
                                  active
                                    ? "border-cyan-400/50 bg-cyan-500/15"
                                    : "border-white/[0.08] bg-white/[0.03] hover:bg-white/[0.06]"
                                }`}
                              >
                                <span className="flex items-center gap-2">
                                  <span
                                    className={`text-[12px] font-semibold ${
                                      active
                                        ? "text-cyan-300"
                                        : "text-slate-300"
                                    }`}
                                  >
                                    {card.title}
                                  </span>
                                  {advised && (
                                    <span className="rounded px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-violet-300 ring-1 ring-violet-400/40">
                                      advised
                                    </span>
                                  )}
                                  {active && (
                                    <span className="ml-auto text-[10px] text-cyan-400">
                                      in use
                                    </span>
                                  )}
                                </span>
                                <span className="mt-1 block text-[11px] leading-snug text-slate-400">
                                  {card.blurb}
                                </span>
                                {card.rows !== undefined && (
                                  <span className="mt-1 block text-[11px] text-slate-300">
                                    → about {card.rows.toLocaleString()} rows
                                  </span>
                                )}
                              </button>
                            );
                          })}
                        </div>
                        <div className="mt-2 flex gap-2">
                          <button
                            type="button"
                            onClick={() =>
                              updateRelationship(index, { join_type: "inner" })
                            }
                            className="rounded-lg border border-white/[0.08] bg-white/[0.04] px-3 py-1.5 text-[11px] font-medium text-slate-300 hover:bg-white/[0.08]"
                          >
                            Use INNER JOIN
                          </button>
                          <button
                            type="button"
                            onClick={() =>
                              updateRelationship(index, { join_type: "left" })
                            }
                            className="rounded-lg border border-white/[0.08] bg-white/[0.04] px-3 py-1.5 text-[11px] font-medium text-slate-300 hover:bg-white/[0.08]"
                          >
                            Use LEFT JOIN
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })()}
              </div>
            ))}
          </div>

          <button
            type="button"
            disabled={
              loading === "columns" ||
              relationships.length >= requiredRelationships ||
              firstFreePair() === null
            }
            onClick={addRelationship}
            className="mt-3 rounded-xl border border-white/[0.08] bg-white/[0.04] px-3 py-2 text-[12px] font-medium text-slate-300 transition-all hover:bg-white/[0.08] disabled:cursor-not-allowed disabled:opacity-40"
          >
            + Add relationship
          </button>

          {relationships.length < requiredRelationships && (
            <p className="mt-2 text-[12px] text-amber-300/80">
              {selectedTables.length} tables need {requiredRelationships}{" "}
              relationship
              {requiredRelationships === 1 ? "" : "s"} so every table is
              connected to {baseTable || "the base table"}.
            </p>
          )}
        </div>
      )}

      {/* ── 4. Preview action ──────────────────────────────────────────── */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          disabled={!canPreview}
          onClick={runPreview}
          className="flex items-center gap-2 rounded-xl border border-cyan-400/30 bg-cyan-500/10 px-4 py-2.5 text-[13px] font-semibold text-cyan-300 transition-all hover:border-cyan-400/50 hover:bg-cyan-500/15 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading === "preview" ? (
            <>
              <svg
                className="h-4 w-4 animate-spin"
                fill="none"
                viewBox="0 0 24 24"
              >
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                />
              </svg>
              Building…
            </>
          ) : (
            "Preview joined dataset"
          )}
        </button>

        {preview && !preview.exceeds_limit && (
          <span className="text-[12px] text-emerald-300">
            Dataset ready — continue to target selection below.
          </span>
        )}
      </div>

      {/* ── 5. Errors and warnings ─────────────────────────────────────── */}
      {error && (
        <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3">
          <p className="text-[13px] text-rose-300">{error}</p>
        </div>
      )}

      {preview?.exceeds_limit && (
        <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3">
          <p className="text-[13px] text-rose-300">
            This join produces about{" "}
            {preview.estimated_row_count?.toLocaleString() ?? "too many"} rows,
            over the {preview.row_limit.toLocaleString()}-row limit. Narrow the
            selection before creating the dataset.
          </p>
        </div>
      )}

      {preview?.warnings && preview.warnings.length > 0 && (
        <div className="rounded-xl border border-amber-400/25 bg-amber-500/10 px-4 py-3">
          {preview.warnings.map((w, i) => (
            <p key={i} className="text-[12px] text-amber-200/90">
              {w}
            </p>
          ))}
        </div>
      )}

      {/* ── 6. Preview grid ────────────────────────────────────────────── */}
      {preview && preview.column_names.length > 0 && (
        <div>
          <label className={LABEL_CLS}>
            Preview: {selectedTables.join(" + ")}
          </label>
          <div className="overflow-x-auto rounded-xl border border-white/[0.08]">
            <table className="w-full text-left text-[12px]">
              <thead className="bg-white/[0.04]">
                <tr>
                  {preview.column_names.map((col) => (
                    <th
                      key={col}
                      title={preview.alias_map[col] ?? col}
                      className="whitespace-nowrap px-3 py-2 font-semibold text-slate-300"
                    >
                      {col}
                      {preview.numeric_columns.includes(col) && (
                        <span className="ml-1 text-[10px] font-normal text-cyan-400">
                          #
                        </span>
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.sample_rows.map((row, idx) => (
                  <tr key={idx} className="border-t border-white/[0.06]">
                    {preview.column_names.map((col) => (
                      <td
                        key={col}
                        className="whitespace-nowrap px-3 py-2 text-slate-400"
                      >
                        {String(row[col] ?? "")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="border-t border-white/[0.06] px-3 py-2 text-[11px] text-slate-500">
              Showing {preview.preview_rows} row
              {preview.preview_rows === 1 ? "" : "s"} of{" "}
              {preview.estimated_row_count !== null
                ? `about ${preview.estimated_row_count.toLocaleString()}`
                : "an unknown number"}{" "}
              · {preview.columns} columns
            </div>
          </div>
        </div>
      )}

      {/* ── 7. Generated SQL ───────────────────────────────────────────── */}
      {preview?.generated_sql && (
        <div>
          <button
            type="button"
            onClick={() => setShowSql((s) => !s)}
            className="text-[12px] font-medium text-cyan-400 transition-colors hover:text-cyan-300"
          >
            {showSql ? "Hide" : "Show"} generated SQL
          </button>
          {showSql && (
            <pre className="mt-2 overflow-x-auto rounded-xl border border-white/[0.08] bg-black/30 p-3 text-[11px] leading-relaxed text-slate-300">
              {preview.generated_sql}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
