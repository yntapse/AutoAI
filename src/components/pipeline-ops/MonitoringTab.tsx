"use client";

/** Monitoring tab: health, freshness against the SLA, and open alerts. */

import { useEffect, useState } from "react";
import {
  EmptyState,
  ErrorNote,
  Panel,
  RateMeter,
  STATUS_COLOR,
  StatTile,
  StatusKind,
  formatCount,
  formatDateTime,
  statusGlyph,
} from "./opsPrimitives";
import {
  Alert,
  PipelineHealth,
  getMonitoring,
  listAlerts,
  resolveAlert,
} from "@/services/pipelineOpsService";

function severityKind(severity: string): StatusKind {
  if (severity === "critical") return "critical";
  if (severity === "warning") return "warning";
  return "neutral";
}

export default function MonitoringTab({ pipelineId }: { pipelineId: string }) {
  const [health, setHealth] = useState<PipelineHealth | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showResolved, setShowResolved] = useState(false);

  async function load(resolved: boolean) {
    const [overview, alertRows] = await Promise.all([
      getMonitoring(pipelineId),
      listAlerts(pipelineId, resolved ? undefined : "open"),
    ]);
    setHealth(overview.pipelines[0] ?? null);
    setAlerts(alertRows);
  }

  useEffect(() => {
    let cancelled = false;
    load(showResolved)
      .catch((err: unknown) =>
        !cancelled &&
        setError(
          err instanceof Error ? err.message : "Failed to load monitoring."
        )
      )
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [pipelineId, showResolved]);

  async function resolve(alertId: string) {
    setBusyId(alertId);
    setError(null);
    try {
      await resolveAlert(alertId);
      await load(showResolved);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not resolve.");
    } finally {
      setBusyId(null);
    }
  }

  if (loading)
    return <p className="text-[12.5px] text-slate-500">Loading monitoring&hellip;</p>;

  const freshnessTone: StatusKind = health?.stale
    ? "critical"
    : health?.never_succeeded
    ? "warning"
    : "good";

  return (
    <div className="space-y-4">
      {error && <ErrorNote>{error}</ErrorNote>}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="Open alerts"
          value={formatCount(health?.open_alerts ?? 0)}
          tone={health?.open_alerts ? "critical" : "good"}
        />
        <StatTile
          label="Hours since success"
          value={
            health?.hours_since_last_success === null ||
            health?.hours_since_last_success === undefined
              ? "never"
              : `${health.hours_since_last_success}h`
          }
          tone={freshnessTone}
          hint={
            health?.freshness_sla_hours
              ? `SLA ${health.freshness_sla_hours}h`
              : "No SLA declared"
          }
        />
        <StatTile
          label="Failed runs"
          value={formatCount(health?.metrics.failed ?? 0)}
          tone={health?.metrics.failed ? "critical" : "neutral"}
        />
        <StatTile
          label="Rows last run"
          value={formatCount(health?.metrics.last_row_count ?? null)}
        />
      </div>

      <Panel
        title="Freshness"
        subtitle="Staleness is judged only against a declared SLA."
      >
        {health?.never_succeeded ? (
          <p className="text-[12.5px]" style={{ color: STATUS_COLOR.warning }}>
            {statusGlyph("warning")} This pipeline has never completed
            successfully, so there is no freshness to measure yet.
          </p>
        ) : health?.freshness_sla_hours ? (
          <p
            className="text-[12.5px]"
            style={{
              color: health.stale ? STATUS_COLOR.critical : STATUS_COLOR.good,
            }}
          >
            {statusGlyph(health.stale ? "critical" : "good")}{" "}
            {health.stale
              ? `Stale: last success was ${health.hours_since_last_success}h ago, past the ${health.freshness_sla_hours}h SLA.`
              : `Fresh: last success ${health.hours_since_last_success}h ago, within the ${health.freshness_sla_hours}h SLA.`}
          </p>
        ) : (
          <p className="text-[12.5px] text-slate-400">
            No schedule on this pipeline declares a freshness SLA, so staleness
            is not evaluated. Add one on the Schedule tab.
          </p>
        )}
        <div className="mt-4 max-w-xs">
          <RateMeter rate={health?.metrics.success_rate ?? null} />
        </div>
      </Panel>

      <Panel
        title="Alerts"
        subtitle="Repeated detections of the same condition collapse into one open alert."
        action={
          <label className="flex items-center gap-2 text-[11.5px] text-slate-400">
            <input
              type="checkbox"
              checked={showResolved}
              onChange={(event) => setShowResolved(event.target.checked)}
              className="accent-cyan-600"
            />
            Include resolved
          </label>
        }
      >
        {alerts.length === 0 ? (
          <EmptyState>
            {showResolved
              ? "No alerts have been recorded for this pipeline."
              : "No open alerts. Nothing needs attention."}
          </EmptyState>
        ) : (
          <ul className="space-y-2">
            {alerts.map((alert) => {
              const kind = severityKind(alert.severity);
              return (
                <li
                  key={alert.id}
                  className="rounded-lg border bg-slate-950 px-3 py-2.5"
                  style={{ borderColor: `${STATUS_COLOR[kind]}44` }}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p
                        className="text-[13px] font-medium"
                        style={{ color: STATUS_COLOR[kind] }}
                      >
                        <span aria-hidden>{statusGlyph(kind)}</span>{" "}
                        {alert.title}
                        <span className="ml-2 text-[11px] uppercase tracking-wide text-slate-500">
                          {alert.severity} &middot;{" "}
                          {alert.alert_type.replace(/_/g, " ")}
                        </span>
                      </p>
                      {alert.message && (
                        <p className="mt-1 text-[12px] text-slate-400">
                          {alert.message}
                        </p>
                      )}
                      <p className="mt-1 text-[11px] text-slate-600">
                        Detected {formatDateTime(alert.created_at)}
                        {alert.resolved_at &&
                          ` · resolved ${formatDateTime(alert.resolved_at)}`}
                      </p>
                    </div>
                    {alert.status === "open" && (
                      <button
                        type="button"
                        disabled={busyId === alert.id}
                        onClick={() => resolve(alert.id)}
                        className="shrink-0 rounded-md border border-slate-800 px-2 py-1 text-[11.5px] text-slate-300 hover:bg-slate-900 disabled:opacity-50"
                      >
                        Resolve
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Panel>
    </div>
  );
}
