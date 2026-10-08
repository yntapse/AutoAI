import type { SourceProfilingReport as SourceProfilingReportData } from "@/services/projectService";
import DataTypeIssues from "./DataTypeIssues";
import DuplicateAnalysis from "./DuplicateAnalysis";
import MissingValuesAnalysis from "./MissingValuesAnalysis";
import OutlierAnalysis from "./OutlierAnalysis";
import ProfilingExecutiveSummary from "./ProfilingExecutiveSummary";

interface SourceProfilingReportProps {
  report: SourceProfilingReportData;
}

export default function SourceProfilingReport({
  report,
}: SourceProfilingReportProps) {
  return (
    <section className="space-y-4 rounded-2xl border border-cyan-400/20 bg-cyan-500/[0.03] p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-lg font-semibold text-white">Source Profiling Report</h3>
          <p className="mt-1 text-xs text-slate-400">
            Snapshot {report.snapshot_id} · schema {report.schema_name} ·{" "}
            {new Date(report.analyzed_at).toLocaleString()}
          </p>
        </div>
        <p className="text-xs text-slate-500">
          Analysis only. Source data is not modified.
        </p>
      </div>

      <ProfilingExecutiveSummary report={report} />

      <div className="grid gap-4 lg:grid-cols-2">
        <MissingValuesAnalysis
          tables={report.tables}
          threshold={report.summary.null_threshold_percentage}
        />
        <DuplicateAnalysis tables={report.tables} />
        <OutlierAnalysis tables={report.tables} />
        <DataTypeIssues tables={report.tables} />
      </div>

      <section className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
        <h4 className="mb-3 text-sm font-semibold text-white">Table and Column Summary</h4>
        <div className="space-y-4">
          {report.tables.map((table) => (
            <div key={table.table_name}>
              <p className="mb-2 text-sm font-medium text-cyan-200">
                {table.table_name} · {table.row_count.toLocaleString()} rows ·{" "}
                {table.column_count} columns
              </p>
              <div className="overflow-x-auto rounded-lg border border-white/[0.06]">
                <table className="w-full min-w-[920px] text-left text-xs">
                  <thead className="bg-white/[0.04] text-slate-300">
                    <tr>
                      <th className="px-3 py-2">Column</th>
                      <th className="px-3 py-2">Type</th>
                      <th className="px-3 py-2">Null %</th>
                      <th className="px-3 py-2">Distinct</th>
                      <th className="px-3 py-2">Min / Max</th>
                      <th className="px-3 py-2">Mean / Median</th>
                      <th className="px-3 py-2">Std Dev</th>
                      <th className="px-3 py-2">Quartiles (Q1 / Q2 / Q3)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {table.columns.map((column) => (
                      <tr
                        key={column.column_name}
                        className="border-t border-white/[0.06] text-slate-300"
                      >
                        <td className="px-3 py-2 font-medium text-white">{column.column_name}</td>
                        <td className="px-3 py-2">{column.data_type}</td>
                        <td className="px-3 py-2">
                          {column.null_percentage === null
                            ? "N/A"
                            : `${column.null_percentage.toFixed(2)}%`}
                        </td>
                        <td className="px-3 py-2">
                          {column.distinct_count === null
                            ? "N/A"
                            : `${column.distinct_count.toLocaleString()}${
                                column.distinct_percentage === null
                                  ? ""
                                  : ` (${column.distinct_percentage.toFixed(1)}%)`
                              }`}
                        </td>
                        <td className="px-3 py-2">{String(column.min ?? "N/A")} / {String(column.max ?? "N/A")}</td>
                        <td className="px-3 py-2">{column.mean ?? "N/A"} / {column.median ?? "N/A"}</td>
                        <td className="px-3 py-2">{column.std_dev ?? "N/A"}</td>
                        <td className="px-3 py-2">
                          {column.quartiles
                            ? `${column.quartiles.q1} / ${column.quartiles.q2 ?? "N/A"} / ${column.quartiles.q3}`
                            : "N/A"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
        <h4 className="mb-3 text-sm font-semibold text-white">Findings and Recommendations</h4>
        {report.issues.length === 0 ? (
          <p className="text-sm text-slate-500">No configured profiling thresholds were exceeded.</p>
        ) : (
          <ul className="mb-3 space-y-2">
            {report.issues.map((issue, index) => (
              <li key={`${issue.category}-${issue.table_name}-${issue.column_name}-${index}`} className="text-xs text-slate-300">
                <span className="font-semibold uppercase text-amber-300">{issue.severity}</span>
                {" · "}{issue.table_name}
                {issue.column_name ? `.${issue.column_name}` : ""}: {issue.message}
              </li>
            ))}
          </ul>
        )}
        {report.recommendations.length > 0 && (
          <ul className="list-inside list-disc space-y-1 text-xs text-slate-400">
            {report.recommendations.map((recommendation) => (
              <li key={recommendation}>{recommendation}</li>
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}
