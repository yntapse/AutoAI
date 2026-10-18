"use client";

/**
 * Version History tab.
 *
 * Reads the EXISTING /pipeline/version endpoints - versions, compare and
 * restore all already exist in routers/pipelines.py, so nothing new was added
 * on the backend for this screen.
 */

import { useEffect, useMemo, useState } from "react";
import {
  EmptyState,
  ErrorNote,
  Panel,
  formatDateTime,
} from "./opsPrimitives";
import {
  PipelineVersionRow,
  VersionComparison,
  compareVersions,
  listVersions,
} from "@/services/pipelineOpsService";

function summarise(pipelineJson: Record<string, unknown>): string[] {
  const bits: string[] = [];
  const tables = pipelineJson?.tables;
  if (Array.isArray(tables)) bits.push(`${tables.length} tables`);
  for (const [key, label] of [
    ["joins", "joins"],
    ["cleaning_steps", "cleaning steps"],
    ["quality_rules", "quality rules"],
    ["derived_columns", "derived columns"],
  ] as const) {
    const value = pipelineJson?.[key];
    if (Array.isArray(value) && value.length) bits.push(`${value.length} ${label}`);
  }
  return bits;
}

export default function VersionHistoryTab({
  pipelineId,
}: {
  pipelineId: string;
}) {
  const [versions, setVersions] = useState<PipelineVersionRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [fromVersion, setFromVersion] = useState<number | null>(null);
  const [toVersion, setToVersion] = useState<number | null>(null);
  const [comparison, setComparison] = useState<VersionComparison | null>(null);
  const [comparing, setComparing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listVersions(pipelineId)
      .then((rows) => {
        if (cancelled) return;
        const sorted = [...rows].sort(
          (a, b) => b.version_number - a.version_number
        );
        setVersions(sorted);
        if (sorted.length >= 2) {
          setFromVersion(sorted[1].version_number);
          setToVersion(sorted[0].version_number);
        }
      })
      .catch((err: unknown) =>
        !cancelled &&
        setError(err instanceof Error ? err.message : "Failed to load versions.")
      )
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [pipelineId]);

  const numbers = useMemo(
    () => versions.map((version) => version.version_number),
    [versions]
  );

  async function runCompare() {
    if (fromVersion === null || toVersion === null) return;
    setComparing(true);
    setError(null);
    try {
      setComparison(await compareVersions(pipelineId, fromVersion, toVersion));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to compare.");
    } finally {
      setComparing(false);
    }
  }

  if (loading)
    return <p className="text-[12.5px] text-slate-500">Loading versions&hellip;</p>;

  return (
    <div className="space-y-4">
      {error && <ErrorNote>{error}</ErrorNote>}

      <Panel
        title="Versions"
        subtitle="Every saved version is immutable. A run always executes one of these."
      >
        {versions.length === 0 ? (
          <EmptyState>No version has been saved for this pipeline yet.</EmptyState>
        ) : (
          <ul className="space-y-2">
            {versions.map((version, index) => (
              <li
                key={version.id}
                className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2.5"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="text-[13px] font-medium text-slate-100">
                    Version {version.version_number}
                    {index === 0 && (
                      <span className="ml-2 rounded-full border border-slate-700 px-2 py-0.5 text-[10.5px] uppercase tracking-wide text-slate-400">
                        latest
                      </span>
                    )}
                  </p>
                  <p className="text-[11px] text-slate-500">
                    {formatDateTime(version.created_at)}
                  </p>
                </div>
                <p className="mt-1 text-[11.5px] text-slate-500">
                  {summarise(version.pipeline_json).join(" · ") ||
                    "No structural contents recorded."}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {versions.length >= 2 && (
        <Panel
          title="Compare versions"
          subtitle="What changed between two saved versions."
        >
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-[12px] text-slate-400">
              From
              <select
                value={fromVersion ?? ""}
                onChange={(event) => setFromVersion(Number(event.target.value))}
                className="mt-1 block rounded-lg border border-slate-800 bg-slate-950 px-3 py-1.5 text-[13px] text-slate-200"
              >
                {numbers.map((number) => (
                  <option key={number} value={number}>
                    v{number}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-[12px] text-slate-400">
              To
              <select
                value={toVersion ?? ""}
                onChange={(event) => setToVersion(Number(event.target.value))}
                className="mt-1 block rounded-lg border border-slate-800 bg-slate-950 px-3 py-1.5 text-[13px] text-slate-200"
              >
                {numbers.map((number) => (
                  <option key={number} value={number}>
                    v{number}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={runCompare}
              disabled={comparing || fromVersion === toVersion}
              className="rounded-lg bg-cyan-600 px-3 py-1.5 text-[13px] font-medium text-white disabled:bg-slate-800 disabled:text-slate-500"
            >
              {comparing ? "Comparing…" : "Compare"}
            </button>
          </div>

          {comparison && (
            <div className="mt-4">
              {Object.keys(comparison.changes).length === 0 ? (
                <EmptyState>
                  v{comparison.from_version} and v{comparison.to_version} are
                  identical.
                </EmptyState>
              ) : (
                <ul className="space-y-2">
                  {Object.entries(comparison.changes).map(([key, change]) => (
                    <li
                      key={key}
                      className="rounded-lg border border-slate-800 bg-slate-950 p-3"
                    >
                      <p className="text-[12.5px] font-medium text-slate-100">
                        {key.replace(/_/g, " ")}
                      </p>
                      <div className="mt-2 grid gap-2 sm:grid-cols-2">
                        <div>
                          <p className="text-[10.5px] uppercase tracking-wide text-slate-600">
                            v{comparison.from_version}
                          </p>
                          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words text-[11px] text-slate-400">
                            {JSON.stringify(change.before, null, 2)}
                          </pre>
                        </div>
                        <div>
                          <p className="text-[10.5px] uppercase tracking-wide text-slate-600">
                            v{comparison.to_version}
                          </p>
                          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words text-[11px] text-slate-300">
                            {JSON.stringify(change.after, null, 2)}
                          </pre>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </Panel>
      )}
    </div>
  );
}
