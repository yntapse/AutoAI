"use client";

/** Pipeline Detail page: header summary plus the five tabs. */

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import AutoMlHandoffTab from "@/components/pipeline-ops/AutoMlHandoffTab";
import MonitoringTab from "@/components/pipeline-ops/MonitoringTab";
import RunHistoryTab from "@/components/pipeline-ops/RunHistoryTab";
import ScheduleTab from "@/components/pipeline-ops/ScheduleTab";
import VersionHistoryTab from "@/components/pipeline-ops/VersionHistoryTab";
import {
  ErrorNote,
  StatusBadge,
  formatCount,
  formatDateTime,
  formatDuration,
} from "@/components/pipeline-ops/opsPrimitives";
import { PipelineRow, listPipelines } from "@/services/pipelineOpsService";

const TABS = [
  { key: "versions", label: "Version History" },
  { key: "runs", label: "Run History" },
  { key: "schedule", label: "Schedule" },
  { key: "monitoring", label: "Monitoring" },
  { key: "automl", label: "AutoML Handoff" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export default function PipelineDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const pipelineId = params?.id ?? "";

  // The tab is read from the URL in an effect rather than with
  // useSearchParams(): this page is a client component, and useSearchParams()
  // would force it behind a Suspense boundary at build time.
  const [tab, setTab] = useState<TabKey>("runs");

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get(
      "tab"
    ) as TabKey | null;
    if (requested && TABS.some((item) => item.key === requested)) {
      setTab(requested);
    }
  }, []);

  const [pipeline, setPipeline] = useState<PipelineRow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!pipelineId) return;
    let cancelled = false;
    listPipelines()
      .then((rows) => {
        if (cancelled) return;
        const match = rows.find((row) => row.pipeline_id === pipelineId) ?? null;
        setPipeline(match);
        if (!match) setError("Pipeline not found.");
      })
      .catch((err: unknown) =>
        !cancelled &&
        setError(
          err instanceof Error ? err.message : "Failed to load the pipeline."
        )
      )
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [pipelineId]);

  function selectTab(next: TabKey) {
    setTab(next);
    router.replace(`/pipelines/${pipelineId}?tab=${next}`, { scroll: false });
  }

  const summary = useMemo(() => {
    if (!pipeline) return [];
    return [
      ["Latest version", `v${pipeline.latest_version ?? "—"}`],
      ["Runs", formatCount(pipeline.metrics.total_runs)],
      [
        "Success rate",
        pipeline.metrics.success_rate === null
          ? "—"
          : `${pipeline.metrics.success_rate}%`,
      ],
      ["Avg duration", formatDuration(pipeline.metrics.avg_duration_seconds)],
      ["Last run", formatDateTime(pipeline.metrics.last_run_at)],
      ["Next run", formatDateTime(pipeline.next_run_at)],
    ] as Array<[string, string]>;
  }, [pipeline]);

  return (
    <div className="px-6 py-6">
      <nav className="mb-3 text-[12px] text-slate-500">
        <Link href="/pipelines" className="hover:text-slate-300">
          Pipelines
        </Link>
        <span className="mx-1.5">/</span>
        <span className="text-slate-400">
          {pipeline?.pipeline_name ?? "Pipeline"}
        </span>
      </nav>

      {error && (
        <div className="mb-4">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}

      {loading && <p className="text-[12.5px] text-slate-500">Loading&hellip;</p>}

      {!loading && pipeline && (
        <>
          <header className="mb-5">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-[22px] font-semibold tracking-tight text-white">
                {pipeline.pipeline_name}
              </h1>
              <StatusBadge status={pipeline.metrics.last_status} />
              {pipeline.open_alerts > 0 && (
                <span className="rounded-full border border-rose-900/60 px-2 py-0.5 text-[11px] text-rose-300">
                  {pipeline.open_alerts} open alert
                  {pipeline.open_alerts === 1 ? "" : "s"}
                </span>
              )}
            </div>
            {pipeline.description && (
              <p className="mt-1 text-[13px] text-slate-500">
                {pipeline.description}
              </p>
            )}

            <dl className="mt-4 flex flex-wrap gap-x-7 gap-y-2">
              {summary.map(([label, value]) => (
                <div key={label}>
                  <dt className="text-[10.5px] uppercase tracking-wide text-slate-600">
                    {label}
                  </dt>
                  <dd className="text-[13px] text-slate-200">{value}</dd>
                </div>
              ))}
            </dl>
          </header>

          <div
            role="tablist"
            aria-label="Pipeline sections"
            className="mb-4 flex flex-wrap gap-1 border-b border-slate-800"
          >
            {TABS.map((item) => (
              <button
                key={item.key}
                role="tab"
                aria-selected={tab === item.key}
                type="button"
                onClick={() => selectTab(item.key)}
                className={`-mb-px border-b-2 px-3 py-2 text-[13px] transition-colors ${
                  tab === item.key
                    ? "border-cyan-500 text-white"
                    : "border-transparent text-slate-500 hover:text-slate-300"
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div role="tabpanel">
            {tab === "versions" && (
              <VersionHistoryTab pipelineId={pipelineId} />
            )}
            {tab === "runs" && <RunHistoryTab pipelineId={pipelineId} />}
            {tab === "schedule" && <ScheduleTab pipelineId={pipelineId} />}
            {tab === "monitoring" && <MonitoringTab pipelineId={pipelineId} />}
            {tab === "automl" && (
              <AutoMlHandoffTab
                pipelineId={pipelineId}
                projectId={pipeline.project_id}
              />
            )}
          </div>
        </>
      )}
    </div>
  );
}
