"use client";

import Navbar from "@/components/Navbar";
import { useState, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { createProject } from "@/services/projectService";

interface CreateProjectResponse {
  file_id: string;
  target_column: string;
}

interface DatasetInfo {
  rows: number;
  columns: number;
  numericCount: number;
  categoricalCount: number;
}

export default function NewProjectPage() {
  const router = useRouter();
  const [currentStep, setCurrentStep] = useState(1);
  const [projectName, setProjectName] = useState("");
  const [projectDescription, setProjectDescription] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [datasetInfo, setDatasetInfo] = useState<DatasetInfo | null>(null);
  const [targetColumn, setTargetColumn] = useState("");
  const [targetColumns, setTargetColumns] = useState<string[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const extractCsvHeaders = async (csvFile: File): Promise<string[]> => {
    const raw = await csvFile.text();
    const firstLine = raw.split(/\r?\n/)[0] ?? "";
    const normalized = firstLine.replace(/^\uFEFF/, "").trim();
    if (!normalized) {
      return [];
    }

    return normalized
      .split(",")
      .map((header) => header.trim().replace(/^"|"$/g, ""))
      .filter(Boolean);
  };

  const analyzeDataset = async (csvFile: File): Promise<DatasetInfo> => {
    const raw = await csvFile.text();
    const lines = raw.split(/\r?\n/).filter((l) => l.trim());
    const headers = await extractCsvHeaders(csvFile);

    // Sample first few rows to detect types
    const sampleRows = lines.slice(1, Math.min(11, lines.length));
    const columnTypes = headers.map((_, colIndex) => {
      const values = sampleRows
        .map((row) => row.split(",")[colIndex])
        .filter((v) => v && v.trim() && v !== "");

      const numericCount = values.filter((v) => !isNaN(Number(v))).length;
      return numericCount / values.length > 0.7 ? "numeric" : "categorical";
    });

    const numericCount = columnTypes.filter((t) => t === "numeric").length;
    const categoricalCount = columnTypes.filter((t) => t === "categorical").length;

    return {
      rows: lines.length - 1,
      columns: headers.length,
      numericCount,
      categoricalCount,
    };
  };

  const handleSelectedFile = useCallback(async (selectedFile: File) => {
    setFile(selectedFile);
    setTargetColumn("");
    setSubmitError(null);

    try {
      const headers = await extractCsvHeaders(selectedFile);
      setTargetColumns(headers);

      const info = await analyzeDataset(selectedFile);
      setDatasetInfo(info);

      // Auto-advance to step 2
      if (currentStep === 1) {
        setCurrentStep(2);
      }
    } catch {
      setTargetColumns([]);
      setDatasetInfo(null);
    }
  }, [currentStep]);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const dropped = e.dataTransfer.files[0];
      if (dropped && dropped.name.endsWith(".csv")) {
        void handleSelectedFile(dropped);
      }
    },
    [handleSelectedFile]
  );

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (selected && selected.name.endsWith(".csv")) {
      void handleSelectedFile(selected);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!projectName.trim()) {
      setSubmitError("Project name is required.");
      return;
    }

    if (!file) {
      setSubmitError("Please upload a CSV file.");
      return;
    }

    if (!targetColumn) {
      setSubmitError("Please select a target column.");
      return;
    }

    setIsSubmitting(true);
    setSubmitError(null);

    try {
      const response = (await createProject(
        projectName.trim(),
        targetColumn,
        file
      )) as CreateProjectResponse;
      console.log("Project created:", response);
      router.push(
        `/training/${encodeURIComponent(response.file_id)}?target_column=${encodeURIComponent(response.target_column)}`
      );
    } catch (error) {
      console.error("Create project failed:", error);
      const message =
        error instanceof Error
          ? error.message
          : "Something went wrong while creating project.";
      setSubmitError(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  // AI suggestion logic
  const suggestedTarget = targetColumns.length > 0 ? targetColumns[targetColumns.length - 1] : "";
  const taskType = suggestedTarget.toLowerCase().includes("price") || suggestedTarget.toLowerCase().includes("salary") || suggestedTarget.toLowerCase().includes("value")
    ? "regression"
    : "classification";

  return (
    <>
      <Navbar title="New Project" />

      <main className="relative flex-1 overflow-auto">
        {/* Background effects */}
        <div className="pointer-events-none absolute inset-0">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_10%,rgba(45,80,180,0.08),transparent_35%),radial-gradient(circle_at_75%_8%,rgba(120,60,220,0.06),transparent_30%),radial-gradient(circle_at_50%_95%,rgba(20,30,80,0.12),transparent_40%)]" />
          <div className="absolute inset-0 opacity-[0.025] bg-[linear-gradient(rgba(100,160,255,1)_1px,transparent_1px),linear-gradient(90deg,rgba(100,160,255,1)_1px,transparent_1px)] [background-size:48px_48px]" />
        </div>

        <div className="relative z-10 px-6 py-8">
          {/* Hero Section */}
          <div className="mx-auto max-w-7xl mb-8">
            <div className="flex items-start gap-4">
              <div className="flex h-14 w-14 flex-shrink-0 items-center justify-center rounded-2xl border border-violet-500/30 bg-gradient-to-br from-violet-600/20 to-cyan-600/20 shadow-[0_0_24px_rgba(139,92,246,0.3)]">
                <svg className="h-7 w-7 text-violet-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
              </div>
              <div className="flex-1">
                <h1 className="text-[28px] font-bold tracking-tight text-white mb-2">
                  Create New AI Project
                </h1>
                <p className="text-[15px] text-slate-400 leading-relaxed max-w-2xl">
                  Upload your dataset and PyrunAI will automatically train, compare, and optimize machine learning models.
                </p>
              </div>
            </div>
          </div>

          {/* Main Layout: Form + Sidebar */}
          <div className="mx-auto max-w-7xl">
            <div className="flex gap-6 items-start">
              {/* Left: Main Form */}
              <div className="flex-1 min-w-0">
                <form onSubmit={handleSubmit} className="space-y-6">
                  {submitError && (
                    <div className="flex items-center gap-3 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3">
                      <svg className="h-5 w-5 flex-shrink-0 text-rose-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      <p className="text-[13px] text-rose-300">{submitError}</p>
                    </div>
                  )}

                  {/* Step 1: Project Setup */}
                  <StepCard
                    stepNumber={1}
                    title="Project Setup"
                    isActive={currentStep === 1}
                    isCompleted={currentStep > 1}
                  >
                    <div className="space-y-4">
                      <div>
                        <label className="mb-2 block text-[13px] font-semibold text-slate-300">
                          Project Name <span className="text-rose-400">*</span>
                        </label>
                        <input
                          type="text"
                          required
                          value={projectName}
                          onChange={(e) => setProjectName(e.target.value)}
                          placeholder="e.g. Customer Churn Predictor"
                          className="w-full rounded-xl border border-white/[0.08] bg-white/[0.04] px-4 py-2.5 text-[14px] text-slate-100 placeholder-slate-500 outline-none transition-all focus:border-cyan-400/40 focus:bg-white/[0.06] focus:ring-2 focus:ring-cyan-400/20"
                        />
                      </div>
                      <div>
                        <label className="mb-2 block text-[13px] font-semibold text-slate-300">
                          Description <span className="text-[11px] font-normal text-slate-500">(optional)</span>
                        </label>
                        <textarea
                          value={projectDescription}
                          onChange={(e) => setProjectDescription(e.target.value)}
                          placeholder="What are you trying to predict?"
                          rows={3}
                          className="w-full rounded-xl border border-white/[0.08] bg-white/[0.04] px-4 py-2.5 text-[14px] text-slate-100 placeholder-slate-500 outline-none transition-all focus:border-cyan-400/40 focus:bg-white/[0.06] focus:ring-2 focus:ring-cyan-400/20 resize-none"
                        />
                      </div>
                      {projectName && (
                        <button
                          type="button"
                          onClick={() => setCurrentStep(2)}
                          className="mt-2 flex items-center gap-2 text-[13px] font-medium text-cyan-400 transition-colors hover:text-cyan-300"
                        >
                          Continue to Upload
                          <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" />
                          </svg>
                        </button>
                      )}
                    </div>
                  </StepCard>

                  {/* Step 2: Upload Dataset */}
                  <StepCard
                    stepNumber={2}
                    title="Upload Dataset"
                    isActive={currentStep === 2}
                    isCompleted={currentStep > 2 || file !== null}
                  >
                    <div
                      onDragOver={(e) => {
                        e.preventDefault();
                        setDragOver(true);
                      }}
                      onDragLeave={() => setDragOver(false)}
                      onDrop={handleDrop}
                      onClick={() => fileInputRef.current?.click()}
                      className={`group cursor-pointer rounded-2xl border-2 border-dashed px-8 py-12 text-center transition-all ${
                        dragOver
                          ? "border-cyan-400/60 bg-cyan-500/10 shadow-[0_0_32px_rgba(6,182,212,0.25)]"
                          : file
                          ? "border-emerald-400/50 bg-emerald-500/10"
                          : "border-white/[0.08] hover:border-cyan-400/40 hover:bg-white/[0.03]"
                      }`}
                    >
                      {file ? (
                        <div className="flex flex-col items-center gap-3">
                          <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-emerald-400/40 bg-emerald-500/15 shadow-[0_0_24px_rgba(52,211,153,0.3)]">
                            <svg className="h-8 w-8 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                          </div>
                          <div>
                            <p className="text-[15px] font-semibold text-slate-100">{file.name}</p>
                            <p className="text-[12px] text-slate-500 mt-1">{(file.size / 1024).toFixed(1)} KB</p>
                          </div>
                          {datasetInfo && (
                            <div className="mt-4 grid grid-cols-3 gap-3 w-full max-w-md">
                              <div className="rounded-xl border border-white/[0.08] bg-white/[0.04] px-4 py-3">
                                <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Rows</p>
                                <p className="mt-1 text-[18px] font-bold text-cyan-400">{datasetInfo.rows.toLocaleString()}</p>
                              </div>
                              <div className="rounded-xl border border-white/[0.08] bg-white/[0.04] px-4 py-3">
                                <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Columns</p>
                                <p className="mt-1 text-[18px] font-bold text-violet-400">{datasetInfo.columns}</p>
                              </div>
                              <div className="rounded-xl border border-white/[0.08] bg-white/[0.04] px-4 py-3">
                                <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Features</p>
                                <p className="mt-1 text-[12px] font-semibold text-slate-300">
                                  {datasetInfo.numericCount}N / {datasetInfo.categoricalCount}C
                                </p>
                              </div>
                            </div>
                          )}
                          <div className="mt-3 flex items-center gap-3">
                            <button
                              type="button"
                              disabled={isUploading}
                              onClick={async (e) => {
                                e.stopPropagation();
                                if (!file) return;
                                setIsUploading(true);
                                setSubmitError(null);
                                try {
                                  const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
                                  const fd = new FormData();
                                  fd.append("file", file);
                                  const res = await fetch(`${API_BASE}/upload-temp`, { method: "POST", body: fd });
                                  if (!res.ok) {
                                    const err = await res.json().catch(() => ({ detail: "Upload failed" }));
                                    throw new Error(err.detail || "Upload failed");
                                  }
                                  const data = await res.json();
                                  router.push(`/training/${encodeURIComponent(data.file_id)}/transform`);
                                } catch (err) {
                                  setSubmitError(err instanceof Error ? err.message : "Upload failed");
                                } finally {
                                  setIsUploading(false);
                                }
                              }}
                              className="group relative flex items-center gap-2 rounded-xl border border-violet-500/30 bg-gradient-to-r from-violet-600/20 to-fuchsia-600/20 px-4 py-2 text-[13px] font-semibold text-violet-300 shadow-[0_0_16px_rgba(139,92,246,0.2)] transition-all hover:border-violet-400/50 hover:shadow-[0_0_24px_rgba(139,92,246,0.35)] disabled:opacity-60"
                            >
                              {isUploading ? (
                                <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
                                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                                </svg>
                              ) : (
                                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                                </svg>
                              )}
                              {isUploading ? "Uploading…" : "Transform Dataset"}
                            </button>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                setFile(null);
                                setDatasetInfo(null);
                                setTargetColumns([]);
                                setTargetColumn("");
                                setCurrentStep(2);
                              }}
                              className="text-[13px] font-medium text-rose-400 transition-colors hover:text-rose-300"
                            >
                              Remove file
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex flex-col items-center gap-3">
                          <div className="relative flex h-16 w-16 items-center justify-center rounded-2xl border border-white/[0.08] bg-white/[0.04] transition-all group-hover:border-cyan-400/40 group-hover:bg-cyan-500/10">
                            <svg className="h-8 w-8 text-slate-500 transition-colors group-hover:text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                            </svg>
                            <div className="absolute -right-1 -top-1 h-3 w-3 rounded-full bg-cyan-400 opacity-0 shadow-[0_0_12px_rgba(6,182,212,0.8)] transition-opacity group-hover:opacity-100 animate-pulse" />
                          </div>
                          <div>
                            <p className="text-[15px] font-semibold text-slate-200">
                              Drop your CSV file here, or{" "}
                              <span className="text-cyan-400">browse</span>
                            </p>
                            <p className="mt-1 text-[12px] text-slate-500">Maximum file size: 100MB</p>
                          </div>
                        </div>
                      )}
                      <input
                        ref={fileInputRef}
                        type="file"
                        accept=".csv"
                        className="hidden"
                        onChange={handleFileChange}
                      />
                    </div>
                    {file && (
                      <button
                        type="button"
                        onClick={() => setCurrentStep(3)}
                        className="mt-4 flex items-center gap-2 text-[13px] font-medium text-cyan-400 transition-colors hover:text-cyan-300"
                      >
                        Continue to Target Selection
                        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 8l4 4m0 0l-4 4m4-4H3" />
                        </svg>
                      </button>
                    )}
                  </StepCard>

                  {/* Step 3: Target Column Selection */}
                  {file && (
                    <StepCard
                      stepNumber={3}
                      title="Select Target Column"
                      isActive={currentStep === 3}
                      isCompleted={targetColumn !== ""}
                    >
                      <div className="space-y-4">
                        <p className="text-[13px] text-slate-400">
                          Choose the column you want to predict
                        </p>

                        {/* AI Suggestion */}
                        {suggestedTarget && !targetColumn && (
                          <div className="flex items-center gap-2 rounded-xl border border-violet-500/25 bg-violet-500/10 px-4 py-2.5">
                            <svg className="h-4 w-4 flex-shrink-0 text-violet-400" fill="currentColor" viewBox="0 0 20 20">
                              <path fillRule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clipRule="evenodd" />
                            </svg>
                            <p className="text-[12px] text-violet-300">
                              <span className="font-semibold">PyrunAI suggests:</span> {suggestedTarget} ({taskType})
                            </p>
                          </div>
                        )}

                        {/* Column Chips */}
                        <div className="flex flex-wrap gap-2">
                          {targetColumns.map((col) => {
                            const isSelected = targetColumn === col;
                            const isSuggested = col === suggestedTarget;
                            return (
                              <button
                                key={col}
                                type="button"
                                onClick={() => setTargetColumn(col)}
                                className={`group relative overflow-hidden rounded-xl border px-4 py-2.5 text-[13px] font-medium transition-all ${
                                  isSelected
                                    ? "border-cyan-400/50 bg-cyan-500/15 text-cyan-300 shadow-[0_0_16px_rgba(6,182,212,0.25)]"
                                    : isSuggested
                                    ? "border-violet-400/30 bg-violet-500/10 text-violet-300 hover:border-violet-400/50 hover:bg-violet-500/15"
                                    : "border-white/[0.08] bg-white/[0.04] text-slate-300 hover:border-white/[0.15] hover:bg-white/[0.08] hover:text-slate-100"
                                }`}
                              >
                                {isSelected && (
                                  <span className="absolute inset-0 bg-gradient-to-r from-cyan-400/10 to-transparent opacity-50" />
                                )}
                                <span className="relative flex items-center gap-2">
                                  {col}
                                  {isSelected && (
                                    <svg className="h-3.5 w-3.5 text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                                    </svg>
                                  )}
                                  {isSuggested && !isSelected && (
                                    <svg className="h-3.5 w-3.5 text-violet-400" fill="currentColor" viewBox="0 0 20 20">
                                      <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                                    </svg>
                                  )}
                                </span>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    </StepCard>
                  )}

                  {/* Submit Button */}
                  {targetColumn && (
                    <div className="flex items-center gap-3 pt-4">
                      <button
                        type="submit"
                        disabled={isSubmitting}
                        className="group relative flex-1 overflow-hidden rounded-xl bg-gradient-to-r from-cyan-600 to-blue-600 px-6 py-4 text-[15px] font-semibold text-white shadow-[0_0_28px_rgba(6,182,212,0.4)] transition-all duration-300 hover:shadow-[0_0_40px_rgba(6,182,212,0.6)] disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        <span className="absolute inset-0 bg-gradient-to-r from-white/20 via-white/5 to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
                        <span className="relative flex items-center justify-center gap-2">
                          {isSubmitting ? (
                            <>
                              <svg className="h-5 w-5 animate-spin" fill="none" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                              </svg>
                              Creating Project...
                            </>
                          ) : (
                            <>
                              <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                              </svg>
                              Start AutoML Training
                            </>
                          )}
                        </span>
                      </button>
                      <button
                        type="button"
                        onClick={() => router.back()}
                        disabled={isSubmitting}
                        className="rounded-xl border border-white/[0.08] bg-white/[0.04] px-6 py-4 text-[14px] font-medium text-slate-300 transition-all hover:bg-white/[0.08] hover:text-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        Cancel
                      </button>
                    </div>
                  )}
                </form>

                {/* What PyrunAI Will Do */}
                {file && (
                  <div className="mt-8">
                    <AIAutomationPreview />
                  </div>
                )}
              </div>

              {/* Right: Training Preview Card */}
              <div className="sticky top-6 w-[320px] flex-shrink-0">
                <TrainingPreviewCard
                  hasDataset={file !== null}
                  hasTarget={targetColumn !== ""}
                  datasetRows={datasetInfo?.rows ?? 0}
                />
              </div>
            </div>
          </div>
        </div>
      </main>
    </>
  );
}

/* ─── Step Card Component ─────────────────────────────────────────────────────── */
function StepCard({
  stepNumber,
  title,
  isActive,
  isCompleted,
  children,
}: {
  stepNumber: number;
  title: string;
  isActive: boolean;
  isCompleted: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      className={`overflow-hidden rounded-2xl border transition-all ${
        isActive
          ? "border-cyan-400/30 bg-[rgba(10,15,35,0.7)] shadow-[0_0_32px_rgba(6,182,212,0.15)]"
          : isCompleted
          ? "border-emerald-400/20 bg-[rgba(10,15,35,0.5)]"
          : "border-white/[0.06] bg-[rgba(10,15,35,0.4)]"
      }`}
    >
      {/* Step Header */}
      <div className="flex items-center gap-3 border-b border-white/[0.06] px-6 py-4">
        <div
          className={`flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border text-[13px] font-bold transition-all ${
            isCompleted
              ? "border-emerald-400/40 bg-emerald-500/15 text-emerald-400"
              : isActive
              ? "border-cyan-400/40 bg-cyan-500/15 text-cyan-400 shadow-[0_0_16px_rgba(6,182,212,0.3)]"
              : "border-white/[0.08] bg-white/[0.04] text-slate-500"
          }`}
        >
          {isCompleted ? (
            <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
            </svg>
          ) : (
            stepNumber
          )}
        </div>
        <div className="flex-1">
          <h3
            className={`text-[15px] font-semibold ${
              isActive ? "text-white" : isCompleted ? "text-emerald-300" : "text-slate-400"
            }`}
          >
            {title}
          </h3>
        </div>
      </div>

      {/* Step Content */}
      <div className="px-6 py-5">{children}</div>
    </div>
  );
}

/* ─── AI Automation Preview ───────────────────────────────────────────────────── */
function AIAutomationPreview() {
  const steps = [
    { icon: "🔍", label: "Detect feature types" },
    { icon: "✨", label: "Clean missing values" },
    { icon: "🚀", label: "Train multiple ML algorithms" },
    { icon: "⚙️", label: "Perform hyperparameter tuning" },
    { icon: "📊", label: "Compare model performance" },
    { icon: "🏆", label: "Select best model" },
  ];

  return (
    <div className="overflow-hidden rounded-2xl border border-violet-500/20 bg-[rgba(10,15,35,0.6)]">
      <div className="border-b border-white/[0.06] px-6 py-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-violet-400/30 bg-violet-500/15">
            <svg className="h-4 w-4 text-violet-400" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clipRule="evenodd" />
            </svg>
          </div>
          <h3 className="text-[15px] font-semibold text-white">What PyrunAI Will Do Automatically</h3>
        </div>
      </div>
      <div className="px-6 py-5">
        <div className="space-y-3">
          {steps.map((step, i) => (
            <div key={i} className="flex items-center gap-3">
              <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-white/[0.08] bg-white/[0.04]">
                <span className="text-[14px]">{step.icon}</span>
              </div>
              <p className="text-[13px] text-slate-300">{step.label}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ─── Training Preview Card ────────────────────────────────────────────────────── */
function TrainingPreviewCard({
  hasDataset,
  hasTarget,
  datasetRows,
}: {
  hasDataset: boolean;
  hasTarget: boolean;
  datasetRows: number;
}) {
  const estimatedMinutes = Math.max(1, Math.ceil(datasetRows / 10000) * 1.5);

  const algorithms = [
    { name: "Random Forest", icon: "🌲" },
    { name: "XGBoost", icon: "⚡" },
    { name: "LightGBM", icon: "💡" },
    { name: "Logistic Regression", icon: "📈" },
  ];

  return (
    <div className="overflow-hidden rounded-2xl border border-white/[0.08] bg-[rgba(10,15,35,0.7)] shadow-[0_4px_24px_rgba(0,0,0,0.3)]">
      <div className="border-b border-white/[0.06] px-5 py-4">
        <h3 className="text-[14px] font-semibold text-white">Training Preview</h3>
      </div>
      <div className="p-5 space-y-5">
        {/* Estimated Time */}
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-2">
            Estimated Training Time
          </p>
          <div className="flex items-baseline gap-2">
            <span className="text-[28px] font-bold text-transparent bg-clip-text bg-gradient-to-r from-cyan-400 to-blue-400">
              ~{estimatedMinutes}
            </span>
            <span className="text-[14px] font-medium text-slate-400">minutes</span>
          </div>
        </div>

        {/* Progress Checklist */}
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-3">
            Requirements
          </p>
          <div className="space-y-2">
            <ChecklistItem label="Dataset uploaded" checked={hasDataset} />
            <ChecklistItem label="Target selected" checked={hasTarget} />
            <ChecklistItem label="Ready to train" checked={hasDataset && hasTarget} />
          </div>
        </div>

        {/* Algorithms */}
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-3">
            Algorithms to Test
          </p>
          <div className="space-y-2">
            {algorithms.map((algo, i) => (
              <div
                key={i}
                className="flex items-center gap-2.5 rounded-lg border border-white/[0.06] bg-white/[0.03] px-3 py-2"
              >
                <span className="text-[14px]">{algo.icon}</span>
                <span className="text-[12px] text-slate-300">{algo.name}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Tip */}
        <div className="rounded-xl border border-cyan-500/20 bg-cyan-500/5 px-4 py-3">
          <div className="flex items-start gap-2">
            <svg className="h-4 w-4 flex-shrink-0 text-cyan-400 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
            </svg>
            <p className="text-[11px] leading-relaxed text-cyan-300">
              Larger datasets may take longer. You'll receive a notification when training completes.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function ChecklistItem({ label, checked }: { label: string; checked: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <div
        className={`flex h-5 w-5 flex-shrink-0 items-center justify-center rounded border transition-all ${
          checked
            ? "border-emerald-400/40 bg-emerald-500/20 text-emerald-400"
            : "border-white/[0.12] bg-white/[0.04] text-transparent"
        }`}
      >
        <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
        </svg>
      </div>
      <span className={`text-[13px] ${checked ? "text-slate-200" : "text-slate-500"}`}>{label}</span>
    </div>
  );
}

