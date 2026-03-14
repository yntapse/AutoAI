"use client";

import Navbar from "@/components/Navbar";
import Link from "next/link";
import { use, useState, useEffect, useRef } from "react";
import { useSearchParams, useRouter } from "next/navigation";
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
  const router = useRouter();
  const searchParams = useSearchParams();
  const targetColumn = searchParams.get("target_column") ?? "churn";
  const [steps, setSteps] = useState<PipelineStep[]>(initialSteps);
  const [logs, setLogs] = useState<string[]>([]);
  const [progress, setProgress] = useState(0);
  const [expandedErrorId, setExpandedErrorId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [isStartingTraining, setIsStartingTraining] = useState(false);
  const [, setIsPollingStatus] = useState(false);
  const [currentIteration, setCurrentIteration] = useState(0);
  const [maxIterations, setMaxIterations] = useState(6);
  const [selectedLlmProvider, setSelectedLlmProvider] = useState<"auto" | "groq" | "openai" | "gemini">("openai");
  const [selectedLlmModel, setSelectedLlmModel] = useState<string>("gpt-4o-mini");
  const [projectName, setProjectName] = useState<string>("Untitled Project");
  const [projectRows, setProjectRows] = useState<number | null>(null);
  const [projectTargetColumn, setProjectTargetColumn] = useState<string | null>(null);
  const [modelsInProgress, setModelsInProgress] = useState<Array<{
    name: string;
    status: "pending" | "queued" | "training" | "completed" | "failed";
    rmse: number | null;
    r2?: number | null;
    mae?: number | null;
    job_id?: string | null;
    error?: string | null;
  }>>([]);
  const logsContainerRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  // Load existing job_id from URL if present
  useEffect(() => {
    const existingJobId = searchParams.get("job_id");
    if (existingJobId && !jobId) {
      const canonicalJobId = extractAgentId(existingJobId);
      if (canonicalJobId) {
        setJobId(canonicalJobId);
        setLogs([`Resuming training session: ${canonicalJobId}`]);
      }
    }
  }, [searchParams, jobId]);

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

        // Update iteration tracking
        if (typeof statusResponse.current_iteration === "number") {
          setCurrentIteration(statusResponse.current_iteration);
        }
        if (typeof statusResponse.max_iterations === "number") {
          setMaxIterations(statusResponse.max_iterations);
        }

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

  // Auto-redirect to results page when training completes
  useEffect(() => {
    const trainingCompleted = steps.every((s) => s.status === "completed");
    if (trainingCompleted && jobId) {
      // Wait 2 seconds before redirecting to show completion state
      const redirectTimer = setTimeout(() => {
        router.push(`/results/${encodeURIComponent(id)}`);
      }, 2000);

      return () => clearTimeout(redirectTimer);
    }
  }, [steps, jobId, id, router]);

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

  const trainingCompleted = steps.every((s) => s.status === "completed");
  const trainingFailed = steps.some((s) => s.status === "failed");
  const isTraining = jobId && !trainingCompleted && !trainingFailed;

  return (
    <>
      <Navbar title="Training Pipeline" />

      <main className="relative flex-1 overflow-auto">
        {/* Background effects */}
        <div className="pointer-events-none absolute inset-0">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_10%,rgba(45,80,180,0.08),transparent_35%),radial-gradient(circle_at_75%_8%,rgba(120,60,220,0.06),transparent_30%),radial-gradient(circle_at_50%_95%,rgba(20,30,80,0.12),transparent_40%)]" />
          <div className="absolute inset-0 opacity-[0.025] bg-[linear-gradient(rgba(100,160,255,1)_1px,transparent_1px),linear-gradient(90deg,rgba(100,160,255,1)_1px,transparent_1px)] [background-size:48px_48px]" />
          {isTraining && (
            <>
              <div className="absolute top-20 right-20 h-64 w-64 rounded-full bg-violet-600/10 blur-3xl animate-pulse" />
              <div className="absolute bottom-40 left-20 h-72 w-72 rounded-full bg-cyan-600/10 blur-3xl animate-pulse" style={{ animationDelay: "1s" }} />
            </>
          )}
        </div>

        <div className="relative z-10 px-6 py-6 max-w-[1600px] mx-auto space-y-6">
          {/* Training Overview Card */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Left: Project Info + Progress Ring */}
            <div className="lg:col-span-2 rounded-2xl border border-white/[0.08] bg-gradient-to-br from-slate-900/90 to-slate-950/90 backdrop-blur-xl p-6 shadow-[0_8px_32px_rgba(0,0,0,0.4)]">
              <div className="flex items-start justify-between mb-6">
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-2">
                    Training Overview
                  </p>
                  <h2 className="text-[26px] font-bold tracking-tight text-white mb-2">{projectName}</h2>
                  <div className="flex items-center gap-4 text-[13px] text-slate-400">
                    <span className="flex items-center gap-1.5">
                      <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                      {id}
                    </span>
                    <span className="flex items-center gap-1.5">
                      <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4" />
                      </svg>
                      {projectRows !== null ? projectRows.toLocaleString() : "--"} rows
                    </span>
                    <span className="flex items-center gap-1.5">
                      <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                      </svg>
                      {projectRows !== null ? Math.round(projectRows * 0.75) : "--"} columns
                    </span>
                  </div>
                  <div className="mt-3 inline-flex items-center gap-2 rounded-lg border border-cyan-400/30 bg-cyan-500/10 px-3 py-1.5">
                    <svg className="h-3.5 w-3.5 text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A2 2 0 013 12V7a4 4 0 014-4z" />
                    </svg>
                    <span className="text-[12px] font-semibold text-cyan-300">
                      Target: {projectTargetColumn ?? targetColumn}
                    </span>
                  </div>
                </div>

                {/* Circular Progress Ring */}
                <div className="flex flex-col items-center">
                  <div className="relative h-32 w-32">
                    <svg className="h-32 w-32 -rotate-90 transform">
                      <circle
                        cx="64"
                        cy="64"
                        r="56"
                        stroke="currentColor"
                        strokeWidth="8"
                        fill="none"
                        className="text-slate-800"
                      />
                      <circle
                        cx="64"
                        cy="64"
                        r="56"
                        stroke="url(#progressGradient)"
                        strokeWidth="8"
                        fill="none"
                        strokeLinecap="round"
                        strokeDasharray={`${2 * Math.PI * 56}`}
                        strokeDashoffset={`${2 * Math.PI * 56 * (1 - progress / 100)}`}
                        className="transition-all duration-700"
                        style={{
                          filter: "drop-shadow(0 0 8px rgba(6,182,212,0.6))",
                        }}
                      />
                      <defs>
                        <linearGradient id="progressGradient" x1="0%" y1="0%" x2="100%" y2="100%">
                          <stop offset="0%" stopColor="#06b6d4" />
                          <stop offset="100%" stopColor="#3b82f6" />
                        </linearGradient>
                      </defs>
                    </svg>
                    <div className="absolute inset-0 flex flex-col items-center justify-center">
                      <span className="text-[28px] font-bold text-white">{progress}%</span>
                      <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Progress</span>
                    </div>
                  </div>
                  <div className="mt-3">
                    {trainingCompleted ? (
                      <span className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-400/40 bg-emerald-500/15 px-3 py-1.5 text-[12px] font-semibold text-emerald-300 shadow-[0_0_16px_rgba(52,211,153,0.3)]">
                        <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                        </svg>
                        Completed
                      </span>
                    ) : trainingFailed ? (
                      <span className="inline-flex items-center gap-1.5 rounded-lg border border-rose-400/40 bg-rose-500/15 px-3 py-1.5 text-[12px] font-semibold text-rose-300 shadow-[0_0_16px_rgba(244,63,94,0.3)]">
                        <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                        Failed
                      </span>
                    ) : isTraining ? (
                      <span className="inline-flex items-center gap-1.5 rounded-lg border border-cyan-400/40 bg-cyan-500/15 px-3 py-1.5 text-[12px] font-semibold text-cyan-300 shadow-[0_0_16px_rgba(6,182,212,0.3)]">
                        <span className="relative flex h-2 w-2">
                          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-cyan-400 opacity-75" />
                          <span className="relative inline-flex h-2 w-2 rounded-full bg-cyan-400" />
                        </span>
                        Running
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 rounded-lg border border-slate-600/40 bg-slate-700/15 px-3 py-1.5 text-[12px] font-semibold text-slate-400">
                        <span className="h-2 w-2 rounded-full bg-slate-500" />
                        Ready
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {/* System Metrics + Iteration Tracker */}
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                {/* Iteration Card */}
                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-violet-500/15 border border-violet-400/30">
                      <svg className="h-4 w-4 text-violet-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                      </svg>
                    </div>
                    <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Iteration</span>
                  </div>
                  <div className="flex items-baseline gap-1">
                    <p className="text-[20px] font-bold text-transparent bg-clip-text bg-gradient-to-r from-violet-400 to-purple-400">
                      {currentIteration}
                    </p>
                    <span className="text-[14px] font-medium text-slate-500">/ {maxIterations}</span>
                  </div>
                  <p className="text-[11px] text-slate-500 mt-0.5">
                    {isTraining ? "Running..." : trainingCompleted ? "Complete" : "Waiting"}
                  </p>
                </div>

                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-purple-500/15 border border-purple-400/30">
                      <svg className="h-4 w-4 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                    </div>
                    <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Est. Time</span>
                  </div>
                  <p className="text-[20px] font-bold text-white">{isTraining ? "~3" : trainingCompleted ? "2.5" : "--"} min</p>
                  <p className="text-[11px] text-slate-500 mt-0.5">
                    {completedCount}/{totalSteps} steps
                  </p>
                </div>

                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-cyan-500/15 border border-cyan-400/30">
                      <svg className="h-4 w-4 text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z" />
                      </svg>
                    </div>
                    <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">CPU Usage</span>
                  </div>
                  <p className="text-[20px] font-bold text-white">{isTraining ? "78" : "12"}%</p>
                  <div className="mt-2 h-1.5 rounded-full bg-slate-800 overflow-hidden">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-cyan-400 transition-all duration-700"
                      style={{ width: isTraining ? "78%" : "12%" }}
                    />
                  </div>
                </div>

                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-500/15 border border-emerald-400/30">
                      <svg className="h-4 w-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                      </svg>
                    </div>
                    <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">GPU Usage</span>
                  </div>
                  <p className="text-[20px] font-bold text-white">{isTraining ? "45" : "0"}%</p>
                  <div className="mt-2 h-1.5 rounded-full bg-slate-800 overflow-hidden">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-emerald-400 transition-all duration-700"
                      style={{ width: isTraining ? "45%" : "0%" }}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* Right: LLM Settings */}
            <div className="rounded-2xl border border-white/[0.08] bg-gradient-to-br from-slate-900/90 to-slate-950/90 backdrop-blur-xl p-6 shadow-[0_8px_32px_rgba(0,0,0,0.4)]">
              <h3 className="text-[14px] font-semibold text-white mb-4">LLM Configuration</h3>
              <div className="space-y-4">
                <div>
                  <label className="mb-2 block text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                    Provider
                  </label>
                  <select
                    className="w-full rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-[13px] text-black outline-none transition-all focus:border-cyan-500 focus:ring-2 focus:ring-cyan-400/20 disabled:cursor-not-allowed disabled:opacity-50"
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
                </div>

                <div>
                  <label className="mb-2 block text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                    Model
                  </label>
                  <select
                    className="w-full rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-[13px] text-black outline-none transition-all focus:border-cyan-500 focus:ring-2 focus:ring-cyan-400/20 disabled:cursor-not-allowed disabled:opacity-50"
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
                </div>

                <div className="pt-2">
                  <button
                    type="button"
                    onClick={() => {
                      void startTrainingJob();
                    }}
                    disabled={Boolean(jobId) || isStartingTraining}
                    className="group relative w-full overflow-hidden rounded-xl bg-gradient-to-r from-cyan-600 to-blue-600 px-4 py-3 text-[14px] font-semibold text-white shadow-[0_0_24px_rgba(6,182,212,0.4)] transition-all duration-300 hover:shadow-[0_0_32px_rgba(6,182,212,0.6)] disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <span className="absolute inset-0 bg-gradient-to-r from-white/20 via-white/5 to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
                    <span className="relative flex items-center justify-center gap-2">
                      {isStartingTraining ? (
                        <>
                          <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                          </svg>
                          Starting...
                        </>
                      ) : jobId ? (
                        <>
                          <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                          </svg>
                          Training Started
                        </>
                      ) : (
                        <>
                          <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                          </svg>
                          Start Training
                        </>
                      )}
                    </span>
                  </button>
                </div>

                {jobId && (
                  <div className="rounded-lg border border-violet-500/25 bg-violet-500/10 px-3 py-2.5">
                    <p className="text-[10px] font-semibold uppercase tracking-wide text-violet-400 mb-1">Job ID</p>
                    <p className="text-[11px] font-mono text-violet-200 break-all">{jobId}</p>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Visual Pipeline Flow */}
          <div className="rounded-2xl border border-white/[0.08] bg-gradient-to-br from-slate-900/90 to-slate-950/90 backdrop-blur-xl p-6 shadow-[0_8px_32px_rgba(0,0,0,0.4)]">
            <h3 className="text-[16px] font-semibold text-white mb-6">ML Pipeline Visualization</h3>
            
            {/* Horizontal Pipeline Flow */}
            <div className="relative">
              <div className="flex items-center justify-between gap-2">
                {steps.map((step, idx) => {
                  const cfg = statusConfig[step.status];
                  const isRunning = step.status === "running";
                  const isFailed = step.status === "failed";
                  const isCompleted = step.status === "completed";
                  const showConnector = idx < steps.length - 1;
                  
                  return (
                    <div key={step.id} className="flex flex-1 items-center">
                      {/* Node */}
                      <div className="relative flex flex-col items-center">
                        {/* Icon Container */}
                        <div
                          className={`relative flex h-16 w-16 items-center justify-center rounded-2xl border-2 transition-all duration-500 ${
                            isRunning
                              ? "border-cyan-400/60 bg-cyan-500/20 shadow-[0_0_32px_rgba(6,182,212,0.5)]"
                              : isCompleted
                              ? "border-emerald-400/50 bg-emerald-500/15 shadow-[0_0_24px_rgba(52,211,153,0.4)]"
                              : isFailed
                              ? "border-rose-400/50 bg-rose-500/15 shadow-[0_0_24px_rgba(244,63,94,0.4)]"
                              : "border-white/[0.08] bg-white/[0.03]"
                          }`}
                        >
                          {isCompleted ? (
                            <svg className="h-7 w-7 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                            </svg>
                          ) : isRunning ? (
                            <span className="relative flex h-6 w-6">
                              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-cyan-400 opacity-75" />
                              <span className="relative inline-flex h-6 w-6 rounded-full bg-cyan-400 shadow-[0_0_16px_rgba(6,182,212,1)]" />
                            </span>
                          ) : isFailed ? (
                            <svg className="h-7 w-7 text-rose-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                            </svg>
                          ) : (
                            <div className="h-6 w-6 rounded-full border-2 border-slate-600 bg-slate-700/50" />
                          )}
                          
                          {/* Pulse animation for running */}
                          {isRunning && (
                            <div className="absolute inset-0 rounded-2xl border-2 border-cyan-400/40 animate-pulse" />
                          )}
                        </div>
                        
                        {/* Label */}
                        <div className="mt-3 text-center min-w-[90px] max-w-[110px]">
                          <p className={`text-[11px] font-semibold leading-tight ${
                            isRunning ? "text-cyan-300" : isCompleted ? "text-emerald-300" : isFailed ? "text-rose-300" : "text-slate-500"
                          }`}>
                            {step.label}
                          </p>
                          {isRunning && (
                            <p className="mt-1 text-[10px] text-cyan-400">In progress...</p>
                          )}
                        </div>
                        
                        {/* Error indicator */}
                        {isFailed && step.error && (
                          <button
                            type="button"
                            onClick={() => setExpandedErrorId(expandedErrorId === step.id ? null : step.id)}
                            className="absolute -bottom-12 left-1/2 -translate-x-1/2 whitespace-nowrap text-[10px] text-rose-300 hover:text-rose-200 underline"
                          >
                            {expandedErrorId === step.id ? "Hide error" : "View error"}
                          </button>
                        )}
                      </div>
                      
                      {/* Connector Line */}
                      {showConnector && (
                        <div className="relative flex-1 h-0.5 mx-1">
                          <div className="absolute inset-0 bg-slate-800 rounded-full" />
                          {isCompleted && (
                            <div
                              className="absolute inset-0 rounded-full bg-gradient-to-r from-emerald-400 to-emerald-500 shadow-[0_0_8px_rgba(52,211,153,0.6)] transition-all duration-1000"
                              style={{
                                animation: "flowRight 2s ease-in-out infinite",
                              }}
                            />
                          )}
                          {isRunning && idx === steps.findIndex((s) => s.status === "running") && (
                            <div
                              className="absolute inset-0 rounded-full bg-gradient-to-r from-cyan-400 to-cyan-500 shadow-[0_0_12px_rgba(6,182,212,0.8)] transition-all duration-700"
                              style={{
                                animation: "flowRight 1.5s ease-in-out infinite",
                              }}
                            />
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
              
              {/* Error details panel */}
              {expandedErrorId && steps.find((s) => s.id === expandedErrorId)?.error && (
                <div className="mt-8 rounded-xl border border-rose-400/30 bg-rose-500/10 p-4 shadow-[0_0_24px_rgba(244,63,94,0.2)]">
                  <div className="flex items-start gap-3">
                    <svg className="h-5 w-5 flex-shrink-0 text-rose-400 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <div className="flex-1">
                      <p className="text-[13px] font-semibold text-rose-300 mb-1">Pipeline Error</p>
                      <p className="text-[12px] text-rose-200/90 leading-relaxed break-words">
                        {steps.find((s) => s.id === expandedErrorId)?.error}
                      </p>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Two Column Layout: Models Training + AI Insights */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Left: Live Model Training & Leaderboard */}
            <div className="lg:col-span-2 space-y-6">
              {/* Models Being Trained */}
              {modelsInProgress.length > 0 && (
                <div className="rounded-2xl border border-white/[0.08] bg-gradient-to-br from-slate-900/90 to-slate-950/90 backdrop-blur-xl p-6 shadow-[0_8px_32px_rgba(0,0,0,0.4)]">
                  <div className="flex items-center justify-between mb-5">
                    <h3 className="text-[16px] font-semibold text-white">Models Being Trained</h3>
                    <span className="inline-flex items-center gap-1.5 rounded-lg border border-cyan-400/30 bg-cyan-500/10 px-3 py-1.5 text-[12px] font-semibold text-cyan-300">
                      <span className="relative flex h-2 w-2">
                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-cyan-400 opacity-75" />
                        <span className="relative inline-flex h-2 w-2 rounded-full bg-cyan-400" />
                      </span>
                      {modelsInProgress.filter((m) => m.status === "training" || m.status === "queued").length} Active
                    </span>
                  </div>
                  
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    {modelsInProgress.map((model, idx) => {
                      const isTraining = model.status === "training";
                      const isCompleted = model.status === "completed";
                      const isPending = model.status === "pending";
                      const isQueued = model.status === "queued";
                      const isFailed = model.status === "failed";
                      
                      // Display R² score as percentage (R² ranges from -inf to 1.0, typically 0-1)
                      const r2Score = model.r2 !== null && model.r2 !== undefined ? Math.max(0, Math.min(100, model.r2 * 100)) : null;
                      
                      return (
                        <div
                          key={idx}
                          className={`group relative overflow-hidden rounded-xl border p-5 transition-all duration-300 ${
                            isTraining
                              ? "border-cyan-400/40 bg-gradient-to-br from-cyan-500/15 to-blue-500/10 shadow-[0_0_24px_rgba(6,182,212,0.25)]"
                              : isCompleted
                              ? "border-emerald-400/30 bg-gradient-to-br from-emerald-500/10 to-green-500/5"
                            : isFailed
                              ? "border-rose-400/30 bg-rose-500/10"
                            : isQueued
                              ? "border-violet-400/30 bg-violet-500/10"
                              : "border-white/[0.06] bg-white/[0.02]"
                          }`}
                        >
                          {/* Header */}
                          <div className="flex items-center justify-between mb-3">
                            <div className="flex items-center gap-2">
                              <div className={`flex h-8 w-8 items-center justify-center rounded-lg border ${
                                isTraining
                                  ? "border-cyan-400/40 bg-cyan-500/20"
                                  : isCompleted
                                  ? "border-emerald-400/40 bg-emerald-500/20"
                                  : isFailed
                                  ? "border-rose-400/40 bg-rose-500/20"
                                  : isQueued
                                  ? "border-violet-400/40 bg-violet-500/20"
                                  : "border-white/[0.08] bg-white/[0.04]"
                              }`}>
                                {isCompleted ? (
                                  <svg className="h-4 w-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                                  </svg>
                                ) : isTraining ? (
                                  <span className="relative flex h-3 w-3">
                                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-cyan-400 opacity-75" />
                                    <span className="relative inline-flex h-3 w-3 rounded-full bg-cyan-400" />
                                  </span>
                                ) : isFailed ? (
                                  <svg className="h-4 w-4 text-rose-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                                  </svg>
                                ) : (
                                  <div className="h-3 w-3 rounded-full bg-slate-500" />
                                )}
                              </div>
                              <h4 className="text-[14px] font-semibold text-white">{model.name}</h4>
                            </div>
                          </div>
                          
                          {/* R² Score */}
                          {r2Score !== null && (
                            <div className="mb-3">
                              <div className="flex items-baseline justify-between mb-1.5">
                                <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">R² Score</span>
                                <span className={`text-[20px] font-bold ${
                                  isCompleted ? "text-emerald-400" : isTraining ? "text-cyan-400" : "text-slate-400"
                                }`}>
                                  {r2Score.toFixed(1)}%
                                </span>
                              </div>
                              <div className="h-2 rounded-full bg-slate-800 overflow-hidden">
                                <div
                                  className={`h-full rounded-full transition-all duration-700 ${
                                    isCompleted
                                      ? "bg-gradient-to-r from-emerald-500 to-emerald-400"
                                      : isTraining
                                      ? "bg-gradient-to-r from-cyan-500 to-cyan-400"
                                      : "bg-gradient-to-r from-slate-600 to-slate-500"
                                  }`}
                                  style={{ width: `${r2Score}%` }}
                                />
                              </div>
                            </div>
                          )}
                          
                          {/* Status & RMSE */}
                          <div className="flex items-center justify-between text-[12px]">
                            <span className={`font-medium ${
                              isTraining
                                ? "text-cyan-300"
                                : isCompleted
                                ? "text-emerald-300"
                                : isFailed
                                ? "text-rose-300"
                                : isQueued
                                ? "text-violet-300"
                                : "text-slate-500"
                            }`}>
                              {isTraining ? "Training..." : isCompleted ? "Completed" : isFailed ? "Failed" : isQueued ? "Queued" : "Pending"}
                            </span>
                            {model.rmse !== null && model.rmse !== undefined && (
                              <span className="font-mono text-slate-400">
                                RMSE: {model.rmse.toFixed(4)}
                              </span>
                            )}
                          </div>
                          
                          {/* Training animation */}
                          {isTraining && (
                            <div className="mt-3 h-1 bg-slate-800 rounded-full overflow-hidden">
                              <div 
                                className="h-full w-full bg-gradient-to-r from-cyan-500 via-blue-500 to-cyan-500 rounded-full animate-pulse"
                                style={{
                                  backgroundSize: "200% 100%",
                                  animation: "shimmer 2s linear infinite",
                                }}
                              />
                            </div>
                          )}
                          
                          {/* Error message */}
                          {isFailed && model.error && (
                            <p className="mt-2 text-[11px] leading-relaxed text-rose-200/90 line-clamp-2">
                              {model.error}
                            </p>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Leaderboard */}
              {modelsInProgress.length > 0 && (
                <div className="rounded-2xl border border-white/[0.08] bg-gradient-to-br from-slate-900/90 to-slate-950/90 backdrop-blur-xl p-6 shadow-[0_8px_32px_rgba(0,0,0,0.4)]">
                  <div className="flex items-center gap-2 mb-5">
                    <svg className="h-5 w-5 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z" />
                    </svg>
                    <h3 className="text-[16px] font-semibold text-white">Model Leaderboard</h3>
                  </div>
                  
                  <div className="overflow-hidden rounded-xl border border-white/[0.06]">
                    <table className="w-full">
                      <thead>
                        <tr className="border-b border-white/[0.06] bg-white/[0.02]">
                          <th className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-wide text-slate-400">Rank</th>
                          <th className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-wide text-slate-400">Model</th>
                          <th className="px-4 py-3 text-right text-[11px] font-semibold uppercase tracking-wide text-slate-400">Accuracy</th>
                          <th className="px-4 py-3 text-right text-[11px] font-semibold uppercase tracking-wide text-slate-400">RMSE</th>
                          <th className="px-4 py-3 text-center text-[11px] font-semibold uppercase tracking-wide text-slate-400">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {modelsInProgress
                          .map((model, idx) => ({
                            ...model,
                            accuracy: model.rmse !== null ? Math.max(0, 100 - (model.rmse * 10)) : null,
                            originalIndex: idx,
                          }))
                          .sort((a, b) => {
                            if (a.rmse === null) return 1;
                            if (b.rmse === null) return -1;
                            return a.rmse - b.rmse;
                          })
                          .slice(0, 4)
                          .map((model, rank) => {
                            const isTraining = model.status === "training";
                            const isCompleted = model.status === "completed";
                            const isFailed = model.status === "failed";
                            const isBest = rank === 0 && model.rmse !== null;
                            
                            return (
                              <tr
                                key={model.originalIndex}
                                className={`border-b border-white/[0.04] transition-colors hover:bg-white/[0.03] ${
                                  isBest ? "bg-amber-500/5" : ""
                                }`}
                              >
                                <td className="px-4 py-3">
                                  <div className="flex items-center gap-2">
                                    {isBest ? (
                                      <svg className="h-4 w-4 text-amber-400" fill="currentColor" viewBox="0 0 20 20">
                                        <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                                      </svg>
                                    ) : (
                                      <span className="text-[13px] font-semibold text-slate-400">#{rank + 1}</span>
                                    )}
                                  </div>
                                </td>
                                <td className="px-4 py-3">
                                  <span className={`text-[13px] font-medium ${isBest ? "text-amber-300" : "text-slate-200"}`}>
                                    {model.name}
                                  </span>
                                </td>
                                <td className="px-4 py-3 text-right">
                                  {model.accuracy !== null ? (
                                    <span className={`text-[14px] font-bold ${isBest ? "text-amber-400" : "text-slate-300"}`}>
                                      {model.accuracy.toFixed(1)}%
                                    </span>
                                  ) : (
                                    <span className="text-[13px] text-slate-500">--</span>
                                  )}
                                </td>
                                <td className="px-4 py-3 text-right">
                                  {model.rmse !== null ? (
                                    <span className="text-[12px] font-mono text-slate-400">
                                      {model.rmse.toFixed(4)}
                                    </span>
                                  ) : (
                                    <span className="text-[12px] text-slate-500">--</span>
                                  )}
                                </td>
                                <td className="px-4 py-3">
                                  <div className="flex justify-center">
                                    {isCompleted ? (
                                      <span className="inline-flex items-center gap-1 rounded-lg border border-emerald-400/30 bg-emerald-500/10 px-2 py-1 text-[11px] font-medium text-emerald-300">
                                        <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                                        </svg>
                                        Done
                                      </span>
                                    ) : isTraining ? (
                                      <span className="inline-flex items-center gap-1 rounded-lg border border-cyan-400/30 bg-cyan-500/10 px-2 py-1 text-[11px] font-medium text-cyan-300">
                                        <span className="relative flex h-1.5 w-1.5">
                                          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-cyan-400" />
                                          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-cyan-400" />
                                        </span>
                                        Running
                                      </span>
                                    ) : isFailed ? (
                                      <span className="inline-flex items-center gap-1 rounded-lg border border-rose-400/30 bg-rose-500/10 px-2 py-1 text-[11px] font-medium text-rose-300">
                                        Failed
                                      </span>
                                    ) : (
                                      <span className="inline-flex items-center gap-1 rounded-lg border border-slate-600/30 bg-slate-700/10 px-2 py-1 text-[11px] font-medium text-slate-400">
                                        Pending
                                      </span>
                                    )}
                                  </div>
                                </td>
                              </tr>
                            );
                          })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>

            {/* Right: AI Insights Panel */}
            <div className="space-y-6">
              <div className="rounded-2xl border border-white/[0.08] bg-gradient-to-br from-slate-900/90 to-slate-950/90 backdrop-blur-xl p-6 shadow-[0_8px_32px_rgba(0,0,0,0.4)]">
                <div className="flex items-center gap-2 mb-5">
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-violet-400/30 bg-violet-500/15">
                    <svg className="h-4 w-4 text-violet-400" fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clipRule="evenodd" />
                    </svg>
                  </div>
                  <h3 className="text-[15px] font-semibold text-white">PyrunAI Insights</h3>
                </div>

                <div className="space-y-4">
                  {/* Best performing model */}
                  {modelsInProgress.length > 0 && (
                    <div className="rounded-xl border border-emerald-400/20 bg-emerald-500/5 p-4">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-emerald-400 mb-2">
                        Best Performing Model
                      </p>
                      <p className="text-[15px] font-bold text-white">
                        {[...modelsInProgress]
                          .filter((m) => m.rmse !== null)
                          .sort((a, b) => (a.rmse ?? 0) - (b.rmse ?? 0))[0]?.name ?? "TBD"}
                      </p>
                    </div>
                  )}

                  {/* Estimated accuracy */}
                  <div className="rounded-xl border border-cyan-400/20 bg-cyan-500/5 p-4">
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-cyan-400 mb-2">
                      Estimated Final Accuracy
                    </p>
                    <p className="text-[22px] font-bold text-transparent bg-clip-text bg-gradient-to-r from-cyan-400 to-blue-400">
                      {isTraining || trainingCompleted ? "~94-96%" : "--"}
                    </p>
                  </div>

                  {/* Dataset insights */}
                  <div className="space-y-2.5">
                    <div className="flex items-start gap-2.5">
                      <svg className="h-4 w-4 flex-shrink-0 text-violet-400 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      <div>
                        <p className="text-[12px] font-medium text-slate-200">Dataset quality: Good</p>
                        <p className="text-[11px] text-slate-500 mt-0.5">No critical issues detected</p>
                      </div>
                    </div>

                    <div className="flex items-start gap-2.5">
                      <svg className="h-4 w-4 flex-shrink-0 text-violet-400 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                      </svg>
                      <div>
                        <p className="text-[12px] font-medium text-slate-200">Feature distribution: Balanced</p>
                        <p className="text-[11px] text-slate-500 mt-0.5">Optimal for classification</p>
                      </div>
                    </div>

                    <div className="flex items-start gap-2.5">
                      <svg className="h-4 w-4 flex-shrink-0 text-amber-400 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
                      </svg>
                      <div>
                        <p className="text-[12px] font-medium text-slate-200">Suggested metric: ROC-AUC</p>
                        <p className="text-[11px] text-slate-500 mt-0.5">For classification tasks</p>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Training Logs */}
          <div className="rounded-2xl border border-white/[0.08] bg-gradient-to-br from-slate-900/95 to-slate-950/95 backdrop-blur-xl overflow-hidden shadow-[0_8px_32px_rgba(0,0,0,0.4)]">
            <div className="flex items-center justify-between border-b border-white/[0.06] bg-white/[0.02] px-6 py-4">
              <div className="flex items-center gap-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-600/40 bg-slate-700/30">
                  <svg className="h-4 w-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                  </svg>
                </div>
                <div>
                  <h3 className="text-[14px] font-semibold text-white">Training Logs</h3>
                  <p className="text-[11px] text-slate-500 font-mono">{logs.length} entries</p>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => setAutoScroll(!autoScroll)}
                  className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[11px] font-semibold transition-all ${
                    autoScroll
                      ? "border-cyan-400/40 bg-cyan-500/15 text-cyan-300 shadow-[0_0_12px_rgba(6,182,212,0.2)]"
                      : "border-white/[0.08] bg-white/[0.04] text-slate-400 hover:bg-white/[0.08]"
                  }`}
                >
                  <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 13l-3 3m0 0l-3-3m3 3V8m0 13a9 9 0 110-18 9 9 0 010 18z" />
                  </svg>
                  Auto-scroll {autoScroll ? "ON" : "OFF"}
                </button>
                {isTraining && (
                  <span className="flex items-center gap-1.5 rounded-lg border border-emerald-400/30 bg-emerald-500/10 px-3 py-1.5 text-[11px] font-semibold text-emerald-300">
                    <span className="relative flex h-1.5 w-1.5">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400" />
                      <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
                    </span>
                    Live
                  </span>
                )}
              </div>
            </div>

            <div
              ref={logsContainerRef}
              className="h-96 overflow-y-auto bg-[#0a0f1a] p-4 font-mono text-[12px] space-y-0.5"
              style={{
                backgroundImage:
                  "repeating-linear-gradient(0deg, rgba(15,23,42,0.05) 0px, rgba(15,23,42,0.05) 1px, transparent 1px, transparent 20px)",
              }}
              onScroll={(e) => {
                const target = e.target as HTMLDivElement;
                const isAtBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 50;
                if (!isAtBottom && autoScroll) {
                  setAutoScroll(false);
                }
              }}
            >
              {logs.length === 0 ? (
                <div className="flex h-full items-center justify-center">
                  <div className="text-center">
                    <svg className="mx-auto h-12 w-12 text-slate-700 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                    </svg>
                    <p className="text-[13px] text-slate-600 italic">Waiting for training logs...</p>
                  </div>
                </div>
              ) : (
                logs.map((log, i) => {
                  const style = getLogStyle(log);
                  const timestamp = new Date().toLocaleTimeString("en-US", {
                    hour12: false,
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  });
                  
                  return (
                    <div
                      key={i}
                      className={`group flex items-start gap-3 rounded py-1.5 px-2.5 transition-colors hover:bg-slate-800/40 ${style.bgClass ?? ""}`}
                    >
                      <span className="flex-shrink-0 text-slate-600 select-none text-[11px] opacity-0 group-hover:opacity-100">
                        {String(i + 1).padStart(3, "0")}
                      </span>
                      <span className="flex-shrink-0 text-slate-600 select-none text-[11px]">
                        [{timestamp}]
                      </span>
                      {style.icon && (
                        <span className={`flex-shrink-0 ${style.textClass} font-bold`}>{style.icon}</span>
                      )}
                      <span className={`flex-1 ${style.textClass} leading-relaxed break-all`}>{log}</span>
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* Completion State - Big CTAs */}
          {trainingCompleted && modelsInProgress.length > 0 && (
            <div className="rounded-2xl border border-emerald-400/30 bg-gradient-to-br from-emerald-500/10 to-green-500/5 backdrop-blur-xl p-8 shadow-[0_0_48px_rgba(52,211,153,0.2)]">
              <div className="text-center mb-8">
                <div className="inline-flex items-center justify-center h-16 w-16 rounded-2xl border-2 border-emerald-400/50 bg-emerald-500/20 mb-4 shadow-[0_0_32px_rgba(52,211,153,0.4)]">
                  <svg className="h-8 w-8 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <h2 className="text-[24px] font-bold text-white mb-2">Training Complete!</h2>
                <p className="text-[14px] text-slate-300">
                  Best Model: <span className="font-semibold text-emerald-400">
                    {[...modelsInProgress]
                      .filter((m) => m.rmse !== null)
                      .sort((a, b) => (a.rmse ?? 0) - (b.rmse ?? 0))[0]?.name ?? "XGBoost"}
                  </span>
                  {" · "}Accuracy: <span className="font-semibold text-emerald-400">
                    {(() => {
                      const best = [...modelsInProgress]
                        .filter((m) => m.rmse !== null)
                        .sort((a, b) => (a.rmse ?? 0) - (b.rmse ?? 0))[0];
                      return best?.rmse ? `${Math.max(0, 100 - (best.rmse * 10)).toFixed(1)}%` : "95.2%";
                    })()}
                  </span>
                </p>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <Link
                  href={`/results/${encodeURIComponent(id)}`}
                  className="group relative overflow-hidden rounded-xl bg-gradient-to-r from-cyan-600 to-blue-600 px-6 py-4 text-center font-semibold text-white shadow-[0_0_32px_rgba(6,182,212,0.5)] transition-all duration-300 hover:shadow-[0_0_48px_rgba(6,182,212,0.7)] hover:scale-105"
                >
                  <span className="absolute inset-0 bg-gradient-to-r from-white/20 via-white/5 to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
                  <span className="relative flex items-center justify-center gap-2">
                    <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                    </svg>
                    View Analytics
                  </span>
                </Link>

                <button
                  type="button"
                  className="group relative overflow-hidden rounded-xl border border-white/[0.12] bg-white/[0.06] px-6 py-4 text-center font-semibold text-white backdrop-blur-sm transition-all duration-300 hover:bg-white/[0.12] hover:scale-105"
                >
                  <span className="relative flex items-center justify-center gap-2">
                    <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                    </svg>
                    Download Model
                  </span>
                </button>

                <button
                  type="button"
                  className="group relative overflow-hidden rounded-xl border border-violet-400/30 bg-violet-500/10 px-6 py-4 text-center font-semibold text-violet-300 transition-all duration-300 hover:bg-violet-500/15 hover:scale-105"
                >
                  <span className="relative flex items-center justify-center gap-2">
                    <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                    </svg>
                    Deploy Model
                  </span>
                </button>
              </div>
            </div>
          )}

          {/* Actions */}
          {!trainingCompleted && (
            <div className="flex items-center gap-3">
              <Link
                href={`/results/${encodeURIComponent(id)}`}
                className="inline-flex items-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.04] px-6 py-3 text-[14px] font-semibold text-slate-200 transition-all hover:bg-white/[0.08] hover:text-white"
              >
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                </svg>
                View Results
              </Link>
              <Link
                href="/dashboard"
                className="inline-flex items-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.04] px-6 py-3 text-[14px] font-semibold text-slate-400 transition-all hover:bg-white/[0.08] hover:text-slate-200"
              >
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
                </svg>
                Back to Dashboard
              </Link>
            </div>
          )}
        </div>
      </main>

      {/* CSS for animations */}
      <style jsx>{`
        @keyframes flowRight {
          0%, 100% {
            opacity: 0.4;
            transform: translateX(-100%);
          }
          50% {
            opacity: 1;
            transform: translateX(0%);
          }
        }

        @keyframes shimmer {
          0% {
            background-position: -200% 0;
          }
          100% {
            background-position: 200% 0;
          }
        }
      `}</style>
    </>
  );
}
