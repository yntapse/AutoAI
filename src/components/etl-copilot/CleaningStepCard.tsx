import type { CleaningStep } from "@/services/projectService";

interface CleaningStepCardProps {
  step: CleaningStep;
  index: number;
  count: number;
  onEdit: () => void;
  onDelete: () => void;
  onMove: (direction: -1 | 1) => void;
  onToggle: () => void;
}

const LABELS: Record<CleaningStep["step_type"], string> = {
  remove_null_rows: "Remove Null Rows",
  replace_null_values: "Replace Null Values",
  remove_duplicates: "Remove Duplicates",
  convert_data_type: "Convert Data Type",
  handle_outliers: "Handle Outliers",
};

export default function CleaningStepCard({
  step,
  index,
  count,
  onEdit,
  onDelete,
  onMove,
  onToggle,
}: CleaningStepCardProps) {
  return (
    <article className="rounded-xl border border-white/10 bg-black/20 p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-slate-500">Step {index + 1}</p>
          <h4 className="font-medium text-white">{step.step_name}</h4>
          <p className="mt-1 text-xs text-slate-400">
            {LABELS[step.step_type]} ·{" "}
            {step.created_at
              ? `Created ${new Date(step.created_at).toLocaleString()}`
              : "Draft"}
          </p>
          <p className="mt-1 break-all text-xs text-slate-500">
            {JSON.stringify(step.configuration)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <button type="button" onClick={onToggle} className="text-amber-300">
            {step.enabled ? "Disable" : "Enable"}
          </button>
          <button type="button" onClick={onEdit} className="text-cyan-300">Edit</button>
          <button type="button" disabled={index === 0} onClick={() => onMove(-1)} className="text-slate-300 disabled:opacity-30">Move up</button>
          <button type="button" disabled={index === count - 1} onClick={() => onMove(1)} className="text-slate-300 disabled:opacity-30">Move down</button>
          <button type="button" onClick={onDelete} className="text-red-300">Delete</button>
        </div>
      </div>
    </article>
  );
}
