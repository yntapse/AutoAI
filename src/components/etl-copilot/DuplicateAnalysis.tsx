import type { SourceProfileTable } from "@/services/projectService";

interface DuplicateAnalysisProps {
  tables: SourceProfileTable[];
}

export default function DuplicateAnalysis({ tables }: DuplicateAnalysisProps) {
  return (
    <section className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
      <h4 className="mb-3 text-sm font-semibold text-white">Duplicate Analysis</h4>
      <div className="space-y-2">
        {tables.map((table) => (
          <div
            key={table.table_name}
            className="rounded-lg border border-white/[0.06] p-3"
          >
            <div className="flex flex-wrap justify-between gap-2">
              <span className="text-sm font-medium text-white">
                {table.table_name}
              </span>
              <span className="text-xs text-slate-300">
                {table.duplicate_rows.count === null
                  ? "Unavailable"
                  : `${table.duplicate_rows.count.toLocaleString()} duplicate rows${
                      table.duplicate_rows.percentage === null
                        ? ""
                        : ` (${table.duplicate_rows.percentage.toFixed(2)}%)`
                    }`}
              </span>
            </div>
            <p className="mt-1 text-xs text-slate-500">
              {table.duplicate_rows.complete
                ? `Compared ${table.duplicate_rows.columns_checked.length} columns for exact-row duplicates.`
                : "Exact duplicate analysis is incomplete because some source columns cannot be grouped safely."}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              Primary key:{" "}
              {table.duplicate_keys.columns.length
                ? `${table.duplicate_keys.columns.join(", ")}; duplicate count ${
                    table.duplicate_keys.duplicate_count ?? "unavailable"
                  }`
                : "no declared primary key"}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}
