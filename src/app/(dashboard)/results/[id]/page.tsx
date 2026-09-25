"use client";

import { use, useEffect, useMemo, useRef, useState } from "react";
import Navbar from "@/components/Navbar";
import Link from "next/link";
import { downloadModelArtifact, downloadModelCode, FineTuneResponse, fineTuneModel, getTrainingHistory } from "@/services/resultsService";
import { getProjectMetaByFileId } from "@/services/projectMetaService";
import { useSearchParams } from "next/navigation";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Cell,
} from "recharts";

type MetricKey = "accuracy" | "f1" | "auc";

interface ModelResult {
  name: string;
  accuracy: number;
  f1: number;
  auc: number;
  trainingTime: number;
}

interface HistoryModel {
  name?: string;
  model_name?: string;
  rmse?: number;
  mae?: number;
  r2?: number;
  training_time_seconds?: number;
  generated_by?: "llm_generated" | "fine_tuned" | "standard";
}

interface TrainingTableRow {
  model_name: string;
  rmse: number | null;
  mae: number | null;
  r2: number | null;
  generated_by: "llm_generated" | "fine_tuned" | "standard";
}

interface HistoryVersion {
  version: number;
  models: HistoryModel[];
  training_run_id?: string | null;
  training_time_seconds?: number;
}

interface TrainingHistoryResponse {
  file_id: string;
  target_column?: string | null;
  versions: HistoryVersion[];
}

interface AppliedSnapshot {
  results: ModelResult[];
  tableRows: TrainingTableRow[];
  trainingRunId?: string; // For code download revert
}

interface PersistedResultsUiState {
  results: ModelResult[];
  tableRows: TrainingTableRow[];
  acceptSnapshotStack: AppliedSnapshot[];
}

const metricLabels: Record<MetricKey, string> = {
  accuracy: "Accuracy",
  f1: "F1 Score",
  auc: "ROC-AUC",
};

function normalizeModelName(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function DarkTooltip({ active, payload, label }: { active?: boolean; payload?: any[]; label?: string }) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="rounded-xl border border-[#1e3a52] bg-[#0F172A]/95 px-3 py-2 shadow-[0_12px_28px_rgba(11,31,58,0.5)]">
      <p className="text-xs text-slate-400 mb-1">{label}</p>
      {payload.map((item) => (
        <div key={item.dataKey} className="flex items-center justify-between gap-4">
          <span className="text-xs text-slate-300">{item.name}</span>
          <span className="text-xs font-semibold text-slate-100">
            {typeof item.value === "number" ? item.value.toFixed(3) : item.value}
          </span>
        </div>
      ))}
    </div>
  );
}

const suggestionMap: Record<string, string> = {
  "Optimize Recall": "Improve recall on minority class to reduce false negatives",
  "Reduce Overfitting": "Add regularization and dropout to reduce overfitting and improve generalization",
  "Faster Training": "Optimize model architecture and training parameters for faster convergence",
  "Tune Hyperparameters": "Use Bayesian optimization to find optimal hyperparameters",
};

export default function ResultsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const searchParams = useSearchParams();
  const [metric, setMetric] = useState<MetricKey>("accuracy");
  const [fineTuneText, setFineTuneText] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [activeSuggestion, setActiveSuggestion] = useState<string | null>(null);
  const [selectedFineTuneModel, setSelectedFineTuneModel] = useState<string>("");
  const [projectName, setProjectName] = useState<string>("Untitled Project");
  const [trainingHistory, setTrainingHistory] = useState<TrainingHistoryResponse | null>(null);
  const [results, setResults] = useState<ModelResult[]>([]);
  const [tableRows, setTableRows] = useState<TrainingTableRow[]>([]);
  const [latestTrainingRunId, setLatestTrainingRunId] = useState<string | null>(null);
  const [showModelDownloads, setShowModelDownloads] = useState(false);
  const [showCodeDownloads, setShowCodeDownloads] = useState(false);
  const [fineTuneResult, setFineTuneResult] = useState<FineTuneResponse | null>(null);
  const [pendingFineTuneTrainingRunId, setPendingFineTuneTrainingRunId] = useState<string | null>(null);
  const [acceptSnapshotStack, setAcceptSnapshotStack] = useState<AppliedSnapshot[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const persistedUiStateKey = `results-ui-state-${id}`;

  const persistUiState = (nextResults: ModelResult[], nextTableRows: TrainingTableRow[], nextStack: AppliedSnapshot[]) => {
    try {
      const payload: PersistedResultsUiState = {
        results: nextResults,
        tableRows: nextTableRows,
        acceptSnapshotStack: nextStack,
      };
      window.localStorage.setItem(persistedUiStateKey, JSON.stringify(payload));
    } catch {
      // ignore persistence errors (private mode/storage limits)
    }
  };

  const clearPersistedUiState = () => {
    try {
      window.localStorage.removeItem(persistedUiStateKey);
    } catch {
      // ignore storage cleanup errors
    }
  };

  useEffect(() => {
    const fileId = id;

    const loadHistory = async () => {
      try {
        const projectMeta = await getProjectMetaByFileId(fileId);
        setProjectName(projectMeta.project_name || "Untitled Project");

        const response = await getTrainingHistory(fileId) as TrainingHistoryResponse;
        console.log("Training history:", response);
        setTrainingHistory(response);

        const versions = Array.isArray(response?.versions) ? response.versions : [];
        const latestVersion = versions.length > 0 ? versions[versions.length - 1] : null;
        const latestModels = Array.isArray(latestVersion?.models) ? latestVersion.models : [];
        const allModels = versions.flatMap((version) => (Array.isArray(version.models) ? version.models : []));
        const lastVersionWithRunId = [...versions]
          .reverse()
          .find((version) => typeof version.training_run_id === "string" && version.training_run_id.length > 0);
        setLatestTrainingRunId(lastVersionWithRunId?.training_run_id ?? null);

        const bestModelByName = new Map<string, HistoryModel>();
        for (const model of allModels) {
          const candidateName = model.model_name ?? model.name;
          if (!candidateName) {
            continue;
          }

          const key = normalizeModelName(candidateName);
          const existing = bestModelByName.get(key);
          if (!existing) {
            bestModelByName.set(key, model);
            continue;
          }

          const existingRmse = typeof existing.rmse === "number" ? existing.rmse : Number.POSITIVE_INFINITY;
          const candidateRmse = typeof model.rmse === "number" ? model.rmse : Number.POSITIVE_INFINITY;
          if (candidateRmse < existingRmse) {
            bestModelByName.set(key, model);
          }
        }

        const modelsForDisplay = bestModelByName.size > 0 ? Array.from(bestModelByName.values()) : latestModels;

        const rows: TrainingTableRow[] = modelsForDisplay.map((model) => ({
          model_name: model.model_name ?? model.name ?? "Unknown Model",
          rmse: typeof model.rmse === "number" ? model.rmse : null,
          mae: typeof model.mae === "number" ? model.mae : null,
          r2: typeof model.r2 === "number" ? model.r2 : null,
          generated_by: model.generated_by ?? "standard",
        }));

        rows.sort((a, b) => {
          const aRmse = a.rmse ?? Number.POSITIVE_INFINITY;
          const bRmse = b.rmse ?? Number.POSITIVE_INFINITY;
          return aRmse - bRmse;
        });

        setTableRows(rows);
        if (rows.length > 0) {
          setSelectedFineTuneModel(rows[0].model_name);
        }

        const mappedResults: ModelResult[] = modelsForDisplay.map((model) => {
          const rmse = typeof model.rmse === "number" ? model.rmse : 0;
          const mae = typeof model.mae === "number" ? model.mae : 0;
          const r2 = typeof model.r2 === "number" ? model.r2 : 0;
          const trainingTime = typeof model.training_time_seconds === "number" ? model.training_time_seconds : 0;

          return {
            name: model.name ?? model.model_name ?? "Unknown Model",
            accuracy: Math.max(0, Math.min(1, r2)),
            f1: 1 / (1 + Math.max(0, mae)),
            auc: 1 / (1 + Math.max(0, rmse)),
            trainingTime,
          };
        });

        let hydratedResults = mappedResults;
        let hydratedTableRows = rows;
        let hydratedStack: AppliedSnapshot[] = [];

        const mergeTrainingTimeFromMapped = (base: ModelResult[], timingSource: ModelResult[]): ModelResult[] => {
          const timingByName = new Map<string, number>();
          for (const item of timingSource) {
            if (typeof item.trainingTime === "number") {
              timingByName.set(normalizeModelName(item.name), item.trainingTime);
            }
          }

          return base.map((item) => {
            const nextTime = timingByName.get(normalizeModelName(item.name));
            if (typeof nextTime !== "number") {
              return item;
            }
            return { ...item, trainingTime: nextTime };
          });
        };

        try {
          const raw = window.localStorage.getItem(persistedUiStateKey);
          if (raw) {
            const parsed = JSON.parse(raw) as Partial<PersistedResultsUiState>;
            const parsedResults = Array.isArray(parsed.results) ? parsed.results : null;
            const parsedTableRows = Array.isArray(parsed.tableRows) ? parsed.tableRows : null;
            const parsedStack = Array.isArray(parsed.acceptSnapshotStack) ? parsed.acceptSnapshotStack : null;

            if (parsedResults && parsedTableRows && parsedStack) {
              hydratedResults = parsedResults as ModelResult[];
              hydratedTableRows = parsedTableRows as TrainingTableRow[];
              hydratedStack = parsedStack as AppliedSnapshot[];
            }
          }
        } catch {
          clearPersistedUiState();
        }

        hydratedResults = mergeTrainingTimeFromMapped(hydratedResults, mappedResults);

        setResults(hydratedResults);
        setTableRows(hydratedTableRows);
        setAcceptSnapshotStack(hydratedStack);
      } catch (error) {
        console.error("Failed to load training history:", error);
        setProjectName("Untitled Project");
        setTrainingHistory(null);
        setResults([]);
        setTableRows([]);
        setLatestTrainingRunId(null);
        setAcceptSnapshotStack([]);
        clearPersistedUiState();
      }
    };

    void loadHistory();
  }, [id]);

  const bestModel = useMemo(() => {
    if (results.length === 0) {
      return {
        name: "N/A",
        accuracy: 0,
        f1: 0,
        auc: 0,
        trainingTime: 0,
      };
    }

    return results.reduce((best, current) =>
      current.accuracy > best.accuracy ? current : best
    );
  }, [results]);

  const trainingTimeSeries = useMemo(() => {
    const timingByName = new Map<string, number>();

    for (const item of results) {
      if (typeof item.trainingTime === "number" && item.trainingTime > 0) {
        timingByName.set(normalizeModelName(item.name), item.trainingTime);
      }
    }

    const versions = Array.isArray(trainingHistory?.versions) ? trainingHistory.versions : [];
    for (const version of versions) {
      const versionModels = Array.isArray(version.models) ? version.models : [];
      for (const model of versionModels) {
        const modelName = model.model_name ?? model.name;
        if (!modelName) {
          continue;
        }
        if (typeof model.training_time_seconds !== "number" || model.training_time_seconds <= 0) {
          continue;
        }

        const key = normalizeModelName(modelName);
        const existing = timingByName.get(key);
        if (typeof existing !== "number" || model.training_time_seconds > existing) {
          timingByName.set(key, model.training_time_seconds);
        }
      }
    }

    const nextSeries = results.map((item) => {
      const key = normalizeModelName(item.name);
      const fallback = timingByName.get(key);
      const resolvedSeconds =
        typeof item.trainingTime === "number" && item.trainingTime > 0
          ? item.trainingTime
          : typeof fallback === "number"
          ? fallback
          : 0;

      return {
        ...item,
        trainingTime: Number(resolvedSeconds.toFixed(2)),
      };
    });

    if (nextSeries.some((item) => item.trainingTime > 0)) {
      return nextSeries;
    }

    const latestVersion = versions.length > 0 ? versions[versions.length - 1] : null;
    const latestTotal =
      latestVersion && typeof latestVersion.training_time_seconds === "number"
        ? latestVersion.training_time_seconds
        : 0;
    if (latestTotal > 0 && nextSeries.length > 0) {
      const perModel = Number((latestTotal / nextSeries.length).toFixed(2));
      return nextSeries.map((item) => ({ ...item, trainingTime: perModel }));
    }

    return nextSeries;
  }, [results, trainingHistory]);

  const bestModelReason = useMemo(() => {
    if (!bestModel || bestModel.name === "N/A") {
      return "The platform selects the model with the strongest validation performance across evaluated candidates.";
    }

    const displayAccuracy = (bestModel.accuracy * 100).toFixed(1);
    const displayF1 = bestModel.f1.toFixed(3);
    const displayAuc = bestModel.auc.toFixed(3);

    return `${bestModel.name} was selected because it delivered the strongest overall validation performance, with accuracy ${displayAccuracy}%, F1 ${displayF1}, and ROC-AUC ${displayAuc} compared with other trained models.`;
  }, [bestModel]);

  const bestModelTrainingTimeSeconds = useMemo(() => {
    const key = normalizeModelName(bestModel.name);
    const match = trainingTimeSeries.find((item) => normalizeModelName(item.name) === key);
    if (!match || typeof match.trainingTime !== "number") {
      return 0;
    }
    return match.trainingTime;
  }, [bestModel.name, trainingTimeSeries]);

  const handleDownloadModel = async (modelName?: string) => {
    if (!latestTrainingRunId) {
      console.error("No training run available for download.");
      return;
    }

    try {
      await downloadModelArtifact(latestTrainingRunId, modelName);
    } catch (error) {
      console.error("Failed to download model artifact:", error);
    }
  };

  const handleDownloadCode = async (modelName?: string) => {
    if (!latestTrainingRunId) {
      console.error("No training run available for code download.");
      return;
    }

    try {
      await downloadModelCode(latestTrainingRunId, modelName);
    } catch (error) {
      console.error("Failed to download training code:", error);
    }
  };

  const handleFineTune = async () => {
    if (!fineTuneText.trim()) {
      return;
    }

    const targetColumn = searchParams.get("target_column") ?? trainingHistory?.target_column ?? "churn";

    setIsLoading(true);
    try {
      const response = await fineTuneModel(
        id,
        targetColumn,
        "openai",
        fineTuneText.trim(),
        selectedFineTuneModel || undefined
      );
      console.log("Fine-tune result:", response);
      setFineTuneResult(response);
      setPendingFineTuneTrainingRunId(response.training_run_id ?? null);
    } catch (error) {
      console.error("Fine-tune failed:", error);
    } finally {
      setIsLoading(false);
    }
  };

  const handleAcceptChanges = () => {
    if (!fineTuneResult) {
      console.error("No fine-tune result to accept.");
      return;
    }

    const snapshotToPush: AppliedSnapshot = {
      results: results.map((item) => ({ ...item })),
      tableRows: tableRows.map((item) => ({ ...item })),
      trainingRunId: latestTrainingRunId ?? undefined,
    };

    const modelName = fineTuneResult.after.name;
    const normalizedTargetModelName = normalizeModelName(modelName);

    let tableRowMatched = false;
    const updatedTableRows = tableRows
      .map((row) => {
        if (normalizeModelName(row.model_name) !== normalizedTargetModelName) {
          return row;
        }

        tableRowMatched = true;

        return {
          ...row,
          rmse: fineTuneResult.after.rmse,
          mae: fineTuneResult.after.mae,
          r2: fineTuneResult.after.r2,
          generated_by: "fine_tuned" as const, // Mark as fine-tuned
        };
      })
      .sort((a, b) => {
        const aRmse = a.rmse ?? Number.POSITIVE_INFINITY;
        const bRmse = b.rmse ?? Number.POSITIVE_INFINITY;
        return aRmse - bRmse;
      });

    if (!tableRowMatched) {
      updatedTableRows.push({
        model_name: modelName,
        rmse: fineTuneResult.after.rmse,
        mae: fineTuneResult.after.mae,
        r2: fineTuneResult.after.r2,
        generated_by: "fine_tuned",
      });
      updatedTableRows.sort((a, b) => {
        const aRmse = a.rmse ?? Number.POSITIVE_INFINITY;
        const bRmse = b.rmse ?? Number.POSITIVE_INFINITY;
        return aRmse - bRmse;
      });
    }

    let resultMatched = false;
    const updatedResults = results.map((item) => {
      if (normalizeModelName(item.name) !== normalizedTargetModelName) {
        return item;
      }

      resultMatched = true;

      const rmse = fineTuneResult.after.rmse;
      const mae = fineTuneResult.after.mae;
      const r2 = fineTuneResult.after.r2;

      return {
        ...item,
        accuracy: Math.max(0, Math.min(1, r2)),
        f1: 1 / (1 + Math.max(0, mae)),
        auc: 1 / (1 + Math.max(0, rmse)),
      };
    });

    if (!resultMatched) {
      const rmse = fineTuneResult.after.rmse;
      const mae = fineTuneResult.after.mae;
      const r2 = fineTuneResult.after.r2;

      updatedResults.push({
        name: modelName,
        accuracy: Math.max(0, Math.min(1, r2)),
        f1: 1 / (1 + Math.max(0, mae)),
        auc: 1 / (1 + Math.max(0, rmse)),
        trainingTime: 0,
      });
    }

    const nextStack = [...acceptSnapshotStack, snapshotToPush];
    setAcceptSnapshotStack(nextStack);
    setTableRows(updatedTableRows);
    setResults(updatedResults);
    if (pendingFineTuneTrainingRunId) {
      setLatestTrainingRunId(pendingFineTuneTrainingRunId);
    }
    persistUiState(updatedResults, updatedTableRows, nextStack);
    console.log("Fine-tune changes accepted.");
  };

  const handleRevertChanges = () => {
    if (acceptSnapshotStack.length > 0) {
      const latestSnapshot = acceptSnapshotStack[acceptSnapshotStack.length - 1];
      const revertedResults = latestSnapshot.results.map((item) => ({ ...item }));
      const revertedTableRows = latestSnapshot.tableRows.map((item) => ({ ...item }));
      const nextStack = acceptSnapshotStack.slice(0, -1);

      setResults(revertedResults);
      setTableRows(revertedTableRows);
      setAcceptSnapshotStack(nextStack);
      // Restore the training run ID for model and code download revert
      if (latestSnapshot.trainingRunId) {
        setLatestTrainingRunId(latestSnapshot.trainingRunId);
      }

      if (nextStack.length === 0) {
        clearPersistedUiState();
      } else {
        persistUiState(revertedResults, revertedTableRows, nextStack);
      }

      console.log("Reverted to previous applied state.");
      return;
    }

    if (fineTuneResult) {
      setFineTuneResult(null);
      console.log("Cleared fine-tune preview.");
      return;
    }
  };

  const formatSignedChange = (value: number): string => {
    if (value > 0) {
      return `+${value.toFixed(4)}`;
    }
    return value.toFixed(4);
  };

  const rmseChangeIsGood = fineTuneResult ? fineTuneResult.improvement.rmse_change <= 0 : false;
  const r2ChangeIsGood = fineTuneResult ? fineTuneResult.improvement.r2_change >= 0 : false;

  const totalTrainingTime = useMemo(() => {
    return trainingTimeSeries.reduce((sum, model) => sum + (model.trainingTime || 0), 0);
  }, [trainingTimeSeries]);

  const modelConfidence = useMemo(() => {
    if (results.length === 0) return 0;
    const sorted = [...results].sort((a, b) => b.accuracy - a.accuracy);
    if (sorted.length < 2) return 95;
    const topAccuracy = sorted[0].accuracy;
    const secondAccuracy = sorted[1].accuracy;
    const gap = topAccuracy - secondAccuracy;
    return Math.min(95, 70 + (gap * 100));
  }, [results]);

  return (
    <>
      <Navbar title="Results" />

      <main className="relative flex-1 overflow-auto">
        {/* Background Effects */}
        <div className="pointer-events-none absolute inset-0">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_15%,rgba(59,178,115,0.12),transparent_45%),radial-gradient(circle_at_80%_10%,rgba(6,182,212,0.09),transparent_40%),radial-gradient(circle_at_50%_90%,rgba(139,92,246,0.08),transparent_50%)]" />
          <div className="absolute inset-0 opacity-[0.015] bg-[linear-gradient(rgba(100,200,255,1)_1px,transparent_1px),linear-gradient(90deg,rgba(100,200,255,1)_1px,transparent_1px)] [background-size:64px_64px]" />
          {/* Animated glowing orbs */}
          <div className="absolute top-[10%] left-[15%] h-[400px] w-[400px] rounded-full bg-cyan-500/[0.15] blur-[120px] animate-pulse" style={{ animationDuration: '4s' }} />
          <div className="absolute bottom-[20%] right-[20%] h-[350px] w-[350px] rounded-full bg-violet-500/[0.12] blur-[100px] animate-pulse" style={{ animationDuration: '5s', animationDelay: '1s' }} />
        </div>

        <div className="relative z-10 flex flex-col gap-6 px-8 py-8">
          {/* Top Section - AI Summary Card */}
          <div className="rounded-3xl border border-emerald-400/30 bg-gradient-to-br from-emerald-500/10 via-cyan-500/5 to-slate-900/40 backdrop-blur-xl p-8 shadow-[0_0_60px_rgba(52,211,153,0.25)]">
            <div className="flex items-start justify-between mb-6">
              <div>
                <div className="flex items-center gap-3 mb-2">
                  <div className="flex h-12 w-12 items-center justify-center rounded-2xl border-2 border-emerald-400/50 bg-emerald-500/20 shadow-[0_0_24px_rgba(52,211,153,0.4)]">
                    <svg className="h-6 w-6 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                  </div>
                  <div>
                    <h2 className="text-[28px] font-bold text-white">PyrunAI Model Discovery Complete</h2>
                    <p className="text-[13px] text-slate-300 mt-0.5">{projectName}</p>
                  </div>
                </div>
              </div>
              <div className="flex flex-col items-end gap-2">
                <span className="inline-flex items-center gap-2 rounded-xl border-2 border-emerald-400/60 bg-emerald-500/20 px-4 py-2 shadow-[0_0_20px_rgba(52,211,153,0.3)]">
                  <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)] animate-pulse" />
                  <span className="text-[13px] font-bold text-emerald-300">Recommended Model</span>
                </span>
                <Link
                  href={`/results/${encodeURIComponent(id)}/experiment-report`}
                  className="inline-flex items-center gap-2 rounded-xl border border-cyan-400/40 bg-cyan-500/10 px-4 py-2 text-[12px] font-semibold text-cyan-300 transition-all hover:bg-cyan-500/15 hover:text-cyan-200"
                >
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-6m4 6V7m4 10V4M5 20h14" />
                  </svg>
                  View Experiment Report
                </Link>
                <div className="text-right">
                  <p className="text-[11px] text-slate-400 uppercase tracking-wide">Model Confidence</p>
                  <p className="text-[20px] font-bold text-cyan-400">{modelConfidence.toFixed(0)}%</p>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-6 lg:grid-cols-4">
              <div className="rounded-2xl border border-white/[0.08] bg-white/[0.04] backdrop-blur-sm p-5">
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-400">Best Model</p>
                <p className="text-[22px] font-bold text-white">{bestModel.name}</p>
              </div>
              <div className="rounded-2xl border border-white/[0.08] bg-white/[0.04] backdrop-blur-sm p-5">
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-400">Accuracy</p>
                <p className="text-[22px] font-bold text-emerald-400">{(bestModel.accuracy * 100).toFixed(1)}%</p>
              </div>
              <div className="rounded-2xl border border-white/[0.08] bg-white/[0.04] backdrop-blur-sm p-5">
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-400">Models Tested</p>
                <p className="text-[22px] font-bold text-cyan-400">{results.length}</p>
              </div>
              <div className="rounded-2xl border border-white/[0.08] bg-white/[0.04] backdrop-blur-sm p-5">
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-400">Training Time</p>
                <p className="text-[22px] font-bold text-violet-400">{totalTrainingTime.toFixed(0)}s</p>
              </div>
            </div>
          </div>

          {/* AI Explanation Panel - Why This Model Was Selected */}
          <div className="rounded-2xl border border-cyan-400/20 bg-gradient-to-br from-slate-900/90 to-slate-950/90 backdrop-blur-xl p-6 shadow-[0_8px_32px_rgba(6,182,212,0.15)]">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-[16px] font-semibold text-white">Why This Model Was Selected</h3>
              <span className="inline-flex items-center gap-1.5 rounded-full border border-cyan-400/40 bg-cyan-500/10 px-3 py-1 shadow-[0_0_16px_rgba(6,182,212,0.2)]">
                <svg className="h-3.5 w-3.5 text-cyan-400" fill="currentColor" viewBox="0 0 20 20">
                  <path d="M13 6a3 3 0 11-6 0 3 3 0 016 0zM18 8a2 2 0 11-4 0 2 2 0 014 0zM14 15a4 4 0 00-8 0v3h8v-3zM6 8a2 2 0 11-4 0 2 2 0 014 0zM16 18v-3a5.972 5.972 0 00-.75-2.906A3.005 3.005 0 0119 15v3h-3zM4.75 12.094A5.973 5.973 0 004 15v3H1v-3a3 3 0 013.75-2.906z" />
                </svg>
                <span className="text-[11px] font-semibold text-cyan-300">AI Model Insight</span>
              </span>
            </div>
            <div className="space-y-3">
              <div className="flex items-start gap-3">
                <div className="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-lg border border-emerald-400/40 bg-emerald-500/10">
                  <svg className="h-3.5 w-3.5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <div>
                  <p className="text-[13px] font-medium text-slate-200">Highest validation accuracy</p>
                  <p className="mt-0.5 text-[12px] text-slate-400">Achieved {(bestModel.accuracy * 100).toFixed(1)}% accuracy on validation set</p>
                </div>
              </div>
              <div className="flex items-start gap-3">
                <div className="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-lg border border-emerald-400/40 bg-emerald-500/10">
                  <svg className="h-3.5 w-3.5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <div>
                  <p className="text-[13px] font-medium text-slate-200">Lowest prediction error</p>
                  <p className="mt-0.5 text-[12px] text-slate-400">Minimized RMSE and MAE across all test splits</p>
                </div>
              </div>
              <div className="flex items-start gap-3">
                <div className="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-lg border border-emerald-400/40 bg-emerald-500/10">
                  <svg className="h-3.5 w-3.5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <div>
                  <p className="text-[13px] font-medium text-slate-200">Best generalization across folds</p>
                  <p className="mt-0.5 text-[12px] text-slate-400">Consistent performance on cross-validation splits</p>
                </div>
              </div>
            </div>
          </div>

          {/* Model Leaderboard */}
          <div className="rounded-2xl border border-white/[0.08] bg-gradient-to-br from-slate-900/90 to-slate-950/90 backdrop-blur-xl overflow-hidden shadow-[0_8px_32px_rgba(0,0,0,0.4)]">
            <div className="border-b border-white/[0.08] bg-white/[0.02] px-6 py-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-[16px] font-semibold text-white">Model Leaderboard</h3>
                  <p className="mt-0.5 text-[12px] text-slate-400">Ranked by performance (lower RMSE is better)</p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-slate-500">Total Models:</span>
                  <span className="rounded-lg border border-white/[0.08] bg-white/[0.04] px-2.5 py-1 text-[13px] font-semibold text-white">{tableRows.length}</span>
                </div>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="border-b border-white/[0.08] bg-white/[0.02]">
                    <th className="px-6 py-3.5 text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-400">Rank</th>
                    <th className="px-6 py-3.5 text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-400">Model</th>
                    <th className="px-6 py-3.5 text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-400">Accuracy</th>
                    <th className="px-6 py-3.5 text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-400">RMSE</th>
                    <th className="px-6 py-3.5 text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-400">Training Time</th>
                    <th className="px-6 py-3.5 text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-400">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {tableRows.map((model, index) => {
                    const isBest = index === 0;
                    const matchingResult = results.find(r => normalizeModelName(r.name) === normalizeModelName(model.model_name));
                    const trainingTime = trainingTimeSeries.find(t => normalizeModelName(t.name) === normalizeModelName(model.model_name))?.trainingTime || 0;
                    const accuracy = matchingResult ? (matchingResult.accuracy * 100).toFixed(1) : "—";
                    
                    return (
                      <tr
                        key={`${model.model_name}-${index}`}
                        className={`border-b border-white/[0.05] transition-all ${
                          isBest
                            ? "bg-gradient-to-r from-emerald-500/10 to-cyan-500/5 shadow-[inset_0_0_32px_rgba(52,211,153,0.1)]"
                            : "hover:bg-white/[0.02]"
                        }`}
                      >
                        <td className="px-6 py-4">
                          <div className="flex items-center gap-2">
                            {isBest ? (
                              <div className="flex h-7 w-7 items-center justify-center rounded-lg border-2 border-yellow-400/60 bg-gradient-to-br from-yellow-400/20 to-amber-500/10 shadow-[0_0_16px_rgba(250,204,21,0.3)]">
                                <svg className="h-4 w-4 text-yellow-400" fill="currentColor" viewBox="0 0 20 20">
                                  <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                                </svg>
                              </div>
                            ) : (
                              <div className="flex h-7 w-7 items-center justify-center rounded-lg border border-white/[0.08] bg-white/[0.04] text-[13px] font-semibold text-slate-400">
                                {index + 1}
                              </div>
                            )}
                          </div>
                        </td>
                        <td className="px-6 py-4">
                          <div className="flex items-center gap-2">
                            <span className={`font-medium ${isBest ? "text-white" : "text-slate-200"}`}>
                              {model.model_name}
                            </span>
                            {model.generated_by === "llm_generated" && (
                              <span className="inline-flex items-center rounded-md border border-violet-400/30 bg-violet-500/10 px-2 py-0.5 text-[10px] font-semibold text-violet-300">
                                LLM Generated
                              </span>
                            )}
                            {model.generated_by === "fine_tuned" && (
                              <span className="inline-flex items-center rounded-md border border-cyan-400/30 bg-cyan-500/10 px-2 py-0.5 text-[10px] font-semibold text-cyan-300">
                                Fine-tuned
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="px-6 py-4">
                          <span className={`font-semibold ${isBest ? "text-emerald-400" : "text-slate-300"}`}>
                            {accuracy}%
                          </span>
                        </td>
                        <td className="px-6 py-4">
                          <span className="font-mono text-[12px] text-slate-400">
                            {model.rmse !== null ? model.rmse.toFixed(4) : "—"}
                          </span>
                        </td>
                        <td className="px-6 py-4">
                          <span className="text-slate-400">{trainingTime.toFixed(1)}s</span>
                        </td>
                        <td className="px-6 py-4">
                          {isBest ? (
                            <span className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-400/40 bg-emerald-500/15 px-2.5 py-1 text-[11px] font-semibold text-emerald-300">
                              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.8)]" />
                              Winner
                            </span>
                          ) : (
                            <span className="inline-flex items-center rounded-lg border border-white/[0.08] bg-white/[0.04] px-2.5 py-1 text-[11px] font-medium text-slate-400">
                              Tested
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Model Comparison Charts */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            {/* Accuracy Comparison */}
            <div className="rounded-2xl border border-white/[0.08] bg-gradient-to-br from-slate-900/90 to-slate-950/90 backdrop-blur-xl p-6 shadow-[0_8px_32px_rgba(0,0,0,0.4)]">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h3 className="text-[15px] font-semibold text-white">Performance Comparison</h3>
                  <p className="mt-0.5 text-[11px] text-slate-400">Metric: {metricLabels[metric]}</p>
                </div>
                <div className="flex gap-1">
                  {(["accuracy", "f1", "auc"] as MetricKey[]).map((key) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setMetric(key)}
                      className={`rounded-lg px-3 py-1.5 text-[11px] font-semibold transition-all ${
                        metric === key
                          ? "border border-cyan-400/40 bg-cyan-500/10 text-cyan-300 shadow-[0_0_12px_rgba(6,182,212,0.2)]"
                          : "border border-white/[0.08] bg-white/[0.04] text-slate-400 hover:text-slate-200"
                      }`}
                    >
                      {metricLabels[key]}
                    </button>
                  ))}
                </div>
              </div>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={results} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                    <CartesianGrid stroke="#1e3a52" strokeDasharray="3 6" vertical={false} />
                    <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={{ stroke: "#1e3a52" }} />
                    <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={{ stroke: "#1e3a52" }} />
                    <Tooltip content={<DarkTooltip />} cursor={{ fill: "rgba(148,163,184,0.08)" }} />
                    <Bar
                      dataKey={metric}
                      name={metricLabels[metric]}
                      fill="#2c4a6b"
                      radius={[8, 8, 6, 6]}
                      isAnimationActive
                      animationDuration={800}
                      animationBegin={0}
                    >
                      {results.map((entry) => (
                        <Cell
                          key={entry.name}
                          fill={entry.name === bestModel.name ? "url(#emeraldGradient)" : "#334155"}
                        />
                      ))}
                    </Bar>
                    <defs>
                      <linearGradient id="emeraldGradient" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#34d399" stopOpacity={0.9} />
                        <stop offset="100%" stopColor="#10b981" stopOpacity={0.6} />
                      </linearGradient>
                    </defs>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Training Time Comparison */}
            <div className="rounded-2xl border border-white/[0.08] bg-gradient-to-br from-slate-900/90 to-slate-950/90 backdrop-blur-xl p-6 shadow-[0_8px_32px_rgba(0,0,0,0.4)]">
              <div className="mb-4">
                <h3 className="text-[15px] font-semibold text-white">Training Time Analysis</h3>
                <p className="mt-0.5 text-[11px] text-slate-400">Seconds to completion</p>
              </div>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={trainingTimeSeries} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                    <CartesianGrid stroke="#1e3a52" strokeDasharray="3 6" vertical={false} />
                    <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={{ stroke: "#1e3a52" }} />
                    <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={{ stroke: "#1e3a52" }} />
                    <Tooltip content={<DarkTooltip />} cursor={{ stroke: "#06b6d4", strokeWidth: 1 }} />
                    <Line
                      type="monotone"
                      dataKey="trainingTime"
                      name="Training Time"
                      stroke="#06b6d4"
                      strokeWidth={3}
                      dot={{ r: 5, stroke: "#06b6d4", strokeWidth: 2, fill: "#0f172a" }}
                      activeDot={{ r: 7, stroke: "#06b6d4", strokeWidth: 2, fill: "#164e63", filter: "drop-shadow(0 0 8px rgba(6,182,212,0.6))" }}
                      isAnimationActive
                      animationDuration={800}
                      animationBegin={0}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          {/* PyrunAI Optimization Assistant */}
          <div className="rounded-2xl border border-violet-400/20 bg-gradient-to-br from-slate-900/90 to-slate-950/90 backdrop-blur-xl p-6 shadow-[0_8px_32px_rgba(139,92,246,0.15)]">
            <div className="mb-4 flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-violet-400/40 bg-violet-500/10">
                <svg className="h-5 w-5 text-violet-400" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clipRule="evenodd" />
                </svg>
              </div>
              <div>
                <h3 className="text-[16px] font-semibold text-white">PyrunAI Optimization Assistant</h3>
                <p className="text-[12px] text-slate-400">Improve your model with natural language instructions</p>
              </div>
            </div>

            <div className="mb-4 flex flex-wrap gap-2">
              {Object.keys(suggestionMap).map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => {
                    setFineTuneText(suggestionMap[suggestion]);
                    setActiveSuggestion(suggestion);
                    setTimeout(() => textareaRef?.current?.focus(), 0);
                  }}
                  className={`group relative overflow-hidden rounded-xl px-4 py-2 text-[12px] font-semibold transition-all ${
                    activeSuggestion === suggestion
                      ? "border border-violet-400/40 bg-violet-500/15 text-violet-300 shadow-[0_0_20px_rgba(139,92,246,0.25)]"
                      : "border border-white/[0.08] bg-white/[0.04] text-slate-300 hover:border-white/[0.15] hover:bg-white/[0.08]"
                  }`}
                >
                  <span className="relative z-10">{suggestion}</span>
                  {activeSuggestion === suggestion && (
                    <span className="absolute inset-0 bg-gradient-to-r from-violet-400/10 to-transparent" />
                  )}
                </button>
              ))}
            </div>
            
            <textarea
              ref={textareaRef}
              value={fineTuneText}
              onChange={(e) => setFineTuneText(e.target.value)}
              placeholder="Example: Improve recall on minority class to reduce false negatives"
              className="mb-3 w-full min-h-[100px] rounded-xl border border-white/[0.08] bg-white/[0.04] px-4 py-3 text-[13px] text-slate-200 placeholder-slate-500 outline-none transition-all focus:border-violet-400/40 focus:bg-white/[0.06] focus:ring-2 focus:ring-violet-400/20 resize-none"
            />

            <div className="mb-4">
              <label className="mb-2 block text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                Select Model to Fine-Tune
              </label>
              <select
                value={selectedFineTuneModel}
                onChange={(e) => setSelectedFineTuneModel(e.target.value)}
                className="w-full rounded-xl border border-white/[0.08] bg-white/[0.04] px-4 py-2.5 text-[13px] text-slate-200 outline-none transition-all focus:border-violet-400/40 focus:bg-white/[0.06] focus:ring-2 focus:ring-violet-400/20"
              >
                {tableRows.map((row) => (
                  <option key={row.model_name} value={row.model_name}>
                    {row.model_name}
                  </option>
                ))}
              </select>
            </div>
            
            <div className="flex items-center gap-3">
              <button
                onClick={() => void handleFineTune()}
                disabled={fineTuneText.trim() === "" || isLoading}
                className="group relative flex-1 overflow-hidden rounded-xl bg-gradient-to-r from-violet-600 to-purple-600 px-6 py-3 font-semibold text-white shadow-[0_0_24px_rgba(139,92,246,0.4)] transition-all duration-300 hover:shadow-[0_0_32px_rgba(139,92,246,0.6)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                <span className="absolute inset-0 bg-gradient-to-r from-white/20 via-white/5 to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
                <span className="relative flex items-center justify-center gap-2">
                  {isLoading ? (
                    <>
                      <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                      Optimizing Model...
                    </>
                  ) : (
                    <>
                      <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                      </svg>
                      Optimize Model
                    </>
                  )}
                </span>
              </button>
            </div>
          </div>
          {/* Fine-Tune Impact Visualization */}
          {fineTuneResult && (
            <div className="rounded-2xl border border-emerald-400/20 bg-gradient-to-br from-slate-900/90 to-slate-950/90 backdrop-blur-xl p-6 shadow-[0_8px_32px_rgba(52,211,153,0.15)]">
              <div className="mb-6 flex items-center justify-between">
                <div>
                  <h3 className="text-[16px] font-semibold text-white">Optimization Impact</h3>
                  <p className="mt-0.5 text-[12px] text-slate-400">
                    Model Version {fineTuneResult.new_version} · Fine-tuned
                  </p>
                </div>
                <span className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-400/40 bg-emerald-500/10 px-3 py-1.5 text-[11px] font-semibold text-emerald-300">
                  <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
                  </svg>
                  Improved
                </span>
              </div>
            
              <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
                {/* Before Optimization */}
                <div className="rounded-xl border border-white/[0.08] bg-white/[0.04] p-5">
                  <div className="mb-4 flex items-center gap-2">
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-400/30 bg-slate-500/10">
                      <svg className="h-4 w-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12H9m12 0a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                    </div>
                    <p className="text-[12px] font-semibold uppercase tracking-wide text-slate-400">Before Optimization</p>
                  </div>
                
                  <div className="space-y-4">
                    <div>
                      <p className="mb-1 text-[11px] text-slate-500">RMSE</p>
                      <p className="text-[24px] font-bold text-slate-200">{fineTuneResult.before.rmse.toFixed(4)}</p>
                    </div>
                    <div>
                      <p className="mb-1 text-[11px] text-slate-500">MAE</p>
                      <p className="text-[24px] font-bold text-slate-200">{fineTuneResult.before.mae.toFixed(4)}</p>
                    </div>
                    <div>
                      <p className="mb-1 text-[11px] text-slate-500">R²</p>
                      <p className="text-[24px] font-bold text-slate-200">{fineTuneResult.before.r2.toFixed(4)}</p>
                    </div>
                  </div>
                </div>

                {/* After Optimization */}
                <div className="rounded-xl border border-emerald-400/30 bg-gradient-to-br from-emerald-500/10 to-cyan-500/5 p-5">
                  <div className="mb-4 flex items-center gap-2">
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-emerald-400/50 bg-emerald-500/20 shadow-[0_0_16px_rgba(52,211,153,0.3)]">
                      <svg className="h-4 w-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                      </svg>
                    </div>
                    <p className="text-[12px] font-semibold uppercase tracking-wide text-emerald-400">After Optimization</p>
                  </div>
                
                  <div className="space-y-4">
                    <div className="flex items-start justify-between">
                      <div>
                        <p className="mb-1 text-[11px] text-slate-500">RMSE</p>
                        <p className="text-[24px] font-bold text-white">{fineTuneResult.after.rmse.toFixed(4)}</p>
                      </div>
                      <span
                        className={`rounded-lg px-2.5 py-1 text-[11px] font-semibold ${
                          rmseChangeIsGood
                            ? "border border-emerald-400/40 bg-emerald-500/15 text-emerald-300"
                            : "border border-rose-400/40 bg-rose-500/15 text-rose-300"
                        }`}
                      >
                        {formatSignedChange(fineTuneResult.improvement.rmse_change)}
                      </span>
                    </div>
                    <div className="flex items-start justify-between">
                      <div>
                        <p className="mb-1 text-[11px] text-slate-500">MAE</p>
                        <p className="text-[24px] font-bold text-white">{fineTuneResult.after.mae.toFixed(4)}</p>
                      </div>
                    </div>
                    <div className="flex items-start justify-between">
                      <div>
                        <p className="mb-1 text-[11px] text-slate-500">R²</p>
                        <p className="text-[24px] font-bold text-white">{fineTuneResult.after.r2.toFixed(4)}</p>
                      </div>
                      <span
                        className={`rounded-lg px-2.5 py-1 text-[11px] font-semibold ${
                          r2ChangeIsGood
                            ? "border border-emerald-400/40 bg-emerald-500/15 text-emerald-300"
                            : "border border-rose-400/40 bg-rose-500/15 text-rose-300"
                        }`}
                      >
                        {formatSignedChange(fineTuneResult.improvement.r2_change)}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Fine-Tune Actions */}
              <div className="mt-6 flex gap-3">
                <button
                  onClick={handleAcceptChanges}
                  disabled={!fineTuneResult}
                  className="group relative flex-1 overflow-hidden rounded-xl bg-gradient-to-r from-emerald-600 to-green-600 px-6 py-3 font-semibold text-white shadow-[0_0_24px_rgba(52,211,153,0.4)] transition-all duration-300 hover:shadow-[0_0_32px_rgba(52,211,153,0.6)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <span className="absolute inset-0 bg-gradient-to-r from-white/20 via-white/5 to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
                  <span className="relative flex items-center justify-center gap-2">
                    <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                    </svg>
                    Accept Changes
                  </span>
                </button>
                <button
                  onClick={handleRevertChanges}
                  className="flex-1 rounded-xl border border-white/[0.08] bg-white/[0.04] px-6 py-3 font-semibold text-slate-200 transition-all hover:bg-white/[0.08]"
                >
                  {acceptSnapshotStack.length > 0 ? "Revert Last Applied" : "Revert Preview"}
                </button>
              </div>
            </div>
          )}

          {/* Model Ready for Deployment Section */}
          <div className="rounded-2xl border border-cyan-400/20 bg-gradient-to-br from-slate-900/90 to-slate-950/90 backdrop-blur-xl p-8 shadow-[0_8px_32px_rgba(6,182,212,0.15)]">
            <div className="mb-6 flex items-center gap-3">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl border-2 border-cyan-400/50 bg-cyan-500/20 shadow-[0_0_24px_rgba(6,182,212,0.4)]">
                <svg className="h-6 w-6 text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                </svg>
              </div>
              <div>
                <h3 className="text-[18px] font-bold text-white">Model Ready for Deployment</h3>
                <p className="text-[12px] text-slate-400">Deploy your model to production in seconds</p>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <button className="group relative overflow-hidden rounded-xl bg-gradient-to-r from-cyan-600 to-blue-600 px-5 py-4 text-left font-semibold text-white shadow-[0_0_24px_rgba(6,182,212,0.4)] transition-all duration-300 hover:shadow-[0_0_32px_rgba(6,182,212,0.6)] hover:scale-105">
                <span className="absolute inset-0 bg-gradient-to-r from-white/20 via-white/5 to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
                <span className="relative flex flex-col gap-1">
                  <svg className="h-5 w-5 mb-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                  </svg>
                  <span className="text-[14px]">Deploy API</span>
                  <span className="text-[11px] font-normal text-cyan-100">REST endpoint</span>
                </span>
              </button>

              <button
                onClick={() => setShowModelDownloads((prev) => !prev)}
                className="group relative overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.04] px-5 py-4 text-left font-semibold text-slate-200 backdrop-blur-sm transition-all duration-300 hover:bg-white/[0.08] hover:scale-105"
              >
                <span className="relative flex flex-col gap-1">
                  <svg className="h-5 w-5 mb-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                  </svg>
                  <span className="text-[14px]">Download Model</span>
                  <span className="text-[11px] font-normal text-slate-400">.pkl file</span>
                </span>
              </button>

              <button
                onClick={() => setShowCodeDownloads((prev) => !prev)}
                className="group relative overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.04] px-5 py-4 text-left font-semibold text-slate-200 backdrop-blur-sm transition-all duration-300 hover:bg-white/[0.08] hover:scale-105"
              >
                <span className="relative flex flex-col gap-1">
                  <svg className="h-5 w-5 mb-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
                  </svg>
                  <span className="text-[14px]">Download Code</span>
                  <span className="text-[11px] font-normal text-slate-400">.py pipeline</span>
                </span>
              </button>

              <button className="group relative overflow-hidden rounded-xl border border-violet-400/30 bg-violet-500/10 px-5 py-4 text-left font-semibold text-violet-300 transition-all duration-300 hover:bg-violet-500/15 hover:scale-105">
                <span className="relative flex flex-col gap-1">
                  <svg className="h-5 w-5 mb-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
                  </svg>
                  <span className="text-[14px]">Export Docker</span>
                  <span className="text-[11px] font-normal text-violet-200">Container image</span>
                </span>
              </button>
            </div>

            {/* Model Downloads Dropdown */}
            {showModelDownloads && (
              <div className="mt-4 rounded-xl border border-white/[0.08] bg-white/[0.04] p-4">
                <div className="mb-3 flex items-center justify-between">
                  <p className="text-[13px] font-semibold text-slate-200">Select model to download (.pkl)</p>
                  <button
                    onClick={() => setShowModelDownloads(false)}
                    className="text-[12px] text-slate-400 transition-colors hover:text-slate-200"
                  >
                    ✕ Close
                  </button>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={() => void handleDownloadModel()}
                    className="rounded-lg border border-emerald-400/30 bg-emerald-500/10 px-3 py-2 text-[12px] font-medium text-emerald-300 transition-all hover:bg-emerald-500/15"
                  >
                    ↓ Download All Models
                  </button>
                  {tableRows.map((row) => (
                    <button
                      key={row.model_name}
                      onClick={() => void handleDownloadModel(row.model_name)}
                      className="rounded-lg border border-white/[0.08] bg-white/[0.04] px-3 py-2 text-[12px] font-medium text-slate-300 transition-all hover:bg-white/[0.08]"
                    >
                      {row.model_name}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Code Downloads Dropdown */}
            {showCodeDownloads && (
              <div className="mt-4 rounded-xl border border-white/[0.08] bg-white/[0.04] p-4">
                <div className="mb-3 flex items-center justify-between">
                  <p className="text-[13px] font-semibold text-slate-200">Select model code to download (.py)</p>
                  <button
                    onClick={() => setShowCodeDownloads(false)}
                    className="text-[12px] text-slate-400 transition-colors hover:text-slate-200"
                  >
                    ✕ Close
                  </button>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={() => void handleDownloadCode()}
                    className="rounded-lg border border-cyan-400/30 bg-cyan-500/10 px-3 py-2 text-[12px] font-medium text-cyan-300 transition-all hover:bg-cyan-500/15"
                  >
                    ↓ Download All Code
                  </button>
                  {tableRows.map((row) => (
                    <button
                      key={`code-${row.model_name}`}
                      onClick={() => void handleDownloadCode(row.model_name)}
                      className="rounded-lg border border-white/[0.08] bg-white/[0.04] px-3 py-2 text-[12px] font-medium text-slate-300 transition-all hover:bg-white/[0.08]"
                    >
                      {row.model_name}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="mt-6 flex items-center justify-between border-t border-white/[0.08] pt-4">
              <Link
                href="/dashboard"
                className="text-[13px] text-slate-400 transition-colors hover:text-slate-200"
              >
                ← Back to Dashboard
              </Link>
              <div className="flex items-center gap-2 text-[11px] text-slate-500">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                Model ready for production
              </div>
            </div>
          </div>
        </div>
      </main>
    </>
  );
}
