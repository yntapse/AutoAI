"use client";

/**
 * AI Pipeline Builder.
 *
 * Requirement -> AI recommendations -> human approval -> canvas + preview ->
 * compile to a saved pipeline version. Compiling freezes the approved plan:
 * execution replays the saved definition and never re-plans.
 */

import { useEffect, useMemo, useState } from "react";
import PipelineCanvas from "@/components/pipeline-builder/PipelineCanvas";
import RecommendationList from "@/components/pipeline-builder/RecommendationList";
import RequirementPrompt from "@/components/pipeline-builder/RequirementPrompt";
import { DataSourceSummary, listSources } from "@/services/catalogService";
import { DashboardProject, getDashboardOverview } from "@/services/dashboardService";
import { CompileResult, Plan, compilePlan } from "@/services/planService";

export default function PipelineBuilderPage() {
  const [sources, setSources] = useState<DataSourceSummary[]>([]);
  const [projects, setProjects] = useState<DashboardProject[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [pipelineName, setPipelineName] = useState("");
  const [projectId, setProjectId] = useState("");
  const [compiling, setCompiling] = useState(false);
  const [compiled, setCompiled] = useState<CompileResult | null>(null);

  useEffect(() => {
    Promise.all([listSources(), getDashboardOverview()])
      .then(([sourceRows, overview]) => {
        setSources(sourceRows);
        setProjects(overview.projects ?? []);
        setProjectId((current) => current || overview.projects?.[0]?.project_id || "");
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to load sources.");
      })
      .finally(() => setLoading(false));
  }, []);

  const scanned = useMemo(
    () => sources.filter((source) => source.last_scanned_at !== null),
    [sources]
  );

  async function compile() {
    if (!plan) return;
    setError(null);
    setCompiling(true);
    try {
      const result = await compilePlan(plan.id, {
        pipeline_name: pipelineName.trim(),
        project_id: projectId,
      });
      setCompiled(result);
      setPlan({ ...plan, status: result.status });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not compile the plan.");
    } finally {
      setCompiling(false);
    }
  }

  return (
    <div className="px-6 py-6">
      <header className="mb-6">
        <h1 className="text-[22px] font-semibold tracking-tight text-white">
          AI Pipeline Builder
        </h1>
        <p className="mt-1 text-[13px] text-slate-500">
          State the requirement in business language. The platform reads your
          scanned metadata, recommends the tables, joins, filters,
          transformations and quality rules, and explains every choice. You
          approve; only then is a pipeline saved.
        </p>
      </header>

      {error && (
        <p className="mb-4 rounded-lg border border-rose-900/60 bg-rose-950/30 px-3 py-2 text-[12.5px] text-rose-300">
          {error}
        </p>
      )}

      {loading && <p className="text-[12.5px] text-slate-500">Loading&hellip;</p>}

      {!loading && scanned.length === 0 && (
        <p className="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3 text-[12.5px] text-slate-400">
          No source has a completed metadata scan yet. Register and scan one in
          the Data Catalog first.
        </p>
      )}

      {!loading && scanned.length > 0 && (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_380px]">
          <div className="space-y-5">
            {!plan && (
              <RequirementPrompt
                sources={scanned}
                onPlanCreated={(created) => {
                  setPlan(created);
                  setCompiled(null);
                  setPipelineName(
                    created.requirement_text
                      .toLowerCase()
                      .replace(/[^a-z0-9]+/g, "_")
                      .replace(/^_+|_+$/g, "")
                      .slice(0, 48) || "new_pipeline"
                  );
                }}
              />
            )}

            {plan && (
              <>
                <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-[13px] text-slate-200">
                        {plan.requirement_text}
                      </p>
                      <p className="mt-1 text-[11.5px] text-slate-600">
                        {plan.recommendation_count} recommendations ·{" "}
                        {plan.accepted_count} accepted · intent from{" "}
                        {plan.intent_origin} · status {plan.status}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setPlan(null);
                        setCompiled(null);
                      }}
                      className="shrink-0 rounded-md border border-slate-800 px-2.5 py-1 text-[11.5px] text-slate-400 hover:bg-slate-900"
                    >
                      New plan
                    </button>
                  </div>
                </div>

                <RecommendationList
                  plan={plan}
                  onPlanChanged={(next) => {
                    setPlan(next);
                    setRevision((value) => value + 1);
                  }}
                />
              </>
            )}
          </div>

          {plan && (
            <aside className="space-y-4">
              <PipelineCanvas planId={plan.id} revision={revision} />

              <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
                <h3 className="text-[13.5px] font-semibold text-white">
                  Save as pipeline
                </h3>
                <p className="mt-0.5 text-[11.5px] text-slate-600">
                  Saving freezes your approved plan as a pipeline version. Running
                  it replays that saved definition exactly &mdash; no model is
                  called and the plan is never regenerated.
                </p>

                <label className="mt-3 block text-[12px] font-medium text-slate-400">
                  Pipeline name
                  <input
                    value={pipelineName}
                    onChange={(event) => setPipelineName(event.target.value)}
                    maxLength={160}
                    className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-[13px] text-slate-200"
                  />
                </label>

                <label className="mt-3 block text-[12px] font-medium text-slate-400">
                  Project
                  <select
                    value={projectId}
                    onChange={(event) => setProjectId(event.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-[13px] text-slate-200"
                  >
                    {projects.length === 0 && <option value="">No project</option>}
                    {projects.map((project) => (
                      <option key={project.project_id} value={project.project_id}>
                        {project.project_name}
                      </option>
                    ))}
                  </select>
                </label>

                <button
                  type="button"
                  onClick={compile}
                  disabled={
                    compiling ||
                    !projectId ||
                    pipelineName.trim().length === 0 ||
                    plan.accepted_count === 0
                  }
                  className="mt-4 w-full rounded-lg bg-cyan-600 px-4 py-2 text-[13px] font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-800 disabled:text-slate-500"
                >
                  {compiling ? "Saving…" : "Save pipeline version"}
                </button>

                {compiled && (
                  <div className="mt-3 rounded-lg border border-emerald-900/60 bg-emerald-950/30 px-3 py-2 text-[12px] text-emerald-300">
                    Saved as version {compiled.version_number} with{" "}
                    {compiled.tables.length} table(s),{" "}
                    {compiled.cleaning_steps} cleaning step(s) and{" "}
                    {compiled.quality_rules} quality rule(s), and{" "}
                    {compiled.filters} filter(s).
                    <p className="mt-1 text-[11.5px] text-emerald-400/80">
                      {compiled.note}
                    </p>
                  </div>
                )}
              </section>
            </aside>
          )}
        </div>
      )}
    </div>
  );
}
