"use client";

import { useState } from "react";
import {
  previewCleaningPipeline,
  saveCleaningSteps,
  type CleaningPreviewResult,
  type CleaningStep,
  type DataModelConnectionParams,
  type PipelineVersion,
} from "@/services/projectService";
import CleaningHistory from "./CleaningHistory";
import CleaningPreview from "./CleaningPreview";
import CleaningStepCard from "./CleaningStepCard";
import CleaningStepEditor from "./CleaningStepEditor";
import VersionHistory from "./VersionHistory";

interface CleaningPipelineProps {
  connection: DataModelConnectionParams;
  userRequest: string;
  columns: string[];
  steps: CleaningStep[];
  onStepsChange: (steps: CleaningStep[]) => void;
  pipelineId: string | null;
  versions: PipelineVersion[];
  onVersionsChange: (versions: PipelineVersion[]) => void;
  onCleaningStepsSaved: (steps: CleaningStep[], versions: PipelineVersion[]) => void;
}

const LABEL: Record<CleaningStep["step_type"], string> = {
  remove_null_rows: "Remove Null Rows",
  replace_null_values: "Replace Null Values",
  remove_duplicates: "Remove Duplicates",
  convert_data_type: "Convert Data Type",
  handle_outliers: "Handle Outliers",
};

export default function CleaningPipeline({
  connection,
  userRequest,
  columns,
  steps,
  onStepsChange,
  pipelineId,
  versions,
  onVersionsChange,
  onCleaningStepsSaved,
}: CleaningPipelineProps) {
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [addingType, setAddingType] = useState<CleaningStep["step_type"]>("remove_null_rows");
  const [preview, setPreview] = useState<CleaningPreviewResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function saveStep(step: CleaningStep) {
    const next = [...steps];
    if (editingIndex === null) next.push(step);
    else next[editingIndex] = step;
    onStepsChange(next.map((item, index) => ({ ...item, step_order: index + 1 })));
    setEditingIndex(null);
    setAdding(false);
    setPreview(null);
  }

  function moveStep(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= steps.length) return;
    const next = [...steps];
    [next[index], next[target]] = [next[target], next[index]];
    onStepsChange(next.map((item, order) => ({ ...item, step_order: order + 1 })));
    setPreview(null);
  }

  async function runPreview() {
    if (!userRequest.trim()) {
      setError("Enter a dataset request before previewing the cleaning steps.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setPreview(
        await previewCleaningPipeline({
          ...connection,
          userRequest,
          cleaningSteps: steps,
        }),
      );
    } catch (previewError) {
      setError(previewError instanceof Error ? previewError.message : "Cleaning preview failed.");
    } finally {
      setBusy(false);
    }
  }

  async function persistSteps() {
    if (!pipelineId) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await saveCleaningSteps(pipelineId, steps);
      const nextVersions = updated.versions;
      onVersionsChange(nextVersions);
      onCleaningStepsSaved(steps, nextVersions);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Could not save cleaning version.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-4 rounded-2xl border border-amber-400/20 bg-amber-500/[0.025] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-white">Data Cleaning</h3>
          <p className="mt-1 text-xs text-slate-400">
            Steps run in order on the generated data in memory; source tables are never modified.
            Save a step set as a new version after a successful build.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy || !userRequest.trim()}
            onClick={() => void runPreview()}
            className="rounded-lg border border-emerald-400/30 px-3 py-2 text-xs font-medium text-emerald-200 disabled:opacity-50"
          >
            {busy ? "Working..." : "Preview"}
          </button>
          {pipelineId && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void persistSteps()}
              className="rounded-lg bg-cyan-700 px-3 py-2 text-xs font-medium text-white disabled:opacity-50"
            >
              Save Cleaning Version
            </button>
          )}
          <button
            type="button"
            disabled={adding || editingIndex !== null}
            onClick={() => setAdding(true)}
            className="rounded-lg bg-amber-600 px-3 py-2 text-xs font-medium text-white disabled:opacity-50"
          >
            + Add Cleaning Step
          </button>
        </div>
      </div>

      {error && <p role="alert" className="text-sm text-red-400">{error}</p>}

      {adding && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-slate-300">New cleaning step</p>
          <select
            aria-label="New cleaning step type"
            className="rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white"
            value={addingType}
            onChange={(event) => {
              const type = event.target.value as CleaningStep["step_type"];
              setAddingType(type);
            }}
          >
            {Object.entries(LABEL).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>
      )}

      {adding && (
        <CleaningStepEditor
          key={`new-${addingType}`}
          step={{
            step_name: LABEL[addingType],
            step_type: addingType,
            configuration: defaultConfig(addingType),
            enabled: true,
          }}
          columns={columns}
          onSave={(step) => {
            saveStep(step);
            setAdding(false);
          }}
          onCancel={() => setAdding(false)}
        />
      )}

      <div className="space-y-2">
        {steps.length === 0 && !adding ? (
          <p className="rounded-lg border border-dashed border-white/10 p-4 text-sm text-slate-500">
            No cleaning steps. Add one to configure the data cleaning pipeline.
          </p>
        ) : steps.map((step, index) => (
          editingIndex === index ? (
            <CleaningStepEditor
              key={step.id ?? `${step.step_type}-${index}`}
              step={step}
              columns={columns}
              onSave={saveStep}
              onCancel={() => setEditingIndex(null)}
            />
          ) : (
            <CleaningStepCard
              key={step.id ?? `${step.step_type}-${index}`}
              step={step}
              index={index}
              count={steps.length}
              onEdit={() => setEditingIndex(index)}
              onDelete={() => {
                onStepsChange(steps.filter((_, itemIndex) => itemIndex !== index).map((item, order) => ({ ...item, step_order: order + 1 })));
                setPreview(null);
              }}
              onMove={(direction) => moveStep(index, direction)}
              onToggle={() => onStepsChange(steps.map((item, itemIndex) => itemIndex === index ? { ...item, enabled: !item.enabled } : item))}
            />
          )
        ))}
      </div>

      <CleaningHistory steps={steps} />
      <CleaningPreview preview={preview} />
      <VersionHistory
        pipelineId={pipelineId}
        versions={versions}
        onVersionsChange={onVersionsChange}
        onStepsRestore={(restoredSteps, restoredVersions) => {
          onStepsChange(restoredSteps);
          onCleaningStepsSaved(restoredSteps, restoredVersions);
        }}
      />
    </section>
  );
}

function defaultConfig(type: CleaningStep["step_type"]): Record<string, unknown> {
  switch (type) {
    case "remove_null_rows": return { scope: "column", column_name: "" };
    case "replace_null_values": return { column_name: "", strategy: "zero" };
    case "remove_duplicates": return { scope: "entire_row" };
    case "convert_data_type": return { column_name: "", target_type: "string" };
    case "handle_outliers": return { column_name: "", strategy: "remove", iqr_factor: 1.5 };
  }
}
