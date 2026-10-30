import type { SourceProfileTable } from "@/services/projectService";

interface DataTypeIssuesProps {
  tables: SourceProfileTable[];
}

export default function DataTypeIssues({ tables }: DataTypeIssuesProps) {
  const columns = tables.flatMap((table) =>
    table.columns
      .filter(
        (column) =>
          column.type_analysis.mixed_type === true ||
          (column.type_analysis.type_mismatch_count ?? 0) > 0,
      )
      .map((column) => ({ tableName: table.table_name, ...column })),
  );

  return (
    <section className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
      <h4 className="mb-3 text-sm font-semibold text-white">Data Type Issues</h4>
      {columns.length === 0 ? (
        <p className="text-sm text-slate-500">
          No mixed observed types were found in the available value inventories.
        </p>
      ) : (
        <div className="space-y-2">
          {columns.map((column) => (
            <div
              key={`${column.tableName}.${column.column_name}`}
              className="rounded-lg border border-amber-400/20 p-3"
            >
              <p className="text-sm text-white">
                {column.tableName}.{column.column_name}
              </p>
              <p className="mt-1 text-xs text-slate-400">
                Declared: {column.type_analysis.declared_type}; observed:
                {" "}{column.type_analysis.observed_types.join(", ")}; expected:
                {" "}{column.type_analysis.expected_type}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                {column.type_analysis.type_mismatch_count !== null
                  ? `${column.type_analysis.type_mismatch_count ?? 0} value(s) do not match the expected type.`
                  : "Mixed types were observed, but the capped inventory does not establish a complete mismatch count."}
              </p>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
