"use client";

/**
 * Step 3-9: the human approval layer.
 *
 * Every recommendation shows WHY it was chosen, why the alternative was not,
 * its confidence and its expected impact. Nothing is executable until it is
 * accepted here.
 */

import { useState } from "react";
import {
  Decision,
  Plan,
  Recommendation,
  RecommendationKind,
  decideRecommendation,
} from "@/services/planService";

const SECTIONS: Array<{ kind: RecommendationKind; title: string; blurb: string }> = [
  {
    kind: "table",
    title: "Tables",
    blurb: "Required, useful or unnecessary — with the reason for each.",
  },
  {
    kind: "relationship",
    title: "Relationships",
    blurb: "Declared foreign keys first, then name-matched candidates.",
  },
  {
    kind: "join",
    title: "Joins & cardinality",
    blurb: "Join type, cardinality and row-explosion risk.",
  },
  {
    kind: "filter",
    title: "Filters",
    blurb: "Explicit conditions stated in the requirement; review before execution.",
  },
  {
    kind: "aggregation",
    title: "Aggregations",
    blurb: "Explicit aggregate functions and grouping dimensions.",
  },
  {
    kind: "transformation",
    title: "Transformations",
    blurb: "Cleaning steps the existing engine can execute.",
  },
  {
    kind: "derived_column",
    title: "Derived columns",
    blurb: "Features the requirement implies.",
  },
  {
    kind: "quality_rule",
    title: "Quality rules",
    blurb: "Checks that gate the build.",
  },
];

const RISK_STYLES: Record<string, string> = {
  high: "border-rose-900/60 bg-rose-950/30 text-rose-300",
  medium: "border-amber-900/60 bg-amber-950/30 text-amber-300",
  low: "border-slate-800 bg-slate-900/40 text-slate-400",
};

const DECISION_STYLES: Record<Decision, string> = {
  accepted: "bg-emerald-950/40 text-emerald-300 border-emerald-900/60",
  edited: "bg-cyan-950/40 text-cyan-300 border-cyan-900/60",
  rejected: "bg-slate-900 text-slate-500 border-slate-800",
  pending: "bg-slate-900/40 text-slate-400 border-slate-800",
};

interface Props {
  plan: Plan;
  onPlanChanged: (plan: Plan) => void;
}

export default function RecommendationList({ plan, onPlanChanged }: Props) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function decide(rec: Recommendation, decision: "accepted" | "rejected") {
    setError(null);
    setBusyId(rec.id);
    try {
      const updated = await decideRecommendation(plan.id, rec.id, { decision });
      const next: Plan = {
        ...plan,
        status: "reviewed",
        recommendations: Object.fromEntries(
          Object.entries(plan.recommendations).map(([kind, rows]) => [
            kind,
            (rows ?? []).map((row) => (row.id === updated.id ? updated : row)),
          ])
        ) as Plan["recommendations"],
      };
      next.accepted_count = Object.values(next.recommendations)
        .flatMap((rows) => rows ?? [])
        .filter((row) => row.decision === "accepted" || row.decision === "edited")
        .length;
      onPlanChanged(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save decision.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-5">
      {error && (
        <p className="rounded-lg border border-rose-900/60 bg-rose-950/30 px-3 py-2 text-[12.5px] text-rose-300">
          {error}
        </p>
      )}

      {SECTIONS.map((section) => {
        const rows = plan.recommendations[section.kind] ?? [];
        if (rows.length === 0) return null;

        return (
          <section key={section.kind}>
            <header className="mb-2 flex items-baseline justify-between">
              <h3 className="text-[13.5px] font-semibold text-white">
                {section.title}
                <span className="ml-2 text-[11.5px] font-normal text-slate-600">
                  {rows.length}
                </span>
              </h3>
              <p className="text-[11.5px] text-slate-600">{section.blurb}</p>
            </header>

            <ul className="space-y-2">
              {rows.map((rec) => (
                <li
                  key={rec.id}
                  className={`rounded-xl border p-3.5 ${
                    RISK_STYLES[rec.risk_level] ?? RISK_STYLES.low
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-[13px] font-medium text-slate-100">
                        {rec.title}
                      </p>
                      <p className="mt-1 text-[12.5px] leading-relaxed text-slate-400">
                        {rec.reason}
                      </p>
                      {rec.alternative_reason && (
                        <p className="mt-1.5 text-[12px] leading-relaxed text-slate-500">
                          <span className="font-medium text-slate-400">
                            Why not the alternative:{" "}
                          </span>
                          {rec.alternative_reason}
                        </p>
                      )}
                      {rec.expected_impact && (
                        <p className="mt-1.5 text-[11.5px] text-slate-500">
                          Impact: {rec.expected_impact}
                        </p>
                      )}
                      <p className="mt-1.5 text-[11px] uppercase tracking-wide text-slate-600">
                        {rec.origin} · confidence{" "}
                        {Math.round(rec.confidence * 100)}% · risk {rec.risk_level}
                      </p>
                    </div>

                    <div className="flex shrink-0 flex-col items-end gap-1.5">
                      <span
                        className={`rounded-full border px-2 py-0.5 text-[10.5px] uppercase tracking-wide ${DECISION_STYLES[rec.decision]}`}
                      >
                        {rec.decision}
                      </span>
                      <div className="flex gap-1.5">
                        <button
                          type="button"
                          disabled={busyId === rec.id}
                          onClick={() => decide(rec, "accepted")}
                          className="rounded-md border border-emerald-900/60 px-2 py-1 text-[11.5px] text-emerald-300 hover:bg-emerald-950/40 disabled:opacity-50"
                        >
                          Accept
                        </button>
                        <button
                          type="button"
                          disabled={busyId === rec.id}
                          onClick={() => decide(rec, "rejected")}
                          className="rounded-md border border-slate-800 px-2 py-1 text-[11.5px] text-slate-400 hover:bg-slate-900 disabled:opacity-50"
                        >
                          Reject
                        </button>
                      </div>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
