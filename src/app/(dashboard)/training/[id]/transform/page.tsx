"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import Navbar from "@/components/Navbar";
import {
  startTransformSession,
  sendTransformPrompt,
  acceptTransformStep,
  undoTransformStep,
  revertTransformSession,
  saveTransformSession,
  analyzeDataset,
  thinkBeforeActing,
  TransformStep,
  TransformDiff,
  DataAnalysis,
  ThinkingResponse,
} from "@/services/transformService";

/* ── Helpers ───────────────────────────────────────────────────── */

function DiffBadge({ diff }: { diff: TransformDiff }) {
  const parts: { label: string; tone: string }[] = [];
  if (diff.rows_changed > 0) {
    parts.push({
      label: `${diff.rows_changed} row${diff.rows_changed === 1 ? "" : "s"} ${diff.rows_after < diff.rows_before ? "removed" : "added"}`,
      tone: diff.rows_after < diff.rows_before ? "text-rose-300 bg-rose-500/15 border-rose-400/30" : "text-emerald-300 bg-emerald-500/15 border-emerald-400/30",
    });
  }
  if (diff.cols_changed > 0) {
    parts.push({
      label: `${diff.cols_changed} col${diff.cols_changed === 1 ? "" : "s"} ${diff.cols_after < diff.cols_before ? "removed" : "added"}`,
      tone: diff.cols_after < diff.cols_before ? "text-amber-300 bg-amber-500/15 border-amber-400/30" : "text-cyan-300 bg-cyan-500/15 border-cyan-400/30",
    });
  }
  if (diff.nulls_removed > 0) {
    parts.push({
      label: `${diff.nulls_removed} null${diff.nulls_removed === 1 ? "" : "s"} removed`,
      tone: "text-emerald-300 bg-emerald-500/15 border-emerald-400/30",
    });
  }
  if (parts.length === 0) {
    parts.push({ label: "Data modified", tone: "text-slate-300 bg-white/[0.04] border-white/[0.08]" });
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {parts.map((p) => (
        <span key={p.label} className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${p.tone}`}>
          {p.label}
        </span>
      ))}
    </div>
  );
}

function QualityBar({ score }: { score: number }) {
  const color = score >= 80 ? "from-emerald-500 to-green-400" : score >= 60 ? "from-amber-500 to-yellow-400" : "from-rose-500 to-red-400";
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-2 rounded-full bg-slate-800 overflow-hidden">
        <div className={`h-full rounded-full bg-gradient-to-r ${color} transition-all duration-1000`} style={{ width: `${score}%` }} />
      </div>
      <span className="text-[11px] font-bold text-slate-300">{score}/100</span>
    </div>
  );
}

function PriorityBadge({ priority }: { priority: string }) {
  const colors = {
    high: "text-rose-300 bg-rose-500/15 border-rose-400/30",
    medium: "text-amber-300 bg-amber-500/15 border-amber-400/30",
    low: "text-slate-300 bg-white/[0.04] border-white/[0.08]",
  };
  return (
    <span className={`rounded-full border px-1.5 py-0.5 text-[9px] font-bold uppercase ${colors[priority as keyof typeof colors] || colors.low}`}>
      {priority}
    </span>
  );
}

function ThinkingPanel({ thinking, onExecutePlan }: { thinking: ThinkingResponse; onExecutePlan?: () => void }) {
  return (
    <div className="rounded-xl border border-violet-400/20 bg-violet-500/[0.05] p-3 space-y-2">
      <div className="flex items-center gap-2">
        <div className="flex h-5 w-5 items-center justify-center rounded-md bg-violet-500/20">
          <svg className="h-3 w-3 text-violet-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
          </svg>
        </div>
        <span className="text-[11px] font-semibold text-violet-300">Thinking...</span>
        <span className={`ml-auto text-[9px] font-bold uppercase rounded-full px-1.5 py-0.5 ${
          thinking.confidence === "high" ? "text-emerald-300 bg-emerald-500/15" :
          thinking.confidence === "medium" ? "text-amber-300 bg-amber-500/15" :
          "text-rose-300 bg-rose-500/15"
        }`}>
          {thinking.confidence} confidence
        </span>
      </div>
      <p className="text-[11px] text-slate-300 leading-5">{thinking.thinking}</p>
      {thinking.plan.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Plan:</p>
          {thinking.plan.map((step, i) => (
            <div key={i} className="flex items-start gap-1.5">
              <span className="mt-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-violet-500/20 text-[9px] font-bold text-violet-300">{i + 1}</span>
              <span className="text-[11px] text-slate-400">{step}</span>
            </div>
          ))}
        </div>
      )}
      {thinking.warnings.length > 0 && (
        <div className="space-y-1">
          {thinking.warnings.map((w, i) => (
            <p key={i} className="text-[10px] text-amber-400 flex items-center gap-1">
              <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L3.732 16.5c-.77.833.192 2.5 1.732 2.5z" /></svg>
              {w}
            </p>
          ))}
        </div>
      )}
      {/* Execute Plan button */}
      {thinking.is_valid && thinking.plan.length > 0 && onExecutePlan && (
        <button
          onClick={onExecutePlan}
          className="mt-2 w-full flex items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-violet-600 to-cyan-600 px-3 py-2 text-[11px] font-semibold text-white shadow-[0_0_14px_rgba(139,92,246,0.3)] transition-all hover:shadow-[0_0_20px_rgba(139,92,246,0.5)] hover:scale-[1.02]"
        >
          <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          Execute Plan
        </button>
      )}
    </div>
  );
}

/* ── Chat Message Types ──────────────────────────────────────────── */
type ChatMessage =
  | { type: "analysis"; data: DataAnalysis }
  | { type: "user"; prompt: string }
  | { type: "thinking"; data: ThinkingResponse }
  | { type: "step"; step: TransformStep }
  | { type: "error"; prompt: string; message: string };

/* ── Main Page ─────────────────────────────────────────────────── */

export default function TransformPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: fileId } = use(params);
  const router = useRouter();

  // Session state
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Data state
  const [preview, setPreview] = useState<Record<string, unknown>[]>([]);
  const [columnNames, setColumnNames] = useState<string[]>([]);
  const [dtypes, setDtypes] = useState<Record<string, string>>({});
  const [nullCounts, setNullCounts] = useState<Record<string, number>>({});
  const [rowCount, setRowCount] = useState(0);
  const [colCount, setColCount] = useState(0);

  // Chat state
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [steps, setSteps] = useState<TransformStep[]>([]);
  const [promptText, setPromptText] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // Initialize session
  const initSession = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await startTransformSession(fileId);
      setSessionId(res.session_id);
      setPreview(res.preview);
      setColumnNames(res.column_names);
      setDtypes(res.dtypes);
      setNullCounts(res.null_counts);
      setRowCount(res.rows);
      setColCount(res.columns);
      return res.session_id;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start session");
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [fileId]);

  // Auto-analyze on session start
  const runAnalysis = useCallback(async (sid: string) => {
    setIsAnalyzing(true);
    try {
      const analysis = await analyzeDataset(sid);
      setMessages((prev) => [...prev, { type: "analysis", data: analysis }]);
    } catch {
      // Silently skip if analysis fails
    } finally {
      setIsAnalyzing(false);
    }
  }, []);

  // Start session on mount
  useEffect(() => {
    let alive = true;
    (async () => {
      const sid = await initSession();
      if (alive && sid) {
        await runAnalysis(sid);
      }
    })();
    return () => { alive = false; };
  }, [initSession, runAnalysis]);

  // Auto-scroll chat
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Send prompt with thinking
  const handleSendPrompt = useCallback(async () => {
    if (!sessionId || !promptText.trim() || isSending) return;
    const prompt = promptText.trim();
    setPromptText("");
    setIsSending(true);

    // Add user message
    setMessages((prev) => [...prev, { type: "user", prompt }]);

    try {
      let currentSid = sessionId;

      // Detect if this is a chat/greeting vs a transform command
      const chatKeywords = /^(hi|hello|hey|yo|sup|thanks|thank you|ok|okay|what|how|why|who|can|is|are|does|tell|show|analyze|analyse|examine|inspect|describe|check|look|see|find)/i;
      const hasAnalyzeIntent = /analy[sz]e|how.*(correct|good|clean|quality)|tell me.*(about|data|correct)|check.*(data|quality)|find null|null values|show null|missing values|scan null/i.test(prompt);
      const hasTransformIntent = /(remove|drop|fill|clean|normalize|encode|fix|impute|replace|convert|scale|do it all|do all|apply all|execute|transform)/i.test(prompt);
      const isChat = (chatKeywords.test(prompt.trim()) || hasAnalyzeIntent) && !hasTransformIntent;

      // Step 1: Think (skip for chat messages)
      let thinking: ThinkingResponse | null = null;
      if (!isChat) {
        try {
          thinking = await thinkBeforeActing(currentSid, prompt);
          setMessages((prev) => [...prev, { type: "thinking", data: thinking! }]);
        } catch {
          // Skip thinking if it fails
        }

        // Only block if thinking says invalid AND it's not a common operation
        if (thinking && !thinking.is_valid && thinking.validation_issues.length > 0) {
          setMessages((prev) => [...prev, {
            type: "error",
            prompt,
            message: `Cannot execute: ${thinking!.validation_issues.join(", ")}`,
          }]);
          setIsSending(false);
          return;
        }
      }

      // Step 3: Execute transform
      let res;
      try {
        res = await sendTransformPrompt(currentSid, prompt);
      } catch (firstErr) {
        const msg = firstErr instanceof Error ? firstErr.message : "";
        if (msg.toLowerCase().includes("session not found") || msg.includes("404")) {
          const newSid = await initSession();
          if (!newSid) throw firstErr;
          currentSid = newSid;
          setSteps([]);
          res = await sendTransformPrompt(currentSid, prompt);
        } else {
          throw firstErr;
        }
      }

      const newStep: TransformStep = {
        step_index: res.step_index,
        prompt,
        code: res.code,
        summary: res.summary,
        diff: res.diff,
        accepted: false,
        preview: res.preview,
        rows: res.rows,
        columns: res.columns,
      };
      setSteps((prev) => [...prev, newStep]);
      setMessages((prev) => [...prev, { type: "step", step: newStep }]);

      // Update preview
      setPreview(res.preview);
      setColumnNames(res.column_names);
      setDtypes(res.dtypes);
      setNullCounts(res.null_counts);
      setRowCount(res.rows);
      setColCount(res.columns);
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : "Prompt failed";
      setMessages((prev) => [...prev, { type: "error", prompt, message: errMsg }]);
    } finally {
      setIsSending(false);
    }
  }, [sessionId, promptText, isSending, initSession]);

  // Accept step
  const handleAccept = useCallback(async (stepIndex: number) => {
    if (!sessionId) return;
    try {
      await acceptTransformStep(sessionId, stepIndex);
      setSteps((prev) => prev.map((s) => (s.step_index === stepIndex ? { ...s, accepted: true } : s)));
      setMessages((prev) => prev.map((m) =>
        m.type === "step" && m.step.step_index === stepIndex
          ? { ...m, step: { ...m.step, accepted: true } }
          : m
      ));
    } catch { /* ignore */ }
  }, [sessionId]);

  // Undo step
  const handleUndo = useCallback(async (stepIndex: number) => {
    if (!sessionId) return;
    try {
      const res = await undoTransformStep(sessionId, stepIndex);
      setSteps((prev) => prev.filter((s) => s.step_index < stepIndex));
      setMessages((prev) => prev.filter((m) => !(m.type === "step" && m.step.step_index >= stepIndex)));
      setPreview(res.preview);
      setRowCount(res.rows);
      setColCount(res.columns);
    } catch { /* ignore */ }
  }, [sessionId]);

  // Revert all
  const handleRevert = useCallback(async () => {
    if (!sessionId) return;
    try {
      const res = await revertTransformSession(sessionId);
      setSteps([]);
      setMessages((prev) => prev.filter((m) => m.type === "analysis"));
      setPreview(res.preview);
      setRowCount(res.rows);
      setColCount(res.columns);
    } catch { /* ignore */ }
  }, [sessionId]);

  // Save
  const handleSave = useCallback(async () => {
    if (!sessionId || isSaving) return;
    setIsSaving(true);
    try {
      await saveTransformSession(fileId, sessionId);
      router.push(`/training/${encodeURIComponent(fileId)}?target_column=`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setIsSaving(false);
    }
  }, [sessionId, fileId, isSaving, router]);

  // Execute a recommended transform
  const handleRunSuggestion = useCallback((prompt: string) => {
    setPromptText(prompt);
  }, []);

  // Execute a plan directly from the thinking panel
  const handleExecutePlan = useCallback((plan: string[]) => {
    if (!sessionId || isSending) return;
    const planPrompt = plan.join(". ");
    setPromptText("");
    // Directly trigger execution with plan as prompt
    const execute = async () => {
      setIsSending(true);
      setMessages((prev) => [...prev, { type: "user", prompt: `Execute plan: ${planPrompt}` }]);
      try {
        let currentSid = sessionId;
        let res;
        try {
          res = await sendTransformPrompt(currentSid, planPrompt);
        } catch (firstErr) {
          const msg = firstErr instanceof Error ? firstErr.message : "";
          if (msg.toLowerCase().includes("session not found") || msg.includes("404")) {
            const newSid = await initSession();
            if (!newSid) throw firstErr;
            currentSid = newSid;
            setSteps([]);
            res = await sendTransformPrompt(currentSid, planPrompt);
          } else {
            throw firstErr;
          }
        }
        const newStep: TransformStep = {
          step_index: res.step_index,
          prompt: planPrompt,
          code: res.code,
          summary: res.summary,
          diff: res.diff,
          accepted: false,
          preview: res.preview,
          rows: res.rows,
          columns: res.columns,
        };
        setSteps((prev) => [...prev, newStep]);
        setMessages((prev) => [...prev, { type: "step", step: newStep }]);
        setPreview(res.preview);
        setColumnNames(res.column_names);
        setDtypes(res.dtypes);
        setNullCounts(res.null_counts);
        setRowCount(res.rows);
        setColCount(res.columns);
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : "Execution failed";
        setMessages((prev) => [...prev, { type: "error", prompt: planPrompt, message: errMsg }]);
      } finally {
        setIsSending(false);
      }
    };
    void execute();
  }, [sessionId, isSending, initSession]);

  const [showCode, setShowCode] = useState<number | null>(null);

  return (
    <>
      <Navbar title="Transform Dataset" />

      <main className="relative flex-1 overflow-hidden flex flex-col">
        {/* Background */}
        <div className="pointer-events-none absolute inset-0">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_15%_15%,rgba(14,165,233,0.10),transparent_35%),radial-gradient(circle_at_85%_10%,rgba(139,92,246,0.08),transparent_30%),radial-gradient(circle_at_50%_100%,rgba(16,185,129,0.06),transparent_40%)]" />
          <div className="absolute inset-0 opacity-[0.02] bg-[linear-gradient(rgba(148,163,184,1)_1px,transparent_1px),linear-gradient(90deg,rgba(148,163,184,1)_1px,transparent_1px)] [background-size:56px_56px]" />
        </div>

        {/* Header bar */}
        <div className="relative z-10 flex items-center justify-between border-b border-white/[0.06] px-6 py-3">
          <div className="flex items-center gap-4">
            <div>
              <h1 className="text-[18px] font-semibold text-white">Transform Dataset</h1>
              <p className="text-[12px] text-slate-400">
                {rowCount.toLocaleString()} rows × {colCount} columns
                {Object.values(nullCounts).reduce((a, b) => a + b, 0) > 0 && (
                  <span className="ml-2 text-amber-400">
                    ({Object.values(nullCounts).reduce((a, b) => a + b, 0).toLocaleString()} nulls)
                  </span>
                )}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {steps.some((s) => s.accepted) && (
              <button onClick={() => void handleSave()} disabled={isSaving} className="flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-emerald-600 to-green-600 px-4 py-2 text-[13px] font-semibold text-white shadow-[0_0_18px_rgba(16,185,129,0.3)] transition-all hover:shadow-[0_0_26px_rgba(16,185,129,0.5)] disabled:opacity-50">
                <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
                {isSaving ? "Saving..." : "Save & Continue"}
              </button>
            )}
            {steps.length > 0 && (
              <button onClick={() => void handleRevert()} className="flex items-center gap-1.5 rounded-xl border border-rose-400/25 bg-rose-500/10 px-3 py-2 text-[12px] font-semibold text-rose-300 transition-all hover:bg-rose-500/20">
                <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6" /></svg>
                Revert All
              </button>
            )}
            <Link href={`/training/${encodeURIComponent(fileId)}`} className="rounded-xl border border-white/[0.08] bg-white/[0.04] px-3 py-2 text-[12px] font-semibold text-slate-300 transition-all hover:bg-white/[0.08]">
              Back to Training
            </Link>
          </div>
        </div>

        {/* Loading / Error */}
        {isLoading && (
          <div className="relative z-10 flex flex-1 items-center justify-center">
            <div className="text-center">
              <div className="mx-auto mb-4 h-10 w-10 animate-spin rounded-full border-2 border-cyan-400/30 border-t-cyan-400" />
              <p className="text-[14px] text-slate-300">Loading dataset...</p>
            </div>
          </div>
        )}

        {!isLoading && error && (
          <div className="relative z-10 flex flex-1 items-center justify-center p-8">
            <div className="rounded-2xl border border-rose-400/25 bg-rose-500/10 p-8 text-center max-w-lg">
              <p className="text-[16px] font-semibold text-white mb-2">Could not load dataset</p>
              <p className="text-[13px] text-rose-200/80">{error}</p>
            </div>
          </div>
        )}

        {/* Main content: Table | Copilot */}
        {!isLoading && !error && (
          <div className="relative z-10 flex flex-1 min-h-0">
            {/* Left: Data Table */}
            <div className="flex-1 flex flex-col min-w-0 border-r border-white/[0.06]">
              <div className="flex items-center gap-2 border-b border-white/[0.06] bg-white/[0.02] px-4 py-2">
                <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500">Data Preview</span>
                <span className="rounded-full border border-white/[0.08] bg-white/[0.04] px-2 py-0.5 text-[10px] font-semibold text-slate-400">{Math.min(200, rowCount)} of {rowCount.toLocaleString()} rows</span>
              </div>
              <div className="flex-1 overflow-auto">
                <table className="w-full text-left text-[12px]">
                  <thead className="sticky top-0 z-10 bg-slate-900/95 backdrop-blur-sm">
                    <tr>
                      <th className="border-b border-r border-white/[0.06] px-3 py-2.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500 bg-white/[0.02] w-12">#</th>
                      {columnNames.map((col) => (
                        <th key={col} className="border-b border-r border-white/[0.06] px-3 py-2.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400 bg-white/[0.02] whitespace-nowrap">
                          <div className="flex flex-col gap-0.5">
                            <span className="text-slate-200">{col}</span>
                            <span className="text-[9px] font-normal text-slate-600">
                              {dtypes[col] || ""}
                              {(nullCounts[col] ?? 0) > 0 && <span className="ml-1 text-amber-500">{nullCounts[col]} null</span>}
                            </span>
                          </div>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.map((row, rowIdx) => (
                      <tr key={rowIdx} className="border-b border-white/[0.03] transition-colors hover:bg-white/[0.02]">
                        <td className="border-r border-white/[0.06] px-3 py-2 text-[11px] text-slate-600 bg-white/[0.01]">{rowIdx + 1}</td>
                        {columnNames.map((col) => {
                          const val = row[col];
                          const isNull = val === null || val === undefined || val === "";
                          return (
                            <td key={col} className={`border-r border-white/[0.03] px-3 py-2 font-mono text-[11px] whitespace-nowrap max-w-[200px] truncate ${isNull ? "text-slate-600 italic" : "text-slate-300"}`}>
                              {isNull ? "null" : String(val)}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Right: AI Copilot Panel */}
            <div className="flex w-[440px] flex-shrink-0 flex-col bg-slate-950/60">
              {/* Header */}
              <div className="flex items-center gap-2.5 border-b border-white/[0.06] px-4 py-3">
                <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-violet-500 to-cyan-400 shadow-[0_0_12px_rgba(139,92,246,0.4)]">
                  <svg className="h-3.5 w-3.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                  </svg>
                </div>
                <div>
                  <p className="text-[13px] font-semibold text-white">AI Data Engineer</p>
                  <p className="text-[10px] text-slate-500">Thinks → Plans → Executes</p>
                </div>
                <div className="ml-auto flex h-5 items-center rounded-full bg-emerald-500/15 border border-emerald-400/30 px-2">
                  <span className="mr-1 h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  <span className="text-[9px] font-semibold text-emerald-300">Online</span>
                </div>
              </div>

              {/* Chat messages */}
              <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
                {/* Analyzing indicator */}
                {isAnalyzing && messages.length === 0 && (
                  <div className="flex justify-start">
                    <div className="rounded-2xl rounded-bl-md border border-white/[0.08] bg-white/[0.03] px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="h-4 w-4 animate-spin rounded-full border-2 border-violet-400/30 border-t-violet-400" />
                        <span className="text-[11px] text-slate-400">Analyzing your dataset like a pro data engineer...</span>
                      </div>
                    </div>
                  </div>
                )}

                {messages.map((msg, idx) => {
                  if (msg.type === "analysis") {
                    return (
                      <div key={idx} className="space-y-2">
                        <div className="flex justify-start">
                          <div className="max-w-[95%] rounded-2xl rounded-bl-md border border-white/[0.08] bg-white/[0.03] p-3 space-y-3">
                            {/* Quality score */}
                            <div className="space-y-1.5">
                              <div className="flex items-center gap-2">
                                <svg className="h-4 w-4 text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" /></svg>
                                <span className="text-[12px] font-semibold text-white">Data Quality Assessment</span>
                              </div>
                              <QualityBar score={msg.data.quality_score} />
                            </div>

                            {/* Summary */}
                            <p className="text-[11px] text-slate-300 leading-5">{msg.data.summary}</p>

                            {/* Issues */}
                            {msg.data.issues.length > 0 && (
                              <div className="space-y-1">
                                <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Issues Found:</p>
                                {msg.data.issues.slice(0, 5).map((issue, i) => (
                                  <p key={i} className="text-[11px] text-amber-300/80 flex items-start gap-1.5">
                                    <span className="mt-0.5 text-amber-400">•</span>
                                    {issue}
                                  </p>
                                ))}
                              </div>
                            )}

                            {/* Recommended transforms */}
                            {msg.data.recommended_transforms.length > 0 && (
                              <div className="space-y-1.5">
                                <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Suggested Transforms:</p>
                                {msg.data.recommended_transforms.slice(0, 6).map((rec, i) => (
                                  <button
                                    key={i}
                                    onClick={() => handleRunSuggestion(rec.prompt)}
                                    className="w-full text-left rounded-lg border border-white/[0.06] bg-white/[0.02] p-2 transition-all hover:bg-white/[0.06] hover:border-cyan-400/20 group"
                                  >
                                    <div className="flex items-center gap-2">
                                      <PriorityBadge priority={rec.priority} />
                                      <span className="text-[11px] text-slate-200 group-hover:text-cyan-300 transition-colors">{rec.action}</span>
                                    </div>
                                    <p className="mt-1 text-[10px] text-slate-500">{rec.reason}</p>
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  }

                  if (msg.type === "user") {
                    return (
                      <div key={idx} className="flex justify-end">
                        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-gradient-to-r from-cyan-600/80 to-blue-600/80 px-3.5 py-2.5">
                          <p className="text-[12px] text-white leading-5">{msg.prompt}</p>
                        </div>
                      </div>
                    );
                  }

                  if (msg.type === "thinking") {
                    return (
                      <div key={idx} className="flex justify-start">
                        <div className="max-w-[95%]">
                          <ThinkingPanel thinking={msg.data} onExecutePlan={() => handleExecutePlan(msg.data.plan)} />
                        </div>
                      </div>
                    );
                  }

                  if (msg.type === "step") {
                    const step = msg.step;
                    return (
                      <div key={idx} className="flex justify-start">
                        <div className="max-w-[92%] rounded-2xl rounded-bl-md border border-white/[0.08] bg-white/[0.03] p-3 space-y-2.5">
                          {step.step_index === -1 ? (
                            <p className="text-[12px] text-slate-200 leading-5 whitespace-pre-line">{step.summary}</p>
                          ) : (
                            <>
                              <div className="flex items-center gap-1.5">
                                <svg className="h-3.5 w-3.5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
                                <span className="text-[11px] font-semibold text-emerald-300">Executed successfully</span>
                              </div>
                              <p className="text-[12px] text-slate-200 leading-5 whitespace-pre-line">{step.summary}</p>
                              <DiffBadge diff={step.diff} />

                              <button onClick={() => setShowCode(showCode === step.step_index ? null : step.step_index)} className="flex items-center gap-1 text-[10px] font-semibold text-slate-500 hover:text-slate-300 transition-colors">
                                <svg className={`h-3 w-3 transition-transform ${showCode === step.step_index ? "rotate-90" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
                                {showCode === step.step_index ? "Hide code" : "Show code"}
                              </button>
                              {showCode === step.step_index && (
                                <pre className="rounded-xl border border-white/[0.06] bg-slate-950/80 p-3 text-[11px] leading-5 text-emerald-300/90 overflow-x-auto"><code>{step.code}</code></pre>
                              )}

                              <div className="flex gap-3 text-[10px] text-slate-500">
                                <span>{step.diff.rows_after.toLocaleString()} rows</span>
                                <span>{step.diff.cols_after} cols</span>
                                <span>{step.diff.nulls_after} nulls</span>
                              </div>

                              {!step.accepted ? (
                                <div className="flex gap-2">
                                  <button onClick={() => void handleAccept(step.step_index)} className="flex items-center gap-1 rounded-lg bg-emerald-500/15 border border-emerald-400/30 px-3 py-1.5 text-[11px] font-semibold text-emerald-300 transition-all hover:bg-emerald-500/25">
                                    <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" /></svg>
                                    Accept
                                  </button>
                                  <button onClick={() => void handleUndo(step.step_index)} className="flex items-center gap-1 rounded-lg bg-white/[0.04] border border-white/[0.08] px-3 py-1.5 text-[11px] font-semibold text-slate-400 transition-all hover:bg-white/[0.08]">
                                    <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6" /></svg>
                                    Discard
                                  </button>
                                </div>
                              ) : (
                                <div className="flex items-center gap-1.5 text-[11px] text-emerald-400/70">
                                  <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" /></svg>
                                  Applied
                                  <button onClick={() => void handleUndo(step.step_index)} className="ml-2 text-[10px] text-slate-500 hover:text-rose-400 transition-colors">(undo)</button>
                                </div>
                              )}
                            </>
                          )}
                        </div>
                      </div>
                    );
                  }

                  if (msg.type === "error") {
                    return (
                      <div key={idx} className="flex justify-start">
                        <div className="max-w-[92%] rounded-2xl rounded-bl-md border border-rose-400/20 bg-rose-500/[0.05] p-3">
                          <p className="text-[12px] text-rose-300 leading-5">{msg.message}</p>
                        </div>
                      </div>
                    );
                  }

                  return null;
                })}

                {isSending && (
                  <div className="flex justify-start">
                    <div className="rounded-2xl rounded-bl-md border border-white/[0.08] bg-white/[0.03] px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="flex gap-1">
                          <span className="h-1.5 w-1.5 rounded-full bg-violet-400 animate-bounce" style={{ animationDelay: "0ms" }} />
                          <span className="h-1.5 w-1.5 rounded-full bg-cyan-400 animate-bounce" style={{ animationDelay: "150ms" }} />
                          <span className="h-1.5 w-1.5 rounded-full bg-violet-400 animate-bounce" style={{ animationDelay: "300ms" }} />
                        </div>
                        <span className="text-[11px] text-slate-500">Thinking & executing...</span>
                      </div>
                    </div>
                  </div>
                )}

                <div ref={chatEndRef} />
              </div>

              {/* Prompt input */}
              <div className="border-t border-white/[0.06] p-3">
                <div className="flex items-end gap-2 rounded-xl border border-white/[0.08] bg-white/[0.03] p-2">
                  <textarea
                    value={promptText}
                    onChange={(e) => setPromptText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        void handleSendPrompt();
                      }
                    }}
                    placeholder="Describe how to transform your data..."
                    rows={2}
                    className="flex-1 resize-none bg-transparent text-[13px] text-slate-200 placeholder-slate-600 outline-none"
                  />
                  <button
                    onClick={() => void handleSendPrompt()}
                    disabled={!promptText.trim() || isSending}
                    className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-gradient-to-r from-violet-600 to-cyan-600 text-white transition-all hover:shadow-[0_0_14px_rgba(139,92,246,0.4)] disabled:opacity-40"
                  >
                    <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19V5m0 0l-7 7m7-7l7 7" />
                    </svg>
                  </button>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {["Remove all null values", "Drop duplicate rows", "Normalize numeric columns", "Convert text to lowercase", "Remove outliers"].map((hint) => (
                    <button
                      key={hint}
                      onClick={() => setPromptText(hint)}
                      className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-1 text-[10px] text-slate-500 transition-all hover:bg-white/[0.06] hover:text-slate-300"
                    >
                      {hint}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}
      </main>
    </>
  );
}
