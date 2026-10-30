"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  clonePipeline,
  comparePipelineVersions,
  deletePipeline,
  etlCopilotStatus,
  listPipelineRuns,
  listPipelines,
  listPipelineVersions,
  listVersionCleaningSteps,
  restorePipelineVersion,
  runPipeline,
  savePipeline,
  updatePipeline,
  type DataModelConnectionParams,
  type CleaningStep,
  type EtlCopilotBuildResult,
  type PipelineDefinition,
  type PipelineRun,
  type PipelineVersion,
  type SourceProfilingReport,
} from "@/services/projectService";
import { startEtlCopilotPolling } from "@/services/etlCopilotPolling.mjs";

interface PipelineManagementProps {
  projectId: string;
  connection: DataModelConnectionParams;
  userRequest: string;
  selectedTable: string;
  preview: {
    spec?: unknown;
    lineage?: unknown;
  } | null;
  sourceProfile: SourceProfilingReport | null;
  buildResult: EtlCopilotBuildResult | null;
  cleaningSteps: CleaningStep[];
  onOpenPipeline: (userRequest: string) => void;
  onPipelineSelected: (
    pipelineId: string | null,
    steps: CleaningStep[],
    versions: PipelineVersion[],
  ) => void;
  onRunCompleted: (result: EtlCopilotBuildResult) => void;
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function isEtlCopilotBuildResult(
  value: unknown,
): value is EtlCopilotBuildResult {
  const result = objectValue(value);
  return Boolean(
    result &&
      typeof result.project_id === "string" &&
      typeof result.file_id === "string" &&
      typeof result.target_column === "string" &&
      typeof result.rows === "number" &&
      typeof result.columns === "number" &&
      objectValue(result.data_quality) &&
      Array.isArray(result.column_profiles),
  );
}

export default function PipelineManagement({
  projectId,
  connection,
  userRequest,
  selectedTable,
  preview,
  sourceProfile,
  buildResult,
  onOpenPipeline,
  onPipelineSelected,
  cleaningSteps,
  onRunCompleted,
}: PipelineManagementProps) {
  const [pipelines, setPipelines] = useState<PipelineDefinition[]>([]);
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [selected, setSelected] = useState<PipelineDefinition | null>(null);
  const [versions, setVersions] = useState<PipelineVersion[]>([]);
  const [compareFrom, setCompareFrom] = useState<number | null>(null);
  const [compareChanges, setCompareChanges] = useState<
    Record<string, { before: unknown; after: unknown }> | null
  >(null);
  const [pipelineName, setPipelineName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const currentConfig = useMemo(() => {
    const spec = objectValue(preview?.spec);
    const lineage = objectValue(preview?.lineage);
    const specTables = spec?.tables;
    return {
      selected_tables:
        Array.isArray(specTables) && specTables.every((item) => typeof item === "string")
          ? specTables
          : selectedTable
            ? [selectedTable]
            : [],
      join_relationships: spec?.relationships ?? lineage?.join_paths ?? [],
      user_request: userRequest,
      profiling_configuration: sourceProfile
        ? {
            snapshot_id: sourceProfile.snapshot_id,
            analyzed_at: sourceProfile.analyzed_at,
            null_threshold_percentage:
              sourceProfile.summary.null_threshold_percentage,
            tables_profiled: sourceProfile.summary.tables_profiled,
          }
        : { enabled: false },
      generated_plan: preview?.spec ?? null,
      build_configuration: {
        target_column: buildResult?.target_column ?? null,
      },
      cleaning_steps: cleaningSteps.map((step, index) => ({
        step_name: step.step_name,
        step_type: step.step_type,
        configuration: step.configuration,
        enabled: step.enabled,
        step_order: index + 1,
      })),
    };
  }, [buildResult?.target_column, cleaningSteps, preview, selectedTable, sourceProfile, userRequest]);

  const refresh = useCallback(async () => {
    const [pipelineList, runList] = await Promise.all([
      listPipelines(projectId),
      listPipelineRuns(projectId),
    ]);
    setPipelines(pipelineList);
    setRuns(runList);
    setSelected((current) =>
      current ? pipelineList.find((item) => item.id === current.id) ?? null : null,
    );
  }, [projectId]);

  useEffect(() => {
    let active = true;
    Promise.all([listPipelines(projectId), listPipelineRuns(projectId)])
      .then(([pipelineList, runList]) => {
        if (!active) return;
        setPipelines(pipelineList);
        setRuns(runList);
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(
            loadError instanceof Error
              ? loadError.message
              : "Could not load pipeline history.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [projectId]);

  async function handleSave() {
    if (!pipelineName.trim() || !buildResult?.pipeline_run_id) return;
    setBusy(true);
    setError(null);
    try {
      const created = await savePipeline({
        projectId,
        pipelineName,
        description,
        pipelineJson: currentConfig,
        initialRunId: buildResult.pipeline_run_id,
      });
      setPipelineName("");
      setDescription("");
      await refresh();
      await selectPipeline(created);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Could not save pipeline.");
    } finally {
      setBusy(false);
    }
  }

  async function selectPipeline(pipeline: PipelineDefinition) {
    setError(null);
    setSelected(pipeline);
    setPipelineName(pipeline.pipeline_name);
    setDescription(pipeline.description ?? "");
    try {
      const history = await listPipelineVersions(pipeline.id);
      setVersions(history);
      setCompareFrom(history.length > 1 ? history[history.length - 2].version_number : null);
      setCompareChanges(null);
      const latest = history[history.length - 1];
      const savedSteps = latest
        ? await listVersionCleaningSteps(latest.id)
        : [];
      onPipelineSelected(pipeline.id, savedSteps, history);
      const savedRequest = latest?.pipeline_json.user_request;
      if (typeof savedRequest === "string") onOpenPipeline(savedRequest);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Could not load versions.");
    }
  }

  async function handleOpen(pipeline: PipelineDefinition) {
    try {
      await selectPipeline(pipeline);
    } catch (openError) {
      setError(openError instanceof Error ? openError.message : "Could not open pipeline.");
    }
  }

  async function handleSaveChanges() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const changed = await updatePipeline(selected.id, {
        pipelineName,
        description,
        pipelineJson: currentConfig,
      });
      await refresh();
      await selectPipeline(changed);
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "Could not update pipeline.");
    } finally {
      setBusy(false);
    }
  }

  async function handleClone(pipeline: PipelineDefinition) {
    setBusy(true);
    setError(null);
    try {
      const copy = await clonePipeline(pipeline.id);
      await refresh();
      await selectPipeline(copy);
    } catch (cloneError) {
      setError(cloneError instanceof Error ? cloneError.message : "Could not clone pipeline.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(pipeline: PipelineDefinition) {
    setBusy(true);
    setError(null);
    try {
      await deletePipeline(pipeline.id);
      await refresh();
      if (selected?.id === pipeline.id) {
        setSelected(null);
        setVersions([]);
        onPipelineSelected(null, [], []);
      }
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Could not delete pipeline.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCompare() {
    if (!selected || compareFrom === null || versions.length < 2) return;
    try {
      const result = await comparePipelineVersions(
        selected.id,
        compareFrom,
        versions[versions.length - 1].version_number,
      );
      setCompareChanges(result.changes);
    } catch (compareError) {
      setError(compareError instanceof Error ? compareError.message : "Could not compare versions.");
    }
  }

  async function handleRestore(version: PipelineVersion) {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const restored = await restorePipelineVersion(selected.id, version.id);
      await refresh();
      await selectPipeline(restored);
    } catch (restoreError) {
      setError(restoreError instanceof Error ? restoreError.message : "Could not restore version.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRun(pipeline: PipelineDefinition) {
    setBusy(true);
    setError(null);
    try {
      const job = await runPipeline(pipeline.id, connection);
      await new Promise<void>((resolve, reject) => {
        startEtlCopilotPolling({
          jobId: job.job_id,
          readStatus: etlCopilotStatus,
          onCompleted: (status) => {
            const result = status.result;
            if (!isEtlCopilotBuildResult(result)) {
              reject(new Error("Pipeline run completed without dataset details."));
              return;
            }
            onRunCompleted(result);
            resolve();
          },
          onFailed: (status) => {
            const failure = objectValue(status.error);
            reject(new Error(
              typeof failure?.detail === "string"
                ? failure.detail
                : "Pipeline run failed.",
            ));
          },
          onNotFound: () => reject(new Error("Pipeline run status was not found.")),
          onError: reject,
          onTimeout: () => reject(new Error("Pipeline run is still in progress.")),
        });
      });
      await refresh();
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : "Could not run pipeline.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-4 rounded-xl border border-white/[0.08] bg-black/20 p-4">
      <h3 className="text-sm font-semibold text-cyan-300">Pipeline Management</h3>
      {buildResult && (
        <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
          <input
            aria-label="Pipeline name"
            className="rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-white"
            placeholder="Pipeline name"
            value={pipelineName}
            onChange={(event) => setPipelineName(event.target.value)}
          />
          <input
            aria-label="Pipeline description"
            className="rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-white"
            placeholder="Description (optional)"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
          <button
            type="button"
            disabled={busy || !pipelineName.trim() || !buildResult.pipeline_run_id}
            onClick={handleSave}
            className="rounded-lg bg-cyan-700 px-3 py-2 text-sm text-white disabled:opacity-50"
          >
            Save Pipeline
          </button>
        </div>
      )}

      {error && <p role="alert" className="text-sm text-red-400">{error}</p>}

      <div>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
          Pipeline History
        </h4>
        {pipelines.length === 0 ? (
          <p className="text-sm text-slate-500">No saved pipelines for this project yet.</p>
        ) : (
          <div className="space-y-2">
            {pipelines.map((pipeline) => (
              <div key={pipeline.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-white/10 p-3">
                <button
                  type="button"
                  className="min-w-0 text-left"
                  onClick={() => void selectPipeline(pipeline)}
                >
                  <span className="block text-sm text-white">{pipeline.pipeline_name}</span>
                  <span className="text-xs text-slate-500">
                    {new Date(pipeline.updated_at).toLocaleString()} · version{" "}
                    {Math.max(0, ...pipeline.versions.map((version) => version.version_number))} · latest run{" "}
                    {pipeline.latest_run_status ?? "not run"}
                  </span>
                </button>
                <div className="flex gap-2">
                  <button type="button" disabled={busy} onClick={() => void handleOpen(pipeline)} className="text-xs text-cyan-300">Open</button>
                  <button type="button" disabled={busy} onClick={() => void handleRun(pipeline)} className="text-xs text-green-300">Re-Run</button>
                  <button type="button" disabled={busy} onClick={() => void handleClone(pipeline)} className="text-xs text-slate-300">Clone</button>
                  <button type="button" disabled={busy} onClick={() => void handleDelete(pipeline)} className="text-xs text-red-300">Delete</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {selected && (
        <div className="space-y-3 border-t border-white/10 pt-3">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Version History · {selected.pipeline_name}
          </h4>
          <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
            <input
              aria-label="Edit pipeline name"
              className="rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-white"
              value={pipelineName}
              onChange={(event) => setPipelineName(event.target.value)}
            />
            <input
              aria-label="Edit pipeline description"
              className="rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-white"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
            <button type="button" disabled={busy} onClick={handleSaveChanges} className="rounded-lg border border-cyan-500/30 px-3 py-2 text-sm text-cyan-200">
              Save Changes / Version
            </button>
          </div>
          <div className="space-y-2">
            {versions.map((version) => (
              <div key={version.id} className="flex items-center justify-between rounded-lg border border-white/10 p-2 text-sm">
                <span className="text-slate-200">
                  Version {version.version_number} · {new Date(version.created_at).toLocaleString()}
                </span>
                <button type="button" disabled={busy || version.version_number === versions[versions.length - 1]?.version_number} onClick={() => void handleRestore(version)} className="text-xs text-cyan-300">
                  Restore as new version
                </button>
              </div>
            ))}
          </div>
          {versions.length > 1 && (
            <div className="flex flex-wrap items-center gap-2">
              <select
                aria-label="Compare from version"
                className="rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-white"
                value={compareFrom ?? ""}
                onChange={(event) => setCompareFrom(Number(event.target.value))}
              >
                {versions.slice(0, -1).map((version) => (
                  <option key={version.id} value={version.version_number}>
                    Version {version.version_number}
                  </option>
                ))}
              </select>
              <button type="button" onClick={() => void handleCompare()} className="rounded-lg border border-white/10 px-3 py-2 text-sm text-slate-200">
                Compare with latest
              </button>
            </div>
          )}
          {compareChanges && (
            <pre className="max-h-56 overflow-auto rounded-lg bg-black/30 p-3 text-xs text-slate-300">
              {JSON.stringify(compareChanges, null, 2)}
            </pre>
          )}
        </div>
      )}

      <div className="border-t border-white/10 pt-3">
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Run History</h4>
        {runs.length === 0 ? (
          <p className="text-sm text-slate-500">No dataset builds have been recorded yet.</p>
        ) : (
          <div className="space-y-2">
            {runs.map((run) => (
              <div key={run.id} className="flex flex-wrap justify-between gap-2 rounded-lg border border-white/10 p-2 text-xs text-slate-300">
                <span>{run.started_at ? new Date(run.started_at).toLocaleString() : "Start time unavailable"} · {run.status}</span>
                <span>
                  {run.row_count ?? "—"} rows · {run.execution_time == null ? "—" : `${run.execution_time.toFixed(2)}s`}
                  {run.dataset_file_id ? ` · ${run.dataset_file_id}` : ""}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
