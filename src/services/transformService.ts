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

export async function sendTransformPrompt(
  sessionId: string,
  prompt: string
): Promise<TransformPromptResponse> {
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
