"use client";

import { useState } from "react";
import {
  comparePipelineVersions,
  listPipelineVersions,
  listVersionCleaningSteps,
  restorePipelineVersion,
  type CleaningStep,
  type PipelineVersion,
} from "@/services/projectService";

interface VersionHistoryProps {
  pipelineId: string | null;
  versions: PipelineVersion[];
  onVersionsChange: (versions: PipelineVersion[]) => void;
  onStepsRestore: (steps: CleaningStep[], versions: PipelineVersion[]) => void;
}

export default function VersionHistory({
  pipelineId,
  versions,
  onVersionsChange,
  onStepsRestore,
}: VersionHistoryProps) {
  const [error, setError] = useState<string | null>(null);
  const [changes, setChanges] = useState<Record<string, { before: unknown; after: unknown }> | null>(null);

  async function restore(version: PipelineVersion) {
    if (!pipelineId) return;
    setError(null);
    try {
      await restorePipelineVersion(pipelineId, version.id);
      const next = await listPipelineVersions(pipelineId);
      onVersionsChange(next);
      const latest = next[next.length - 1];
      onStepsRestore(
        latest ? await listVersionCleaningSteps(latest.id) : [],
        next,
      );
    } catch (restoreError) {
      setError(restoreError instanceof Error ? restoreError.message : "Could not restore the version.");
    }
  }

  async function compare() {
    if (!pipelineId || versions.length < 2) return;
    setError(null);
    try {
      const result = await comparePipelineVersions(
        pipelineId,
        versions[versions.length - 2].version_number,
        versions[versions.length - 1].version_number,
      );
      setChanges(result.changes);
    } catch (compareError) {
      setError(compareError instanceof Error ? compareError.message : "Could not compare versions.");
    }
  }

  return (
    <section className="space-y-2 rounded-lg border border-white/10 p-3">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
          Saved Cleaning Versions
        </h4>
        {versions.length > 1 && (
          <button type="button" onClick={() => void compare()} className="text-xs text-cyan-300">
            Compare latest
          </button>
        )}
      </div>
      {!pipelineId ? (
        <p className="text-xs text-slate-500">Save a pipeline after a successful build to enable version history.</p>
      ) : versions.length === 0 ? (
        <p className="text-xs text-slate-500">No saved versions yet.</p>
      ) : (
        <ul className="space-y-2">
          {versions.map((version, index) => (
            <li key={version.id} className="flex items-center justify-between gap-2 text-xs">
              <span className="text-slate-300">
                Version {version.version_number} · {new Date(version.created_at).toLocaleString()}
              </span>
              {index < versions.length - 1 && (
                <button type="button" onClick={() => void restore(version)} className="text-amber-300">
                  Restore as new version
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {changes && (
        <pre className="max-h-48 overflow-auto rounded bg-black/30 p-2 text-xs text-slate-300">
          {JSON.stringify(changes.cleaning_steps ?? changes, null, 2)}
        </pre>
      )}
      {error && <p role="alert" className="text-xs text-red-400">{error}</p>}
    </section>
  );
}
