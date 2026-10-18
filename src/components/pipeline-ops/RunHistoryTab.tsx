"use client";

/** Run History tab: every run of this pipeline, newest first, plus the trend. */

import { useEffect, useState } from "react";
import RunTrendChart from "./RunTrendChart";
import {
  EmptyState,
  ErrorNote,
  Panel,
  RateMeter,
  StatTile,
  StatusBadge,
  formatCount,
  formatDateTime,
  formatDuration,
} from "./opsPrimitives";
import {
  PipelineRun,
  RunMetrics,
  TrendPoint,
  getPipelineMetrics,
  listRuns,
} from "@/services/pipelineOpsService";

export default function RunHistoryTab({ pipelineId }: { pipelineId: string }) {
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [metrics, setMetrics] = useState<RunMetrics | null>(null);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([listRuns(pipelineId), getPipelineMetrics(pipelineId)])
      .then(([runRows, metricsBody]) => {
        if (cancelled) return;
        setRuns(runRows);
        setMetrics(metricsBody.metrics);
        setTrend(metricsBody.trend);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load runs.");
        }
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [pipelineId]);

  if (loading) return <p className="text-[12.5px] text-slate-500">Loading runs&hellip;</p>;
  if (error) return <ErrorNote>{error}</ErrorNote>;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Total runs" value={formatCount(metrics?.total_runs)} />
        <StatTile
          label="Succeeded"
          value={formatCount(metrics?.succeeded)}
          tone="good"
        />
        <StatTile
          label="Failed"
          value={formatCount(metrics?.failed)}
          tone={metrics?.failed ? "critical" : "neutral"}
        />
        <StatTile
          label="Rows processed"
          value={formatCount(metrics?.total_rows_processed)}
          hint="Across every run"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_260px]">
        <Panel
          title="Duration per run"
          subtitle="One bar per run, coloured by outcome."
        >
          <RunTrendChart points={trend} />
        </Panel>

        <Panel title="Health" subtitle="Finished runs only.">
          <RateMeter rate={metrics?.success_rate ?? null} />
          <dl className="mt-4 space-y-2 text-[12px]">
            {[
              ["Average", formatDuration(metrics?.avg_duration_seconds)],
              ["p95", formatDuration(metrics?.p95_duration_seconds)],
              ["Slowest", formatDuration(metrics?.max_duration_seconds)],
              ["Last run", formatDateTime(metrics?.last_run_at)],
            ].map(([label, value]) => (
              <div key={label} className="flex justify-between gap-2">
                <dt className="text-slate-500">{label}</dt>
                <dd className="text-slate-200">{value}</dd>
              </div>
            ))}
          </dl>
        </Panel>
      </div>

      <Panel
        title="All runs"
        subtitle="The table view - the same data the chart shows."
      >
        {runs.length === 0 ? (
          <EmptyState>
            This pipeline has not run yet. Run it from the pipeline page.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[12px]">
              <thead className="text-[11px] uppercase tracking-wide text-slate-500">
                <tr className="border-b border-slate-800">
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 pr-3 font-medium">Started</th>
                  <th className="py-2 pr-3 font-medium">Duration</th>
                  <th className="py-2 pr-3 font-medium">Rows</th>
                  <th className="py-2 font-medium">Dataset</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id} className="border-b border-slate-900">
                    <td className="py-2 pr-3">
                      <StatusBadge status={run.status} />
                    </td>
                    <td className="py-2 pr-3 text-slate-300">
                      {formatDateTime(run.started_at)}
                    </td>
                    <td className="py-2 pr-3 text-slate-300">
                      {formatDuration(run.execution_time)}
                    </td>
                    <td className="py-2 pr-3 text-slate-300">
                      {formatCount(run.row_count)}
                    </td>
                    <td className="py-2 text-slate-500">
                      {run.dataset_file_id ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
