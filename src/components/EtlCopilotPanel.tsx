"use client";

import { useEffect, useRef, useState } from "react";

import {
  etlCopilotSchema,
  etlCopilotPreview,
  etlCopilotBuild,
  etlCopilotStatus,
  analyzeSourceProfile,
  type DataModelConnectionParams,
  type EtlCopilotSchemaResponse,
  type EtlCopilotPreviewResponse,
  type EtlCopilotBuildResult,
  type CleaningStep,
  type PipelineVersion,
  type SourceProfilingReport as SourceProfilingReportData,
} from "@/services/projectService";
import {
  createBuildGuard,
  startEtlCopilotPolling,
} from "@/services/etlCopilotPolling.mjs";
import DatabaseExplorer, {
  type ExplorerRelationship,
  type ExplorerTable,
} from "@/components/etl-copilot/DatabaseExplorer";
import ExplainabilityCard from "@/components/etl-copilot/ExplainabilityCard";
import JoinPreview from "@/components/etl-copilot/JoinPreview";
import LineageGraph, {
  type PreviewPlanSpec,
} from "@/components/etl-copilot/LineageGraph";
import TablePreview from "@/components/etl-copilot/TablePreview";
import DatasetReadyCard from "@/components/etl-copilot/DatasetReadyCard";
import ReconciliationReport, {
  type ReconciliationData,
} from "@/components/etl-copilot/ReconciliationReport";
import SavedRecipes, {
  type SavedRecipe,
} from "@/components/etl-copilot/SavedRecipes";
import DataQualityAnalysis from "@/components/etl-copilot/DataQualityAnalysis";
import ColumnProfiling from "@/components/etl-copilot/ColumnProfiling";
import SourceProfilingReport from "@/components/etl-copilot/SourceProfilingReport";
import PipelineManagement from "@/components/etl-copilot/PipelineManagement";
import CleaningPipeline from "@/components/etl-copilot/CleaningPipeline";

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

const INPUT_CLS =
  "w-full rounded-xl border border-white/[0.08] bg-white/[0.04] px-3 py-2 text-[13px] text-slate-100 outline-none transition-all focus:border-cyan-400/40 focus:bg-white/[0.06] focus:ring-2 focus:ring-cyan-400/20";

const LABEL_CLS = "mb-2 block text-[13px] font-semibold text-slate-300";

const PANEL_CLS = "rounded-xl border border-white/[0.08] bg-white/[0.02] p-4";

function formatBuildJobError(detail: unknown) {
  if (typeof detail === "string" && detail) return detail;
  if (!detail || typeof detail !== "object") return "Dataset build failed.";

  const error = detail as {
    message?: unknown;
    error?: unknown;
    hint?: unknown;
    issues?: { message?: unknown }[];
  };
  const parts = [error.message, error.error, error.hint]
    .filter(Boolean)
    .map(String);
  if (Array.isArray(error.issues)) {
    parts.push(...error.issues.map((issue) => String(issue?.message ?? "")).filter(Boolean));
  }
  return parts.length ? parts.join(" - ") : "Dataset build failed.";
}

export interface EtlCopilotPanelProps extends DataModelConnectionParams {
  availableTables: string[];

  onDatasetReady: (
    fileId: string,
    projectId: string,
    targetColumn: string,
  ) => void;

  onReset: () => void;
}

export default function EtlCopilotPanel({
  dbType,
  host,
  port,
  database,
  username,
  password,
  schemaName,
  availableTables,
  onDatasetReady,
  onReset,
}: EtlCopilotPanelProps) {
  const [loadingState, setLoadingState] = useState<
    "idle" | "schema" | "previewing" | "building" | "polling"
  >("idle");
  const [buildJobId, setBuildJobId] = useState<string | null>(null);
  const [buildGuard] = useState(createBuildGuard);
  const isMountedRef = useRef(false);
  const cancelBuildPollingRef = useRef<(() => void) | null>(null);

  const [userRequest, setUserRequest] = useState("");

  const [schema, setSchema] = useState<EtlCopilotSchemaResponse | null>(null);
  const [selectedTableName, setSelectedTableName] = useState("");
  const [tableSampleRows, setTableSampleRows] = useState<
    Record<string, unknown>[]
  >([]);
  const [tableSampleColumns, setTableSampleColumns] = useState<string[]>([]);
  const [isLoadingTableSample, setIsLoadingTableSample] = useState(false);
  const [tableSampleError, setTableSampleError] = useState<string | null>(null);

  const [preview, setPreview] = useState<EtlCopilotPreviewResponse | null>(
    null,
  );
  const [history, setHistory] = useState<SavedRecipe[]>([]);
  const [datasetSummary, setDatasetSummary] = useState<{
    rows?: number;
    columns?: number;
    fileId: string;
    projectId: string;
    projectName?: string;
  } | null>(null);
  const [pipelineProjectId, setPipelineProjectId] = useState<string | null>(null);
  const [cleaningSteps, setCleaningSteps] = useState<CleaningStep[]>([]);
  const [selectedPipelineId, setSelectedPipelineId] = useState<string | null>(null);
  const [pipelineVersions, setPipelineVersions] = useState<PipelineVersion[]>([]);
  const [buildReconciliation, setBuildReconciliation] =
    useState<ReconciliationData | null>(null);
  const [buildProfile, setBuildProfile] = useState<EtlCopilotBuildResult | null>(
    null,
  );
  const [sourceProfile, setSourceProfile] =
    useState<SourceProfilingReportData | null>(null);
  const [isProfiling, setIsProfiling] = useState(false);
  const [profilingError, setProfilingError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [copiedSql, setCopiedSql] = useState(false);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      cancelBuildPollingRef.current?.();
      cancelBuildPollingRef.current = null;
      buildGuard.finish();
    };
  }, [buildGuard]);

  useEffect(() => {
    let isMounted = true;

    async function loadSchema() {
      try {
        setLoadingState("schema");

        const result = await etlCopilotSchema({
          dbType,
          host,
          port,
          database,
          username,
          password, 
        });

        if (!isMounted) return;

        setSchema(result);
        setError(null);
        setLoadingState("idle");
      } catch (err) {
        if (!isMounted) return;

        setError(err instanceof Error ? err.message : "Could not load schema.");

        setLoadingState("idle");
      }
    }

    void loadSchema();

    return () => {
      isMounted = false;
    };
  }, [dbType, host, port, database, username, password]);
    
    useEffect(() => {
      const saved = localStorage.getItem("pyrunai_etl_history");

      if (saved) {
        setHistory(JSON.parse(saved) as SavedRecipe[]);
      }
    }, []);
    

    function handleRestoreHistory(item: SavedRecipe) {
      setUserRequest(item.prompt);
      setPreview(item.preview ?? null);
      setBuildReconciliation(null);
    }

    function handleClearHistory() {
      setHistory([]);
      localStorage.removeItem("pyrunai_etl_history");
    }

  async function handleGeneratePlan() {
    try {
      setError(null);
      setPreview(null);
      setBuildReconciliation(null);
      setLoadingState("previewing");

      const result = await etlCopilotPreview({
        dbType,
        host,
        port,
        database,
        username,   
        password,
        userRequest,
      });

      setPreview(result);
      const newHistory = [
        {
          prompt: userRequest,
          preview: result,
          timestamp: Date.now(),
        },
        ...history,
      ].slice(0, 10);

      setHistory(newHistory);

      localStorage.setItem("pyrunai_etl_history", JSON.stringify(newHistory));
      setLoadingState("idle");
    } catch (err) {
      setLoadingState("idle");

      setError(
        err instanceof Error ? err.message : "Preview generation failed",
      );
    }
  }

  async function handleProfileSelectedTable() {
    if (!selectedTableForPreview || isProfiling) return;
    setIsProfiling(true);
    setProfilingError(null);
    setSourceProfile(null);
    try {
      const result = await analyzeSourceProfile(
        {
          dbType,
          host,
          port,
          database,
          username,
          password,
          schemaName,
        },
        [selectedTableForPreview],
      );
      setSourceProfile(result);
    } catch (err) {
      setProfilingError(
        err instanceof Error ? err.message : "Source profiling failed.",
      );
    } finally {
      setIsProfiling(false);
    }
  }

  void onReset;

  function finishBuild(message?: string) {
    if (!isMountedRef.current) return;
    cancelBuildPollingRef.current = null;
    buildGuard.finish();
    setLoadingState("idle");
    if (message) setError(message);
  }

  async function handleBuildDataset() {
    if (!buildGuard.tryStart()) return;

    try {
      setError(null);
      setDatasetSummary(null);
      setBuildProfile(null);
      setBuildReconciliation(null);
      setLoadingState("building");

      const result = await etlCopilotBuild({
        dbType,
        host,
        port,
        database,
        username,
        password,
        projectName: `ETL_${Date.now()}`,
        userRequest,
        cleaningSteps,
      });

      if (!isMountedRef.current) {
        buildGuard.finish();
        return;
      }
      if (!result.job_id) {
        throw new Error("Build job did not return a job ID.");
      }

      setBuildJobId(result.job_id);
      setLoadingState("polling");
      cancelBuildPollingRef.current = startEtlCopilotPolling({
        jobId: result.job_id,
        readStatus: etlCopilotStatus,
        onCompleted: (job) => {
          if (!isMountedRef.current) return;
          const completedResult = job.result as EtlCopilotBuildResult | undefined;
          if (
            !completedResult?.file_id ||
            !completedResult.project_id ||
            !completedResult.target_column
          ) {
            finishBuild("Completed build did not return project details.");
            return;
          }

          try {
            setBuildProfile(completedResult);
            setDatasetSummary({
              rows: completedResult.rows,
              columns: completedResult.columns,
              fileId: completedResult.file_id,
              projectId: completedResult.project_id,
              projectName: completedResult.project_name,
            });
            setPipelineProjectId((current) => current ?? completedResult.project_id);
            setBuildReconciliation(completedResult.reconciliation ?? null);
            onDatasetReady(
              completedResult.file_id,
              completedResult.project_id,
              completedResult.target_column,
            );
            finishBuild();
          } catch (err) {
            finishBuild(
              err instanceof Error ? err.message : "Dataset generation failed",
            );
          }
        },
        onFailed: (job) => {
          const error = job.error as { detail?: unknown } | undefined;
          finishBuild(formatBuildJobError(error?.detail ?? job.error));
        },
        onNotFound: () => {
          finishBuild(
            `Build job ${result.job_id} was not found. Completion could not be confirmed.`,
          );
        },
        onError: (err) => {
          finishBuild(
            err instanceof Error
              ? `Could not confirm the build status: ${err.message}`
              : "Could not confirm the build status.",
          );
        },
        onTimeout: () => {
          finishBuild(
            `Build ${result.job_id} is still running, but its status could not be confirmed within 90 seconds.`,
          );
        },
      });
    } catch (err) {
      finishBuild(
        err instanceof Error ? err.message : "Dataset generation failed",
      );
    }
  }

  async function handleCopySql() {
    if (!preview?.sql) return;

    try {
      await navigator.clipboard.writeText(preview.sql);
      setCopiedSql(true);
      window.setTimeout(() => setCopiedSql(false), 2000);
    } catch {
      setCopiedSql(false);
    }
  }

  const catalog = schema as
    | (EtlCopilotSchemaResponse & {
        tables?: Record<string, string[]>;
        relationships?: ExplorerRelationship[];
      })
    | null;
  const tables: ExplorerTable[] = catalog?.tables
    ? Object.entries(catalog.tables).map(([name, columns]) => ({
        name,
        columns,
      }))
    : availableTables.map((name) => ({ name, columns: [] }));
  const selectedTable =
    tables.find((table) => table.name === selectedTableName) ??
    tables[0] ??
    null;
  const selectedTableForPreview = selectedTable?.name ?? "";
  const planSpec = (preview?.spec as PreviewPlanSpec | undefined) ?? null;
  const previewReconciliation = preview?.reconciliation;

  useEffect(() => {
    if (!selectedTableForPreview) {
      setTableSampleRows([]);
      setTableSampleColumns([]);
      setTableSampleError(null);
      setIsLoadingTableSample(false);
      return;
    }

    const controller = new AbortController();
    setTableSampleRows([]);
    setTableSampleColumns([]);
    setTableSampleError(null);
    setIsLoadingTableSample(true);

    const quotedTable =
      dbType === "mssql"
        ? `[${selectedTableForPreview.replace(/\]/g, "]]")}]`
        : `"${selectedTableForPreview.replace(/"/g, '""')}"`;

    async function loadTableSample() {
      try {
        const response = await fetch(`${API_BASE}/datasource/preview`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            db_type: dbType,
            host,
            port: Number(port),
            database,
            username,
            password,
            query: `SELECT * FROM ${quotedTable}`,
          }),
        });
        const payload = (await response.json()) as {
          sample_rows?: Record<string, unknown>[];
          column_names?: string[];
          detail?: string;
        };

        if (!response.ok) {
          throw new Error(payload.detail || "Could not load table sample.");
        }

        setTableSampleRows(
          Array.isArray(payload.sample_rows) ? payload.sample_rows : [],
        );
        setTableSampleColumns(
          Array.isArray(payload.column_names) ? payload.column_names : [],
        );
      } catch (err) {
        if (controller.signal.aborted) return;
        setTableSampleError(
          err instanceof Error ? err.message : "Could not load table sample.",
        );
      } finally {
        if (!controller.signal.aborted) setIsLoadingTableSample(false);
      }
    }

    void loadTableSample();
    return () => controller.abort();
  }, [selectedTableForPreview, dbType, host, port, database, username, password]);

  return (
    <div className="w-full space-y-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-lg font-semibold text-white">ETL Copilot</h3>

        <p className="mt-2 text-sm text-slate-400">
          Describe the dataset you want in plain English.
        </p>

        {loadingState === "schema" && (
          <p className="mt-3 text-sm text-cyan-400">Loading schema...</p>
        )}

        {error && <p className="mt-3 text-sm text-red-400">{error}</p>}

      </div>

      {schema && (
        <>
          <DatabaseExplorer
            tables={tables}
            relationships={catalog?.relationships ?? []}
            tableCount={schema.table_count}
            relationshipCount={schema.relationship_count}
            selectedTable={selectedTable?.name ?? ""}
            onSelectTable={(tableName) => {
              setSelectedTableName(tableName);
              setSourceProfile(null);
              setProfilingError(null);
            }}
          />
          <TablePreview
            table={selectedTable}
            sampleRows={tableSampleRows}
            sampleColumns={tableSampleColumns}
            isLoading={isLoadingTableSample}
            error={tableSampleError}
          />
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={!selectedTableForPreview || isProfiling}
              onClick={handleProfileSelectedTable}
              className="rounded-xl border border-cyan-400/30 bg-cyan-500/10 px-4 py-2 text-sm font-medium text-cyan-200 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isProfiling ? "Profiling source..." : "Profile selected table"}
            </button>
            {profilingError && (
              <p role="alert" className="text-sm text-red-400">
                {profilingError}
              </p>
            )}
          </div>
          {sourceProfile && <SourceProfilingReport report={sourceProfile} />}
        </>
      )}

      <CleaningPipeline
        connection={{
          dbType,
          host,
          port,
          database,
          username,
          password,
          schemaName,
        }}
        userRequest={userRequest}
        columns={
          (catalog?.tables?.[selectedTableForPreview] ?? tableSampleColumns)
        }
        steps={cleaningSteps}
        onStepsChange={setCleaningSteps}
        pipelineId={selectedPipelineId}
        versions={pipelineVersions}
        onVersionsChange={setPipelineVersions}
        onCleaningStepsSaved={(steps, versions) => {
          setCleaningSteps(steps);
          setPipelineVersions(versions);
        }}
      />

      {datasetSummary && (
        <DatasetReadyCard
          rows={datasetSummary.rows}
          columns={datasetSummary.columns}
          fileId={datasetSummary.fileId}
          projectId={datasetSummary.projectId}
          projectName={datasetSummary.projectName}
        />
      )}

      {buildProfile && (
        <>
          <DataQualityAnalysis metrics={buildProfile.data_quality} />
          <ColumnProfiling columns={buildProfile.column_profiles} />
        </>
      )}

      <SavedRecipes
        recipes={history}
        onSelect={handleRestoreHistory}
        onClear={handleClearHistory}
      />

      {pipelineProjectId && (
        <PipelineManagement
          projectId={pipelineProjectId}
          connection={{
            dbType,
            host,
            port,
            database,
            username,
            password,
            schemaName,
          }}
          userRequest={userRequest}
          selectedTable={selectedTableForPreview}
          preview={preview}
          sourceProfile={sourceProfile}
          buildResult={buildProfile}
          cleaningSteps={cleaningSteps}
          onOpenPipeline={(savedRequest) => {
            setUserRequest(savedRequest);
            setPreview(null);
            setBuildReconciliation(null);
          }}
          onPipelineSelected={(pipelineId, steps, versions) => {
            setSelectedPipelineId(pipelineId);
            setCleaningSteps(steps);
            setPipelineVersions(versions);
          }}
          onRunCompleted={(completedResult) => {
            setBuildProfile(completedResult);
            setDatasetSummary({
              rows: completedResult.rows,
              columns: completedResult.columns,
              fileId: completedResult.file_id,
              projectId: completedResult.project_id,
              projectName: completedResult.project_name,
            });
            onDatasetReady(
              completedResult.file_id,
              completedResult.project_id,
              completedResult.target_column,
            );
          }}
        />
      )}

      <div className="mb-3 flex items-center justify-between">
        <label className={LABEL_CLS}>Dataset Request</label>

        <textarea
          className={INPUT_CLS}
          rows={5}
          value={userRequest}
          onChange={(e) => setUserRequest(e.target.value)}
          placeholder="Show customer name, city, total sales and order count"
        />

        <div className="mt-4">
          <button
            type="button"
            disabled={false}
            onClick={handleGeneratePlan}
            className="rounded-xl bg-cyan-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {loadingState === "previewing" ? "Generating..." : "Generate Plan"}
          </button>
          <div className="mt-3">
            <button
              type="button"
              disabled={
                !preview ||
                loadingState === "building" ||
                loadingState === "polling"
              }
              onClick={handleBuildDataset}
              className="rounded-xl bg-green-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {loadingState === "building"
                ? "Starting build..."
                : loadingState === "polling"
                  ? "Building dataset..."
                  : "Build Dataset"}
            </button>
            {loadingState === "polling" && buildJobId && (
              <p className="mt-2 text-xs text-slate-400">
                Build job: {buildJobId}
              </p>
            )}
          </div>
        </div>
      </div>

      {preview && (
        <div className="w-full space-y-4">
          <div className="mb-3 flex items-center justify-between">
            <div className="mb-3 text-sm font-semibold text-cyan-400">
              ETL Preview
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="rounded-lg bg-black/20 p-3">
                <div className="text-xs text-slate-500">Blocking</div>
                <div
                  className={
                    preview.blocking
                      ? "font-medium text-red-400"
                      : "font-medium text-green-400"
                  }
                >
                  {preview.blocking ? "Yes" : "No"}
                </div>
              </div>

              <div className="rounded-lg bg-black/20 p-3">
                <div className="text-xs text-slate-500">Output Columns</div>
                <div className="font-medium text-white">
                  {preview.columns.length}
                </div>
              </div>
            </div>
          </div>

          <ReconciliationReport
            reconciliation={buildReconciliation ?? previewReconciliation}
            mode={buildReconciliation ? "build" : "preview"}
          />

          <div className="grid gap-4 md:grid-cols-2">
            <ExplainabilityCard
              spec={planSpec}
              explanation={preview.explanation}
            />
            <JoinPreview
              paths={planSpec?.join_paths ?? []}
            />
          </div>

          <LineageGraph
            spec={planSpec}
            lineage={preview.lineage}
            selectedTable={selectedTable?.name ?? ""}
            finalColumns={preview.columns}
          />

          <div className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
            <h3 className="mb-3 text-lg font-semibold text-cyan-400">
              Output Columns
            </h3>

            <div className="flex flex-wrap gap-2">
              {preview.columns.map((column, index) => (
                <span
                  key={`${column}-${index}`}
                  className="rounded-full border border-cyan-500/20 bg-cyan-500/10 px-3 py-1.5 text-sm text-cyan-300"
                >
                  {column}
                </span>
              ))}
            </div>
          </div>

          {Array.isArray(preview.warnings) && preview.warnings.length > 0 && (
            <div className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
              <h3 className="mb-3 text-lg font-semibold text-yellow-400">
                Warnings
              </h3>

              <div className="space-y-2">
                {(preview.warnings as Array<{ message?: string }>).map(
                  (warning, index) => (
                    <div
                      key={index}
                      className="rounded-lg border border-yellow-500/20 bg-yellow-500/5 p-3 text-sm text-yellow-300"
                    >
                      {warning?.message ?? "Warning"}
                    </div>
                  ),
                )}
              </div>
            </div>
          )}

          <div className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h3 className="text-lg font-semibold text-cyan-400">
                Generated SQL
              </h3>

              <button
                type="button"
                onClick={handleCopySql}
                className="rounded-lg border border-cyan-500/30 bg-cyan-500/10 px-3 py-1.5 text-xs font-medium text-cyan-300 transition hover:bg-cyan-500/20"
              >
                {copiedSql ? "Copied!" : "Copy SQL"}
              </button>
            </div>

            <pre className="min-h-[250px] overflow-auto whitespace-pre-wrap rounded-lg bg-black/30 p-4 text-xs text-slate-300">
              {preview.sql}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
