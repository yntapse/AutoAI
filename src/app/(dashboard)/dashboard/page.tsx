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

  const formatAccuracy = (accuracyPercent: number | null): string => {
    if (typeof accuracyPercent !== "number" || Number.isNaN(accuracyPercent)) {
      return "—";
    }
    return `${accuracyPercent.toFixed(1)}%`;
  };

  const formatRows = (numRows: number): string => {
    return Number.isFinite(numRows) ? numRows.toLocaleString() : "0";
  };

  const formatCreatedAt = (createdAt: string | null): string => {
    if (!createdAt) {
      return "—";
    }

    const parsedDate = new Date(createdAt);
    if (Number.isNaN(parsedDate.getTime())) {
      return "—";
    }

    return parsedDate.toLocaleDateString("en-US", {
      month: "short",
      day: "2-digit",
      year: "numeric",
    });
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

      <main className="flex-1 px-8 py-8 overflow-auto">
        {/* Stats row */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-5 mb-9">
          <StatCard label="Total Projects" value={total} icon="📁" color="navy" />
          <StatCard label="Completed" value={completed} icon="✅" color="green" />
          <StatCard label="Running" value={running} icon="⚙️" color="blue" />
        </div>

        {/* Header row */}
        <div className="flex items-center justify-between mb-5.5">
          <div>
            <h2 className="text-[17px] font-semibold tracking-tight text-slate-100">Recent Projects</h2>
            <p className="text-sm text-slate-400 mt-1">Your latest ML training runs</p>
          </div>
          <Link
            href="/new-project"
            className="inline-flex items-center gap-2 text-white text-sm font-medium px-4 py-2.5 rounded-xl bg-[#3BB273] hover:bg-[#2FA565] shadow-[0_0_24px_rgba(59,178,115,0.3)] hover:shadow-[0_0_30px_rgba(59,178,115,0.4)] transition-all duration-200"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Create New Project
          </Link>
        </div>

        {/* Projects Table */}
        <div className="bg-[#0F172A]/70 backdrop-blur-sm rounded-2xl border border-[#1e3a52]/85 shadow-[0_0_30px_rgba(11,31,58,0.6)] overflow-hidden">
          {loadError && (
            <div className="px-6 py-3 text-sm text-amber-300 border-b border-[#1e3a52] bg-amber-500/10">
              Unable to connect to backend API ({loadError}).
            </div>
          )}
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#1e3a52] bg-[#0F172A]/90">
                <th className="text-left px-6 py-3.5 text-xs font-semibold text-slate-400 uppercase tracking-wide">
                  Project Name
                </th>
                <th className="text-left px-6 py-3.5 text-xs font-semibold text-slate-400 uppercase tracking-wide">
                  Status
                </th>
                <th className="text-left px-6 py-3.5 text-xs font-semibold text-slate-400 uppercase tracking-wide">
                  Best Accuracy
                </th>
                <th className="text-left px-6 py-3.5 text-xs font-semibold text-slate-400 uppercase tracking-wide">
                  Rows
                </th>
                <th className="text-left px-6 py-3.5 text-xs font-semibold text-slate-400 uppercase tracking-wide">
                  Created
                </th>
                <th className="px-6 py-3.5"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1e3a52]/90">
              {projects.map((project) => (
                <tr key={project.project_id} className="hover:bg-[#123C66]/80 hover:shadow-[inset_0_0_0_1px_rgba(59,178,115,0.15)] transition-all duration-200 group">
                  <td className="px-6 py-4 font-medium text-slate-100 tracking-tight">{project.project_name}</td>
                  <td className="px-6 py-4">
                    <span
                      className={`inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-medium ${
                        statusColors[project.status] ?? "bg-[#123C66] text-slate-300 border border-[#1e3a52]"
                      }`}
                    >
                      {project.status === "Training" && <span className="mr-1.5 h-1.5 w-1.5 rounded-full bg-[#5EDC8A] animate-pulse" />}
                      {project.status}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-slate-300 font-mono text-xs">{formatAccuracy(project.accuracy_percent)}</td>
                  <td className="px-6 py-4 text-slate-400 text-sm">{formatRows(project.num_rows)}</td>
                  <td className="px-6 py-4 text-slate-400 text-sm">{formatCreatedAt(project.created_at)}</td>
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                      <Link
                        href={`/training/${project.file_id}`}
                        className="text-xs text-[#5EDC8A] hover:text-[#3BB273] hover:underline font-medium"
                      >
                        View
                      </Link>
                      {project.status === "Completed" && (
                        <Link
                          href={`/results/${project.file_id}`}
                          className="text-xs text-slate-400 hover:text-slate-200 hover:underline"
                        >
                          Results
                        </Link>
                      )}
                      <button
                        type="button"
                        onClick={() => void handleDeleteProject(project.project_id, project.project_name)}
                        disabled={deletingProjectId === project.project_id}
                        className="text-xs text-rose-300 hover:text-rose-200 hover:underline disabled:opacity-60 disabled:cursor-not-allowed"
                      >
                        {deletingProjectId === project.project_id ? "Deleting..." : "Delete"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {!isLoading && projects.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-6 py-8 text-center text-sm text-slate-400">
                    No projects found yet. Create a new project to get started.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </main>
    </>
  );
}

function StatCard({
  label,
  value,
  icon,
  color,
}: {
  label: string;
  value: number;
  icon: string;
  color: "navy" | "green" | "blue";
}) {
  const bgMap = {
    navy: "bg-gradient-to-br from-[#123C66]/20 to-[#3BB273]/10",
    green: "bg-gradient-to-br from-[#3BB273]/20 to-[#5EDC8A]/15",
    blue: "bg-gradient-to-br from-[#123C66]/20 to-[#3BB273]/10",
  };
  const textMap = { navy: "text-[#5EDC8A]", green: "text-[#5EDC8A]", blue: "text-[#5EDC8A]" };

  return (
    <div className="relative overflow-hidden bg-[#0F172A]/70 backdrop-blur-sm rounded-2xl border border-[#1e3a52]/85 shadow-[0_0_28px_rgba(11,31,58,0.55)] p-6 transition-all duration-200 hover:border-[#3BB273]/35 hover:shadow-[0_0_30px_rgba(59,178,115,0.18)]">
      <div className={`absolute inset-0 opacity-80 pointer-events-none ${bgMap[color]}`} />
      <div className="flex items-center justify-between mb-3">
        <span className="relative text-slate-300 text-sm font-medium tracking-tight">{label}</span>
        <span className={`relative w-9 h-9 bg-[#123C66]/90 border border-[#1e3a52] ${textMap[color]} rounded-xl flex items-center justify-center text-lg`}>
          {icon}
        </span>
      </div>
      <div className="relative flex items-center gap-2">
        <p className="text-[31px] leading-none font-semibold tracking-tight text-slate-100">{value}</p>
        {label === "Running" && <span className="h-2 w-2 rounded-full bg-[#5EDC8A] animate-pulse shadow-[0_0_10px_rgba(94,220,138,0.8)]" />}
      </div>
    </div>
  );
}
