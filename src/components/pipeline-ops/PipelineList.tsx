"use client";

/** Pipeline List: every pipeline with its run metrics and schedule state. */

import Link from "next/link";
import {
  EmptyState,
  RateMeter,
  StatusBadge,
  formatCount,
  formatDateTime,
  formatDuration,
} from "./opsPrimitives";
import {
  PipelineRow,
  renamePipeline,
  deletePipeline,
  clonePipeline,
} from "@/services/pipelineOpsService";


export default function PipelineList({ rows }: { rows: PipelineRow[] }) {
  if (rows.length === 0) {
    return (
      <EmptyState>
        No pipeline yet. Build one in the AI Pipeline Builder, then save a
        version.
      </EmptyState>
    );
  }

  return (
    <ul className="space-y-2">
      {rows.map((row) => (
        <li
          key={row.pipeline_id}
          className="rounded-xl border border-slate-800 bg-slate-950 transition-colors hover:border-slate-700"
        >
          <Link
            href={`/pipelines/${row.pipeline_id}`}
            className="block px-4 py-3"
          >
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-[14px] font-medium text-white">
                    {row.pipeline_name}
                  </p>
                  <StatusBadge status={row.metrics.last_status} />
                  {row.open_alerts > 0 && (
                    <span className="rounded-full border border-rose-900/60 px-2 py-0.5 text-[10.5px] text-rose-300">
                      {row.open_alerts} alert
                      {row.open_alerts === 1 ? "" : "s"}
                    </span>
                  )}
                </div>
                {row.description && (
                  <p className="mt-1 truncate text-[12px] text-slate-500">
                    {row.description}
                  </p>
                )}
                <p className="mt-1.5 text-[11.5px] text-slate-600">
                  v{row.latest_version ?? "—"} of {row.version_count}{" "}
                  &middot; {row.active_schedules} active schedule
                  {row.active_schedules === 1 ? "" : "s"}
                  {row.next_run_at &&
                    ` · next run ${formatDateTime(row.next_run_at)}`}
                </p>
              </div>

              <dl className="flex shrink-0 flex-wrap gap-x-6 gap-y-1 text-[11.5px]">
                {[
                  ["Runs", formatCount(row.metrics.total_runs)],
                  ["Failed", formatCount(row.metrics.failed)],
                  ["Avg", formatDuration(row.metrics.avg_duration_seconds)],
                  ["Rows", formatCount(row.metrics.total_rows_processed)],
                  ["Last run", formatDateTime(row.metrics.last_run_at)],
                ].map(([label, value]) => (
                  <div key={label}>
                    <dt className="text-[10.5px] uppercase tracking-wide text-slate-600">
                      {label}
                    </dt>
                    <dd className="text-slate-300">{value}</dd>
                  </div>
                ))}
              </dl>

              <div className="w-full max-w-[180px] shrink-0">
                <RateMeter rate={row.metrics.success_rate} />
              </div>
            </div>

          </Link>

          <div className="flex flex-wrap gap-2 px-4 pb-3">
            <button
              type="button"
              onClick={async () => {
                const name = window.prompt("Enter new pipeline name:", row.pipeline_name);
                if (name === null || !name.trim()) return;

                try {
                  await renamePipeline(row.pipeline_id, {
                    pipeline_name: name.trim(),
                  });
                  window.location.reload();
                } catch (error) {
                  window.alert(
                    error instanceof Error ? error.message : "Rename failed."
                  );
                }
              }}
              className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-200"
            >
              Rename
            </button>

            <button
              type="button"
              onClick={async () => {
                if (!window.confirm(`Clone "${row.pipeline_name}"?`)) return;

                try {
                  await clonePipeline(row.pipeline_id);
                  window.location.reload();
                } catch (error) {
                  window.alert(
                    error instanceof Error ? error.message : "Clone failed."
                  );
                }
              }}
              className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-200"
            >
              Clone
            </button>

            <button
              type="button"
              onClick={async () => {
                if (!window.confirm(`Delete "${row.pipeline_name}"?`)) return;

                try {
                  await deletePipeline(row.pipeline_id);
                  window.location.reload();
                } catch (error) {
                  window.alert(
                    error instanceof Error ? error.message : "Delete failed."
                  );
                }
              }}
              className="rounded border border-rose-900 px-3 py-1 text-xs text-rose-300"
            >
              Delete
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}
