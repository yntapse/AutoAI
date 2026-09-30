export interface ExplorerTable {
  name: string;
  columns: string[];
}

export interface ExplorerRelationship {
  left_table: string;
  left_column: string;
  right_table: string;
  right_column: string;
  join_type?: string;
  cardinality?: string | null;
  match_rate?: number | null;
  row_multiplication?: boolean | null;
}

interface DatabaseExplorerProps {
  tables: ExplorerTable[];
  relationships: ExplorerRelationship[];
  tableCount: number;
  relationshipCount: number;
  selectedTable: string;
  onSelectTable: (tableName: string) => void;
}

export default function DatabaseExplorer({
  tables,
  relationships,
  tableCount,
  relationshipCount,
  selectedTable,
  onSelectTable,
}: DatabaseExplorerProps) {
  return (
    <section className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h4 className="text-sm font-semibold text-white">Database Explorer</h4>
        <div className="flex gap-4 text-xs text-slate-400">
          <span>
            Tables <strong className="ml-1 text-white">{tableCount}</strong>
          </span>
          <span>
            Relationships
            <strong className="ml-1 text-white">{relationshipCount}</strong>
          </span>
        </div>
      </div>

      {tables.length === 0 ? (
        <p className="text-sm text-slate-500">No tables are available.</p>
      ) : (
        <div className="flex max-h-36 flex-wrap gap-2 overflow-y-auto">
          {tables.map((table) => (
            <button
              key={table.name}
              type="button"
              aria-pressed={selectedTable === table.name}
              onClick={() => onSelectTable(table.name)}
              className={`rounded-lg border px-3 py-1.5 text-left text-xs transition ${
                selectedTable === table.name
                  ? "border-cyan-400/50 bg-cyan-500/15 text-cyan-200"
                  : "border-white/10 bg-white/[0.03] text-slate-300 hover:border-white/20"
              }`}
            >
              {table.name}
            </button>
          ))}
        </div>
      )}

      {relationships.length > 0 && (
        <p className="mt-3 text-xs text-slate-500">
          {relationships.length} declared relationship
          {relationships.length === 1 ? "" : "s"} available
        </p>
      )}
    </section>
  );
}