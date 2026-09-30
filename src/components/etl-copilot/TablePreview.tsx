import type { ExplorerTable } from "./DatabaseExplorer";

interface TablePreviewProps {
  table: ExplorerTable | null;
  sampleRows: Record<string, unknown>[];
  sampleColumns: string[];
  isLoading: boolean;
  error: string | null;
}

export default function TablePreview({
  table,
  sampleRows,
  sampleColumns,
  isLoading,
  error,
}: TablePreviewProps) {
  return (
    <section className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
      <h4 className="mb-3 text-sm font-semibold text-white">
        Table Preview{table ? `: ${table.name}` : ""}
      </h4>

      {table ? (
        table.columns.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {table.columns.map((column) => (
              <span
                key={column}
                className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-1 text-xs text-slate-300"
              >
                {column}
              </span>
            ))}
          </div>
        ) : (
          <p className="text-sm text-slate-500">
            Column metadata is not available for this table.
          </p>
        )
      ) : (
        <p className="text-sm text-slate-500">Select a table to inspect it.</p>
      )}

      {isLoading && (
        <p className="mt-4 text-sm text-cyan-400">Loading table sample...</p>
      )}

      {error && <p className="mt-4 text-sm text-red-400">{error}</p>}

      {!isLoading && !error && sampleRows.length > 0 && sampleColumns.length > 0 && (
        <div className="mt-4">
          <div className="mb-2 text-xs font-medium text-slate-400">
            Sample rows from {table?.name}
          </div>
          <div className="max-h-56 overflow-auto rounded-lg border border-white/[0.08]">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-slate-900">
                <tr>
                  {sampleColumns.map((column) => (
                    <th
                      key={column}
                      className="whitespace-nowrap px-3 py-2 font-medium text-slate-300"
                    >
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sampleRows.map((row, rowIndex) => (
                  <tr
                    key={rowIndex}
                    className="border-t border-white/[0.06] text-slate-400"
                  >
                    {sampleColumns.map((column) => (
                      <td
                        key={column}
                        className="whitespace-nowrap px-3 py-2"
                      >
                        {String(row[column] ?? "")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!isLoading && !error && table && sampleRows.length === 0 && (
        <p className="mt-4 text-sm text-slate-500">
          No sample rows are available for this table.
        </p>
      )}
    </section>
  );
}