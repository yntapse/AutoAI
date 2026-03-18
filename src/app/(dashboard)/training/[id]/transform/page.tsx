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
  TransformStep,
  TransformDiff,
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
  const [steps, setSteps] = useState<TransformStep[]>([]);
  const [promptText, setPromptText] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // Initialize / re-initialize session
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

  // Start session on mount
  useEffect(() => {
    let alive = true;
    (async () => {
      const sid = await initSession();
      if (!alive && sid) {
        // cleanup not needed — session stays on server
      }
    })();
    return () => { alive = false; };
  }, [initSession]);

  // Auto-scroll chat
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [steps]);

  // Send prompt
  const handleSendPrompt = useCallback(async () => {
    if (!sessionId || !promptText.trim() || isSending) return;
    const prompt = promptText.trim();
    setPromptText("");
    setIsSending(true);
    try {
      let currentSid = sessionId;
      let res;
      try {
        res = await sendTransformPrompt(currentSid, prompt);
      } catch (firstErr) {
        // Auto-recover: if session expired, restart and retry once
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
      // Show preview from this step
      setPreview(res.preview);
      setColumnNames(res.column_names);
      setDtypes(res.dtypes);
      setNullCounts(res.null_counts);
      setRowCount(res.rows);
      setColCount(res.columns);
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : "Prompt failed";
      // Add an error step to chat
      setSteps((prev) => [
        ...prev,
        {
          step_index: -1,
          prompt,
          code: "",
          summary: `Error: ${errMsg}`,
          diff: { rows_before: 0, rows_after: 0, cols_before: 0, cols_after: 0, nulls_before: 0, nulls_after: 0, rows_changed: 0, cols_changed: 0, nulls_removed: 0 },
          accepted: false,
          preview: [],
          rows: rowCount,
          columns: colCount,
        },
      ]);
    } finally {
      setIsSending(false);
    }
  }, [sessionId, promptText, isSending, rowCount, colCount, initSession]);

  // Accept step
  const handleAccept = useCallback(async (stepIndex: number) => {
    if (!sessionId) return;
    try {
      await acceptTransformStep(sessionId, stepIndex);
      setSteps((prev) =>
        prev.map((s) => (s.step_index === stepIndex ? { ...s, accepted: true } : s))
      );
    } catch {
      /* ignore */
    }
  }, [sessionId]);

  // Undo step
  const handleUndo = useCallback(async (stepIndex: number) => {
    if (!sessionId) return;
    try {
      const res = await undoTransformStep(sessionId, stepIndex);
      setSteps((prev) => prev.filter((s) => s.step_index < stepIndex));
      setPreview(res.preview);
      setRowCount(res.rows);
      setColCount(res.columns);
    } catch {
      /* ignore */
    }
  }, [sessionId]);

  // Revert all
  const handleRevert = useCallback(async () => {
    if (!sessionId) return;
    try {
      const res = await revertTransformSession(sessionId);
      setSteps([]);
      setPreview(res.preview);
      setRowCount(res.rows);
      setColCount(res.columns);
    } catch {
      /* ignore */
    }
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
              <button
                onClick={() => void handleSave()}
                disabled={isSaving}
                className="flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-emerald-600 to-green-600 px-4 py-2 text-[13px] font-semibold text-white shadow-[0_0_18px_rgba(16,185,129,0.3)] transition-all hover:shadow-[0_0_26px_rgba(16,185,129,0.5)] disabled:opacity-50"
              >
                <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
                {isSaving ? "Saving..." : "Save & Continue"}
              </button>
            )}
            {steps.length > 0 && (
              <button
                onClick={() => void handleRevert()}
                className="flex items-center gap-1.5 rounded-xl border border-rose-400/25 bg-rose-500/10 px-3 py-2 text-[12px] font-semibold text-rose-300 transition-all hover:bg-rose-500/20"
              >
                <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6" />
                </svg>
                Revert All
              </button>
            )}
            <Link
              href={`/training/${encodeURIComponent(fileId)}`}
              className="rounded-xl border border-white/[0.08] bg-white/[0.04] px-3 py-2 text-[12px] font-semibold text-slate-300 transition-all hover:bg-white/[0.08]"
            >
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

        {/* Main content: Table | Chat */}
        {!isLoading && !error && (
          <div className="relative z-10 flex flex-1 min-h-0">
            {/* Left: Data Table */}
            <div className="flex-1 flex flex-col min-w-0 border-r border-white/[0.06]">
              {/* Column header bar */}
              <div className="flex items-center gap-2 border-b border-white/[0.06] bg-white/[0.02] px-4 py-2">
                <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500">
                  Data Preview
                </span>
                <span className="rounded-full border border-white/[0.08] bg-white/[0.04] px-2 py-0.5 text-[10px] font-semibold text-slate-400">
                  {Math.min(200, rowCount)} of {rowCount.toLocaleString()} rows
                </span>
              </div>

              {/* Scrollable table */}
              <div className="flex-1 overflow-auto">
                <table className="w-full text-left text-[12px]">
                  <thead className="sticky top-0 z-10 bg-slate-900/95 backdrop-blur-sm">
                    <tr>
                      <th className="border-b border-r border-white/[0.06] px-3 py-2.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500 bg-white/[0.02] w-12">
                        #
                      </th>
                      {columnNames.map((col) => (
                        <th
                          key={col}
                          className="border-b border-r border-white/[0.06] px-3 py-2.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400 bg-white/[0.02] whitespace-nowrap"
                        >
                          <div className="flex flex-col gap-0.5">
                            <span className="text-slate-200">{col}</span>
                            <span className="text-[9px] font-normal text-slate-600">
                              {dtypes[col] || ""}
                              {(nullCounts[col] ?? 0) > 0 && (
                                <span className="ml-1 text-amber-500">{nullCounts[col]} null</span>
                              )}
                            </span>
                          </div>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.map((row, rowIdx) => (
                      <tr
                        key={rowIdx}
                        className="border-b border-white/[0.03] transition-colors hover:bg-white/[0.02]"
                      >
                        <td className="border-r border-white/[0.06] px-3 py-2 text-[11px] text-slate-600 bg-white/[0.01]">
                          {rowIdx + 1}
                        </td>
                        {columnNames.map((col) => {
                          const val = row[col];
                          const isNull = val === null || val === undefined || val === "";
                          return (
                            <td
                              key={col}
                              className={`border-r border-white/[0.03] px-3 py-2 font-mono text-[11px] whitespace-nowrap max-w-[200px] truncate ${
                                isNull ? "text-slate-600 italic" : "text-slate-300"
                              }`}
                            >
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

            {/* Right: Copilot Chat Panel */}
            <div className="flex w-[420px] flex-shrink-0 flex-col bg-slate-950/60">
              {/* Chat header */}
              <div className="flex items-center gap-2.5 border-b border-white/[0.06] px-4 py-3">
                <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-violet-500 to-cyan-400 shadow-[0_0_12px_rgba(139,92,246,0.4)]">
                  <svg className="h-3.5 w-3.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
                  </svg>
                </div>
                <div>
                  <p className="text-[13px] font-semibold text-white">chat</p>
                  <p className="text-[10px] text-slate-500">Describe changes in plain English</p>
                </div>
              </div>

              {/* Chat messages */}
              <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
                {steps.length === 0 && (
                  <div className="flex flex-col items-center justify-center h-full text-center px-4">
                    <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-white/[0.08] bg-white/[0.04]">
                      <svg className="h-6 w-6 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                      </svg>
                    </div>
                    <p className="text-[14px] font-semibold text-slate-300 mb-2">Start transforming</p>
                    <p className="text-[12px] text-slate-500 leading-5">
                      Type a prompt like{" "}
                      <span className="text-cyan-400">&quot;remove all null values&quot;</span>,{" "}
                      <span className="text-cyan-400">&quot;drop column age&quot;</span>, or{" "}
                      <span className="text-cyan-400">&quot;normalize salary column&quot;</span>
                    </p>
                  </div>
                )}

                {steps.map((step) => (
                  <div key={step.step_index} className="space-y-2">
                    {/* User prompt */}
                    <div className="flex justify-end">
                      <div className="max-w-[85%] rounded-2xl rounded-br-md bg-gradient-to-r from-cyan-600/80 to-blue-600/80 px-3.5 py-2.5">
                        <p className="text-[12px] text-white leading-5">{step.prompt}</p>
                      </div>
                    </div>

                    {/* Assistant response */}
                    <div className="flex justify-start">
                      <div className="max-w-[92%] rounded-2xl rounded-bl-md border border-white/[0.08] bg-white/[0.03] p-3 space-y-2.5">
                        {step.step_index === -1 ? (
                          <p
                            className={`text-[12px] leading-5 ${
                              step.summary.toLowerCase().startsWith("no transform detected")
                                ? "text-amber-200"
                                : "text-rose-300"
                            }`}
                          >
                            {step.summary}
                          </p>
                        ) : (
                          <>
                            <p className="text-[12px] text-slate-200 leading-5">{step.summary}</p>

                            <DiffBadge diff={step.diff} />

                            {/* Code toggle */}
                            <button
                              onClick={() => setShowCode(showCode === step.step_index ? null : step.step_index)}
                              className="flex items-center gap-1 text-[10px] font-semibold text-slate-500 hover:text-slate-300 transition-colors"
                            >
                              <svg className={`h-3 w-3 transition-transform ${showCode === step.step_index ? "rotate-90" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                              </svg>
                              {showCode === step.step_index ? "Hide code" : "Show code"}
                            </button>

                            {showCode === step.step_index && (
                              <pre className="rounded-xl border border-white/[0.06] bg-slate-950/80 p-3 text-[11px] leading-5 text-emerald-300/90 overflow-x-auto">
                                <code>{step.code}</code>
                              </pre>
                            )}

                            {/* Shape info */}
                            <div className="flex gap-3 text-[10px] text-slate-500">
                              <span>{step.diff.rows_after.toLocaleString()} rows</span>
                              <span>{step.diff.cols_after} cols</span>
                              <span>{step.diff.nulls_after} nulls</span>
                            </div>

                            {/* Accept / Undo buttons */}
                            {!step.accepted ? (
                              <div className="flex gap-2">
                                <button
                                  onClick={() => void handleAccept(step.step_index)}
                                  className="flex items-center gap-1 rounded-lg bg-emerald-500/15 border border-emerald-400/30 px-3 py-1.5 text-[11px] font-semibold text-emerald-300 transition-all hover:bg-emerald-500/25"
                                >
                                  <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                                  </svg>
                                  Accept
                                </button>
                                <button
                                  onClick={() => void handleUndo(step.step_index)}
                                  className="flex items-center gap-1 rounded-lg bg-white/[0.04] border border-white/[0.08] px-3 py-1.5 text-[11px] font-semibold text-slate-400 transition-all hover:bg-white/[0.08]"
                                >
                                  <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6" />
                                  </svg>
                                  Discard
                                </button>
                              </div>
                            ) : (
                              <div className="flex items-center gap-1.5 text-[11px] text-emerald-400/70">
                                <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                                </svg>
                                Applied
                                <button
                                  onClick={() => void handleUndo(step.step_index)}
                                  className="ml-2 text-[10px] text-slate-500 hover:text-rose-400 transition-colors"
                                >
                                  (undo)
                                </button>
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                ))}

                {isSending && (
                  <div className="flex justify-start">
                    <div className="rounded-2xl rounded-bl-md border border-white/[0.08] bg-white/[0.03] px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="flex gap-1">
                          <span className="h-1.5 w-1.5 rounded-full bg-cyan-400 animate-bounce" style={{ animationDelay: "0ms" }} />
                          <span className="h-1.5 w-1.5 rounded-full bg-cyan-400 animate-bounce" style={{ animationDelay: "150ms" }} />
                          <span className="h-1.5 w-1.5 rounded-full bg-cyan-400 animate-bounce" style={{ animationDelay: "300ms" }} />
                        </div>
                        <span className="text-[11px] text-slate-500">Generating transform...</span>
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
                    className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-gradient-to-r from-cyan-600 to-blue-600 text-white transition-all hover:shadow-[0_0_14px_rgba(6,182,212,0.4)] disabled:opacity-40"
                  >
                    <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19V5m0 0l-7 7m7-7l7 7" />
                    </svg>
                  </button>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {["Remove all null values", "Drop duplicate rows", "Normalize numeric columns"].map((hint) => (
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
