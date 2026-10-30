import type { SourceProfileTable } from "@/services/projectService";

interface MissingValuesAnalysisProps {
  tables: SourceProfileTable[];
  threshold: number;
}

export default function MissingValuesAnalysis({
  tables,
  threshold,
}: MissingValuesAnalysisProps) {
  const columns = tables.flatMap((table) =>
    table.columns
      .filter((column) => column.null_count > 0)
      .map((column) => ({ tableName: table.table_name, ...column })),
  );

  return (
    <section className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
      <h4 className="mb-3 text-sm font-semibold text-white">Missing Values</h4>
      <p className="mb-3 text-xs text-slate-400">
        Columns above {threshold}% nulls are flagged for review. No values are modified.
      </p>
      {columns.length === 0 ? (
        <p className="text-sm text-slate-500">No missing values found.</p>
      ) : (
        <div className="space-y-2">
          {columns.map((column) => (
            <div
              key={`${column.tableName}.${column.column_name}`}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-white/[0.06] p-3"
            >
              <span className="text-sm text-white">
                {column.tableName}.{column.column_name}
              </span>
              <span
                className={`text-xs ${
                  column.null_percentage !== null &&
                  column.null_percentage > threshold
                    ? "text-amber-300"
                    : "text-slate-400"
                }`}
              >
                {column.null_count.toLocaleString()} nulls
                {column.null_percentage !== null
                  ? ` (${column.null_percentage.toFixed(2)}%)`
                  : ""}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
