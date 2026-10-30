"use client";

/**
 * Step 2 of the AI Pipeline Builder: the user states the business requirement
 * in plain language, against a source whose metadata has already been scanned.
 *
 * Only the sentence and the table/column NAMES ever reach the model. No row
 * value, no sample, no customer data.
 */

import { useState } from "react";
import { DataSourceSummary } from "@/services/catalogService";
import { Plan, createPlan } from "@/services/planService";

const EXAMPLES = [
  "Build a credit risk dataset for every loan with the customer and their repayment behaviour",
  "Create a customer 360 view combining accounts, loans and transactions",
  "Prepare a churn dataset: one row per customer with activity and complaints",
];

interface Props {
  sources: DataSourceSummary[];
  onPlanCreated: (plan: Plan) => void;
}

export default function RequirementPrompt({ sources, onPlanCreated }: Props) {
  const [sourceId, setSourceId] = useState(sources[0]?.id ?? "");
  const [requirement, setRequirement] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const plan = await createPlan({
        data_source_id: sourceId,
        requirement: requirement.trim(),
      });
      onPlanCreated(plan);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not build a plan.");
    } finally {
      setBusy(false);
    }
  }

  const disabled = busy || !sourceId || requirement.trim().length < 5;

  return (
    <form
      onSubmit={submit}
      className="rounded-xl border border-slate-800 bg-slate-900/40 p-5"
    >
      <h2 className="text-[15px] font-semibold text-white">
        What dataset do you need?
      </h2>
      <p className="mt-1 text-[12.5px] text-slate-500">
        Describe it in business language. The planner reads your sentence and the
        scanned table and column names only &mdash; never any row data.
      </p>

      <label className="mt-4 block text-[12px] font-medium text-slate-400">
        Source
        <select
          value={sourceId}
          onChange={(event) => setSourceId(event.target.value)}
          className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-[13px] text-slate-200"
        >
          {sources.length === 0 && <option value="">No scanned source</option>}
          {sources.map((source) => (
            <option key={source.id} value={source.id}>
              {source.source_name}
              {source.schema_name ? ` · ${source.schema_name}` : ""}
            </option>
          ))}
        </select>
      </label>

      <label className="mt-4 block text-[12px] font-medium text-slate-400">
        Business requirement
        <textarea
          value={requirement}
          onChange={(event) => setRequirement(event.target.value)}
          rows={3}
          maxLength={2000}
          placeholder={EXAMPLES[0]}
          className="mt-1 w-full resize-y rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-[13px] text-slate-200 placeholder:text-slate-600"
        />
      </label>

      <div className="mt-2 flex flex-wrap gap-2">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            onClick={() => setRequirement(example)}
            className="rounded-full border border-slate-800 px-2.5 py-1 text-[11px] text-slate-500 hover:border-slate-700 hover:text-slate-300"
          >
            {example.slice(0, 42)}&hellip;
          </button>
        ))}
      </div>

      {error && (
        <p className="mt-3 rounded-lg border border-rose-900/60 bg-rose-950/30 px-3 py-2 text-[12.5px] text-rose-300">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={disabled}
        className="mt-4 rounded-lg bg-cyan-600 px-4 py-2 text-[13px] font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-800 disabled:text-slate-500"
      >
        {busy ? "Analysing schema…" : "Generate plan"}
      </button>
      <p className="mt-2 text-[11.5px] text-slate-600">
        Nothing runs yet. You review and approve every recommendation first.
      </p>
    </form>
  );
}
