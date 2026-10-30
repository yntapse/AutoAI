interface QualityScoreCardProps {
  score: number;
  coverage: number;
}

export default function QualityScoreCard({
  score,
  coverage,
}: QualityScoreCardProps) {
  const tone =
    score >= 85
      ? "text-emerald-300"
      : score >= 65
        ? "text-amber-300"
        : "text-rose-300";

  return (
    <div className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
      <p className="text-xs text-slate-400">Source quality score</p>
      <p className={`mt-1 text-3xl font-semibold ${tone}`}>
        {score.toFixed(1)}
        <span className="ml-1 text-sm text-slate-500">/ 100</span>
      </p>
      <p className="mt-2 text-xs text-slate-500">
        Formula v1 subtracts capped penalties for missing cells (1.5x), duplicate
        rows (3x), IQR outliers (2x), and expected-type mismatches (0.2x). It is a
        profiling indicator, not a compliance rating.
      </p>
      <p className="mt-1 text-xs text-slate-500">
        Metric coverage: {coverage.toFixed(0)}%; unavailable dimensions are not
        penalized.
      </p>
    </div>
  );
}
