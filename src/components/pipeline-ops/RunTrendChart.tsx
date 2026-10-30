"use client";

/**
 * Run duration over time, one bar per run, coloured by outcome.
 *
 * Form: magnitude over an ordered sequence of discrete runs -> bars, not a
 * line (runs are events, not a continuous signal, and a line would imply
 * values between them). Oldest on the left.
 *
 * Colour is the reserved STATUS palette, and the legend plus per-bar glyph
 * mean outcome is never carried by hue alone. Grid is a single recessive
 * hairline at the mean; axes are implied by the baseline. Bars get 4px
 * rounded data-ends anchored to the baseline and a 2px gap between them.
 */

import { useState } from "react";
import {
  STATUS_COLOR,
  StatusKind,
  formatDateTime,
  formatDuration,
  statusGlyph,
  statusKind,
} from "./opsPrimitives";
import { TrendPoint } from "@/services/pipelineOpsService";

const HEIGHT = 120;
const MIN_BAR = 3;

export default function RunTrendChart({ points }: { points: TrendPoint[] }) {
  const [active, setActive] = useState<number | null>(null);

  const usable = points.filter((point) => point.duration_seconds !== null);
  if (usable.length === 0) {
    return (
      <p className="text-[12.5px] text-slate-500">
        No completed run has a recorded duration yet.
      </p>
    );
  }

  const max = Math.max(...usable.map((point) => point.duration_seconds ?? 0));
  const mean =
    usable.reduce((total, point) => total + (point.duration_seconds ?? 0), 0) /
    usable.length;
  const meanOffset = max > 0 ? HEIGHT - (mean / max) * HEIGHT : HEIGHT;

  const kinds: StatusKind[] = Array.from(
    new Set(points.map((point) => statusKind(point.status)))
  );

  return (
    <div>
      <div className="relative">
        {/* the mean hairline - the only gridline this chart earns */}
        <div
          className="pointer-events-none absolute inset-x-0 border-t border-dashed border-slate-700"
          style={{ top: `${meanOffset}px` }}
        >
          <span className="absolute -top-[14px] right-0 text-[10px] text-slate-500">
            mean {formatDuration(mean)}
          </span>
        </div>

        <div
          className="flex items-end gap-[2px]"
          style={{ height: `${HEIGHT}px` }}
          role="img"
          aria-label={`Duration of the last ${points.length} runs`}
        >
          {points.map((point, index) => {
            const kind = statusKind(point.status);
            const duration = point.duration_seconds;
            const height =
              duration === null || max === 0
                ? MIN_BAR
                : Math.max(MIN_BAR, (duration / max) * HEIGHT);
            return (
              <button
                key={point.run_id ?? index}
                type="button"
                onMouseEnter={() => setActive(index)}
                onMouseLeave={() => setActive(null)}
                onFocus={() => setActive(index)}
                onBlur={() => setActive(null)}
                className="group relative min-w-[6px] flex-1 cursor-default rounded-t-[4px] outline-none"
                style={{
                  height: `${height}px`,
                  backgroundColor: STATUS_COLOR[kind],
                  opacity: active === null || active === index ? 1 : 0.55,
                }}
                aria-label={`${point.status ?? "unknown"} run, ${formatDuration(
                  duration
                )}`}
              />
            );
          })}
        </div>
      </div>

      {/* tooltip, below the plot so it never covers the bars */}
      <div className="mt-2 min-h-[34px]">
        {active !== null && points[active] ? (
          <div className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-1.5 text-[11.5px] text-slate-300">
            <span style={{ color: STATUS_COLOR[statusKind(points[active].status)] }}>
              {statusGlyph(statusKind(points[active].status))}{" "}
              {points[active].status ?? "unknown"}
            </span>
            {" · "}
            {formatDuration(points[active].duration_seconds)}
            {points[active].row_count !== null && (
              <> {" · "}{points[active].row_count?.toLocaleString()} rows</>
            )}
            {" · "}
            {formatDateTime(points[active].started_at)}
          </div>
        ) : (
          <p className="text-[11px] text-slate-600">
            Oldest run on the left. Hover a bar for its outcome and duration.
          </p>
        )}
      </div>

      {/* legend - always present, because there is more than one outcome */}
      <ul className="mt-1 flex flex-wrap gap-3">
        {kinds.map((kind) => (
          <li key={kind} className="flex items-center gap-1.5 text-[11px] text-slate-400">
            <span
              className="h-2 w-2 rounded-full"
              style={{ backgroundColor: STATUS_COLOR[kind] }}
              aria-hidden
            />
            <span aria-hidden>{statusGlyph(kind)}</span>
            {kind === "good"
              ? "succeeded"
              : kind === "critical"
              ? "failed"
              : kind === "warning"
              ? "in flight"
              : "unknown"}
          </li>
        ))}
      </ul>
    </div>
  );
}
