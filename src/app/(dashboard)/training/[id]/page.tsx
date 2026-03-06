"use client";

import Navbar from "@/components/Navbar";
import Link from "next/link";
import { use, useState, useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { extractAgentId, getTrainingStatus, startTraining } from "@/services/trainingService";
import { getProjectMetaByFileId } from "@/services/projectMetaService";

type StepStatus = "pending" | "running" | "completed" | "failed";

interface PipelineStep {
  id: string;
  label: string;
  description: string;
  status: StepStatus;
  error?: string;
}

const initialSteps: PipelineStep[] = [
  { id: "analysis", label: "Dataset Analysis", description: "Detecting columns, types, and missing values", status: "running" },
  { id: "planning", label: "Model Planning", description: "Selecting candidate algorithms based on your dataset", status: "pending" },
  { id: "codegen", label: "Code Generation", description: "Generating training pipelines for each model", status: "pending" },
  { id: "training", label: "Training Models", description: "Fitting models with cross-validation", status: "pending" },
  { id: "evaluation", label: "Evaluation", description: "Comparing metrics and selecting the best model", status: "pending" },
  { id: "selecting", label: "Selecting Best Model", description: "Ranking models and finalizing deployment package", status: "pending" },
];

const statusConfig: Record<StepStatus, { label: string; bg: string; text: string; dot: string }> = {
  pending: { label: "Pending", bg: "bg-[#123C66]/90 border border-[#1e3a52]", text: "text-slate-400", dot: "bg-slate-500" },
  running: { label: "Running", bg: "bg-[#3BB273]/12 border border-[#3BB273]/40", text: "text-[#5EDC8A]", dot: "bg-[#5EDC8A]" },
  completed: { label: "Completed", bg: "bg-[#3BB273]/12 border border-[#3BB273]/35", text: "text-[#5EDC8A]", dot: "bg-[#5EDC8A]" },
  failed: { label: "Failed", bg: "bg-rose-500/12 border border-rose-400/35", text: "text-rose-300", dot: "bg-rose-300" },
};

const llmModelOptions: Record<"auto" | "groq" | "openai" | "gemini", string[]> = {
  auto: ["auto"],
  groq: ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
  openai: ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1"],
  gemini: ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-pro-latest"],
};

function mapBackendStageToSteps(stage: string, status: string, error?: string | null): PipelineStep[] {
  const nextSteps: PipelineStep[] = initialSteps.map((step): PipelineStep => ({
    ...step,
    status: "pending",
    error: undefined,
  }));

  const setStatus = (stepId: string, stepStatus: StepStatus, stepError?: string) => {
    const idx = nextSteps.findIndex((step) => step.id === stepId);
    if (idx >= 0) {
      nextSteps[idx] = { ...nextSteps[idx], status: stepStatus, error: stepError };
    }
  };

  if (status === "failed") {
    const failedStep =
      stage === "training_models" ? "training" :
      stage === "evaluating_models" ? "evaluation" :
      stage === "ranking_models" ? "selecting" :
      stage === "preprocessing" ? "planning" :
      "analysis";

    for (const step of nextSteps) {
      if (step.id === failedStep) {
        setStatus(step.id, "failed", error ?? "Training failed.");
        break;
      }
      setStatus(step.id, "completed");
    }
    return nextSteps;
  }

  if (status === "completed" || stage === "completed") {
    return nextSteps.map((step) => ({ ...step, status: "completed" as StepStatus, error: undefined }));
  }

  if (stage === "analyzing_dataset") {
    setStatus("analysis", "running");
    return nextSteps;
  }

  if (stage === "preprocessing") {
    setStatus("analysis", "completed");
    setStatus("planning", "running");
    return nextSteps;
  }

  if (stage === "training_models") {
    setStatus("analysis", "completed");
    setStatus("planning", "completed");
    setStatus("codegen", "completed");
    setStatus("training", "running");
    return nextSteps;
  }

  if (stage === "evaluating_models") {
    setStatus("analysis", "completed");
    setStatus("planning", "completed");
    setStatus("codegen", "completed");
    setStatus("training", "completed");
    setStatus("evaluation", "running");
    return nextSteps;
  }

  if (stage === "ranking_models") {
    setStatus("analysis", "completed");
    setStatus("planning", "completed");
    setStatus("codegen", "completed");
    setStatus("training", "completed");
    setStatus("evaluation", "completed");
    setStatus("selecting", "running");
    return nextSteps;
  }

  return nextSteps;
}

export default function TrainingPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const searchParams = useSearchParams();
  const targetColumn = searchParams.get("target_column") ?? "churn";
  const [steps, setSteps] = useState<PipelineStep[]>(initialSteps);
  const [logs, setLogs] = useState<string[]>([]);
  const [progress, setProgress] = useState(0);
  const [expandedErrorId, setExpandedErrorId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [isStartingTraining, setIsStartingTraining] = useState(false);
  const [, setIsPollingStatus] = useState(false);
  const [selectedLlmProvider, setSelectedLlmProvider] = useState<"auto" | "groq" | "openai" | "gemini">("openai");
  const [selectedLlmModel, setSelectedLlmModel] = useState<string>("gpt-4o-mini");
  const [projectName, setProjectName] = useState<string>("Untitled Project");
  const [projectRows, setProjectRows] = useState<number | null>(null);
  const [projectTargetColumn, setProjectTargetColumn] = useState<string | null>(null);
  const [modelsInProgress, setModelsInProgress] = useState<Array<{
    name: string;
    status: "pending" | "queued" | "training" | "completed" | "failed";
    rmse: number | null;
    job_id?: string | null;
    error?: string | null;
  }>>([]);
  const logsContainerRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  useEffect(() => {
    let cancelled = false;

    const loadProjectMeta = async () => {
      try {
        const meta = await getProjectMetaByFileId(id);
        if (cancelled) {
          return;
        }
        setProjectName(meta.project_name || "Untitled Project");
        setProjectRows(typeof meta.num_rows === "number" ? meta.num_rows : null);
        setProjectTargetColumn(meta.target_column || null);
      } catch {
        if (!cancelled) {
          setProjectName("Untitled Project");
          setProjectRows(null);
          setProjectTargetColumn(null);
        }
      }
    };

    void loadProjectMeta();

    return () => {
      cancelled = true;
    };
  }, [id]);

  const startTrainingJob = async () => {
    if (!id || jobId || isStartingTraining) {
      return;
    }

    const fileId = id;
    setIsStartingTraining(true);
    setLogs(["Starting training job..."]);
    try {
      const response = await startTraining(fileId, targetColumn, {
        llmProvider: selectedLlmProvider,
        llmModel: selectedLlmProvider === "auto" ? undefined : selectedLlmModel,
      });
      const nextJobId = extractAgentId(response.agent_run_id ?? response.job_id);
      if (!nextJobId) {
        throw new Error("Training started but no valid agent id was returned.");
      }

      setJobId(nextJobId);
      setLogs((prev) => [
        ...prev,
        `Training job started: ${nextJobId}`,
        `LLM provider: ${selectedLlmProvider}`,
        `LLM model: ${selectedLlmProvider === "auto" ? "auto" : selectedLlmModel}`,
      ]);
    } catch (error) {
      setLogs((prev) => [...prev, `Failed to start training: ${error instanceof Error ? error.message : "Unknown error"}`]);
    } finally {
      setIsStartingTraining(false);
    }
  };

  useEffect(() => {
    if (!jobId) {
      return;
    }

    const canonicalJobId = extractAgentId(jobId);
    if (!canonicalJobId) {
      setLogs((prev) => {
        const next = `Ignored invalid training run id: ${jobId}`;
        if (prev[prev.length - 1] === next) {
          return prev;
        }
        return [...prev, next];
      });
      setIsPollingStatus(false);
      return;
    }

    let isActive = true;
    setIsPollingStatus(true);

    const pollStatus = async () => {
      try {
        const statusResponse = await getTrainingStatus(canonicalJobId);

        if (!isActive) {
          return;
        }

        setProgress(typeof statusResponse.progress === "number" ? statusResponse.progress : 0);
        setSteps(mapBackendStageToSteps(statusResponse.stage, statusResponse.status, statusResponse.error));

        if (Array.isArray(statusResponse.logs) && statusResponse.logs.length > 0) {
          setLogs(statusResponse.logs);
        }
        
        // Update models in progress for parallel execution display
        if (Array.isArray(statusResponse.models_in_progress) && statusResponse.models_in_progress.length > 0) {
          setModelsInProgress(statusResponse.models_in_progress);
        } else {
          setModelsInProgress([]);
        }

        if (statusResponse.status === "failed") {
          const failedStep =
            statusResponse.stage === "training_models" ? "training" :
            statusResponse.stage === "evaluating_models" ? "evaluation" :
            statusResponse.stage === "ranking_models" ? "selecting" :
            statusResponse.stage === "preprocessing" ? "planning" :
            "analysis";
          setExpandedErrorId(failedStep);
        }

        if (statusResponse.status === "completed" || statusResponse.status === "failed") {
          setIsPollingStatus(false);
          return;
        }
      } catch (error) {
        if (!isActive) {
          return;
        }
        console.error("Failed to fetch training status:", error);
        setLogs((prev) => {
          const next = `Failed to fetch training status: ${error instanceof Error ? error.message : "Unknown error"}`;
          if (prev[prev.length - 1] === next) {
            return prev;
          }
          return [...prev, next];
        });
      }
    };

    void pollStatus();
    const intervalId = setInterval(() => {
      if (isActive) {
        void pollStatus();
      }
    }, 1500);

    return () => {
      isActive = false;
      setIsPollingStatus(false);
      clearInterval(intervalId);
    };
  }, [jobId]);

  const completedCount = steps.filter((s) => s.status === "completed").length;
  const totalSteps = steps.length;

  // Auto-scroll logs to bottom when new entries arrive
  useEffect(() => {
    if (autoScroll && logsContainerRef.current) {
      const container = logsContainerRef.current;
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
    }
  }, [logs, autoScroll]);

  // Helper to determine log entry style based on content
  const getLogStyle = (log: string): { textClass: string; icon?: string; bgClass?: string } => {
    const lower = log.toLowerCase();
    if (lower.includes("error") || lower.includes("failed") || lower.includes("failure")) {
      return { textClass: "text-rose-300", icon: "✗", bgClass: "bg-rose-500/10" };
    }
    if (lower.includes("completed") || lower.includes("success") || lower.includes("finished")) {
      return { textClass: "text-[#5EDC8A]", icon: "✓", bgClass: "bg-[#3BB273]/10" };
    }
    if (lower.includes("queued") || lower.includes("starting") || lower.includes("started")) {
      return { textClass: "text-[#7ec8ff]", icon: "→", bgClass: "bg-[#3b82f6]/10" };
    }
    if (lower.includes("training") || lower.includes("running") || lower.includes("executing")) {
      return { textClass: "text-amber-300", icon: "◐", bgClass: "bg-amber-500/10" };
    }
    if (lower.includes("iteration") || lower.includes("stage")) {
      return { textClass: "text-purple-300", icon: "●" };
    }
    return { textClass: "text-slate-400" };
  };

  return (
    <>
      <Navbar title="Training Status" />

      <main className="flex-1 px-8 py-8 overflow-auto">
        <div className="max-w-3xl mx-auto space-y-6.5">
          {/* Project header */}
          <div className="bg-slate-900/70 backdrop-blur-sm rounded-2xl border border-slate-800/85 shadow-[0_0_30px_rgba(15,23,42,0.6)] p-6">
            <div className="mb-4 rounded-xl border border-[#1e3a52] bg-[#0B1F3A]/55 p-4">
              <p className="text-xs uppercase tracking-wide text-slate-400 mb-2">LLM Settings</p>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <label className="text-xs text-slate-300 flex flex-col gap-1">
                  Provider
                  <select
                    className="rounded-lg border border-[#1e3a52] bg-[#0F172A] px-3 py-2 text-sm text-slate-100"
                    value={selectedLlmProvider}
                    disabled={Boolean(jobId) || isStartingTraining}
                    onChange={(e) => {
                      const nextProvider = e.target.value as "auto" | "groq" | "openai" | "gemini";
                      setSelectedLlmProvider(nextProvider);
                      setSelectedLlmModel(llmModelOptions[nextProvider][0]);
                    }}
                  >
                    <option value="auto">Auto</option>
                    <option value="openai">OpenAI</option>
                    <option value="groq">Groq</option>
                    <option value="gemini">Gemini</option>
                  </select>
                </label>

                <label className="text-xs text-slate-300 flex flex-col gap-1 sm:col-span-2">
                  Model
                  <select
                    className="rounded-lg border border-[#1e3a52] bg-[#0F172A] px-3 py-2 text-sm text-slate-100"
                    value={selectedLlmProvider === "auto" ? "auto" : selectedLlmModel}
                    disabled={Boolean(jobId) || isStartingTraining || selectedLlmProvider === "auto"}
                    onChange={(e) => setSelectedLlmModel(e.target.value)}
                  >
                    {llmModelOptions[selectedLlmProvider].map((model) => (
                      <option key={model} value={model}>
                        {model}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="mt-3">
                <button
                  type="button"
                  onClick={() => {
                    void startTrainingJob();
                  }}
                  disabled={Boolean(jobId) || isStartingTraining}
                  className="text-white font-medium text-sm px-4 py-2 rounded-lg bg-[#3BB273] hover:bg-[#2FA565] disabled:opacity-50 disabled:cursor-not-allowed transition-all"
                >
                  {isStartingTraining ? "Starting..." : jobId ? "Training Started" : "Start Training"}
                </button>
              </div>
            </div>

            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs text-slate-500 font-medium uppercase tracking-wide mb-1">Project</p>
                <h2 className="text-[23px] font-semibold tracking-tight text-slate-100">{projectName}</h2>
                <p className="text-sm text-slate-400 mt-1">
                  CSV: {id} · {projectRows !== null ? projectRows.toLocaleString() : "--"} rows · Target: {projectTargetColumn ?? targetColumn}
                </p>
              </div>
              <span className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-[#3BB273]/12 border border-[#3BB273]/30 text-[#5EDC8A] text-xs font-semibold rounded-lg shadow-[0_0_12px_rgba(59,178,115,0.22)]">
                <span className="w-1.5 h-1.5 rounded-full bg-[#5EDC8A] animate-pulse"></span>
                Training
              </span>
            </div>

            {/* Progress bar */}
            <div className="mt-5.5">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-slate-400">Overall Progress</span>
                <span className="text-xs font-semibold text-[#5EDC8A]">{progress}%</span>
              </div>
              <div className="h-2 bg-[#123C66] rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-[#3BB273] to-[#5EDC8A] rounded-full transition-all duration-700 shadow-[0_0_12px_rgba(59,178,115,0.35)]"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <p className="text-xs text-slate-500 mt-2">{completedCount} of {totalSteps} steps completed · ~3 min remaining</p>
            </div>
          </div>

          {/* Pipeline steps */}
          <div className="bg-slate-900/70 backdrop-blur-sm rounded-2xl border border-slate-800/85 shadow-[0_0_30px_rgba(15,23,42,0.6)] p-6">
            <h3 className="text-[15px] font-semibold tracking-tight text-slate-100 mb-5">Pipeline Steps</h3>
            <div className="space-y-4">
              {steps.map((step, idx) => {
                const cfg = statusConfig[step.status];
                const isRunning = step.status === "running";
                const isFailed = step.status === "failed";
                const isCompleted = step.status === "completed";
                return (
                  <div key={step.id} className="flex items-start gap-4 transition-all duration-300">
                    {/* Step indicator */}
                    <div className="relative flex flex-col items-center">
                      <div
                        className={`w-10 h-10 rounded-xl flex items-center justify-center ${cfg.bg} flex-shrink-0 transition-all duration-300 ${
                          isRunning
                            ? "shadow-[0_0_18px_rgba(59,178,115,0.45)]"
                            : isCompleted
                            ? "shadow-[0_0_18px_rgba(59,178,115,0.4)]"
                            : isFailed
                            ? "shadow-[0_0_18px_rgba(244,63,94,0.4)]"
                            : ""
                        }`}
                      >
                        {isCompleted ? (
                          <svg className="w-4 h-4 text-[#5EDC8A]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                          </svg>
                        ) : isRunning ? (
                          <span className="relative flex h-3.5 w-3.5">
                            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#5EDC8A]/60" />
                            <span className="relative inline-flex h-3.5 w-3.5 rounded-full bg-[#5EDC8A] shadow-[0_0_12px_rgba(94,220,138,0.9)]" />
                          </span>
                        ) : isFailed ? (
                          <svg className="w-4 h-4 text-rose-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                          </svg>
                        ) : (
                          <span className="text-xs font-semibold text-slate-500">{idx + 1}</span>
                        )}
                      </div>
                      {idx < steps.length - 1 && (
                        <div className={`w-0.5 h-4 mt-1 rounded ${step.status === "completed" ? "bg-[#3BB273]/40" : "bg-[#1e3a52]"}`} />
                      )}
                    </div>

                    {/* Step content */}
                    <div className="flex-1 pb-4">
                      <div className="flex items-center gap-3">
                        <span className="text-sm font-medium text-slate-100 tracking-tight">{step.label}</span>
                        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium ${cfg.bg} ${cfg.text} transition-all duration-300`}>
                          {isRunning && (
                            <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot} animate-pulse shadow-[0_0_8px_rgba(94,220,138,0.8)]`}></span>
                          )}
                          {cfg.label}
                        </span>
                      </div>
                      <p className={`text-xs mt-0.5 ${step.status === "pending" ? "text-slate-600" : "text-slate-500"}`}>
                        {step.description}
                      </p>
                      {isFailed && step.error && (
                        <div className="mt-3">
                          <button
                            type="button"
                            onClick={() => setExpandedErrorId(expandedErrorId === step.id ? null : step.id)}
                            className="text-xs text-rose-300 hover:text-rose-200 transition-colors"
                          >
                            {expandedErrorId === step.id ? "Hide error log" : "View error log"}
                          </button>
                          {expandedErrorId === step.id && (
                            <div className="mt-2 rounded-xl border border-rose-400/30 bg-rose-500/10 p-3 text-xs text-rose-200 shadow-[0_0_18px_rgba(244,63,94,0.2)]">
                              {step.error}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="mt-6">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-slate-500">Pipeline Progress</span>
                <span className="text-xs text-[#5EDC8A] font-semibold">{progress}%</span>
              </div>
              <div className="h-2.5 rounded-full bg-[#123C66] overflow-hidden">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-[#3BB273] to-[#5EDC8A] transition-all duration-700 shadow-[0_0_16px_rgba(59,178,115,0.4)]"
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>
          </div>

          {/* Parallel Model Execution - Only show when models are training */}
          {modelsInProgress.length > 0 && (
            <div className="bg-slate-900/70 backdrop-blur-sm rounded-2xl border border-slate-800/85 shadow-[0_0_30px_rgba(15,23,42,0.6)] p-6">
              <div className="flex items-center gap-2 mb-5">
                <h3 className="text-[15px] font-semibold tracking-tight text-slate-100">Parallel Model Execution</h3>
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-[#3BB273]/12 border border-[#3BB273]/30 text-[#5EDC8A] text-xs font-semibold rounded-lg">
                  <span className="w-1.5 h-1.5 rounded-full bg-[#5EDC8A] animate-pulse"></span>
                  {modelsInProgress.filter((m) => m.status === "training" || m.status === "queued").length} Active
                </span>
              </div>
              
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {modelsInProgress.map((model, idx) => {
                  const isTraining = model.status === "training";
                  const isCompleted = model.status === "completed";
                  const isPending = model.status === "pending";
                  const isQueued = model.status === "queued";
                  const isFailed = model.status === "failed";
                  
                  return (
                    <div
                      key={idx}
                      className={`rounded-xl border p-4 transition-all duration-300 ${
                        isTraining
                          ? "bg-[#3BB273]/12 border-[#3BB273]/40 shadow-[0_0_18px_rgba(59,178,115,0.25)]"
                          : isCompleted
                          ? "bg-[#3BB273]/8 border-[#3BB273]/25"
                        : isFailed
                          ? "bg-rose-500/10 border-rose-400/30"
                        : isQueued
                          ? "bg-[#123C66]/95 border-[#2b537b]"
                          : "bg-[#123C66]/90 border-[#1e3a52]"
                      }`}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-sm font-medium text-slate-200 truncate pr-2">
                          {model.name}
                        </span>
                        {isTraining ? (
                          <span className="relative flex h-3 w-3 flex-shrink-0">
                            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#5EDC8A]/60" />
                            <span className="relative inline-flex h-3 w-3 rounded-full bg-[#5EDC8A] shadow-[0_0_12px_rgba(94,220,138,0.9)]" />
                          </span>
                        ) : isCompleted ? (
                          <svg className="w-4 h-4 text-[#5EDC8A] flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                          </svg>
                        ) : isFailed ? (
                          <svg className="w-4 h-4 text-rose-300 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" />
                          </svg>
                        ) : isQueued ? (
                          <div className="w-3 h-3 rounded-full bg-[#7ec8ff] flex-shrink-0 shadow-[0_0_10px_rgba(126,200,255,0.5)]" />
                        ) : (
                          <div className="w-3 h-3 rounded-full bg-slate-500 flex-shrink-0" />
                        )}
                      </div>
                      
                      <div className="flex items-center justify-between">
                        <span className={`text-xs font-medium ${
                          isTraining
                            ? "text-[#5EDC8A]"
                            : isCompleted
                              ? "text-[#5EDC8A]/80"
                              : isFailed
                                ? "text-rose-300"
                                : isQueued
                                  ? "text-[#7ec8ff]"
                                  : "text-slate-500"
                        }`}>
                          {isTraining ? "Training..." : isCompleted ? "Completed" : isFailed ? "Failed" : isQueued ? "Queued" : "Pending"}
                        </span>
                        {model.rmse !== null && model.rmse !== undefined && (
                          <span className="text-xs text-slate-400 font-mono">
                            RMSE: {model.rmse.toFixed(4)}
                          </span>
                        )}
                      </div>

                      {model.job_id && (
                        <p className="mt-2 truncate text-[10px] uppercase tracking-wide text-slate-500">
                          Job {model.job_id}
                        </p>
                      )}

                      {isFailed && model.error && (
                        <p className="mt-2 text-[11px] leading-relaxed text-rose-200/90">
                          {model.error}
                        </p>
                      )}
                      
                      {isTraining && (
                        <div className="mt-3 h-1 bg-[#123C66] rounded-full overflow-hidden">
                          <div className="h-full w-full bg-gradient-to-r from-[#3BB273] to-[#5EDC8A] rounded-full animate-pulse" />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
              
              <div className="mt-4 flex items-center gap-2 text-xs text-slate-400">
                <svg className="w-4 h-4 text-[#5EDC8A]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
                <span>Sandbox jobs are queued, tracked live, and surfaced here as they start and finish.</span>
              </div>
            </div>
          )}

          {/* Logs */}
          <div className="bg-[#0F172A]/70 backdrop-blur-sm rounded-2xl border border-[#1e3a52]/85 shadow-[0_0_30px_rgba(11,31,58,0.6)] overflow-hidden">
            <div className="flex items-center justify-between px-6 py-4 border-b border-[#1e3a52]">
              <div className="flex items-center gap-3">
                <h3 className="text-[15px] font-semibold tracking-tight text-slate-100">Training Logs</h3>
                <span className="text-xs text-slate-500 font-mono">({logs.length} entries)</span>
              </div>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => setAutoScroll(!autoScroll)}
                  className={`text-xs px-2 py-1 rounded transition-colors ${
                    autoScroll
                      ? "bg-[#3BB273]/20 text-[#5EDC8A] border border-[#3BB273]/30"
                      : "bg-slate-700/50 text-slate-400 border border-slate-600/30"
                  }`}
                >
                  Auto-scroll {autoScroll ? "ON" : "OFF"}
                </button>
                <span className="flex items-center gap-1.5 text-xs text-[#5EDC8A] font-medium">
                  <span className="w-1.5 h-1.5 rounded-full bg-[#5EDC8A] animate-pulse"></span>
                  Live
                </span>
              </div>
            </div>
            <div
              ref={logsContainerRef}
              className="bg-[#0B1F3A] h-80 overflow-y-auto p-4 font-mono text-xs space-y-1"
              onScroll={(e) => {
                const target = e.target as HTMLDivElement;
                const isAtBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 50;
                if (!isAtBottom && autoScroll) {
                  setAutoScroll(false);
                }
              }}
            >
              {logs.length === 0 ? (
                <p className="text-slate-500 italic">Waiting for training logs...</p>
              ) : (
                logs.map((log, i) => {
                  const style = getLogStyle(log);
                  return (
                    <div
                      key={i}
                      className={`py-1.5 px-2 rounded transition-colors hover:bg-slate-800/50 break-words ${style.bgClass ?? ""}`}
                    >
                      <span className="text-slate-600 mr-2 select-none">{String(i + 1).padStart(3, " ")}</span>
                      {style.icon && <span className={`mr-2 ${style.textClass}`}>{style.icon}</span>}
                      <span className={`${style.textClass} leading-relaxed break-all`}>{log}</span>
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3">
            <Link
              href={`/results/${encodeURIComponent(id)}`}
              className="text-white font-medium text-sm px-6 py-2.5 rounded-xl bg-[#3BB273] hover:bg-[#2FA565] shadow-[0_0_24px_rgba(59,178,115,0.3)] hover:shadow-[0_0_30px_rgba(59,178,115,0.4)] transition-all duration-200"
            >
              View Results
            </Link>
            <Link
              href="/dashboard"
              className="border border-[#1e3a52] text-slate-300 hover:bg-[#123C66]/80 font-medium text-sm px-6 py-2.5 rounded-xl transition-all duration-200"
            >
              Back to Dashboard
            </Link>
          </div>
        </div>
      </main>
    </>
  );
}
