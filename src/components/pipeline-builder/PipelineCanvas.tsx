"use client";

/**
 * Step 10 + 12: the visual pipeline canvas and the pre-execution preview.
 *
 * Reads metadata only. No SQL is run to draw this.
 */

import { useEffect, useState } from "react";
import { Canvas, PlanPreview, getCanvas, getPreview } from "@/services/planService";

const RISK_DOT: Record<string, string> = {
  high: "bg-rose-400",
  medium: "bg-amber-400",
  low: "bg-emerald-400",
  none: "bg-emerald-400",
};

interface Props {
  planId: string;
  /** Changes whenever a decision is saved, so the canvas refetches. */
  revision: number;
}

export default function PipelineCanvas({ planId, revision }: Props) {
  const [canvas, setCanvas] = useState<Canvas | null>(null);
  const [preview, setPreview] = useState<PlanPreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getCanvas(planId), getPreview(planId)])
      .then(([nextCanvas, nextPreview]) => {
        if (cancelled) return;
        setError(null);
        setCanvas(nextCanvas);
        setPreview(nextPreview);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load canvas.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [planId, revision]);

  if (error) {
    return (
      <p className="rounded-lg border border-rose-900/60 bg-rose-950/30 px-3 py-2 text-[12.5px] text-rose-300">
        {error}
      </p>
    );
  }

  if (!canvas || !preview) {
    return <p className="text-[12.5px] text-slate-500">Loading canvas&hellip;</p>;
  }

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
        <h3 className="text-[13.5px] font-semibold text-white">Pipeline canvas</h3>
        <p className="mt-0.5 text-[11.5px] text-slate-600">
          Anchor grain: {canvas.anchor_table ?? "not resolved"} &middot; one row per
          anchor record.
        </p>

        <ul className="mt-3 grid gap-2 sm:grid-cols-2">
          {canvas.nodes.map((node) => (
            <li
              key={node.id}
              className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2"
            >
              <p className="text-[12.5px] font-medium text-slate-100">
                {node.label}
              </p>
              <p className="mt-0.5 text-[11px] text-slate-600">
                {node.classification} ·{" "}
                {node.row_count === null
                  ? "row count unknown"
                  : `${node.row_count.toLocaleString()} rows`}{" "}
                · {node.column_count ?? 0} cols
              </p>
            </li>
          ))}
          {canvas.nodes.length === 0 && (
            <li className="text-[12.5px] text-slate-500">
              No tables accepted yet.
            </li>
          )}
        </ul>

        {canvas.edges.length > 0 && (
          <ul className="mt-3 space-y-1.5">
            {canvas.edges.map((edge) => (
              <li
                key={edge.id}
                className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-[12px] text-slate-300"
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    RISK_DOT[edge.explosion_risk ?? "low"] ?? RISK_DOT.low
                  }`}
                />
                <span className="font-medium">
                  {edge.source.replace("table:", "")}
                </span>
                <span className="text-slate-600">{edge.label}</span>
                <span className="font-medium">
                  {edge.target.replace("table:", "")}
                </span>
                {edge.requires_pre_aggregation && (
                  <span className="ml-auto rounded-full border border-amber-900/60 px-2 py-0.5 text-[10.5px] text-amber-300">
                    pre-aggregated
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}

        <p className="mt-3 text-[11.5px] text-slate-600">
          Accepted stages &mdash; transformations {canvas.stages.transformations},
          quality rules {canvas.stages.quality_rules}, derived columns{" "}
          {canvas.stages.derived_columns}, aggregations{" "}
          {canvas.stages.aggregations}
        </p>
      </section>

      <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
        <h3 className="text-[13.5px] font-semibold text-white">
          Preview (estimated)
        </h3>
        <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            ["Tables", preview.tables_selected],
            ["Joins", preview.joins_selected],
            [
              "Rows",
              preview.estimated_rows === null
                ? "unknown"
                : preview.estimated_rows.toLocaleString(),
            ],
            ["Columns", preview.estimated_columns],
          ].map(([label, value]) => (
            <div
              key={String(label)}
              className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2"
            >
              <dt className="text-[11px] uppercase tracking-wide text-slate-600">
                {label}
              </dt>
              <dd className="mt-0.5 text-[15px] font-semibold text-slate-100">
                {value}
              </dd>
            </div>
          ))}
        </dl>
        <p className="mt-2 text-[11.5px] text-slate-600">
          {preview.row_estimate_basis}
        </p>

        <p className="mt-3 text-[12px] text-slate-400">
          Duplicates removed in {preview.duplicate_impact.dedupe_steps} step(s) ·
          nulls filled in {preview.null_impact.fill_steps} · null rows dropped in{" "}
          {preview.null_impact.drop_steps} · {preview.quality_impact.rules} quality
          rule(s), {preview.quality_impact.blocking_rules} blocking
        </p>

        {preview.warnings.length > 0 && (
          <ul className="mt-3 space-y-1.5">
            {preview.warnings.map((warning) => (
              <li
                key={warning}
                className="rounded-lg border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-[12px] text-amber-300"
              >
                {warning}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
