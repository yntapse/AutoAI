import type { SourceProfileTable } from "@/services/projectService";

interface OutlierAnalysisProps {
  tables: SourceProfileTable[];
}

export default function OutlierAnalysis({ tables }: OutlierAnalysisProps) {
  const findings = tables.flatMap((table) =>
    table.columns
      .filter((column) => column.outliers.complete && column.outliers.count)
      .map((column) => ({ tableName: table.table_name, ...column })),
  );

  return (
    <section className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
      <h4 className="mb-3 text-sm font-semibold text-white">IQR Outliers</h4>
      {findings.length === 0 ? (
        <p className="text-sm text-slate-500">
          No IQR outliers found in eligible columns. Columns with insufficient data are not evaluated.
        </p>
      ) : (
        <div className="space-y-2">
          {findings.map((column) => (
            <div
              key={`${column.tableName}.${column.column_name}`}
              className="rounded-lg border border-white/[0.06] p-3"
            >
              <div className="flex flex-wrap justify-between gap-2">
                <span className="text-sm text-white">
                  {column.tableName}.{column.column_name}
                </span>
                <span className="text-xs text-amber-300">
                  {column.outliers.count?.toLocaleString()} outliers
                  {column.outliers.percentage === null
                    ? ""
                    : ` (${column.outliers.percentage.toFixed(2)}%)`}
                </span>
              </div>
              <p className="mt-1 text-xs text-slate-500">
                Bounds: {column.outliers.lower_bound} to{" "}
                {column.outliers.upper_bound}; IQR multiplier{" "}
                {column.outliers.multiplier ?? 1.5}. Outliers are review signals,
                not automatic removal instructions.
              </p>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
