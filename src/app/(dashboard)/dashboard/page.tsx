"use client";

import { useEffect, useMemo, useState } from "react";
import Navbar from "@/components/Navbar";
import Link from "next/link";
import { DashboardProject, deleteProject, getDashboardOverview } from "@/services/dashboardService";

const statusColors: Record<string, string> = {
  Completed: "bg-[#3BB273]/12 text-[#5EDC8A] border border-[#3BB273]/30 shadow-[0_0_12px_rgba(59,178,115,0.25)]",
  Training: "bg-[#123C66]/12 text-[#5EDC8A] border border-[#3BB273]/30 shadow-[0_0_12px_rgba(59,178,115,0.28)]",
  Failed: "bg-rose-500/12 text-rose-300 border border-rose-400/30 shadow-[0_0_12px_rgba(251,113,133,0.25)]",
  Pending: "bg-amber-500/12 text-amber-300 border border-amber-400/30 shadow-[0_0_12px_rgba(251,191,36,0.22)]",
};

export default function DashboardPage() {
  const [projects, setProjects] = useState<DashboardProject[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [deletingProjectId, setDeletingProjectId] = useState<string | null>(null);

  useEffect(() => {
    const loadOverview = async () => {
      try {
        const overview = await getDashboardOverview();
        setProjects(Array.isArray(overview.projects) ? overview.projects : []);
        setLoadError(null);
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to load dashboard overview";
        setLoadError(message);
        setProjects([]);
      } finally {
        setIsLoading(false);
      }
    };

    void loadOverview();
  }, []);

  const total = projects.length;
  const completed = useMemo(() => projects.filter((project) => project.status === "Completed").length, [projects]);
  const running = useMemo(() => projects.filter((project) => project.status === "Training").length, [projects]);
  const avgAccuracy = useMemo(() => {
    const withAcc = projects.filter((p) => p.status === "Completed" && typeof p.accuracy_percent === "number" && !Number.isNaN(p.accuracy_percent));
    if (withAcc.length === 0) return null;
    return withAcc.reduce((sum, p) => sum + (p.accuracy_percent ?? 0), 0) / withAcc.length;
  }, [projects]);

  const formatAccuracy = (accuracyPercent: number | null): string => {
    if (typeof accuracyPercent !== "number" || Number.isNaN(accuracyPercent)) {
      return "â€”";
    }
    return `${accuracyPercent.toFixed(1)}%`;
  };

  const formatRows = (numRows: number): string => {
    return Number.isFinite(numRows) ? numRows.toLocaleString() : "0";
  };

  // Store formatted dates in state to avoid SSR hydration mismatch
  const [formattedDates, setFormattedDates] = useState<Record<string, string>>({});

  useEffect(() => {
    const newFormatted: Record<string, string> = {};
    projects.forEach((project) => {
      const createdAt = project.created_at;
      if (!createdAt) {
        newFormatted[project.project_id] = "â€”";
        return;
      }
      const parsedDate = new Date(createdAt);
      if (Number.isNaN(parsedDate.getTime())) {
        newFormatted[project.project_id] = "â€”";
        return;
      }
      newFormatted[project.project_id] = parsedDate.toLocaleDateString("en-US", {
        month: "short",
        day: "2-digit",
        year: "numeric",
      });
    });
    setFormattedDates(newFormatted);
  }, [projects]);

  const formatCreatedAt = (projectId: string): string => {
    return formattedDates[projectId] || "â€”";
  };

  const handleDeleteProject = async (projectId: string, projectName: string) => {
    const confirmed = window.confirm(`Delete project \"${projectName}\"? This action cannot be undone.`);
    if (!confirmed) {
      return;
    }

    try {
      setDeletingProjectId(projectId);
      await deleteProject(projectId);
      setProjects((previous) => previous.filter((project) => project.project_id !== projectId));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to delete project";
      window.alert(message);
    } finally {
      setDeletingProjectId(null);
    }
  };

  return (
    <>
      <Navbar title="Dashboard" />

      <main className="relative flex-1 overflow-auto">
        {/* Background layers */}
        <div className="pointer-events-none absolute inset-0">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_15%_10%,rgba(45,80,180,0.1),transparent_38%),radial-gradient(circle_at_80%_5%,rgba(40,140,130,0.07),transparent_32%),radial-gradient(circle_at_50%_90%,rgba(20,30,80,0.15),transparent_42%)]" />
          <div className="absolute inset-0 opacity-[0.04] bg-[linear-gradient(rgba(100,160,255,1)_1px,transparent_1px),linear-gradient(90deg,rgba(100,160,255,1)_1px,transparent_1px)] [background-size:56px_56px]" />
        </div>

        <div className="relative z-10 flex h-full flex-col gap-5 px-6 py-6">
          {/* Page title row */}
          <div className="flex items-center gap-3">
            <div>
              <h2 className="text-[18px] font-bold tracking-tight text-slate-100">Overview</h2>
              <p className="mt-0.5 text-[12px] text-slate-500">All your ML projects at a glance</p>
            </div>
            <div className="ml-auto flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.03] px-3 py-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.9)] animate-pulse" />
              <span className="text-[11px] font-medium text-slate-400">Live</span>
            </div>
          </div>

          {/* Stats grid â€” 4 cards */}
          {isLoading ? (
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="h-[120px] animate-pulse rounded-2xl border border-white/[0.05] bg-white/[0.04]" />
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <StatCard label="Total Projects" value={total} valueDisplay={String(total)} subtitle="All time" icon="total" color="navy" />
              <StatCard label="Completed" value={completed} valueDisplay={String(completed)} subtitle="Successful runs" icon="completed" color="green" />
              <StatCard label="Running Now" value={running} valueDisplay={String(running)} subtitle="Active training" icon="running" color="blue" />
              <StatCard
                label="Avg Accuracy"
                value={avgAccuracy ?? 0}
                valueDisplay={avgAccuracy !== null ? `${avgAccuracy.toFixed(1)}%` : "â€”"}
                subtitle="Completed models"
                icon="accuracy"
                color="purple"
              />
            </div>
          )}

          {/* Main content: projects table + AI insights panel */}
          <div className="flex flex-1 gap-5 min-h-0">
            {/* Left: Projects table */}
            <div className="flex min-w-0 flex-1 flex-col gap-0">
              {/* Table header */}
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h3 className="text-[14px] font-semibold text-slate-200">Recent Projects</h3>
                  {!isLoading && (
                    <p className="text-[11px] text-slate-500 mt-0.5">{total} project{total !== 1 ? "s" : ""} total</p>
                  )}
                </div>
                <Link
                  href="/new-project"
                  className="group relative flex items-center gap-1.5 overflow-hidden rounded-xl border border-cyan-400/30 bg-cyan-500/10 px-3.5 py-1.5 text-[12.5px] font-semibold text-cyan-300 shadow-[0_0_16px_rgba(6,182,212,0.2)] transition-all duration-200 hover:bg-cyan-500/[0.18] hover:shadow-[0_0_22px_rgba(6,182,212,0.35)]"
                >
                  <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 4v16m8-8H4" />
                  </svg>
                  Create New Project
                </Link>
              </div>

              {/* Error state */}
              {loadError && (
                <div className="mb-3 flex items-center gap-3 rounded-xl border border-rose-500/25 bg-rose-500/[0.08] px-4 py-3">
                  <svg className="h-4 w-4 flex-shrink-0 text-rose-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <p className="text-[13px] text-rose-300">{loadError}</p>
                </div>
              )}

              {/* Table */}
              <div className="overflow-hidden rounded-2xl border border-white/[0.06] bg-[rgba(8,13,28,0.7)] shadow-[0_4px_24px_rgba(0,0,0,0.3)]">
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="border-b border-white/[0.06]">
                        <th className="px-5 py-3 text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500">Project</th>
                        <th className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500">Status</th>
                        <th className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500">Accuracy</th>
                        <th className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500">Dataset</th>
                        <th className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500">Trend</th>
                        <th className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500">Created</th>
                        <th className="px-4 py-3 text-right text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-500">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {isLoading ? (
                        Array.from({ length: 4 }).map((_, i) => (
                          <tr key={i} className="border-b border-white/[0.04]">
                            {[1,2,3,4,5,6,7].map((col) => (
                              <td key={col} className="px-4 py-3.5">
                                <div className="h-3 animate-pulse rounded-full bg-white/[0.06]" style={{width: `${50 + (col * 13) % 40}%`}} />
                              </td>
                            ))}
                          </tr>
                        ))
                      ) : projects.length === 0 ? (
                        <tr>
                          <td colSpan={7} className="py-16 text-center">
                            <div className="flex flex-col items-center gap-3">
                              <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-white/[0.06] bg-white/[0.03]">
                                <svg className="h-6 w-6 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                                </svg>
                              </div>
                              <p className="text-[13px] text-slate-500">No projects yet</p>
                              <Link href="/new-project" className="text-[13px] font-medium text-cyan-400 hover:text-cyan-300 transition-colors">
                                Create your first project â†’
                              </Link>
                            </div>
                          </td>
                        </tr>
                      ) : (
                        projects.map((project, rowIdx) => (
                          <tr
                            key={project.project_id}
                            className="group border-b border-white/[0.04] transition-colors hover:bg-white/[0.025] last:border-0"
                          >
                            {/* Project name */}
                            <td className="px-5 py-3.5">
                              <div className="flex items-center gap-2.5">
                                <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg border border-white/[0.07] bg-white/[0.04] text-[11px] font-bold text-slate-400">
                                  {project.project_name.charAt(0).toUpperCase()}
                                </div>
                                <div className="min-w-0">
                                  <p className="truncate text-[13px] font-medium text-slate-200 max-w-[160px]">{project.project_name}</p>
                                </div>
                              </div>
                            </td>
                            {/* Status */}
                            <td className="px-4 py-3.5">
                              <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold ${statusColors[project.status] ?? "bg-slate-700/40 text-slate-400 border border-slate-600/30"}`}>
                                {project.status === "Training" && (
                                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.9)] animate-pulse" />
                                )}
                                {project.status}
                              </span>
                            </td>
                            {/* Accuracy with progress bar */}
                            <td className="px-4 py-3.5">
                              <div className="flex flex-col gap-1 min-w-[80px]">
                                <span className="text-[13px] font-semibold text-slate-200">{formatAccuracy(project.accuracy_percent)}</span>
                                {typeof project.accuracy_percent === "number" && !Number.isNaN(project.accuracy_percent) && (
                                  <div className="h-1 w-full rounded-full bg-white/[0.06] overflow-hidden">
                                    <div
                                      className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-emerald-400"
                                      style={{ width: `${Math.min(100, project.accuracy_percent)}%` }}
                                    />
                                  </div>
                                )}
                              </div>
                            </td>
                            {/* Dataset */}
                            <td className="px-4 py-3.5">
                              <span className="text-[13px] text-slate-400">{formatRows(project.num_rows)} rows</span>
                            </td>
                            {/* Trend glyph */}
                            <td className="px-4 py-3.5">
                              <TrendGlyph seed={rowIdx} />
                            </td>
                            {/* Created */}
                            <td className="px-4 py-3.5">
                              <span className="text-[12.5px] text-slate-500">{formatCreatedAt(project.created_at)}</span>
                            </td>
                            {/* Actions */}
                            <td className="px-4 py-3.5">
                              <div className="flex items-center justify-end gap-1">
                                {/* Show "View Training" button only when training is running */}
                                {project.status === "Training" && (
                                  <Link
                                    href={`/training/${project.file_id}${
                                      project.agent_run_id ? `?job_id=${encodeURIComponent(project.agent_run_id)}` : ""
                                    }`}
                                    title="View Training"
                                    className="flex h-7 w-7 items-center justify-center rounded-lg border border-transparent text-slate-500 transition-all hover:border-cyan-400/30 hover:bg-cyan-500/[0.08] hover:text-cyan-300"
                                  >
                                    <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                                    </svg>
                                  </Link>
                                )}
                                {/* Show "View Results" button for completed projects */}
                                {project.status === "Completed" && (
                                  <Link
                                    href={`/results/${project.file_id}`}
                                    title="View Results"
                                    className="flex h-7 w-7 items-center justify-center rounded-lg border border-transparent text-slate-500 transition-all hover:border-emerald-400/30 hover:bg-emerald-500/[0.08] hover:text-emerald-300"
                                  >
                                    <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                                    </svg>
                                  </Link>
                                )}
                                {/* Show "View Project" button for failed/pending projects */}
                                {(project.status === "Failed" || project.status === "Pending") && (
                                  <Link
                                    href={`/projects/${project.project_id}`}
                                    title="View Project"
                                    className="flex h-7 w-7 items-center justify-center rounded-lg border border-transparent text-slate-500 transition-all hover:border-white/[0.08] hover:bg-white/[0.06] hover:text-slate-200"
                                  >
                                    <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                                    </svg>
                                  </Link>
                                )}
                                <button
                                  onClick={() => void handleDeleteProject(project.project_id, project.project_name)}
                                  disabled={deletingProjectId === project.project_id}
                                  title="Delete"
                                  className="flex h-7 w-7 items-center justify-center rounded-lg border border-transparent text-slate-500 transition-all hover:border-rose-500/25 hover:bg-rose-500/[0.08] hover:text-rose-400 disabled:opacity-40"
                                >
                                  {deletingProjectId === project.project_id ? (
                                    <svg className="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                                    </svg>
                                  ) : (
                                    <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                    </svg>
                                  )}
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            {/* Right: AI Insights Panel */}
            <div className="w-[256px] flex-shrink-0">
              <AIInsightsPanel projects={projects} avgAccuracy={avgAccuracy} completed={completed} running={running} />
            </div>
          </div>
        </div>
      </main>
    </>
  );
}

/* â”€â”€â”€ AI Insights Panel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
function AIInsightsPanel({
  projects,
  avgAccuracy,
  completed,
  running,
}: {
  projects: import("@/services/dashboardService").DashboardProject[];
  avgAccuracy: number | null;
  completed: number;
  running: number;
}) {
  const bestProject = projects
    .filter((p) => p.status === "Completed" && typeof p.accuracy_percent === "number" && !Number.isNaN(p.accuracy_percent))
    .sort((a, b) => (b.accuracy_percent ?? 0) - (a.accuracy_percent ?? 0))[0] ?? null;

  const insights = [
    { text: "Try ensemble methods to boost accuracy on tabular data.", icon: "âœ¦" },
    { text: "Increase training samples â€” models with 10k+ rows show 12% higher accuracy.", icon: "âœ¦" },
    { text: "Hyperparameter tuning available for 2 stalled projects.", icon: "âœ¦" },
  ];

  return (
    <div className="flex flex-col gap-4">
      {/* Panel header */}
      <div className="flex items-center gap-2">
        <div className="flex h-5 w-5 items-center justify-center rounded-md bg-gradient-to-br from-violet-500/30 to-cyan-500/30 border border-violet-400/25">
          <svg className="h-3 w-3 text-violet-300" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clipRule="evenodd" />
          </svg>
        </div>
        <h3 className="text-[13px] font-semibold text-slate-300">AI Insights</h3>
      </div>

      {/* Best model card */}
      <div className="overflow-hidden rounded-xl border border-white/[0.07] bg-[rgba(10,14,30,0.7)] p-4">
        <p className="mb-2.5 text-[10.5px] font-bold uppercase tracking-[0.12em] text-slate-600">Top Performing Model</p>
        {bestProject ? (
          <>
            <p className="truncate text-[13px] font-semibold text-slate-200">{bestProject.project_name}</p>
            <div className="mt-2 flex items-center gap-2">
              <span className="text-[22px] font-bold text-transparent bg-clip-text bg-gradient-to-r from-cyan-400 to-emerald-400">
                {(bestProject.accuracy_percent ?? 0).toFixed(1)}%
              </span>
              <span className="rounded-full bg-emerald-500/15 border border-emerald-500/25 px-2 py-0.5 text-[10px] font-semibold text-emerald-400">accuracy</span>
            </div>
            <div className="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
              <div
                className="h-full rounded-full bg-gradient-to-r from-cyan-500 via-teal-400 to-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.5)]"
                style={{ width: `${Math.min(100, bestProject.accuracy_percent ?? 0)}%` }}
              />
            </div>
          </>
        ) : (
          <p className="text-[12.5px] text-slate-500">No completed models yet</p>
        )}
      </div>

      {/* Platform activity */}
      <div className="overflow-hidden rounded-xl border border-white/[0.07] bg-[rgba(10,14,30,0.7)] p-4">
        <p className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.12em] text-slate-600">Platform Activity</p>
        <div className="space-y-2.5">
          <div className="flex items-center justify-between">
            <span className="text-[12px] text-slate-400">Completed</span>
            <span className="text-[12px] font-semibold text-slate-200">{completed}</span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
            <div
              className="h-full rounded-full bg-gradient-to-r from-emerald-600 to-emerald-400"
              style={{ width: projects.length ? `${(completed / projects.length) * 100}%` : "0%" }}
            />
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[12px] text-slate-400">Running</span>
            <div className="flex items-center gap-1.5">
              {running > 0 && <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.9)] animate-pulse" />}
              <span className="text-[12px] font-semibold text-slate-200">{running}</span>
            </div>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
            <div
              className="h-full rounded-full bg-gradient-to-r from-blue-600 to-cyan-400"
              style={{ width: projects.length ? `${(running / projects.length) * 100}%` : "0%" }}
            />
          </div>
        </div>
        {avgAccuracy !== null && (
          <div className="mt-3 flex items-center justify-between rounded-lg border border-cyan-500/15 bg-cyan-500/[0.06] px-3 py-2">
            <span className="text-[11px] text-slate-400">Avg Accuracy</span>
            <span className="text-[13px] font-bold text-cyan-300">{avgAccuracy.toFixed(1)}%</span>
          </div>
        )}
      </div>

      {/* AI Recommendations */}
      <div className="overflow-hidden rounded-xl border border-white/[0.07] bg-[rgba(10,14,30,0.7)] p-4">
        <p className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.12em] text-slate-600">Recommendations</p>
        <div className="space-y-2.5">
          {insights.map((tip, i) => (
            <div key={i} className="flex gap-2.5">
              <span className="mt-px text-[10px] text-violet-400 flex-shrink-0">{tip.icon}</span>
              <p className="text-[11.5px] leading-relaxed text-slate-400">{tip.text}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Quick links */}
      <div className="overflow-hidden rounded-xl border border-white/[0.07] bg-[rgba(10,14,30,0.7)] p-3">
        <p className="mb-2 text-[10.5px] font-bold uppercase tracking-[0.12em] text-slate-600">Quick Actions</p>
        <div className="space-y-0.5">
          {[
            { label: "Run AutoML sweep", icon: "âš¡", href: "/new-project" },
            { label: "Compare experiments", icon: "âŽ‡", href: "/experiments" },
            { label: "Export best model", icon: "â†—", href: "/models" },
          ].map((action) => (
            <Link
              key={action.label}
              href={action.href}
              className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[12px] text-slate-400 transition-colors hover:bg-white/[0.05] hover:text-slate-200"
            >
              <span className="text-[11px] text-violet-400">{action.icon}</span>
              {action.label}
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}

/* â”€â”€â”€ StatCard â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
function StatCard({
  label,
  valueDisplay,
  subtitle,
  icon,
  color,
}: {
  label: string;
  value: number;
  valueDisplay: string;
  subtitle: string;
  icon: "total" | "completed" | "running" | "accuracy";
  color: "navy" | "green" | "blue" | "purple";
}) {
  const bgMap = {
    navy:   "from-[#1a3a68]/90 via-[#162e55]/85 to-[#0d1c3a]/80",
    green:  "from-[#1a4a3a]/90 via-[#163d38]/85 to-[#0d2a26]/80",
    blue:   "from-[#1e3555]/90 via-[#182d4a]/85 to-[#0f1e38]/80",
    purple: "from-[#2a1a55]/90 via-[#221448]/85 to-[#120d30]/80",
  };

  const illustration =
    icon === "total" ? (
      <svg className="absolute -right-2 -top-2 h-[140px] w-[160px]" viewBox="0 0 200 180" fill="none">
        <defs>
          <filter id="fGlow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="6" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
          <filter id="fGlowStrong" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="10" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
          <linearGradient id="f3dBack" x1="40" y1="55" x2="160" y2="150" gradientUnits="userSpaceOnUse">
            <stop stopColor="rgba(50,140,230,0.7)" /><stop offset="1" stopColor="rgba(30,80,170,0.45)" />
          </linearGradient>
          <linearGradient id="f3dFront" x1="40" y1="68" x2="160" y2="150" gradientUnits="userSpaceOnUse">
            <stop stopColor="rgba(70,170,250,0.75)" /><stop offset="0.5" stopColor="rgba(50,130,220,0.55)" /><stop offset="1" stopColor="rgba(35,90,180,0.4)" />
          </linearGradient>
          <linearGradient id="f3dTab" x1="40" y1="41" x2="100" y2="55" gradientUnits="userSpaceOnUse">
            <stop stopColor="rgba(100,200,255,0.85)" /><stop offset="1" stopColor="rgba(60,160,240,0.6)" />
          </linearGradient>
          <linearGradient id="f3dDoc1" x1="110" y1="28" x2="138" y2="78" gradientUnits="userSpaceOnUse">
            <stop stopColor="rgba(120,210,255,0.7)" /><stop offset="1" stopColor="rgba(70,160,240,0.35)" />
          </linearGradient>
          <linearGradient id="f3dDoc2" x1="120" y1="18" x2="144" y2="60" gradientUnits="userSpaceOnUse">
            <stop stopColor="rgba(140,220,255,0.65)" /><stop offset="1" stopColor="rgba(80,170,250,0.3)" />
          </linearGradient>
        </defs>
        <ellipse cx="100" cy="90" rx="65" ry="55" fill="rgba(60,160,255,0.12)" filter="url(#fGlowStrong)" />
        <ellipse cx="100" cy="165" rx="55" ry="10" fill="rgba(40,120,220,0.25)" />
        <path d="M40 55l60-30 60 30v65l-60 30-60-30V55z" fill="url(#f3dBack)" stroke="rgba(120,200,255,0.5)" strokeWidth="1" filter="url(#fGlow)" />
        <path d="M40 120l60 30 60-30" stroke="rgba(100,190,255,0.5)" strokeWidth="1" />
        <path d="M40 55l20-14h30l10 14" fill="url(#f3dTab)" stroke="rgba(140,220,255,0.6)" strokeWidth="1" />
        <path d="M40 68l60 30 60-30v52l-60 30-60-30V68z" fill="url(#f3dFront)" stroke="rgba(120,210,255,0.5)" strokeWidth="1" />
        <path d="M60 95l40 20" stroke="rgba(150,230,255,0.35)" strokeWidth="1.5" strokeLinecap="round" />
        <path d="M60 105l30 15" stroke="rgba(150,230,255,0.25)" strokeWidth="1.5" strokeLinecap="round" />
        <g filter="url(#fGlow)">
          <path d="M110 28l28 14v36l-28-14V28z" fill="url(#f3dDoc1)" stroke="rgba(160,230,255,0.55)" strokeWidth="0.8" />
          <path d="M116 40l16 8M116 48l12 6M116 56l8 4" stroke="rgba(180,240,255,0.4)" strokeWidth="1" strokeLinecap="round" />
        </g>
        <g opacity="0.8" filter="url(#fGlow)">
          <path d="M120 18l24 12v30l-24-12V18z" fill="url(#f3dDoc2)" stroke="rgba(180,240,255,0.5)" strokeWidth="0.7" />
          <path d="M126 28l12 6M126 34l8 4" stroke="rgba(180,240,255,0.35)" strokeWidth="0.8" strokeLinecap="round" />
        </g>
        <circle cx="145" cy="50" r="2.5" fill="rgba(180,240,255,0.8)" filter="url(#fGlow)" />
        <circle cx="155" cy="38" r="1.8" fill="rgba(160,230,255,0.7)" filter="url(#fGlow)" />
        <circle cx="50" cy="48" r="2" fill="rgba(140,220,255,0.6)" filter="url(#fGlow)" />
        <circle cx="165" cy="65" r="1.5" fill="rgba(170,235,255,0.6)" filter="url(#fGlow)" />
      </svg>
    ) : icon === "completed" ? (
      <svg className="absolute -right-2 -top-2 h-[140px] w-[160px]" viewBox="0 0 200 180" fill="none">
        <defs>
          <filter id="gGlow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="5" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
          <filter id="gGlowBig" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="12" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
          <filter id="gGlowCheck" x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="8" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
          <linearGradient id="leafL1" x1="45" y1="130" x2="70" y2="75" gradientUnits="userSpaceOnUse">
            <stop stopColor="rgba(80,240,170,0.6)" /><stop offset="1" stopColor="rgba(50,200,140,0.15)" />
          </linearGradient>
          <linearGradient id="leafR1" x1="155" y1="125" x2="130" y2="70" gradientUnits="userSpaceOnUse">
            <stop stopColor="rgba(80,240,170,0.55)" /><stop offset="1" stopColor="rgba(50,200,140,0.12)" />
          </linearGradient>
          <radialGradient id="chkRing" cx="100" cy="82" r="40" gradientUnits="userSpaceOnUse">
            <stop stopColor="rgba(80,255,180,0.55)" /><stop offset="0.7" stopColor="rgba(50,200,140,0.3)" /><stop offset="1" stopColor="rgba(30,150,100,0.15)" />
          </radialGradient>
          <linearGradient id="chkStroke" x1="80" y1="96" x2="120" y2="64" gradientUnits="userSpaceOnUse">
            <stop stopColor="rgba(100,255,200,1)" /><stop offset="1" stopColor="rgba(160,255,230,0.95)" />
          </linearGradient>
        </defs>
        <ellipse cx="100" cy="85" rx="60" ry="50" fill="rgba(60,230,160,0.1)" filter="url(#gGlowBig)" />
        <g filter="url(#gGlow)">
          <path d="M45 130c-8-20 5-45 25-50 -15 18-12 38-5 50" fill="url(#leafL1)" />
          <path d="M55 125c-12-15 0-40 18-48 -10 16-8 34-2 48" fill="rgba(80,240,170,0.3)" />
        </g>
        <g filter="url(#gGlow)">
          <path d="M155 125c8-20-5-45-25-50 15 18 12 38 5 50" fill="url(#leafR1)" />
          <path d="M148 120c10-18-2-38-20-45 12 14 10 32 4 45" fill="rgba(80,240,170,0.25)" />
        </g>
        <ellipse cx="100" cy="158" rx="45" ry="8" fill="rgba(60,220,160,0.18)" />
        <circle cx="100" cy="82" r="38" fill="url(#chkRing)" stroke="rgba(100,255,200,0.55)" strokeWidth="1.8" filter="url(#gGlow)" />
        <circle cx="100" cy="82" r="32" fill="rgba(10,35,55,0.5)" stroke="rgba(120,255,210,0.35)" strokeWidth="1" />
        <path d="M80 82l12 14 28-32" stroke="url(#chkStroke)" strokeWidth="5.5" strokeLinecap="round" strokeLinejoin="round" filter="url(#gGlowCheck)" />
        <circle cx="60" cy="55" r="2.5" fill="rgba(130,255,210,0.75)" filter="url(#gGlow)" />
        <circle cx="145" cy="48" r="2" fill="rgba(120,255,200,0.7)" filter="url(#gGlow)" />
        <circle cx="50" cy="95" r="1.8" fill="rgba(140,255,220,0.6)" filter="url(#gGlow)" />
        <circle cx="150" cy="100" r="2.2" fill="rgba(110,255,200,0.55)" filter="url(#gGlow)" />
      </svg>
    ) : icon === "running" ? (
      <svg className="absolute -right-2 -top-2 h-[140px] w-[160px]" viewBox="0 0 200 180" fill="none">
        <defs>
          <filter id="rGlow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="5" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
          <filter id="rGlowBig" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="10" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
          <linearGradient id="gearMain" x1="68" y1="48" x2="148" y2="138" gradientUnits="userSpaceOnUse">
            <stop stopColor="rgba(100,200,255,0.75)" /><stop offset="0.5" stopColor="rgba(70,160,235,0.55)" /><stop offset="1" stopColor="rgba(45,110,190,0.35)" />
          </linearGradient>
          <radialGradient id="gearHub" cx="108" cy="88" r="8" gradientUnits="userSpaceOnUse">
            <stop stopColor="rgba(140,230,255,0.65)" /><stop offset="1" stopColor="rgba(80,180,240,0.25)" />
          </radialGradient>
          <linearGradient id="gearBack" x1="92" y1="30" x2="142" y2="94" gradientUnits="userSpaceOnUse">
            <stop stopColor="rgba(90,180,250,0.6)" /><stop offset="1" stopColor="rgba(50,120,200,0.25)" />
          </linearGradient>
          <linearGradient id="gearSmall" x1="140" y1="55" x2="168" y2="100" gradientUnits="userSpaceOnUse">
            <stop stopColor="rgba(110,200,255,0.7)" /><stop offset="1" stopColor="rgba(60,140,220,0.3)" />
          </linearGradient>
        </defs>
        <ellipse cx="108" cy="88" rx="60" ry="50" fill="rgba(70,180,255,0.1)" filter="url(#rGlowBig)" />
        <ellipse cx="105" cy="162" rx="48" ry="9" fill="rgba(70,160,240,0.2)" />
        <g opacity="0.6" filter="url(#rGlow)">
          <path d="M120 30l5 8h8l5-3 3 5-5 6v9l8 5v6l-8 5v9l5 6-3 5-5-3h-8l-5 8h-6l-4-8h-8l-5 3-3-5 5-6v-9l-8-5v-6l8-5v-9l-5-6 3-5 5 3h8l4-8h6z" fill="url(#gearBack)" stroke="rgba(140,210,255,0.5)" strokeWidth="1" />
          <circle cx="117" cy="62" r="10" fill="rgba(15,30,60,0.6)" stroke="rgba(140,210,255,0.4)" strokeWidth="1" />
        </g>
        <g filter="url(#rGlow)">
          <path d="M112 48l7 11h12l7-5 5 8-7 10v14l12 7v10l-12 7v14l7 10-5 8-7-5h-12l-7 11h-9l-5-11H84l-7 5-5-8 7-10V95l-12-7v-10l12-7V57l-7-10 5-8 7 5h14l5-11h9z" fill="url(#gearMain)" stroke="rgba(150,220,255,0.6)" strokeWidth="1.2" />
          <circle cx="108" cy="88" r="18" fill="rgba(15,25,55,0.7)" stroke="rgba(160,230,255,0.6)" strokeWidth="1.5" />
          <circle cx="108" cy="88" r="8" fill="url(#gearHub)" stroke="rgba(170,240,255,0.5)" strokeWidth="1" />
          <path d="M108 70v4M108 102v4M90 88h4M122 88h4" stroke="rgba(150,230,255,0.45)" strokeWidth="1.2" strokeLinecap="round" />
        </g>
        <g opacity="0.75" filter="url(#rGlow)">
          <path d="M155 55l3 5h5l3-2 2 4-3 4v6l5 3v4l-5 3v6l3 4-2 4-3-2h-5l-3 5h-4l-2-5h-5l-3 2-2-4 3-4v-6l-5-3v-4l5-3v-6l-3-4 2-4 3 2h5l2-5h4z" fill="url(#gearSmall)" stroke="rgba(160,220,255,0.5)" strokeWidth="0.8" />
          <circle cx="153" cy="78" r="7" fill="rgba(15,30,60,0.6)" stroke="rgba(160,220,255,0.45)" strokeWidth="0.8" />
          <circle cx="153" cy="78" r="3" fill="rgba(130,220,255,0.4)" />
        </g>
        <circle cx="165" cy="45" r="2.2" fill="rgba(170,240,255,0.75)" filter="url(#rGlow)" />
        <circle cx="145" cy="110" r="1.8" fill="rgba(150,230,255,0.65)" filter="url(#rGlow)" />
      </svg>
    ) : (
      /* Accuracy: glowing neural network nodes */
      <svg className="absolute -right-2 -top-2 h-[140px] w-[160px]" viewBox="0 0 200 180" fill="none">
        <defs>
          <filter id="aGlow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="5" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
          <filter id="aGlowBig" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="12" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
          <radialGradient id="nodeGrad" cx="50%" cy="50%" r="50%">
            <stop stopColor="rgba(180,130,255,0.85)" /><stop offset="1" stopColor="rgba(120,60,220,0.4)" />
          </radialGradient>
          <radialGradient id="nodeGrad2" cx="50%" cy="50%" r="50%">
            <stop stopColor="rgba(100,200,255,0.8)" /><stop offset="1" stopColor="rgba(60,130,220,0.35)" />
          </radialGradient>
        </defs>
        <ellipse cx="100" cy="90" rx="65" ry="55" fill="rgba(140,80,255,0.08)" filter="url(#aGlowBig)" />
        {/* Connections */}
        <line x1="62" y1="55" x2="100" y2="80" stroke="rgba(160,100,255,0.3)" strokeWidth="1"/>
        <line x1="62" y1="90" x2="100" y2="80" stroke="rgba(100,180,255,0.3)" strokeWidth="1"/>
        <line x1="62" y1="125" x2="100" y2="80" stroke="rgba(160,100,255,0.25)" strokeWidth="1"/>
        <line x1="62" y1="55" x2="100" y2="110" stroke="rgba(100,180,255,0.2)" strokeWidth="1"/>
        <line x1="62" y1="90" x2="100" y2="110" stroke="rgba(160,100,255,0.2)" strokeWidth="1"/>
        <line x1="62" y1="125" x2="100" y2="110" stroke="rgba(100,180,255,0.25)" strokeWidth="1"/>
        <line x1="100" y1="80" x2="140" y2="68" stroke="rgba(160,100,255,0.3)" strokeWidth="1"/>
        <line x1="100" y1="80" x2="140" y2="95" stroke="rgba(100,180,255,0.3)" strokeWidth="1"/>
        <line x1="100" y1="110" x2="140" y2="95" stroke="rgba(160,100,255,0.25)" strokeWidth="1"/>
        <line x1="100" y1="110" x2="140" y2="122" stroke="rgba(100,180,255,0.25)" strokeWidth="1"/>
        {/* Input layer */}
        <circle cx="62" cy="55" r="7" fill="url(#nodeGrad2)" filter="url(#aGlow)" />
        <circle cx="62" cy="90" r="7" fill="url(#nodeGrad2)" filter="url(#aGlow)" />
        <circle cx="62" cy="125" r="7" fill="url(#nodeGrad2)" filter="url(#aGlow)" />
        {/* Hidden layer */}
        <circle cx="100" cy="80" r="8" fill="url(#nodeGrad)" filter="url(#aGlow)" />
        <circle cx="100" cy="110" r="8" fill="url(#nodeGrad)" filter="url(#aGlow)" />
        {/* Output layer */}
        <circle cx="140" cy="68" r="7" fill="url(#nodeGrad2)" filter="url(#aGlow)" />
        <circle cx="140" cy="95" r="9" fill="url(#nodeGrad)" stroke="rgba(180,140,255,0.6)" strokeWidth="1.5" filter="url(#aGlow)" />
        <circle cx="140" cy="122" r="7" fill="url(#nodeGrad2)" filter="url(#aGlow)" />
        {/* Output node glow ring */}
        <circle cx="140" cy="95" r="14" fill="none" stroke="rgba(180,130,255,0.3)" strokeWidth="1" filter="url(#aGlow)" />
        {/* Sparkles */}
        <circle cx="155" cy="45" r="2" fill="rgba(200,160,255,0.75)" filter="url(#aGlow)" />
        <circle cx="48" cy="40" r="1.5" fill="rgba(140,200,255,0.65)" filter="url(#aGlow)" />
        <circle cx="165" cy="115" r="1.8" fill="rgba(180,140,255,0.6)" filter="url(#aGlow)" />
      </svg>
    );

  return (
    <div className="group relative overflow-hidden rounded-2xl border border-white/[0.06] bg-[linear-gradient(130deg,rgba(20,38,78,0.65),rgba(10,22,50,0.8)_50%,rgba(6,14,32,0.92)_100%)] p-5 shadow-[0_0_24px_rgba(20,70,130,0.22)] transition-all duration-200 hover:border-cyan-300/25 hover:shadow-[0_0_36px_rgba(80,210,230,0.2)]">
      {/* Corner glow effects */}
      <div className="pointer-events-none absolute -left-[1px] -top-[1px] h-20 w-20 rounded-tl-2xl border-l-2 border-t-2 border-cyan-400/50" style={{WebkitMaskImage: 'linear-gradient(135deg, white 35%, transparent 100%)', maskImage: 'linear-gradient(135deg, white 35%, transparent 100%)'}} />
      <div className="pointer-events-none absolute -right-[1px] -top-[1px] h-14 w-24 rounded-tr-2xl border-r-2 border-t-2 border-cyan-300/35" style={{WebkitMaskImage: 'linear-gradient(225deg, white 25%, transparent 100%)', maskImage: 'linear-gradient(225deg, white 25%, transparent 100%)'}} />
      <div className="pointer-events-none absolute -bottom-[1px] -left-[1px] h-14 w-14 rounded-bl-2xl border-b-2 border-l-2 border-cyan-400/30" style={{WebkitMaskImage: 'linear-gradient(315deg, white 25%, transparent 100%)', maskImage: 'linear-gradient(315deg, white 25%, transparent 100%)'}} />
      <div className="pointer-events-none absolute -bottom-[1px] -right-[1px] h-12 w-16 rounded-br-2xl border-b-2 border-r-2 border-teal-400/35" style={{WebkitMaskImage: 'linear-gradient(45deg, white 20%, transparent 100%)', maskImage: 'linear-gradient(45deg, white 20%, transparent 100%)'}} />
      <div className={`pointer-events-none absolute inset-0 bg-gradient-to-br opacity-60 ${bgMap[color]}`} />
      <div className="pointer-events-none absolute inset-0">{illustration}</div>

      <div className="relative mb-3">
        <p className="text-sm font-medium tracking-tight text-slate-300">{label}</p>
        <p className="mt-0.5 text-xs text-cyan-100/55">{subtitle}</p>
      </div>
      <div className="relative flex items-end gap-3">
        <p className="text-[32px] font-semibold leading-none tracking-tight text-slate-100">{valueDisplay}</p>
        {icon === "running" && valueDisplay !== "0" && (
          <span className="mb-2 h-2.5 w-2.5 rounded-full bg-emerald-300 shadow-[0_0_12px_rgba(110,245,190,0.9)] animate-pulse" />
        )}
      </div>
    </div>
  );
}

/* â”€â”€â”€ TrendGlyph â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
const trendPatterns = [
  [14, 10, 16, 8, 13, 6, 15, 9, 12, 7, 14],
  [6, 12, 9, 15, 7, 14, 10, 16, 8, 13, 11],
  [10, 14, 7, 11, 16, 9, 5, 13, 8, 15, 12],
  [8, 5, 11, 14, 9, 16, 12, 7, 15, 10, 13],
  [12, 16, 8, 6, 14, 11, 7, 15, 10, 5, 9],
  [5, 9, 14, 12, 7, 16, 10, 8, 13, 15, 11],
  [15, 8, 12, 6, 10, 14, 9, 16, 7, 11, 13],
  [9, 13, 6, 15, 11, 8, 14, 5, 16, 10, 12],
];

function TrendGlyph({ seed = 0 }: { seed?: number }) {
  const pts = trendPatterns[seed % trendPatterns.length];
  const w = 48;
  const h = 20;
  const step = w / (pts.length - 1);
  const gradId = `trendFill${seed}`;
  const line = pts.map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)} ${(h - v).toFixed(1)}`).join(" ");
  const area = `${line} L${w} ${h} L0 ${h} Z`;
  return (
    <svg className="h-5 w-12" viewBox={`0 0 ${w} ${h}`} fill="none">
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2={h} gradientUnits="userSpaceOnUse">
          <stop stopColor="rgba(100,220,200,0.45)" />
          <stop offset="1" stopColor="rgba(100,220,200,0.05)" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gradId})`} />
      <path d={line} stroke="rgba(100,230,210,0.8)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
