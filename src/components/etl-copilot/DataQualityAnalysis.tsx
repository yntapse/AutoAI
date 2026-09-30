export interface DataQualityMetrics {
  total_rows: number;
  total_columns: number;
  missing_value_percentage: number | null;
  duplicate_rows: number;
  quality_score: number;
}

interface DataQualityAnalysisProps {
  metrics: DataQualityMetrics;
}

function displayNumber(value: number | null | undefined, suffix = "") {
  return typeof value === "number" && Number.isFinite(value)
    ? `${value.toLocaleString()}${suffix}`
    : "Not Available";
}

export default function DataQualityAnalysis({
  metrics,
}: DataQualityAnalysisProps) {
  const items = [
    { label: "Total Rows", value: displayNumber(metrics.total_rows) },
    { label: "Total Columns", value: displayNumber(metrics.total_columns) },
    {
      label: "Missing Value %",
      value: displayNumber(metrics.missing_value_percentage, "%"),
    },
    { label: "Duplicate Rows", value: displayNumber(metrics.duplicate_rows) },
    {
      label: "Data Quality Score",
      value: displayNumber(metrics.quality_score, "/100"),
    },
  ];

  return (
    <section className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
      <h4 className="mb-3 text-sm font-semibold text-white">Data Quality</h4>
      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {items.map((item) => (
          <div
            key={item.label}
            className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3"
          >
            <dt className="text-xs text-slate-500">{item.label}</dt>
            <dd className="mt-1 text-sm font-medium text-slate-100">
              {item.value}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}