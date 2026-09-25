const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

export interface FineTuneResponse {
  previous_version: number;
  new_version: number;
  training_run_id?: string;
  before: {
    name: string;
    rmse: number;
    mae: number;
    r2: number;
  };
  after: {
    name: string;
    rmse: number;
    mae: number;
    r2: number;
  };
  improvement: {
    rmse_change: number;
    r2_change: number;
  };
}

export interface ExperimentReportIteration {
  iteration: number;
  training_run_id: string;
  status: string;
  model_name: string | null;
  rmse: number | null;
  mae: number | null;
  r2: number | null;
  duration_seconds: number | null;
  started_at: string | null;
  completed_at: string | null;
  delta_from_previous_rmse: number | null;
  delta_from_best_rmse: number | null;
  is_new_best: boolean;
  strategy_summary: string | null;
  candidate_models: Array<{
    model_name: string;
    rank_position: number | null;
    rmse: number | null;
    mae: number | null;
    r2: number | null;
    hyperparameters: Record<string, unknown> | null;
  }>;
  preprocessing_tokens: string[];
  training_tokens: string[];
  agent_signals: {
    architect_blueprints: number;
    architect_fallbacks: number;
    single_model_gate_rejections: number;
    compile_fallbacks: number;
    telemetry_events: number;
  };
  log_excerpt: string[];
}

export interface ExperimentReportResponse {
  file_id: string;
  project_id: string;
  project_name: string;
  target_column: string;
  agent_run_id: string;
  agent_status: string;
  started_at: string | null;
  completed_at: string | null;
  summary: {
    iterations_completed: number;
    max_iterations: number;
    baseline_iteration: ExperimentReportIteration | null;
    best_iteration: ExperimentReportIteration | null;
    rmse_reduction: number | null;
    rmse_reduction_percent: number | null;
    r2_gain: number | null;
    best_model_name: string | null;
  };
  iterations: ExperimentReportIteration[];
  strategy_themes: Array<{
    name: string;
    count: number;
  }>;
  log_retention: {
    available: boolean;
    captured_lines: number;
  };
}

export async function getTrainingHistory(fileId: string): Promise<unknown> {
  const response = await fetch(`${API_BASE}/training/history/${encodeURIComponent(fileId)}`);

  const contentType = response.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const message =
      isJson && payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail?: unknown }).detail)
        : typeof payload === "string"
        ? payload
        : "Failed to fetch training history.";

    throw new Error(message);
  }

  return payload;
}

export async function getExperimentReport(fileId: string): Promise<ExperimentReportResponse> {
  const response = await fetch(`${API_BASE}/training/report/${encodeURIComponent(fileId)}`, {
    cache: "no-store",
  });

  const contentType = response.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const message =
      isJson && payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail?: unknown }).detail)
        : typeof payload === "string"
        ? payload
        : "Failed to fetch experiment report.";

    throw new Error(message);
  }

  return payload as ExperimentReportResponse;
}

function downloadFromEndpoint(url: string): void {
  const link = document.createElement("a");
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

export async function downloadModelArtifact(trainingRunId: string, modelName?: string): Promise<void> {
  const query = modelName ? `?model_name=${encodeURIComponent(modelName)}` : "";
  const url = `${API_BASE}/models/download/${encodeURIComponent(trainingRunId)}${query}`;
  downloadFromEndpoint(url);
}

export async function downloadModelCode(trainingRunId: string, modelName?: string): Promise<void> {
  const query = modelName ? `?model_name=${encodeURIComponent(modelName)}` : "";
  const url = `${API_BASE}/models/code/${encodeURIComponent(trainingRunId)}${query}`;
  downloadFromEndpoint(url);
}

export async function fineTuneModel(
  fileId: string,
  targetColumn: string,
  llmProvider: "openai" | "claude" | "gemini" | "mistral" | "groq",
  prompt?: string,
  modelName?: string
): Promise<FineTuneResponse> {
  const response = await fetch(`${API_BASE}/training/fine-tune`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      file_id: fileId,
      target_column: targetColumn,
      llm_provider: llmProvider,
      prompt,
      model_name: modelName,
    }),
  });

  const contentType = response.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const message =
      isJson && payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail?: unknown }).detail)
        : typeof payload === "string"
        ? payload
        : "Failed to fine-tune model.";

    throw new Error(message);
  }

  return payload as FineTuneResponse;
}
