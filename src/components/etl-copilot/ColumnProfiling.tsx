export interface ColumnProfile {
  column_name: string;
  data_type: string;
  null_count: number;
  distinct_count: number;
  sample_values: unknown[];
}

interface ColumnProfilingProps {
  columns: ColumnProfile[];
}

function formatValue(value: unknown) {
  if (value === null) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export default function ColumnProfiling({ columns }: ColumnProfilingProps) {
  return (
    <section className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
      <h4 className="mb-3 text-sm font-semibold text-white">Column Profiling</h4>
      {columns.length === 0 ? (
        <p className="text-sm text-slate-500">
          Column profile is not available.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-white/[0.08]">
          <table className="w-full min-w-[680px] text-left text-xs">
            <thead className="bg-white/[0.04] text-slate-300">
              <tr>
                <th className="px-3 py-2 font-medium">Column Name</th>
                <th className="px-3 py-2 font-medium">Data Type</th>
                <th className="px-3 py-2 font-medium">Null Count</th>
                <th className="px-3 py-2 font-medium">Distinct Count</th>
                <th className="px-3 py-2 font-medium">Sample Values</th>
              </tr>
            </thead>
            <tbody>
              {columns.map((column) => (
                <tr
                  key={column.column_name}
                  className="border-t border-white/[0.06] align-top text-slate-300"
                >
                  <td className="px-3 py-2 font-medium text-white">
                    {column.column_name}
                  </td>
                  <td className="px-3 py-2">{column.data_type}</td>
                  <td className="px-3 py-2">
                    {column.null_count.toLocaleString()}
                  </td>
                  <td className="px-3 py-2">
                    {column.distinct_count.toLocaleString()}
                  </td>
                  <td className="max-w-md whitespace-normal px-3 py-2">
                    {column.sample_values.length > 0
                      ? column.sample_values.map(formatValue).join(", ")
                      : "Not Available"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}