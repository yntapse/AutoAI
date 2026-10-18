"use client";

/**
 * The guided workflow strip above the canvas.
 *
 * It is a view of the pipeline, not a second pipeline: every status comes from
 * computeStages(), which reads the real canvas, the server's validation answer
 * and the previews that have actually run. Moving between stages only changes
 * which help is shown. Nothing runs when you move, and nothing here changes the
 * canvas unless you press one of the action buttons.
 */

import { instructionFor } from "./guidance";
import { FAN_OUT_EXPLANATION, STAGE_ORDER, STATUS_TEXT, type JoinCheck, type Stage, type StageId, type StageStatus } from "./guidedStages";

interface Props {
  stages: Stage[];
  focus: StageId;
  onFocus: (id: StageId) => void;
  collapsed: boolean;
  onToggle: () => void;
  hasOutput: boolean;
  sourceCount: number;
  joinCount: number;
  onPreviewOutput: () => void;
  /** Why the final dataset cannot be previewed right now, or null if it can. */
  previewBlockedReason: string | null;
  previewing: boolean;
  /** Per-join Configured / Measured state (joins that feed the output). */
  joinStates: JoinCheck[];
  /** Selects the join on the canvas; measuring happens in its settings panel. */
  onOpenJoin: (id: string) => void;
  measureBlockedReason: (id: string) => string | null;
  measuring: boolean;
  onAcknowledge: (id: string) => void;
  onWithdraw: (id: string) => void;
}

const BADGE: Record<StageStatus, string> = {
  complete: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  incomplete: "border-white/[0.12] text-slate-400",
  invalid: "border-rose-500/30 bg-rose-500/10 text-rose-300",
  measurement_required: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  measurement_outdated: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  review_required: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  not_validated: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  outdated: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  optional: "border-white/[0.12] text-slate-500",
  not_needed: "border-white/[0.12] text-slate-500",
};

const GUIDE: Record<StageId, string> = {
  select: "Choose the tables you need from the list on the left. Tables ticked in the Data Catalog arrive here already placed.",
  join: "Tell the pipeline how the tables connect. Accept a suggested join (check its type and evidence) or add a join step and pick the matching columns yourself.",
  transform: "Optional. Filter rows, drop or rename columns, remove duplicates or handle missing values. Skip this if the joined data is already what you need.",
  validate: "Validated means two things: every join has been measured on the real data (Measured), and the final dataset preview succeeds on the current settings. Both read from your database (read-only) and only run when you press a button.",
  final: "This is the dataset you have defined and checked. Saving it, publishing it and sending it to AutoML are not available yet.",
};

const btn =
  "rounded-lg border border-white/[0.1] px-2.5 py-1 text-[12px] text-slate-200 hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-40";

export default function GuidedStrip(p: Props) {
  const index = STAGE_ORDER.indexOf(p.focus);
  const stage = p.stages.find((s) => s.id === p.focus) as Stage;
  const instruction = instructionFor(stage, { hasOutput: p.hasOutput, sourceCount: p.sourceCount, joinCount: p.joinCount });
  // A listed problem that the instruction already says word for word adds nothing; every other one stays visible.
  const problems = stage.problems.filter((t) => !instruction.toLowerCase().includes(t.toLowerCase().replace(/\.$/, "")));
  return (
    <section className="mb-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-2.5" aria-label="Guided workflow" data-testid="guided-strip">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-[12.5px] font-semibold text-white" title="Worked out from your pipeline. The canvas below is the same one.">
          Guided workflow
        </h2>
        <ol className="flex flex-wrap gap-1.5">
          {p.stages.map((s, i) => (
            <li key={s.id}>
              <button
                className={`flex items-center gap-1.5 rounded-lg border px-2 py-1 text-left text-[11.5px] ${
                  s.id === p.focus ? "border-cyan-500/50 bg-cyan-500/10 text-white" : "border-white/[0.08] text-slate-300 hover:bg-white/[0.05]"
                }`}
                onClick={() => p.onFocus(s.id)}
                aria-current={s.id === p.focus ? "step" : undefined}
                data-testid={`stage-${s.id}`}
                data-status={s.status}
              >
                <span className="text-slate-500">{i + 1}</span>
                <span>{s.label}</span>
                <span className={`rounded-full border px-1.5 py-[1px] text-[10px] ${BADGE[s.status]}`}>{STATUS_TEXT[s.status]}</span>
              </button>
            </li>
          ))}
        </ol>
        <button className={`${btn} ml-auto`} onClick={p.onToggle} aria-expanded={!p.collapsed} data-testid="guided-toggle">
          {p.collapsed ? "Show guide" : "Hide guide"}
        </button>
      </div>

      {!p.collapsed && (
        <div className="mt-2 rounded-lg bg-white/[0.03] p-2.5" data-testid="stage-panel" data-stage={stage.id}>
          <div className="text-[13px] font-semibold text-white" data-testid="stage-instruction">
            {instruction}
          </div>
          <div className="mt-0.5 text-[11.5px] text-slate-400">
            {index + 1}. {stage.label}: {stage.summary}
          </div>
          {problems.length > 0 && (
            <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-[11.5px] text-amber-300" data-testid="stage-problems">
              {problems.map((t) => (
                <li key={t}>{t}</li>
              ))}
            </ul>
          )}

          {stage.notes.length > 0 && (
            <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-[11.5px] text-amber-200" data-testid="stage-notes">
              {stage.notes.map((t) => (
                <li key={t}>{t}</li>
              ))}
            </ul>
          )}
          {(stage.id === "join" || stage.id === "validate") && p.joinStates.length > 0 && (
            <ul className="mt-2 space-y-1" data-testid="join-states">
              {p.joinStates.map((j) => {
                const reason = p.measureBlockedReason(j.id);
                const canAct = stage.id === "validate" && (j.state === "configured" || j.state === "measurement_outdated");
                return (
                  <li key={j.id} className="flex flex-wrap items-center gap-2 text-[12px]" data-testid="join-state" data-state={j.state}>
                    <span className="font-medium text-slate-200">{j.id}</span>
                    <span className="text-slate-400">{j.text}</span>
                    {canAct && (
                      <button className={btn} onClick={() => p.onOpenJoin(j.id)} data-testid="open-join">
                        Open this join to {j.state === "configured" ? "measure it" : "measure it again"}
                      </button>
                    )}
                    {canAct && reason && <span className="text-[11.5px] text-slate-500">{reason}</span>}
                    {stage.id === "validate" && j.fanOut && (
                      <div
                        className="mt-1 w-full rounded-lg border border-amber-500/25 bg-amber-500/[0.06] p-2"
                        data-testid="fan-out"
                        data-acknowledged={j.fanOut.acknowledged}
                      >
                        <div className="text-[12px] font-medium text-amber-200">
                          Warning, not an error: {j.id} repeats rows
                        </div>
                        <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11.5px] text-slate-300">
                          {j.fanOut.evidence.map((t) => (
                            <li key={t}>{t}</li>
                          ))}
                        </ul>
                        <p className="mt-1 text-[11.5px] text-slate-400">{FAN_OUT_EXPLANATION}</p>
                        {j.fanOut.acknowledged ? (
                          <div className="mt-1.5 flex items-center gap-2 text-[11.5px] text-emerald-300">
                            You acknowledged this warning for the current settings.
                            <button className={btn} onClick={() => p.onWithdraw(j.id)}>
                              Withdraw
                            </button>
                          </div>
                        ) : (
                          <button className={`${btn} mt-1.5`} onClick={() => p.onAcknowledge(j.id)}>
                            I understand and accept the repeated rows
                          </button>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          <div className="mt-2 flex flex-wrap items-center gap-2">
            {stage.id === "validate" && p.hasOutput && (
              <>
                <button className={btn} onClick={p.onPreviewOutput} disabled={p.previewBlockedReason !== null || p.previewing} data-testid="guided-preview">
                  {p.previewing ? "Previewing…" : "Preview the final dataset"}
                </button>
                {p.previewBlockedReason && <span className="text-[11.5px] text-slate-500">{p.previewBlockedReason}</span>}
              </>
            )}
            {stage.id === "final" && (
              <>
                {["Save dataset definition", "Publish dataset", "Send to AutoML"].map((t) => (
                  <button key={t} className={btn} disabled title="Not available yet" data-testid="final-action">
                    {t}
                  </button>
                ))}
                <span className="text-[11.5px] text-slate-500">Not available yet. Your pipeline stays as a draft in this browser.</span>
              </>
            )}
            <span className="ml-auto flex flex-wrap items-center gap-2">
              <button className={btn} onClick={() => p.onFocus(STAGE_ORDER[index - 1])} disabled={index === 0}>
                Back
              </button>
              <button className={btn} onClick={() => p.onFocus(STAGE_ORDER[index + 1])} disabled={index === STAGE_ORDER.length - 1}>
                Continue
              </button>
            </span>
          </div>

          <details className="mt-1.5 text-[11.5px] text-slate-400" data-testid="stage-help">
            <summary className="w-fit cursor-pointer select-none text-slate-300">How this works</summary>
            <p className="mt-1 max-w-3xl">{GUIDE[stage.id]}</p>
            <p className="mt-1 text-slate-500">Moving between stages never runs anything.</p>
          </details>
        </div>
      )}
    </section>
  );
}
