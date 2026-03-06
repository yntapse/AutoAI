"use client";

import Navbar from "@/components/Navbar";
import { useState, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { createProject } from "@/services/projectService";

interface CreateProjectResponse {
  file_id: string;
  target_column: string;
}

export default function NewProjectPage() {
  const router = useRouter();
  const [projectName, setProjectName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [targetColumn, setTargetColumn] = useState("");
  const [targetColumns, setTargetColumns] = useState<string[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
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

  const handleSelectedFile = useCallback(async (selectedFile: File) => {
    setFile(selectedFile);
    setTargetColumn("");
    setSubmitError(null);

    try {
      const headers = await extractCsvHeaders(selectedFile);
      setTargetColumns(headers);
    } catch {
      setTargetColumns([]);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped && dropped.name.endsWith(".csv")) {
      void handleSelectedFile(dropped);
    }
  }, [handleSelectedFile]);

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
      const response = await createProject(projectName.trim(), targetColumn, file) as CreateProjectResponse;
      console.log("Project created:", response);
      router.push(
        `/training/${encodeURIComponent(response.file_id)}?target_column=${encodeURIComponent(response.target_column)}`
      );
    } catch (error) {
      console.error("Create project failed:", error);
      const message = error instanceof Error ? error.message : "Something went wrong while creating project.";
      setSubmitError(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <>
      <Navbar title="New Project" />

      <main className="flex-1 px-8 py-8 overflow-auto">
        <div className="max-w-2xl mx-auto">
          <div className="mb-6.5">
            <h2 className="text-[23px] font-semibold tracking-tight text-slate-100">Create New Project</h2>
            <p className="text-sm text-slate-400 mt-1.5">
              Upload a CSV dataset and AutoAI will automatically train and compare ML models.
            </p>
          </div>

          <div className="bg-[#0F172A]/70 backdrop-blur-sm rounded-2xl border border-[#1e3a52]/85 shadow-[0_0_28px_rgba(11,31,58,0.55)] p-8">
            <form onSubmit={handleSubmit} className="space-y-7">
              {submitError && (
                <div className="rounded-xl border border-rose-400/35 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
                  {submitError}
                </div>
              )}

              {/* Project Name */}
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-2">
                  Project Name <span className="text-red-400">*</span>
                </label>
                <input
                  type="text"
                  required
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  placeholder="e.g. Customer Churn Predictor"
                  className="w-full px-3.5 py-2.5 rounded-xl border border-[#1e3a52] bg-[#0B1F3A]/70 text-sm text-slate-100 placeholder-slate-500 outline-none focus:ring-2 focus:ring-[#3BB273]/60 focus:border-[#3BB273]/35 transition-all duration-200"
                />
              </div>

              {/* CSV Upload */}
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-2">
                  Training Dataset <span className="text-red-400">*</span>
                </label>
                <div
                  onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={handleDrop}
                  onClick={() => fileInputRef.current?.click()}
                  className={`cursor-pointer border-2 border-dashed rounded-xl px-6 py-10 text-center transition-colors ${
                    dragOver
                      ? "border-[#3BB273]/60 bg-[#3BB273]/10"
                      : file
                      ? "border-[#5EDC8A]/45 bg-[#3BB273]/10"
                      : "border-[#1e3a52] hover:border-[#3BB273]/45 hover:bg-[#123C66]/70"
                  }`}
                >
                  {file ? (
                    <div className="flex flex-col items-center gap-2">
                      <div className="w-10 h-10 rounded-xl bg-[#3BB273]/20 border border-[#5EDC8A]/35 flex items-center justify-center">
                        <svg className="w-5 h-5 text-[#5EDC8A]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                        </svg>
                      </div>
                      <p className="text-sm font-medium tracking-tight text-slate-100">{file.name}</p>
                      <p className="text-xs text-slate-500">{(file.size / 1024).toFixed(1)} KB</p>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setFile(null);
                          setTargetColumns([]);
                          setTargetColumn("");
                        }}
                        className="text-xs text-rose-400 hover:text-rose-300 mt-1 transition-colors"
                      >
                        Remove file
                      </button>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center gap-2">
                      <div className="w-10 h-10 rounded-xl bg-[#123C66] border border-[#1e3a52] flex items-center justify-center mb-1">
                        <svg className="w-5 h-5 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                        </svg>
                      </div>
                      <p className="text-sm font-medium text-slate-300 tracking-tight">
                        Drop your CSV file here, or{" "}
                        <span className="text-[#5EDC8A]">browse</span>
                      </p>
                      <p className="text-xs text-slate-500">Only .csv files are supported</p>
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
              </div>

              {/* Target Column */}
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-2">
                  Target Column <span className="text-red-400">*</span>
                </label>
                <p className="text-xs text-slate-500 mb-2">
                  The column you want to predict (detected from your CSV)
                </p>
                <select
                  required
                  value={targetColumn}
                  onChange={(e) => setTargetColumn(e.target.value)}
                  className="w-full px-3.5 py-2.5 rounded-xl border border-[#1e3a52] text-sm text-slate-100 outline-none focus:ring-2 focus:ring-[#3BB273]/60 focus:border-[#3BB273]/35 transition-all duration-200 bg-[#0B1F3A]/70 appearance-none"
                >
                  <option value="" disabled>Select target column...</option>
                  {targetColumns.map((col) => (
                    <option key={col} value={col}>{col}</option>
                  ))}
                </select>
                {file && targetColumns.length === 0 && (
                  <p className="text-xs text-amber-300 mt-2">
                    Could not detect columns automatically. Please verify CSV format.
                  </p>
                )}
              </div>

              {/* Info card */}
              <div className="bg-[#0F172A]/70 border border-[#1e3a52] rounded-xl p-4.5 flex gap-3">
                <svg className="w-5 h-5 text-[#5EDC8A] flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <div>
                  <p className="text-sm font-medium text-slate-200">AutoAI will automatically:</p>
                  <ul className="text-xs text-slate-400 mt-1 space-y-0.5 list-disc list-inside">
                    <li>Analyze your dataset and detect feature types</li>
                    <li>Train multiple ML algorithms in parallel</li>
                    <li>Select and evaluate the best model</li>
                  </ul>
                </div>
              </div>

              <div className="flex items-center gap-3 pt-2">
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="flex-1 text-white font-medium text-sm py-3 rounded-xl bg-[#3BB273] hover:bg-[#2FA565] shadow-[0_0_24px_rgba(59,178,115,0.3)] hover:shadow-[0_0_30px_rgba(59,178,115,0.4)] transition-all duration-200 flex items-center justify-center gap-2"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                  </svg>
                  {isSubmitting ? "Creating Project..." : "Start Training"}
                </button>
                <button
                  type="button"
                  onClick={() => router.back()}
                  className="px-5 py-3 rounded-xl border border-[#1e3a52] text-sm font-medium text-slate-300 hover:bg-[#123C66]/80 transition-all duration-200"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      </main>
    </>
  );
}
