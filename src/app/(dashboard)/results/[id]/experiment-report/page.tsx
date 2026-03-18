"use client";

import { use, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import Navbar from "@/components/Navbar";
import { ExperimentReportIteration, ExperimentReportResponse, getExperimentReport } from "@/services/resultsService";
import jsPDF from "jspdf";
import autoTable from "jspdf-autotable";

function formatMetric(value: number | null | undefined, digits = 4): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
}

function formatSignedMetric(value: number | null | undefined, digits = 4): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function formatPercent(value: number | null | undefined, digits = 2): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  return `${value.toFixed(digits)}%`;
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "—";
  }
  return parsed.toLocaleString();
}

function toneForDelta(value: number | null | undefined, goodWhenPositive = true): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "text-slate-400";
  }
  if (value === 0) {
    return "text-slate-300";
  }
  const isGood = goodWhenPositive ? value > 0 : value < 0;
  return isGood ? "text-emerald-300" : "text-rose-300";
}

type ModelTimelineEntry = {
  iteration: number;
  rank_position: number | null;
  rmse: number | null;
  mae: number | null;
  r2: number | null;
};

type ModelTimeline = {
  model_name: string;
  best_rmse: number | null;
  latest_rmse: number | null;
  best_r2: number | null;
  appearances: number;
  iterations: ModelTimelineEntry[];
};

function IterationCandidateTable({ item }: { item: ExperimentReportIteration }) {
  return (
    <div className="overflow-hidden rounded-2xl border border-white/[0.08] bg-slate-950/70">
      <div className="flex items-center justify-between border-b border-white/[0.08] px-4 py-3">
        <div>
          <p className="text-[12px] font-semibold text-white">All Models Tested In Iteration {item.iteration}</p>
          <p className="mt-0.5 text-[11px] text-slate-500">Each row is one model experiment generated for this iteration.</p>
        </div>
        <span className="rounded-full border border-white/[0.08] bg-white/[0.04] px-3 py-1 text-[11px] font-semibold text-slate-300">
          {item.candidate_models.length} models
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-left text-[12px]">
          <thead className="bg-white/[0.03] text-slate-500">
            <tr>
              <th className="px-4 py-3 font-semibold uppercase tracking-[0.12em]">Rank</th>
              <th className="px-4 py-3 font-semibold uppercase tracking-[0.12em]">Model</th>
              <th className="px-4 py-3 font-semibold uppercase tracking-[0.12em]">RMSE</th>
              <th className="px-4 py-3 font-semibold uppercase tracking-[0.12em]">MAE</th>
              <th className="px-4 py-3 font-semibold uppercase tracking-[0.12em]">R²</th>
              <th className="px-4 py-3 font-semibold uppercase tracking-[0.12em]">Hyperparameters</th>
            </tr>
          </thead>
          <tbody>
            {item.candidate_models.map((model) => (
              <tr key={`${item.iteration}-${model.model_name}`} className="border-t border-white/[0.06] text-slate-300">
                <td className="px-4 py-3">
                  <span className={`inline-flex h-7 min-w-7 items-center justify-center rounded-full px-2 text-[11px] font-bold ${model.rank_position === 1 ? "bg-emerald-500/15 text-emerald-300" : "bg-white/[0.04] text-slate-300"}`}>
                    {model.rank_position ?? "—"}
                  </span>
                </td>
                <td className="px-4 py-3 font-medium text-white">{model.model_name}</td>
                <td className="px-4 py-3 font-mono text-[12px]">{formatMetric(model.rmse)}</td>
                <td className="px-4 py-3 font-mono text-[12px]">{formatMetric(model.mae)}</td>
                <td className="px-4 py-3 font-mono text-[12px]">{formatMetric(model.r2)}</td>
                <td className="px-4 py-3 text-slate-400">
                  {model.hyperparameters && Object.keys(model.hyperparameters).length > 0
                    ? Object.entries(model.hyperparameters)
                        .slice(0, 3)
                        .map(([key, value]) => `${key}=${String(value)}`)
                        .join(", ")
                    : "No tuned params stored"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function IterationCard({ item, maxRmse }: { item: ExperimentReportIteration; maxRmse: number }) {
  const barWidth =
    typeof item.rmse === "number" && maxRmse > 0
      ? `${Math.max(10, Math.min(100, (item.rmse / maxRmse) * 100))}%`
      : "10%";

  return (
    <article className="rounded-[28px] border border-white/[0.08] bg-[linear-gradient(180deg,rgba(15,23,42,0.96),rgba(2,6,23,0.98))] p-6 shadow-[0_18px_48px_rgba(2,6,23,0.38)]">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <span className="inline-flex h-10 w-10 items-center justify-center rounded-2xl border border-cyan-400/30 bg-cyan-500/10 text-[14px] font-bold text-cyan-300">
              {item.iteration}
            </span>
            <div>
              <h3 className="text-[20px] font-semibold text-white">Iteration {item.iteration}</h3>
              <p className="text-[13px] text-slate-400">Winning model: {item.model_name || "—"}</p>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-full px-3 py-1 text-[11px] font-semibold ${item.is_new_best ? "border border-emerald-400/40 bg-emerald-500/15 text-emerald-300" : "border border-white/[0.08] bg-white/[0.04] text-slate-300"}`}>
            {item.is_new_best ? "New Best" : item.status}
          </span>
          <span className="rounded-full border border-white/[0.08] bg-white/[0.04] px-3 py-1 text-[11px] font-semibold text-slate-300">
            {formatMetric(item.duration_seconds, 1)}s runtime
          </span>
        </div>
      </div>

      <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-4">
          <p className="text-[11px] uppercase tracking-[0.12em] text-slate-500">RMSE</p>
          <p className="mt-2 text-[24px] font-bold text-white">{formatMetric(item.rmse)}</p>
          <p className={`mt-1 text-[12px] ${toneForDelta(item.delta_from_previous_rmse, true)}`}>
            vs prev {formatSignedMetric(item.delta_from_previous_rmse)}
          </p>
        </div>
        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-4">
          <p className="text-[11px] uppercase tracking-[0.12em] text-slate-500">R²</p>
          <p className="mt-2 text-[24px] font-bold text-cyan-300">{formatMetric(item.r2)}</p>
          <p className="mt-1 text-[12px] text-slate-400">MAE {formatMetric(item.mae)}</p>
        </div>
        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-4">
          <p className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Agent Guardrails</p>
          <p className="mt-2 text-[18px] font-bold text-white">{item.agent_signals.single_model_gate_rejections} gate rejects</p>
          <p className="mt-1 text-[12px] text-slate-400">
            {item.agent_signals.architect_blueprints} blueprints, {item.agent_signals.architect_fallbacks} fallbacks
          </p>
        </div>
        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-4">
          <p className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Completed</p>
          <p className="mt-2 text-[16px] font-semibold text-white">{formatDateTime(item.completed_at)}</p>
          <p className="mt-1 text-[12px] text-slate-400">{item.candidate_models.length} candidate models evaluated</p>
        </div>
      </div>

      <div className="mt-5">
        <div className="mb-2 flex items-center justify-between text-[11px] uppercase tracking-[0.12em] text-slate-500">
          <span>RMSE Relative To Worst Iteration</span>
          <span>{formatMetric(item.delta_from_best_rmse)}</span>
        </div>
        <div className="h-3 rounded-full bg-white/[0.05] p-[2px]">
          <div className="h-full rounded-full bg-gradient-to-r from-rose-400/75 via-amber-300/80 to-emerald-300/85" style={{ width: barWidth }} />
        </div>
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-[1.35fr_1fr]">
        <div className="rounded-2xl border border-cyan-400/15 bg-cyan-500/[0.03] p-4">
          <p className="text-[11px] uppercase tracking-[0.12em] text-cyan-300/80">LLM Strategy</p>
          <p className="mt-2 text-[14px] leading-6 text-slate-200">
            {item.strategy_summary || "No strategy summary was persisted for this iteration."}
          </p>
          {item.log_excerpt.length > 0 && (
            <div className="mt-4 rounded-2xl border border-white/[0.06] bg-slate-950/70 p-3">
              <p className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Execution Highlights</p>
              <div className="mt-2 space-y-2">
                {item.log_excerpt.map((line, index) => (
                  <p key={`${item.iteration}-${index}`} className="text-[12px] leading-5 text-slate-400">
                    {line}
                  </p>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="space-y-4">
          <div className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-4">
            <p className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Preprocessing Moves</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {item.preprocessing_tokens.length > 0 ? item.preprocessing_tokens.map((token) => (
                <span key={`${item.iteration}-pre-${token}`} className="rounded-full border border-emerald-400/30 bg-emerald-500/10 px-3 py-1 text-[11px] font-semibold text-emerald-300">
                  {token}
                </span>
              )) : <span className="text-[12px] text-slate-500">No preprocessing tokens recorded.</span>}
            </div>
          </div>

          <div className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-4">
            <p className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Training Moves</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {item.training_tokens.length > 0 ? item.training_tokens.map((token) => (
                <span key={`${item.iteration}-train-${token}`} className="rounded-full border border-violet-400/30 bg-violet-500/10 px-3 py-1 text-[11px] font-semibold text-violet-300">
                  {token}
                </span>
              )) : <span className="text-[12px] text-slate-500">No training tokens recorded.</span>}
            </div>
          </div>
        </div>
      </div>

      <div className="mt-5">
        <IterationCandidateTable item={item} />
      </div>
    </article>
  );
}

export default function ExperimentReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [report, setReport] = useState<ExperimentReportResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isDownloading, setIsDownloading] = useState(false);

  useEffect(() => {
    let alive = true;

    const loadReport = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const nextReport = await getExperimentReport(id);
        if (!alive) {
          return;
        }
        setReport(nextReport);
      } catch (loadError) {
        if (!alive) {
          return;
        }
        setError(loadError instanceof Error ? loadError.message : "Failed to load experiment report.");
      } finally {
        if (alive) {
          setIsLoading(false);
        }
      }
    };

    void loadReport();
    return () => {
      alive = false;
    };
  }, [id]);

  const maxRmse = useMemo(() => {
    if (!report) {
      return 0;
    }
    return report.iterations.reduce((max, item) => {
      if (typeof item.rmse !== "number") {
        return max;
      }
      return Math.max(max, item.rmse);
    }, 0);
  }, [report]);

  const modelTimelines = useMemo<ModelTimeline[]>(() => {
    if (!report) {
      return [];
    }

    const timelineMap = new Map<string, ModelTimeline>();
    for (const iteration of report.iterations) {
      for (const candidate of iteration.candidate_models) {
        const existing = timelineMap.get(candidate.model_name) ?? {
          model_name: candidate.model_name,
          best_rmse: null,
          latest_rmse: null,
          best_r2: null,
          appearances: 0,
          iterations: [],
        };

        existing.appearances += 1;
        existing.latest_rmse = candidate.rmse;
        if (typeof candidate.rmse === "number") {
          existing.best_rmse = existing.best_rmse === null ? candidate.rmse : Math.min(existing.best_rmse, candidate.rmse);
        }
        if (typeof candidate.r2 === "number") {
          existing.best_r2 = existing.best_r2 === null ? candidate.r2 : Math.max(existing.best_r2, candidate.r2);
        }
        existing.iterations.push({
          iteration: iteration.iteration,
          rank_position: candidate.rank_position,
          rmse: candidate.rmse,
          mae: candidate.mae,
          r2: candidate.r2,
        });

        timelineMap.set(candidate.model_name, existing);
      }
    }

    return Array.from(timelineMap.values()).sort((left, right) => {
      const leftScore = left.best_rmse ?? Number.POSITIVE_INFINITY;
      const rightScore = right.best_rmse ?? Number.POSITIVE_INFINITY;
      return leftScore - rightScore;
    });
  }, [report]);

  const downloadPdf = useCallback(() => {
    if (!report) return;
    setIsDownloading(true);
    try {
      const doc = new jsPDF({ orientation: "portrait", unit: "pt", format: "a4" });
      const pageWidth = doc.internal.pageSize.getWidth();
      const margin = 40;
      let y = 40;

      const addPageIfNeeded = (needed: number) => {
        if (y + needed > doc.internal.pageSize.getHeight() - 40) {
          doc.addPage();
          y = 40;
        }
      };

      // Title
      doc.setFontSize(22);
      doc.setFont("helvetica", "bold");
      doc.text(report.project_name || "Experiment Report", margin, y);
      y += 28;

      doc.setFontSize(10);
      doc.setFont("helvetica", "normal");
      doc.setTextColor(100);
      doc.text(`Dataset Experiment Ledger — Generated ${new Date().toLocaleString()}`, margin, y);
      y += 24;

      // Summary section
      doc.setTextColor(0);
      doc.setFontSize(14);
      doc.setFont("helvetica", "bold");
      doc.text("Summary", margin, y);
      y += 18;

      const summaryData = [
        ["Status", report.agent_status],
        ["Target Column", report.target_column],
        ["Best Model", report.summary.best_model_name || "—"],
        ["Iterations", `${report.summary.iterations_completed} / ${report.summary.max_iterations}`],
        ["RMSE Improvement", formatMetric(report.summary.rmse_reduction)],
        ["RMSE Improvement %", formatPercent(report.summary.rmse_reduction_percent)],
        ["R² Gain", formatSignedMetric(report.summary.r2_gain)],
        ["Baseline Iteration", report.summary.baseline_iteration ? `#${report.summary.baseline_iteration.iteration} — ${report.summary.baseline_iteration.model_name} (RMSE ${formatMetric(report.summary.baseline_iteration.rmse)})` : "—"],
        ["Best Iteration", report.summary.best_iteration ? `#${report.summary.best_iteration.iteration} — ${report.summary.best_iteration.model_name} (R² ${formatMetric(report.summary.best_iteration.r2)})` : "—"],
        ["Started", formatDateTime(report.started_at)],
        ["Completed", formatDateTime(report.completed_at)],
      ];

      autoTable(doc, {
        startY: y,
        margin: { left: margin, right: margin },
        head: [["Metric", "Value"]],
        body: summaryData,
        theme: "striped",
        headStyles: { fillColor: [15, 23, 42], textColor: 255, fontStyle: "bold", fontSize: 9 },
        bodyStyles: { fontSize: 9 },
        columnStyles: { 0: { fontStyle: "bold", cellWidth: 140 } },
      });
      y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 24;

      // Strategy Themes
      if (report.strategy_themes.length > 0) {
        addPageIfNeeded(60);
        doc.setFontSize(14);
        doc.setFont("helvetica", "bold");
        doc.text("Strategy Themes", margin, y);
        y += 18;

        autoTable(doc, {
          startY: y,
          margin: { left: margin, right: margin },
          head: [["Theme", "Count"]],
          body: report.strategy_themes.map((t) => [t.name, String(t.count)]),
          theme: "striped",
          headStyles: { fillColor: [15, 23, 42], textColor: 255, fontStyle: "bold", fontSize: 9 },
          bodyStyles: { fontSize: 9 },
        });
        y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 24;
      }

      // Each Iteration
      for (const item of report.iterations) {
        addPageIfNeeded(120);
        doc.setFontSize(13);
        doc.setFont("helvetica", "bold");
        doc.setTextColor(0);
        doc.text(`Iteration ${item.iteration} — ${item.model_name || "Unknown"}`, margin, y);
        y += 16;

        doc.setFontSize(9);
        doc.setFont("helvetica", "normal");
        doc.setTextColor(80);
        const meta = [
          `Status: ${item.status}`,
          item.is_new_best ? "★ New Best" : "",
          `RMSE: ${formatMetric(item.rmse)}`,
          `R²: ${formatMetric(item.r2)}`,
          `MAE: ${formatMetric(item.mae)}`,
          `Duration: ${formatMetric(item.duration_seconds, 1)}s`,
          `Completed: ${formatDateTime(item.completed_at)}`,
        ].filter(Boolean).join("  |  ");
        doc.text(meta, margin, y, { maxWidth: pageWidth - margin * 2 });
        y += 14;

        if (item.strategy_summary) {
          doc.setTextColor(60);
          doc.setFont("helvetica", "italic");
          const lines = doc.splitTextToSize(`Strategy: ${item.strategy_summary}`, pageWidth - margin * 2);
          addPageIfNeeded(lines.length * 12 + 8);
          doc.text(lines, margin, y);
          y += lines.length * 12 + 8;
        }

        // Candidate models table
        if (item.candidate_models.length > 0) {
          addPageIfNeeded(50);
          doc.setFont("helvetica", "normal");
          doc.setTextColor(0);

          autoTable(doc, {
            startY: y,
            margin: { left: margin, right: margin },
            head: [["Rank", "Model", "RMSE", "MAE", "R²", "Hyperparameters"]],
            body: item.candidate_models.map((m) => [
              m.rank_position != null ? String(m.rank_position) : "—",
              m.model_name,
              formatMetric(m.rmse),
              formatMetric(m.mae),
              formatMetric(m.r2),
              m.hyperparameters && Object.keys(m.hyperparameters).length > 0
                ? Object.entries(m.hyperparameters).map(([k, v]) => `${k}=${String(v)}`).join(", ")
                : "—",
            ]),
            theme: "striped",
            headStyles: { fillColor: [15, 23, 42], textColor: 255, fontStyle: "bold", fontSize: 8 },
            bodyStyles: { fontSize: 8 },
            columnStyles: { 5: { cellWidth: 130 } },
          });
          y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 20;
        }
      }

      // Model Timelines
      if (modelTimelines.length > 0) {
        addPageIfNeeded(80);
        doc.setFontSize(14);
        doc.setFont("helvetica", "bold");
        doc.setTextColor(0);
        doc.text("Each Model Experiment Report", margin, y);
        y += 18;

        for (const tl of modelTimelines) {
          addPageIfNeeded(60);
          doc.setFontSize(11);
          doc.setFont("helvetica", "bold");
          doc.text(`${tl.model_name}  (${tl.appearances} iteration${tl.appearances === 1 ? "" : "s"})`, margin, y);
          y += 14;

          doc.setFontSize(9);
          doc.setFont("helvetica", "normal");
          doc.setTextColor(80);
          doc.text(`Best RMSE: ${formatMetric(tl.best_rmse)}   |   Best R²: ${formatMetric(tl.best_r2)}   |   Latest RMSE: ${formatMetric(tl.latest_rmse)}`, margin, y);
          y += 14;
          doc.setTextColor(0);

          autoTable(doc, {
            startY: y,
            margin: { left: margin, right: margin },
            head: [["Iteration", "Rank", "RMSE", "MAE", "R²"]],
            body: tl.iterations.map((e) => [
              String(e.iteration),
              e.rank_position != null ? String(e.rank_position) : "—",
              formatMetric(e.rmse),
              formatMetric(e.mae),
              formatMetric(e.r2),
            ]),
            theme: "striped",
            headStyles: { fillColor: [15, 23, 42], textColor: 255, fontStyle: "bold", fontSize: 8 },
            bodyStyles: { fontSize: 8 },
          });
          y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 18;
        }
      }

      // Footer on every page
      const pageCount = doc.getNumberOfPages();
      for (let i = 1; i <= pageCount; i++) {
        doc.setPage(i);
        doc.setFontSize(8);
        doc.setFont("helvetica", "normal");
        doc.setTextColor(160);
        doc.text(`PyrunAI — Experiment Report — Page ${i} of ${pageCount}`, margin, doc.internal.pageSize.getHeight() - 20);
      }

      const safeName = (report.project_name || "experiment-report").replace(/[^a-zA-Z0-9_-]/g, "_");
      doc.save(`${safeName}_experiment_report.pdf`);
    } finally {
      setIsDownloading(false);
    }
  }, [report, modelTimelines]);

  return (
    <>
      <Navbar title="Experiment Report" />

      <main className="relative flex-1 overflow-auto">
        <div className="pointer-events-none absolute inset-0">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_12%_18%,rgba(14,165,233,0.14),transparent_32%),radial-gradient(circle_at_82%_10%,rgba(16,185,129,0.12),transparent_28%),radial-gradient(circle_at_50%_100%,rgba(249,115,22,0.10),transparent_40%)]" />
          <div className="absolute inset-0 opacity-[0.025] bg-[linear-gradient(rgba(148,163,184,1)_1px,transparent_1px),linear-gradient(90deg,rgba(148,163,184,1)_1px,transparent_1px)] [background-size:72px_72px]" />
        </div>

        <div className="relative z-10 mx-auto flex w-full max-w-7xl flex-col gap-6 px-6 py-8 lg:px-10">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-[11px] uppercase tracking-[0.2em] text-cyan-300/70">Dataset Experiment Ledger</p>
              <h1 className="mt-2 text-[34px] font-semibold tracking-tight text-white">
                {report?.project_name || "Experiment Report"}
              </h1>
              <p className="mt-2 max-w-3xl text-[14px] leading-6 text-slate-400">
                Review each iteration, each model tested inside it, and the exact strategy shifts the LLM used while searching for a better regression result.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              {report && (
                <button
                  onClick={downloadPdf}
                  disabled={isDownloading}
                  className="flex items-center gap-2 rounded-2xl bg-gradient-to-r from-cyan-600 to-blue-600 px-4 py-2.5 text-[13px] font-semibold text-white shadow-[0_0_18px_rgba(6,182,212,0.3)] transition-all hover:shadow-[0_0_26px_rgba(6,182,212,0.5)] hover:from-cyan-500 hover:to-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  {isDownloading ? "Generating..." : "Download PDF"}
                </button>
              )}
              <Link
                href={`/results/${encodeURIComponent(id)}`}
                className="rounded-2xl border border-white/[0.08] bg-white/[0.04] px-4 py-2.5 text-[13px] font-semibold text-slate-200 transition-all hover:bg-white/[0.08]"
              >
                Back To Results
              </Link>
            </div>
          </div>

          {isLoading && (
            <div className="rounded-3xl border border-white/[0.08] bg-slate-950/70 p-8 text-[14px] text-slate-300">
              Loading experiment report...
            </div>
          )}

          {!isLoading && error && (
            <div className="rounded-3xl border border-rose-400/25 bg-rose-500/10 p-8">
              <h2 className="text-[18px] font-semibold text-white">Report unavailable</h2>
              <p className="mt-2 text-[14px] leading-6 text-rose-100/85">{error}</p>
            </div>
          )}

          {!isLoading && report && (
            <>
              <section className="rounded-[28px] border border-white/[0.08] bg-[linear-gradient(135deg,rgba(8,47,73,0.92),rgba(15,23,42,0.96),rgba(6,78,59,0.92))] p-8 shadow-[0_24px_80px_rgba(6,182,212,0.18)]">
                <div className="flex flex-wrap items-start justify-between gap-6">
                  <div className="max-w-3xl">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded-full border border-cyan-300/30 bg-cyan-400/10 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-200">
                        {report.agent_status}
                      </span>
                      <span className="rounded-full border border-white/[0.08] bg-white/[0.05] px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-300">
                        Target {report.target_column}
                      </span>
                    </div>
                    <h2 className="mt-4 text-[30px] font-semibold text-white">Autonomous Regression Search</h2>
                    <p className="mt-3 text-[14px] leading-6 text-slate-300/85">
                      Started {formatDateTime(report.started_at)} and last updated {formatDateTime(report.completed_at)}. The summary below compares the baseline completed iteration with the best iteration from the latest autonomous run.
                    </p>
                  </div>

                  <div className="grid min-w-[300px] gap-3 sm:grid-cols-2">
                    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.05] p-4">
                      <p className="text-[11px] uppercase tracking-[0.12em] text-slate-400">Best Model</p>
                      <p className="mt-2 text-[20px] font-bold text-white">{report.summary.best_model_name || "—"}</p>
                    </div>
                    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.05] p-4">
                      <p className="text-[11px] uppercase tracking-[0.12em] text-slate-400">Iterations</p>
                      <p className="mt-2 text-[20px] font-bold text-white">{report.summary.iterations_completed} / {report.summary.max_iterations}</p>
                    </div>
                  </div>
                </div>

                <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                  <div className="rounded-2xl border border-emerald-400/20 bg-emerald-500/10 p-5">
                    <p className="text-[11px] uppercase tracking-[0.12em] text-emerald-200/80">RMSE Improvement</p>
                    <p className="mt-2 text-[28px] font-bold text-white">{formatMetric(report.summary.rmse_reduction)}</p>
                    <p className="mt-1 text-[12px] text-emerald-100/80">{formatPercent(report.summary.rmse_reduction_percent)} from baseline</p>
                  </div>
                  <div className="rounded-2xl border border-cyan-400/20 bg-cyan-500/10 p-5">
                    <p className="text-[11px] uppercase tracking-[0.12em] text-cyan-200/80">R² Gain</p>
                    <p className="mt-2 text-[28px] font-bold text-white">{formatSignedMetric(report.summary.r2_gain)}</p>
                    <p className="mt-1 text-[12px] text-cyan-100/80">Improvement in regression fit quality</p>
                  </div>
                  <div className="rounded-2xl border border-white/[0.08] bg-white/[0.05] p-5">
                    <p className="text-[11px] uppercase tracking-[0.12em] text-slate-400">Baseline Iteration</p>
                    <p className="mt-2 text-[28px] font-bold text-white">{report.summary.baseline_iteration?.iteration ?? "—"}</p>
                    <p className="mt-1 text-[12px] text-slate-400">
                      {report.summary.baseline_iteration?.model_name || "—"} · RMSE {formatMetric(report.summary.baseline_iteration?.rmse)}
                    </p>
                  </div>
                  <div className="rounded-2xl border border-white/[0.08] bg-white/[0.05] p-5">
                    <p className="text-[11px] uppercase tracking-[0.12em] text-slate-400">Best Iteration</p>
                    <p className="mt-2 text-[28px] font-bold text-white">{report.summary.best_iteration?.iteration ?? "—"}</p>
                    <p className="mt-1 text-[12px] text-slate-400">
                      {report.summary.best_iteration?.model_name || "—"} · R² {formatMetric(report.summary.best_iteration?.r2)}
                    </p>
                  </div>
                </div>
              </section>

              <section className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
                <div className="rounded-3xl border border-white/[0.08] bg-slate-950/80 p-6 shadow-[0_18px_48px_rgba(2,6,23,0.32)]">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <h2 className="text-[18px] font-semibold text-white">Strategy Themes</h2>
                      <p className="mt-1 text-[13px] text-slate-400">Most common preprocessing and training mechanisms used during the run.</p>
                    </div>
                    <div className="rounded-full border border-white/[0.08] bg-white/[0.04] px-3 py-1 text-[11px] font-semibold text-slate-300">
                      {report.strategy_themes.length} themes
                    </div>
                  </div>
                  <div className="mt-5 flex flex-wrap gap-3">
                    {report.strategy_themes.length > 0 ? report.strategy_themes.map((theme) => (
                      <div key={theme.name} className="rounded-2xl border border-white/[0.08] bg-white/[0.04] px-4 py-3">
                        <p className="text-[13px] font-semibold text-white">{theme.name}</p>
                        <p className="mt-1 text-[11px] uppercase tracking-[0.12em] text-slate-500">Used {theme.count} time{theme.count === 1 ? "" : "s"}</p>
                      </div>
                    )) : (
                      <p className="text-[13px] text-slate-400">No strategy themes recorded yet.</p>
                    )}
                  </div>
                </div>

                <div className="rounded-3xl border border-white/[0.08] bg-slate-950/80 p-6 shadow-[0_18px_48px_rgba(2,6,23,0.32)]">
                  <h2 className="text-[18px] font-semibold text-white">Run Diagnostics</h2>
                  <div className="mt-5 space-y-4 text-[13px] text-slate-300">
                    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.04] p-4">
                      <p className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Agent Run ID</p>
                      <p className="mt-2 break-all font-mono text-[12px] text-slate-200">{report.agent_run_id}</p>
                    </div>
                    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.04] p-4">
                      <p className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Models Tracked</p>
                      <p className="mt-2 text-[22px] font-bold text-white">{modelTimelines.length}</p>
                      <p className="mt-1 text-[12px] text-slate-400">Distinct models across all iterations</p>
                    </div>
                    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.04] p-4">
                      <p className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Retained Agent Logs</p>
                      <p className="mt-2 text-[22px] font-bold text-white">{report.log_retention.captured_lines}</p>
                      <p className="mt-1 text-[12px] text-slate-400">
                        {report.log_retention.available ? "Detailed agent events are available for this report." : "Only persisted DB signals were available for this report."}
                      </p>
                    </div>
                  </div>
                </div>
              </section>

              <section className="rounded-[28px] border border-white/[0.08] bg-slate-950/80 p-6 shadow-[0_18px_48px_rgba(2,6,23,0.32)]">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h2 className="text-[18px] font-semibold text-white">Each Model Experiment Report</h2>
                    <p className="mt-1 text-[13px] text-slate-400">Model-by-model performance history across all iterations.</p>
                  </div>
                  <span className="rounded-full border border-white/[0.08] bg-white/[0.04] px-3 py-1 text-[11px] font-semibold text-slate-300">
                    {modelTimelines.length} tracked models
                  </span>
                </div>

                <div className="mt-5 grid gap-4 lg:grid-cols-2">
                  {modelTimelines.map((timeline) => (
                    <article key={timeline.model_name} className="rounded-2xl border border-white/[0.08] bg-white/[0.03] p-5">
                      <div className="flex items-start justify-between gap-4">
                        <div>
                          <h3 className="text-[18px] font-semibold text-white">{timeline.model_name}</h3>
                          <p className="mt-1 text-[12px] text-slate-400">Appeared in {timeline.appearances} iteration{timeline.appearances === 1 ? "" : "s"}</p>
                        </div>
                        <div className="rounded-2xl border border-white/[0.08] bg-slate-950/70 px-3 py-2 text-right">
                          <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Best RMSE</p>
                          <p className="mt-1 text-[18px] font-bold text-emerald-300">{formatMetric(timeline.best_rmse)}</p>
                        </div>
                      </div>

                      <div className="mt-4 grid gap-3 sm:grid-cols-2">
                        <div className="rounded-2xl border border-white/[0.06] bg-slate-950/65 p-3">
                          <p className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Latest RMSE</p>
                          <p className="mt-2 text-[18px] font-bold text-white">{formatMetric(timeline.latest_rmse)}</p>
                        </div>
                        <div className="rounded-2xl border border-white/[0.06] bg-slate-950/65 p-3">
                          <p className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Best R²</p>
                          <p className="mt-2 text-[18px] font-bold text-cyan-300">{formatMetric(timeline.best_r2)}</p>
                        </div>
                      </div>

                      <div className="mt-4 overflow-hidden rounded-2xl border border-white/[0.06] bg-slate-950/70">
                        <table className="w-full text-left text-[12px]">
                          <thead className="bg-white/[0.03] text-slate-500">
                            <tr>
                              <th className="px-3 py-2.5 font-semibold uppercase tracking-[0.12em]">Iteration</th>
                              <th className="px-3 py-2.5 font-semibold uppercase tracking-[0.12em]">Rank</th>
                              <th className="px-3 py-2.5 font-semibold uppercase tracking-[0.12em]">RMSE</th>
                              <th className="px-3 py-2.5 font-semibold uppercase tracking-[0.12em]">R²</th>
                            </tr>
                          </thead>
                          <tbody>
                            {timeline.iterations.map((entry) => (
                              <tr key={`${timeline.model_name}-${entry.iteration}`} className="border-t border-white/[0.06] text-slate-300">
                                <td className="px-3 py-2.5">{entry.iteration}</td>
                                <td className="px-3 py-2.5">{entry.rank_position ?? "—"}</td>
                                <td className="px-3 py-2.5 font-mono">{formatMetric(entry.rmse)}</td>
                                <td className="px-3 py-2.5 font-mono">{formatMetric(entry.r2)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </article>
                  ))}
                </div>
              </section>

              <section className="space-y-5">
                {report.iterations.map((item) => (
                  <IterationCard key={item.training_run_id} item={item} maxRmse={maxRmse} />
                ))}
              </section>
            </>
          )}
        </div>
      </main>
    </>
  );
}
