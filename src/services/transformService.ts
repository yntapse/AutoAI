const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"
).replace(/\/$/, "");

/* ── Types ─────────────────────────────────────────────────────── */

export interface TransformDiff {
  rows_before: number;
  rows_after: number;
  cols_before: number;
  cols_after: number;
  nulls_before: number;
  nulls_after: number;
  rows_changed: number;
  cols_changed: number;
  nulls_removed: number;
}

export interface TransformStartResponse {
  session_id: string;
  rows: number;
  columns: number;
  column_names: string[];
  dtypes: Record<string, string>;
  preview: Record<string, unknown>[];
  null_counts: Record<string, number>;
}

export interface TransformPromptResponse {
  step_index: number;
  code: string;
  summary: string;
  diff: TransformDiff;
  preview: Record<string, unknown>[];
  rows: number;
  columns: number;
  column_names: string[];
  dtypes: Record<string, string>;
  null_counts: Record<string, number>;
}

export interface TransformStep {
  step_index: number;
  prompt: string;
  code: string;
  summary: string;
  diff: TransformDiff;
  accepted: boolean;
  preview: Record<string, unknown>[];
  rows: number;
  columns: number;
}

export interface RecommendedTransform {
  action: string;
  reason: string;
  priority: "high" | "medium" | "low";
  prompt: string;
}

export interface DataAnalysis {
  quality_score: number;
  issues: string[];
  column_insights: string[];
  recommended_transforms: RecommendedTransform[];
  summary: string;
}

export interface ThinkingResponse {
  thinking: string;
  is_valid: boolean;
  validation_issues: string[];
  plan: string[];
  estimated_impact: Record<string, number>;
  confidence: "high" | "medium" | "low";
  warnings: string[];
}

/* ── API Calls ─────────────────────────────────────────────────── */

export async function startTransformSession(
  fileId: string
): Promise<TransformStartResponse> {
  const res = await fetch(`${API_BASE}/transform/start/${fileId}`, {
    method: "POST",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Failed to start transform session (${res.status})`);
  }
  return res.json();
}

export async function analyzeDataset(
  sessionId: string
): Promise<DataAnalysis> {
  const res = await fetch(`${API_BASE}/transform/analyze/${sessionId}`, {
    method: "POST",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Analysis failed (${res.status})`);
  }
  return res.json();
}

export async function thinkBeforeActing(
  sessionId: string,
  prompt: string
): Promise<ThinkingResponse> {
  const res = await fetch(`${API_BASE}/transform/think`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, prompt }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Thinking failed (${res.status})`);
  }
  return res.json();
}

export async function sendTransformPrompt(
  sessionId: string,
  prompt: string
): Promise<TransformPromptResponse> {
  // Detect chat/analysis prompts — skip tool calling for these
  const isChatPrompt = /^(hi|hello|hey|analy[sz]e|tell me|what|how|why|show|describe|examine)/i.test(prompt.trim())
    && !/(remove|drop|fill|clean|normalize|encode|fix|impute|replace|convert|scale|do it all|do all|apply all|execute|transform)/i.test(prompt);

  if (!isChatPrompt) {
    // Try MCP tool-calling first, fall back to code generation
    try {
      const toolRes = await fetch(`${API_BASE}/transform/execute-tools`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, prompt }),
      });
      if (toolRes.ok) {
        return toolRes.json();
      }
    } catch {
      // Fall through to code gen
    }
  }

  // Fallback: standard code generation (also handles chat responses)
  const res = await fetch(`${API_BASE}/transform/prompt`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, prompt }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Transform prompt failed (${res.status})`);
  }
  return res.json();
}

export async function acceptTransformStep(
  sessionId: string,
  stepIndex: number
): Promise<{ status: string }> {
  const res = await fetch(`${API_BASE}/transform/accept`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, step_index: stepIndex }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Accept failed (${res.status})`);
  }
  return res.json();
}

export async function undoTransformStep(
  sessionId: string,
  stepIndex: number
): Promise<{ status: string; rows: number; columns: number; preview: Record<string, unknown>[]; steps_remaining: number }> {
  const res = await fetch(`${API_BASE}/transform/undo`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, step_index: stepIndex }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Undo failed (${res.status})`);
  }
  return res.json();
}

export async function revertTransformSession(
  sessionId: string
): Promise<{ status: string; rows: number; columns: number; preview: Record<string, unknown>[] }> {
  const res = await fetch(`${API_BASE}/transform/revert`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Revert failed (${res.status})`);
  }
  return res.json();
}

export async function saveTransformSession(
  fileId: string,
  sessionId: string
): Promise<{ status: string; rows: number; columns: number }> {
  const form = new FormData();
  form.append("session_id", sessionId);
  const res = await fetch(`${API_BASE}/transform/save/${fileId}`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Save failed (${res.status})`);
  }
  return res.json();
}
