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
