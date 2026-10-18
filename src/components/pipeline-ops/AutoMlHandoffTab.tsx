"use client";

/**
 * AutoML Handoff tab.
 *
 * The dataset a successful run produced, assessed with the SAME readiness
 * check the training path uses (services/training_readiness.py), with ranked
 * target candidates. "Train model" hands off to the existing training flow -
 * no new training entry point is introduced here.
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  EmptyState,
  ErrorNote,
  Panel,
  STATUS_COLOR,
  StatTile,
  StatusKind,
  formatCount,
  statusGlyph,
} from "./opsPrimitives";
import {
  AutoMlReadiness,
  getAutoMlReadiness,
} from "@/services/pipelineOpsService";

export default function AutoMlHandoffTab({
  pipelineId,
  projectId,
}: {
  pipelineId: string;
  projectId: string;
}) {
  const router = useRouter();
  const [readiness, setReadiness] = useState<AutoMlReadiness | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAutoMlReadiness(pipelineId)
      .then((body) => {
        if (cancelled) return;
        setReadiness(body);
        setSelected(body.target_candidates[0]?.column ?? "");
      })
      .catch((err: unknown) =>
        !cancelled &&
        setError(
          err instanceof Error ? err.message : "Failed to check readiness."
        )
      )
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [pipelineId]);

  if (loading)
    return (
      <p className="text-[12.5px] text-slate-500">Checking the dataset&hellip;</p>
    );
  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!readiness) return <EmptyState>Nothing to assess.</EmptyState>;

  const readyTone: StatusKind = readiness.dataset_ready ? "good" : "warning";
  const qualityTone: StatusKind =
    readiness.quality_score === undefined
      ? "neutral"
      : readiness.quality_score >= 98
      ? "good"
      : readiness.quality_score >= 90
      ? "warning"
      : "critical";

  return (
    <div className="space-y-4">
      <Panel
        title="Dataset status"
        subtitle="Assessed with the same check the trainer applies."
      >
        <p
          className="text-[13px]"
          style={{ color: STATUS_COLOR[readyTone] }}
        >
          <span aria-hidden>{statusGlyph(readyTone)}</span>{" "}
          {readiness.dataset_ready
            ? "Dataset ready for training."
            : readiness.reason || "Dataset is not ready."}
        </p>

        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatTile label="Rows" value={formatCount(readiness.rows)} />
          <StatTile label="Columns" value={formatCount(readiness.columns)} />
          <StatTile
            label="Quality score"
            value={
              readiness.quality_score === undefined
                ? "—"
                : `${readiness.quality_score}%`
            }
            tone={qualityTone}
            hint={readiness.quality_basis}
          />
          <StatTile
            label="CV folds"
            value={readiness.cv_folds ?? "—"}
            hint="Cross-validation the trainer will use"
          />
        </div>

        {readiness.warnings.length > 0 && (
          <ul className="mt-3 space-y-1.5">
            {readiness.warnings.map((warning) => (
              <li
                key={warning}
                className="rounded-lg border px-3 py-2 text-[12px]"
                style={{
                  borderColor: `${STATUS_COLOR.warning}44`,
                  color: STATUS_COLOR.warning,
                }}
              >
                {statusGlyph("warning")} {warning}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel
        title="Target candidates"
        subtitle="Ranked by how well each column behaves as a label. Identifiers are excluded."
      >
        {readiness.target_candidates.length === 0 ? (
          <EmptyState>
            No target candidate yet &mdash; the dataset needs a successful run
            first.
          </EmptyState>
        ) : (
          <>
            <ul className="space-y-2">
              {readiness.target_candidates.map((candidate) => (
                <li key={candidate.column}>
                  <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2.5 hover:border-slate-700">
                    <input
                      type="radio"
                      name="target"
                      value={candidate.column}
                      checked={selected === candidate.column}
                      onChange={() => setSelected(candidate.column)}
                      className="mt-0.5 accent-cyan-600"
                    />
                    <span className="min-w-0">
                      <span className="block font-mono text-[12.5px] text-slate-100">
                        {candidate.column}
                      </span>
                      <span className="mt-0.5 block text-[11.5px] text-slate-500">
                        {candidate.task_type} &middot;{" "}
                        {candidate.distinct_values} distinct value(s) &middot;{" "}
                        {candidate.null_percentage}% null &middot; score{" "}
                        {candidate.score}
                      </span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>

            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={!readiness.dataset_ready || !selected || !readiness.dataset_file_id}  
                onClick={() =>

                    router.push(
                      `/training/${encodeURIComponent(readiness.dataset_file_id!)}?target_column=${encodeURIComponent(selected)}`
                    )
                }
                className="rounded-lg bg-cyan-600 px-4 py-2 text-[13px] font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-800 disabled:text-slate-500"
              >
                Train model
              </button>
              <p className="text-[11.5px] text-slate-600">
                {readiness.dataset_ready
                  ? "Opens the existing training flow with this dataset and target."
                  : "Training is disabled until the dataset passes the readiness check."}
              </p>
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}
