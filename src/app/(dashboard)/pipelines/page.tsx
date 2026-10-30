"use client";

/** Pipeline Dashboard + Pipeline List. */

import { useEffect, useState } from "react";
import Link from "next/link";

import PipelineList from "@/components/pipeline-ops/PipelineList";
import {
  ErrorNote,
  Panel,
  STATUS_COLOR,
  StatTile,
  formatCount,
  formatDateTime,
  formatDuration,
  statusGlyph,
} from "@/components/pipeline-ops/opsPrimitives";
import { DashboardResponse, getDashboard } from "@/services/pipelineOpsService";

export default function PipelinesPage() {
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    getDashboard()
      .then((body) => {
        if (!cancelled) setData(body);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : "Failed to load the dashboard.",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const totals = data?.totals;

  return (
    <div className="px-6 py-6">
      <header className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight text-white">
            Pipelines
          </h1>
          <p className="mt-1 text-[13px] text-slate-500">
            Every saved pipeline, how often it runs, whether it is succeeding,
            and what needs attention.
          </p>
        </div>

        <Link
          href="/pipeline-builder"
          className="rounded-lg bg-cyan-600 px-4 py-2 text-[13px] font-medium text-white hover:bg-cyan-500"
        >
          + New Pipeline
        </Link>
      </header>

      {error && (
        <div className="mb-4">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}

      {loading && (
        <p className="text-[12.5px] text-slate-500">Loading&hellip;</p>
      )}

      {!loading && data && (
        <div className="space-y-5">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <StatTile
              label="Pipelines"
              value={formatCount(totals?.pipelines)}
              hint={
                totals?.pipelines_never_run
                  ? `${totals.pipelines_never_run} never run`
                  : "All have run"
              }
            />

            <StatTile
              label="Success rate"
              value={
                totals?.success_rate == null ? "—" : `${totals.success_rate}%`
              }
              tone={
                totals?.success_rate == null
                  ? "neutral"
                  : totals.success_rate >= 95
                    ? "good"
                    : totals.success_rate >= 80
                      ? "warning"
                      : "critical"
              }
              hint="Finished runs only"
            />

            <StatTile
              label="Failing now"
              value={formatCount(totals?.pipelines_failing)}
              tone={totals?.pipelines_failing ? "critical" : "good"}
              hint="Last run failed"
            />

            <StatTile
              label="Total runs"
              value={formatCount(totals?.total_runs)}
              hint={
                totals?.running
                  ? `${totals.running} in flight`
                  : "None in flight"
              }
            />

            <StatTile
              label="Rows processed"
              value={formatCount(totals?.total_rows_processed)}
              hint={
                totals?.avg_duration_seconds
                  ? `avg ${formatDuration(totals.avg_duration_seconds)}`
                  : undefined
              }
            />
          </div>

          {data.open_alerts.length > 0 && (
            <Panel
              title="Needs attention"
              subtitle="Open alerts across every pipeline."
            >
              <ul className="space-y-2">
                {data.open_alerts.map((alert) => (
                  <li
                    key={alert.id}
                    className="rounded-lg border bg-slate-950 px-3 py-2"
                    style={{
                      borderColor:
                        alert.severity === "critical"
                          ? `${STATUS_COLOR.critical}44`
                          : `${STATUS_COLOR.warning}44`,
                    }}
                  >
                    <p
                      className="text-[12.5px]"
                      style={{
                        color:
                          alert.severity === "critical"
                            ? STATUS_COLOR.critical
                            : STATUS_COLOR.warning,
                      }}
                    >
                      <span aria-hidden>
                        {statusGlyph(
                          alert.severity === "critical"
                            ? "critical"
                            : "warning",
                        )}
                      </span>{" "}
                      {alert.title}
                      <span className="ml-2 text-[11px] uppercase tracking-wide text-slate-500">
                        {alert.alert_type.replace(/_/g, " ")}
                      </span>
                    </p>

                    {alert.message && (
                      <p className="mt-0.5 text-[11.5px] text-slate-500">
                        {alert.message}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          <Panel
            title="Pipeline list"
            subtitle={`Generated ${formatDateTime(data.generated_at)}`}
          >
            <PipelineList rows={data.pipelines} />
          </Panel>
        </div>
      )}
    </div>
  );
}
