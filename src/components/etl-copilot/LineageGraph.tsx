export interface PreviewJoinHop {
  left_table: string;
  left_column: string;
  right_table: string;
  right_column: string;
  join_type?: string;
  cardinality?: string | null;
  match_rate?: number | null;
  row_multiplication?: boolean | null;
}

export interface PreviewJoinPath {
  entity_table: string;
  source_table: string;
  hops: PreviewJoinHop[];
  verified?: boolean;
  discovered_via?: string;
}

export interface PreviewPlanSpec {
  entity_table?: string;
  entity_key?: string[];
  join_paths?: PreviewJoinPath[];
  attributes?: Array<{
    name?: string | null;
    source_table: string;
    source_column: string;
  }>;
  measures?: Array<{
    name: string;
    aggregation: string;
    source_table: string;
    source_column?: string | null;
  }>;
}

interface LineageGraphProps {
  spec: PreviewPlanSpec | null;
  selectedTable: string;
  finalColumns: string[];
}

function FlowStage({
  title,
  items,
}: {
  title: string;
  items: string[];
}) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.03] p-3">
      <h5 className="mb-2 text-xs font-semibold uppercase text-cyan-300">
        {title}
      </h5>
      {items.length > 0 ? (
        <ul className="space-y-1 text-xs text-slate-300">
          {items.map((item) => (
            <li key={item} className="break-words">
              {item}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-slate-500">No details in the current plan.</p>
      )}
    </div>
  );
}

export default function LineageGraph({
  spec,
  selectedTable,
  finalColumns,
}: LineageGraphProps) {
  const sourceTables = Array.from(
    new Set(
      [
        spec?.entity_table,
        ...(spec?.join_paths ?? []).flatMap((path) => [
          path.entity_table,
          path.source_table,
          ...path.hops.flatMap((hop) => [hop.left_table, hop.right_table]),
        ]),
        ...(spec?.attributes ?? []).map((attribute) => attribute.source_table),
        ...(spec?.measures ?? []).map((measure) => measure.source_table),
      ].filter((table): table is string => Boolean(table)),
    ),
  );

  const joins = (spec?.join_paths ?? []).flatMap((path) =>
    path.hops.map(
      (hop) =>
        `${hop.left_table}.${hop.left_column} -> ${hop.right_table}.${hop.right_column}`,
    ),
  );
  const transformations = [
    ...(spec?.attributes ?? []).map(
      (attribute) =>
        `${attribute.name || attribute.source_column} <- ${attribute.source_table}.${attribute.source_column}`,
    ),
    ...(spec?.measures ?? []).map(
      (measure) =>
        `${measure.name} = ${measure.aggregation}(${measure.source_table}.${measure.source_column || "*"})`,
    ),
  ];

  if (sourceTables.length === 0 && selectedTable) sourceTables.push(selectedTable);

  return (
    <section className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
      <h4 className="mb-3 text-sm font-semibold text-white">Lineage</h4>
      <div className="mx-auto max-w-2xl space-y-2">
        <FlowStage title="Source Tables" items={sourceTables} />
        <div className="text-center text-sm text-cyan-400" aria-hidden="true">
          ↓
        </div>
        <FlowStage title="Joins" items={joins} />
        <div className="text-center text-sm text-cyan-400" aria-hidden="true">
          ↓
        </div>
        <FlowStage title="Transformations" items={transformations} />
        <div className="text-center text-sm text-cyan-400" aria-hidden="true">
          ↓
        </div>
        <FlowStage title="Final Dataset" items={finalColumns} />
      </div>
    </section>
  );
}