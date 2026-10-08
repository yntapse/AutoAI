"use client";

import { useState } from "react";
import type { CleaningStep } from "@/services/projectService";

interface CleaningStepEditorProps {
  step: CleaningStep | null;
  columns: string[];
  onSave: (step: CleaningStep) => void;
  onCancel: () => void;
}

type StepType = CleaningStep["step_type"];

const STEP_TYPES: { value: StepType; label: string }[] = [
  { value: "remove_null_rows", label: "Remove Null Rows" },
  { value: "replace_null_values", label: "Replace Null Values" },
  { value: "remove_duplicates", label: "Remove Duplicates" },
  { value: "convert_data_type", label: "Convert Data Type" },
  { value: "handle_outliers", label: "Handle Outliers" },
];

const INPUT =
  "w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white";

function initialConfiguration(type: StepType): Record<string, unknown> {
  switch (type) {
    case "remove_null_rows":
      return { scope: "column", column_name: "" };
    case "replace_null_values":
      return { column_name: "", strategy: "zero" };
    case "remove_duplicates":
      return { scope: "entire_row" };
    case "convert_data_type":
      return { column_name: "", target_type: "string" };
    case "handle_outliers":
      return { column_name: "", strategy: "remove", iqr_factor: 1.5 };
  }
}

export default function CleaningStepEditor({
  step,
  columns,
  onSave,
  onCancel,
}: CleaningStepEditorProps) {
  const [stepType, setStepType] = useState<StepType>(
    step?.step_type ?? "remove_null_rows",
  );
  const [stepName, setStepName] = useState(step?.step_name ?? "");
  const [config, setConfig] = useState<Record<string, unknown>>(
    step?.configuration ?? initialConfiguration(step?.step_type ?? "remove_null_rows"),
  );

  function changeType(value: StepType) {
    setStepType(value);
    setConfig(initialConfiguration(value));
    setStepName(STEP_TYPES.find((item) => item.value === value)?.label ?? "");
  }

  function setValue(key: string, value: unknown) {
    setConfig((current) => ({ ...current, [key]: value }));
  }

  const columnSelect = (
    <label className="space-y-1 text-xs text-slate-300">
      Column
      <select
        className={INPUT}
        value={String(config.column_name ?? "")}
        onChange={(event) => setValue("column_name", event.target.value)}
      >
        <option value="">Select a column</option>
        {columns.map((column) => <option key={column} value={column}>{column}</option>)}
      </select>
    </label>
  );

  return (
    <div className="space-y-3 rounded-xl border border-cyan-500/20 bg-cyan-500/[0.03] p-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-xs text-slate-300">
          Cleaning step
          <select
            className={INPUT}
            value={stepType}
            onChange={(event) => changeType(event.target.value as StepType)}
          >
            {STEP_TYPES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>
        <label className="space-y-1 text-xs text-slate-300">
          Step name
          <input className={INPUT} value={stepName} onChange={(event) => setStepName(event.target.value)} />
        </label>
      </div>

      {stepType === "remove_null_rows" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-xs text-slate-300">
            Remove nulls in
            <select
              className={INPUT}
              value={String(config.scope ?? "column")}
              onChange={(event) => setValue("scope", event.target.value)}
            >
              <option value="column">Selected column</option>
              <option value="all_columns">All columns</option>
            </select>
          </label>
          {config.scope !== "all_columns" && columnSelect}
        </div>
      )}

      {stepType === "replace_null_values" && (
        <div className="grid gap-3 sm:grid-cols-2">
          {columnSelect}
          <label className="space-y-1 text-xs text-slate-300">
            Replace with
            <select
              className={INPUT}
              value={String(config.strategy ?? "zero")}
              onChange={(event) => setValue("strategy", event.target.value)}
            >
              <option value="zero">0</option>
              <option value="mean">Mean</option>
              <option value="median">Median</option>
              <option value="mode">Mode</option>
              <option value="custom">Custom value</option>
            </select>
          </label>
          {config.strategy === "custom" && (
            <label className="space-y-1 text-xs text-slate-300">
              Custom value
              <input
                className={INPUT}
                value={String(config.custom_value ?? "")}
                onChange={(event) => setValue("custom_value", event.target.value)}
              />
            </label>
          )}
        </div>
      )}

      {stepType === "remove_duplicates" && (
        <div className="space-y-3">
          <label className="space-y-1 text-xs text-slate-300">
            Check duplicates by
            <select
              className={INPUT}
              value={String(config.scope ?? "entire_row")}
              onChange={(event) => setValue("scope", event.target.value)}
            >
              <option value="entire_row">Entire row</option>
              <option value="selected_columns">Selected columns</option>
            </select>
          </label>
          {config.scope === "selected_columns" && (
            <div className="grid gap-2 sm:grid-cols-3">
              {columns.map((column) => {
                const chosen = Array.isArray(config.columns)
                  ? config.columns as string[]
                  : [];
                return (
                  <label key={column} className="flex items-center gap-2 text-xs text-slate-300">
                    <input
                      type="checkbox"
                      checked={chosen.includes(column)}
                      onChange={(event) =>
                        setValue(
                          "columns",
                          event.target.checked
                            ? [...chosen, column]
                            : chosen.filter((item) => item !== column),
                        )
                      }
                    />
                    {column}
                  </label>
                );
              })}
            </div>
          )}
        </div>
      )}

      {stepType === "convert_data_type" && (
        <div className="grid gap-3 sm:grid-cols-2">
          {columnSelect}
          <label className="space-y-1 text-xs text-slate-300">
            Convert to
            <select
              className={INPUT}
              value={String(config.target_type ?? "string")}
              onChange={(event) => setValue("target_type", event.target.value)}
            >
              {["string", "integer", "float", "boolean", "date"].map((type) => (
                <option key={type} value={type}>{type[0].toUpperCase() + type.slice(1)}</option>
              ))}
            </select>
          </label>
        </div>
      )}

      {stepType === "handle_outliers" && (
        <div className="grid gap-3 sm:grid-cols-3">
          {columnSelect}
          <label className="space-y-1 text-xs text-slate-300">
            Handling
            <select
              className={INPUT}
              value={String(config.strategy ?? "remove")}
              onChange={(event) => setValue("strategy", event.target.value)}
            >
              <option value="remove">Remove outliers</option>
              <option value="cap">Cap outliers</option>
              <option value="median">Replace with median</option>
            </select>
          </label>
          <label className="space-y-1 text-xs text-slate-300">
            IQR factor
            <input
              className={INPUT}
              type="number"
              min="0.01"
              step="0.1"
              value={String(config.iqr_factor ?? 1.5)}
              onChange={(event) => setValue("iqr_factor", Number(event.target.value))}
            />
          </label>
        </div>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          disabled={!stepName.trim()}
          onClick={() =>
            onSave({
              ...step,
              step_name: stepName.trim(),
              step_type: stepType,
              configuration: config,
              enabled: step?.enabled ?? true,
              created_at: step?.created_at ?? new Date().toISOString(),
            })
          }
          className="rounded-lg bg-cyan-700 px-3 py-2 text-xs font-medium text-white disabled:opacity-50"
        >
          Save Step
        </button>
        <button type="button" onClick={onCancel} className="rounded-lg border border-white/10 px-3 py-2 text-xs text-slate-300">
          Cancel
        </button>
      </div>
    </div>
  );
}
