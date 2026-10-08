import type { CleaningStep } from "@/services/projectService";

interface CleaningHistoryProps {
  steps: CleaningStep[];
}

export default function CleaningHistory({ steps }: CleaningHistoryProps) {
  if (steps.length === 0) return null;
  return (
    <div className="space-y-1">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Cleaning Steps</h4>
      <ol className="space-y-1">
        {steps.map((step, index) => (
          <li key={step.id ?? `${step.step_type}-${index}`} className="text-xs text-slate-400">
            {index + 1}. {step.step_name} · {step.enabled ? "enabled" : "disabled"}
          </li>
        ))}
      </ol>
    </div>
  );
}
