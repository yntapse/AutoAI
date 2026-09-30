import type { PreviewPlanSpec } from "./LineageGraph";

interface ExplainabilityCardProps {
  spec: PreviewPlanSpec | null;
  explanation: string[];
}

export default function ExplainabilityCard({
  spec,
  explanation,
}: ExplainabilityCardProps) {
  const sourceColumns = [
    ...(spec?.attributes ?? []).map(
      (attribute) => `${attribute.source_table}.${attribute.source_column}`,
    ),
    ...(spec?.measures ?? [])
      .filter((measure) => measure.source_column)
      .map((measure) => `${measure.source_table}.${measure.source_column}`),
  ];
  const uniqueSourceColumns = Array.from(new Set(sourceColumns));
  const groupingColumns = spec?.entity_key ?? [];

  return (
    <section className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
      <h4 className="mb-3 text-sm font-semibold text-white">Explainability</h4>

      <div className="space-y-4 text-sm">
        <div>
          <h5 className="mb-2 text-xs font-semibold text-slate-400">
            Source columns
          </h5>
          {uniqueSourceColumns.length > 0 ? (
            <ul className="space-y-1 text-slate-300">
              {uniqueSourceColumns.map((column) => (
                <li key={column}>{column}</li>
              ))}
            </ul>
          ) : (
            <p className="text-slate-500">No source columns in this plan.</p>
          )}
        </div>

        <div>
          <h5 className="mb-2 text-xs font-semibold text-slate-400">
            Aggregation logic
          </h5>
          {spec?.measures?.length ? (
            <ul className="space-y-1 text-slate-300">
              {spec.measures.map((measure, index) => (
                <li key={`${measure.name}-${index}`}>
                  {measure.name}: {measure.aggregation}(
                  {measure.source_table}.
                  {measure.source_column || "*"})
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-slate-500">No aggregation is specified.</p>
          )}
        </div>

        <div>
          <h5 className="mb-2 text-xs font-semibold text-slate-400">
            Grouping columns
          </h5>
          {groupingColumns.length > 0 ? (
            <p className="text-slate-300">{groupingColumns.join(", ")}</p>
          ) : (
            <p className="text-slate-500">No grouping columns are specified.</p>
          )}
        </div>

        {explanation.length > 0 && (
          <div>
            <h5 className="mb-2 text-xs font-semibold text-slate-400">
              Plan explanation
            </h5>
            <ul className="space-y-1 text-slate-300">
              {explanation.map((item, index) => (
                <li key={`${item}-${index}`}>{item}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}