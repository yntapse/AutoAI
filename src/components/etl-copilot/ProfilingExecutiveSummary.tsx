import type { SourceProfilingReport as SourceProfilingReportData } from "@/services/projectService";
import QualityScoreCard from "./QualityScoreCard";

interface ProfilingExecutiveSummaryProps {
  report: SourceProfilingReportData;
}

export default function ProfilingExecutiveSummary({
  report,
}: ProfilingExecutiveSummaryProps) {
  const { summary } = report;
  const issueCount = Object.values(summary.issue_counts).reduce(
    (total, count) => total + count,
    0,
  );

  return (
    <section className="space-y-3">
      <h4 className="text-sm font-semibold text-white">Executive Summary</h4>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <QualityScoreCard
          score={summary.overall_quality_score}
          coverage={summary.score_coverage}
        />
        <div className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
          <p className="text-xs text-slate-400">Tables profiled</p>
          <p className="mt-1 text-2xl font-semibold text-white">
            {summary.tables_profiled.toLocaleString()}
          </p>
        </div>
        <div className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
          <p className="text-xs text-slate-400">Rows reviewed</p>
          <p className="mt-1 text-2xl font-semibold text-white">
            {summary.total_rows.toLocaleString()}
          </p>
        </div>
        <div className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
          <p className="text-xs text-slate-400">Profiling findings</p>
          <p className="mt-1 text-2xl font-semibold text-white">{issueCount}</p>
          <p className="mt-1 text-xs text-slate-500">
            {summary.columns_above_null_threshold} columns exceed the null threshold
          </p>
        </div>
      </div>
    </section>
  );
}
