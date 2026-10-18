"use client";

/**
 * Shared primitives for the Pipeline Management screens.
 *
 * Colour: run state is a STATUS encoding, not a categorical series, so it uses
 * the reserved status palette (good / warning / critical) and never a series
 * hue. Every status is shipped with an icon glyph AND a text label, so meaning
 * never rests on colour alone - the three steps clear 3:1 against this app's
 * slate surface and CVD separation, but a colourblind or monochrome reader
 * still reads the label.
 */

import { ReactNode } from "react";

export const STATUS_COLOR = {
  good: "#0ca30c",
  warning: "#fab219",
  critical: "#d03b3b",
  neutral: "#898781",
} as const;

export type StatusKind = keyof typeof STATUS_COLOR;

const SUCCESS = ["completed", "succeeded", "success"];
const FAILURE = ["failed", "error", "cancelled"];

export function statusKind(status: string | null | undefined): StatusKind {
  const value = String(status ?? "").toLowerCase();
  if (SUCCESS.includes(value)) return "good";
  if (FAILURE.includes(value)) return "critical";
  if (!value) return "neutral";
  return "warning";
}

/** Icon glyph per status - the non-colour channel. */
export function statusGlyph(kind: StatusKind): string {
  if (kind === "good") return "✓";
  if (kind === "critical") return "✕";
  if (kind === "warning") return "●";
  return "—";
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  const kind = statusKind(status);
  const label = status ? String(status) : "never run";
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px]"
      style={{ borderColor: `${STATUS_COLOR[kind]}66`, color: STATUS_COLOR[kind] }}
    >
      <span aria-hidden>{statusGlyph(kind)}</span>
      <span className="capitalize">{label}</span>
    </span>
  );
}

/**
 * A stat tile. The form heuristic says a single headline figure is not a
 * chart - so this is a number with a label, never a one-value donut.
 */
export function StatTile({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: StatusKind;
}) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/40 px-4 py-3">
      <p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
      <p
        className="mt-1 text-[22px] font-semibold leading-tight"
        style={{ color: tone === "neutral" ? "#ffffff" : STATUS_COLOR[tone] }}
      >
        {value}
      </p>
      {hint && <p className="mt-0.5 text-[11px] text-slate-500">{hint}</p>}
    </div>
  );
}

/** A thin success-rate meter. Recessive track, 2px marks, no gradient. */
export function RateMeter({ rate }: { rate: number | null }) {
  if (rate === null) {
    return <p className="text-[12px] text-slate-500">No finished runs yet.</p>;
  }
  const tone: StatusKind =
    rate >= 95 ? "good" : rate >= 80 ? "warning" : "critical";
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="text-[11px] uppercase tracking-wide text-slate-500">
          Success rate
        </span>
        <span
          className="text-[13px] font-semibold"
          style={{ color: STATUS_COLOR[tone] }}
        >
          {statusGlyph(tone)} {rate}%
        </span>
      </div>
      <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
        <div
          className="h-full rounded-full"
          style={{
            width: `${Math.max(2, Math.min(100, rate))}%`,
            backgroundColor: STATUS_COLOR[tone],
          }}
        />
      </div>
    </div>
  );
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${Math.round(seconds * 10) / 10}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${rest}s`;
}

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString();
}

export function Panel({
  title,
  subtitle,
  action,
  children,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
      <header className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-[13.5px] font-semibold text-white">{title}</h3>
          {subtitle && (
            <p className="mt-0.5 text-[11.5px] text-slate-500">{subtitle}</p>
          )}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-6 text-center text-[12.5px] text-slate-500">
      {children}
    </p>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <p
      className="rounded-lg border px-3 py-2 text-[12.5px]"
      style={{
        borderColor: `${STATUS_COLOR.critical}66`,
        color: STATUS_COLOR.critical,
      }}
    >
      {statusGlyph("critical")} {children}
    </p>
  );
}
