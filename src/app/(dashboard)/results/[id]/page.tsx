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

  return (
    <>
      <Navbar title="Results" />

      <main className="flex-1 px-8 py-8 overflow-auto">
        <div className="max-w-6xl mx-auto space-y-8">
          {/* Header */}
          <div className="flex items-start justify-between">
            <div>
              <p className="text-xs text-slate-500 font-medium uppercase tracking-wide mb-1">
                Project
              </p>
              <h2 className="text-[23px] font-semibold tracking-tight text-slate-100">{projectName}</h2>
              <p className="text-sm text-slate-400 mt-1">
                Training complete · {results.length} models evaluated
              </p>
            </div>
            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-[#123C66] text-slate-200 text-xs font-semibold rounded-lg border border-[#1e3a52]">
              Recommended
            </span>
          </div>

          {/* Best Model Highlight */}
          <div className="bg-[#0F172A]/70 backdrop-blur-sm rounded-2xl border border-[#1e3a52]/85 shadow-[0_0_28px_rgba(11,31,58,0.6)] p-6">
            <div className="flex items-start justify-between gap-6">
              <div>
                <p className="text-xs text-slate-400 uppercase tracking-wide">Best Model</p>
                <h3 className="text-[28px] font-semibold tracking-tight text-slate-100 mt-1">
                  {bestModel.name}
                </h3>
                <div className="flex items-center gap-6 mt-4">
                  <div>
                    <p className="text-xs text-slate-400">Accuracy</p>
                    <p className="text-3xl font-semibold text-slate-100">
                      {(bestModel.accuracy * 100).toFixed(1)}%
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400">F1 Score</p>
                    <p className="text-lg font-semibold text-slate-200">
                      {bestModel.f1.toFixed(3)}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400">Training Time</p>
                    <p className="text-lg font-semibold text-slate-200">
                      {bestModelTrainingTimeSeconds.toFixed(2)}s
                    </p>
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="px-2.5 py-1 rounded-md text-xs font-semibold bg-[#123C66] text-slate-200 border border-[#1e3a52]">
                  Recommended
                </span>
              </div>
            </div>
          </div>

          {/* Why This Model Was Selected */}
          <div className="mt-6 bg-[#0F172A] rounded-2xl border border-[#1e3a52] p-6">
            <div className="relative">
              <div className="absolute top-3 right-3">
                <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold bg-[#123C66] border border-[#1e3a52] text-slate-300">
                  AI Insight
                </span>
              </div>
              <h3 className="text-lg font-semibold text-white mb-2">Why This Model Was Selected</h3>
              <p className="text-sm text-slate-400">
                {bestModelReason}
              </p>
            </div>
          </div>

          {/* AI Fine-Tune Assistant */}
          <div className="mt-8 bg-[#0F172A] rounded-2xl border border-[#1e3a52] p-6 hover:shadow-[0_0_20px_rgba(59,178,115,0.15)] transition-all">
            <h3 className="text-lg font-semibold text-white mb-2">AI Fine-Tune Assistant</h3>
            <p className="text-sm text-slate-400 mb-4">Improve your best model using natural language instructions.</p>
            
            <textarea
              ref={textareaRef}
              value={fineTuneText}
              onChange={(e) => setFineTuneText(e.target.value)}
              placeholder="Example: Improve recall on minority class"
              className="w-full min-h-[120px] rounded-xl bg-[#0B1F3A] border border-[#1e3a52] text-slate-200 placeholder-slate-500 p-3 outline-none focus:ring-2 focus:ring-[#3BB273]/50 focus:border-transparent resize-none transition-all"
            />

            <div className="mt-3">
              <label className="block text-xs text-slate-400 mb-1">Model to Fine-Tune</label>
              <select
                value={selectedFineTuneModel}
                onChange={(e) => setSelectedFineTuneModel(e.target.value)}
                className="w-full px-3 py-2 rounded-xl bg-[#0B1F3A] border border-[#1e3a52] text-slate-200 text-sm outline-none focus:ring-2 focus:ring-[#3BB273]/50 focus:border-transparent"
              >
                {tableRows.map((row) => (
                  <option key={row.model_name} value={row.model_name}>
                    {row.model_name}
                  </option>
                ))}
              </select>
            </div>
            
            <div className="flex flex-wrap gap-2 mt-4 mb-4">
              {Object.keys(suggestionMap).map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => {
                    setFineTuneText(suggestionMap[suggestion]);
                    setActiveSuggestion(suggestion);
                    setTimeout(() => textareaRef?.current?.focus(), 0);
                  }}
                  className={`text-sm rounded-full px-4 py-1 transition-all font-medium ${
                    activeSuggestion === suggestion
                      ? "bg-[#3BB273]/30 text-[#5EDC8A] border border-[#3BB273]/50 shadow-[0_0_12px_rgba(59,178,115,0.3)]"
                      : "bg-[#123C66] text-slate-200 border border-transparent hover:bg-[#1e3a52] hover:border-[#2c5278]"
                  }`}
                >
                  {suggestion}
                </button>
              ))}
            </div>
            
            <div className="flex gap-2 items-center mt-4">
              <button
                onClick={() => void handleFineTune()}
                disabled={fineTuneText.trim() === "" || isLoading}
                className="bg-[#3BB273] hover:bg-[#2FA565] disabled:bg-[#123C66] disabled:text-slate-400 disabled:cursor-not-allowed text-white font-medium rounded-xl px-6 py-2 transition-all opacity-100 disabled:opacity-75 flex items-center gap-2 justify-center shadow-[0_0_20px_rgba(59,178,115,0.3)] hover:shadow-[0_0_24px_rgba(59,178,115,0.4)]"
              >
                {isLoading ? (
                  <>
                    <svg className="w-4 h-4 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                    Optimizing...
                  </>
                ) : (
                  "Fine-Tune Model"
                )}
              </button>
              {isLoading && (
                <button
                  onClick={() => setIsLoading(false)}
                  className="border border-[#1e3a52] text-slate-300 hover:bg-[#123C66] rounded-xl px-4 py-2 transition-colors flex items-center gap-2"
                >
                  <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                    <rect x="6" y="4" width="4" height="16" />
                    <rect x="14" y="4" width="4" height="16" />
                  </svg>
                  Pause
                </button>
              )}
            </div>
          </div>

          {/* Fine-Tune Results Comparison */}
          <div className="mt-6 bg-[#0F172A] rounded-2xl border border-[#1e3a52] p-6">
            <p className="border-t border-[#1e3a52] pt-4 text-xs text-slate-400 mb-2">
              {fineTuneResult
                ? `Model Version ${fineTuneResult.new_version} · Fine-tuned`
                : "Model Version 2 · Optimized for Recall"}
            </p>
            <h3 className="text-lg font-semibold text-white mb-6">Fine-Tune Results</h3>
            
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Before Optimization */}
              <div className="border border-[#1e3a52] rounded-xl bg-[#0B1F3A]/50 p-5">
                <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-4">Before Optimization</p>
                
                <div className="space-y-4">
                  <div>
                    <p className="text-xs text-slate-500 mb-1">RMSE</p>
                    <p className="text-2xl font-semibold text-slate-100">{fineTuneResult ? fineTuneResult.before.rmse.toFixed(4) : "—"}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">MAE</p>
                    <p className="text-2xl font-semibold text-slate-100">{fineTuneResult ? fineTuneResult.before.mae.toFixed(4) : "—"}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">R²</p>
                    <p className="text-2xl font-semibold text-slate-100">{fineTuneResult ? fineTuneResult.before.r2.toFixed(4) : "—"}</p>
                  </div>
                </div>
              </div>

              {/* After Optimization */}
              <div className="border border-[#1e3a52] rounded-xl bg-[#0B1F3A]/50 p-5">
                <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-4">After Optimization</p>
                
                <div className="space-y-4">
                  <div className="flex items-start justify-between">
                    <div>
                      <p className="text-xs text-slate-500 mb-1">RMSE</p>
                      <p className="text-2xl font-semibold text-slate-100">{fineTuneResult ? fineTuneResult.after.rmse.toFixed(4) : "—"}</p>
                    </div>
                    {fineTuneResult && (
                      <span
                        className={`text-xs font-semibold px-2.5 py-1 rounded ${
                          rmseChangeIsGood
                            ? "text-[#5EDC8A] bg-[#3BB273]/10"
                            : "text-rose-300 bg-rose-500/10"
                        }`}
                      >
                        {formatSignedChange(fineTuneResult.improvement.rmse_change)}
                      </span>
                    )}
                  </div>
                  <div className="flex items-start justify-between">
                    <div>
                      <p className="text-xs text-slate-500 mb-1">MAE</p>
                      <p className="text-2xl font-semibold text-slate-100">{fineTuneResult ? fineTuneResult.after.mae.toFixed(4) : "—"}</p>
                    </div>
                  </div>
                  <div className="flex items-start justify-between">
                    <div>
                      <p className="text-xs text-slate-500 mb-1">R²</p>
                      <p className="text-2xl font-semibold text-slate-100">{fineTuneResult ? fineTuneResult.after.r2.toFixed(4) : "—"}</p>
                    </div>
                    {fineTuneResult && (
                      <span
                        className={`text-xs font-semibold px-2.5 py-1 rounded ${
                          r2ChangeIsGood
                            ? "text-[#5EDC8A] bg-[#3BB273]/10"
                            : "text-rose-300 bg-rose-500/10"
                        }`}
                      >
                        {formatSignedChange(fineTuneResult.improvement.r2_change)}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </div>

            {/* Fine-Tune Actions */}
            <div className="flex gap-3 mt-6">
              <button
                onClick={handleAcceptChanges}
                disabled={!fineTuneResult}
                className="flex-1 bg-[#3BB273] hover:bg-[#2FA565] disabled:bg-[#123C66] disabled:text-slate-400 disabled:cursor-not-allowed text-white font-medium rounded-xl px-6 py-2.5 transition-all shadow-[0_0_20px_rgba(59,178,115,0.3)] hover:shadow-[0_0_24px_rgba(59,178,115,0.4)]"
              >
                Accept Changes
              </button>
              <button
                onClick={handleRevertChanges}
                className="flex-1 border border-[#1e3a52] hover:bg-[#123C66] text-slate-200 font-medium rounded-xl px-6 py-2.5 transition-colors"
              >
                {acceptSnapshotStack.length > 0 ? "Revert Last Applied Change" : "Revert Preview"}
              </button>
            </div>
          </div>

          {/* Metric Toggle */}
          <div className="flex items-center gap-2">
            {(["accuracy", "f1", "auc"] as MetricKey[]).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => setMetric(key)}
                className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-all duration-200 border ${
                  metric === key
                    ? "text-slate-100 border-[#3BB273] bg-[#123C66] shadow-[0_0_12px_rgba(59,178,115,0.2)]"
                    : "text-slate-400 border-[#1e3a52] hover:border-[#2c5278] hover:text-slate-200"
                }`}
              >
                {metricLabels[key]}
              </button>
            ))}
          </div>

          {/* Charts */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="bg-[#0F172A]/70 backdrop-blur-sm rounded-2xl border border-[#1e3a52]/85 shadow-[0_0_28px_rgba(11,31,58,0.55)] p-6">
              <h3 className="text-[15px] font-semibold tracking-tight text-slate-100 mb-1">Performance</h3>
              <p className="text-xs text-slate-500 mb-4">Metric: {metricLabels[metric]}</p>
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
                      animationDuration={700}
                    >
                      {results.map((entry) => (
                        <Cell
                          key={entry.name}
                          fill={entry.name === bestModel.name ? "#3BB273" : "#2c4a6b"}
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="bg-[#0F172A]/70 backdrop-blur-sm rounded-2xl border border-[#1e3a52]/85 shadow-[0_0_28px_rgba(11,31,58,0.55)] p-6">
              <h3 className="text-[15px] font-semibold tracking-tight text-slate-100 mb-1">Training Time</h3>
              <p className="text-xs text-slate-500 mb-4">Seconds to completion</p>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={trainingTimeSeries} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                    <CartesianGrid stroke="#1e3a52" strokeDasharray="3 6" vertical={false} />
                    <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={{ stroke: "#1e3a52" }} />
                    <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={{ stroke: "#1e3a52" }} />
                    <Tooltip content={<DarkTooltip />} cursor={{ fill: "rgba(148,163,184,0.08)" }} />
                    <Line
                      type="monotone"
                      dataKey="trainingTime"
                      name="Training Time"
                      stroke="#3BB273"
                      strokeWidth={2.5}
                      dot={{ r: 4, stroke: "#5EDC8A", strokeWidth: 1.5, fill: "#0F172A" }}
                      activeDot={{ r: 6, stroke: "#5EDC8A", strokeWidth: 2, fill: "#123C66" }}
                      isAnimationActive
                      animationDuration={700}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          {/* Ranked Table */}
          <div className="bg-[#0F172A]/70 backdrop-blur-sm rounded-2xl border border-[#1e3a52]/85 shadow-[0_0_28px_rgba(11,31,58,0.55)] overflow-hidden">
            <div className="px-6 py-4 border-b border-[#1e3a52]">
              <h3 className="text-[15px] font-semibold tracking-tight text-slate-100">Ranked Models</h3>
              <p className="text-xs text-slate-500 mt-1">Sorted by RMSE (lower is better)</p>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-[#0F172A]/90 border-b border-[#1e3a52]">
                  <th className="text-left px-6 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wide">Model Name</th>
                  <th className="text-left px-6 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wide">RMSE</th>
                  <th className="text-left px-6 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wide">MAE</th>
                  <th className="text-left px-6 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wide">R²</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1e3a52]">
                {tableRows.map((model, index) => {
                    const isBest = index === 0;
                    return (
                      <tr
                        key={`${model.model_name}-${index}`}
                        className={`${
                          isBest ? "bg-[#123C66]/60" : "hover:bg-[#123C66]/40"
                        } transition-colors`}
                      >
                        <td className="px-6 py-3.5 font-medium text-slate-100">
                          <div className="flex items-center gap-2">
                            <span>{model.model_name}</span>
                            {model.generated_by === "llm_generated" && (
                              <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold bg-[#3BB273]/12 text-[#5EDC8A] border border-[#3BB273]/35">
                                LLM Generated
                              </span>
                            )}
                            {model.generated_by === "fine_tuned" && (
                              <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold bg-[#123C66] text-slate-200 border border-[#1e3a52]">
                                Fine-tuned
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="px-6 py-3.5 text-slate-300 font-mono text-xs">{model.rmse !== null ? model.rmse.toFixed(4) : "—"}</td>
                        <td className="px-6 py-3.5 text-slate-300 font-mono text-xs">{model.mae !== null ? model.mae.toFixed(4) : "—"}</td>
                        <td className="px-6 py-3.5 text-slate-300 font-mono text-xs">{model.r2 !== null ? model.r2.toFixed(4) : "—"}</td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>

          {/* Deployment */}
          <div className="flex items-center gap-3 pb-4">
            <button className="px-5 py-2.5 rounded-xl border border-[#1e3a52] text-slate-200 bg-[#123C66] hover:bg-[#1e3a52] transition-colors text-sm font-medium">
              Deploy Model
            </button>
            <button
              onClick={() => setShowModelDownloads((prev) => !prev)}
              className="px-5 py-2.5 rounded-xl border border-[#1e3a52] text-slate-300 hover:bg-[#123C66]/80 transition-colors text-sm font-medium"
            >
              Download Model
            </button>
            <button
              onClick={() => setShowCodeDownloads((prev) => !prev)}
              className="px-5 py-2.5 rounded-xl border border-[#1e3a52] text-slate-300 hover:bg-[#123C66]/80 transition-colors text-sm font-medium"
            >
              Download Code
            </button>
            <Link
              href="/dashboard"
              className="ml-auto text-sm text-slate-400 hover:text-slate-200 transition-colors"
            >
              Back to Dashboard
            </Link>
          </div>

          {showModelDownloads && (
            <div className="bg-[#0F172A]/70 backdrop-blur-sm rounded-2xl border border-[#1e3a52]/85 shadow-[0_0_28px_rgba(11,31,58,0.55)] p-4 mb-4">
              <div className="flex items-center justify-between mb-3">
                <p className="text-sm font-medium text-slate-200">Select model to download (.pkl)</p>
                <button
                  onClick={() => setShowModelDownloads(false)}
                  className="text-xs text-slate-400 hover:text-slate-200 transition-colors"
                >
                  Close
                </button>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={() => void handleDownloadModel()}
                  className="px-3 py-1.5 rounded-lg border border-[#1e3a52] text-slate-300 hover:bg-[#123C66]/80 transition-colors text-xs"
                >
                  Download All Models
                </button>
                {tableRows.map((row) => (
                  <button
                    key={row.model_name}
                    onClick={() => void handleDownloadModel(row.model_name)}
                    className="px-3 py-1.5 rounded-lg border border-[#1e3a52] text-slate-300 hover:bg-[#123C66]/80 transition-colors text-xs"
                  >
                    {row.model_name}
                  </button>
                ))}
              </div>
            </div>
          )}

          {showCodeDownloads && (
            <div className="bg-[#0F172A]/70 backdrop-blur-sm rounded-2xl border border-[#1e3a52]/85 shadow-[0_0_28px_rgba(11,31,58,0.55)] p-4">
              <div className="flex items-center justify-between mb-3">
                <p className="text-sm font-medium text-slate-200">Select model code to download (.py)</p>
                <button
                  onClick={() => setShowCodeDownloads(false)}
                  className="text-xs text-slate-400 hover:text-slate-200 transition-colors"
                >
                  Close
                </button>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={() => void handleDownloadCode()}
                  className="px-3 py-1.5 rounded-lg border border-[#1e3a52] text-slate-300 hover:bg-[#123C66]/80 transition-colors text-xs"
                >
                  Download All Code
                </button>
                {tableRows.map((row) => (
                  <button
                    key={`code-${row.model_name}`}
                    onClick={() => void handleDownloadCode(row.model_name)}
                    className="px-3 py-1.5 rounded-lg border border-[#1e3a52] text-slate-300 hover:bg-[#123C66]/80 transition-colors text-xs"
                  >
                    {row.model_name}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </main>
    </>
  );
}
