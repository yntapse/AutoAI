const API_BASE = "http://localhost:8000";

interface StartTrainingResponse {
  job_id: string;
  agent_run_id?: string;
  status: string;
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function extractAgentId(value: string | null | undefined): string | null {
  if (typeof value !== "string") {
    return null;
  }

  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }

  if (UUID_PATTERN.test(trimmed)) {
    return trimmed;
  }

  const match = trimmed.match(
    /[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/i
  );
  return match ? match[0] : null;
}

export interface TrainingStatusResponse {
  status: string;
  stage: string;
  progress: number;
  result: unknown;
  error: string | null;
  completed_at: string | null;
  logs: string[];
  models_in_progress?: Array<{
    name: string;
    status: "pending" | "queued" | "training" | "completed" | "failed";
    rmse: number | null;
    job_id?: string | null;
    error?: string | null;
  }>;
}

export async function startTraining(
  fileId: string,
  targetColumn: string,
  options?: {
    llmProvider?: "auto" | "groq" | "openai" | "gemini";
    llmModel?: string;
  }
): Promise<StartTrainingResponse> {
  const response = await fetch(`${API_BASE}/agent/start-by-file`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      file_id: fileId,
      target_column: targetColumn,
      max_iterations: 6,
      improvement_threshold: 0.0005,
      llm_provider: options?.llmProvider,
      llm_model: options?.llmModel,
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
        : "Failed to start training.";

    throw new Error(message);
  }

  const typedPayload = payload as StartTrainingResponse;
  const canonicalAgentId = extractAgentId(
    typedPayload.agent_run_id ?? typedPayload.job_id
  );

  if (!canonicalAgentId) {
    throw new Error("Backend returned an invalid agent id.");
  }

  return {
    ...typedPayload,
    job_id: canonicalAgentId,
    agent_run_id: canonicalAgentId,
  };
}

export async function getTrainingStatus(jobId: string): Promise<TrainingStatusResponse> {
  const canonicalAgentId = extractAgentId(jobId);
  if (!canonicalAgentId) {
    throw new Error("Invalid agent id. Waiting for a valid training run id.");
  }

  const response = await fetch(
    `${API_BASE}/agent/status/${encodeURIComponent(canonicalAgentId)}?ts=${Date.now()}`,
    {
      cache: "no-store",
      headers: {
        "Cache-Control": "no-cache",
        Pragma: "no-cache",
      },
    }
  );

  const contentType = response.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const message =
      isJson && payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail?: unknown }).detail)
        : typeof payload === "string"
        ? payload
        : "Failed to fetch training status.";

    throw new Error(message);
  }

  return payload as TrainingStatusResponse;
}
