"use client";

/**
 * Schedule tab: create, pause, resume, disable and delete schedules.
 *
 * A schedule pins a saved version, so a draft can never be scheduled. No
 * password is ever sent - the form asks for the NAME of an environment
 * variable the worker resolves when it fires.
 */

import { useEffect, useState } from "react";
import {
  EmptyState,
  ErrorNote,
  Panel,
  StatusBadge,
  formatDateTime,
} from "./opsPrimitives";
import {
  Frequency,
  PipelineVersionRow,
  Schedule,
  createSchedule,
  deleteSchedule,
  disableSchedule,
  listSchedules,
  listVersions,
  pauseSchedule,
  resumeSchedule,
} from "@/services/pipelineOpsService";
import { DataSourceSummary, listSources } from "@/services/catalogService";

const FREQUENCIES: Array<{ value: Frequency; label: string }> = [
  { value: "once", label: "Run once" },
  { value: "hourly", label: "Hourly" },
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "cron", label: "Custom cron" },
];

export default function ScheduleTab({ pipelineId }: { pipelineId: string }) {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [sources, setSources] = useState<DataSourceSummary[]>([]);
  const [versions, setVersions] = useState<PipelineVersionRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [frequency, setFrequency] = useState<Frequency>("daily");
  const [cron, setCron] = useState("0 2 * * *");
  const [runAt, setRunAt] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [credentialEnv, setCredentialEnv] = useState("");
  const [slaHours, setSlaHours] = useState("");
  const [saving, setSaving] = useState(false);

  async function refresh() {
    setSchedules(await listSchedules(pipelineId));
  }

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      listSchedules(pipelineId),
      listSources(),
      listVersions(pipelineId),
    ])
      .then(([scheduleRows, sourceRows, versionRows]) => {
        if (cancelled) return;
        setSchedules(scheduleRows);
        setSources(sourceRows);
        const sorted = [...versionRows].sort(
          (a, b) => b.version_number - a.version_number
        );
        setVersions(sorted);
        setVersionId((current) => current || sorted[0]?.id || "");
        setSourceId((current) => current || sourceRows[0]?.id || "");
      })
      .catch((err: unknown) =>
        !cancelled &&
        setError(
          err instanceof Error ? err.message : "Failed to load schedules."
        )
      )
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [pipelineId]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSaving(true);
    try {
      await createSchedule({
        pipeline_definition_id: pipelineId,
        pipeline_version_id: versionId || undefined,
        data_source_id: sourceId,
        frequency,
        cron_expression: frequency === "cron" ? cron.trim() : undefined,
        run_at: frequency === "once" && runAt ? new Date(runAt).toISOString() : undefined,
        credential_env: credentialEnv.trim() || undefined,
        freshness_sla_hours: slaHours ? Number(slaHours) : undefined,
      });
      await refresh();
      setCredentialEnv("");
      setSlaHours("");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not create the schedule."
      );
    } finally {
      setSaving(false);
    }
  }

  async function act(
    scheduleId: string,
    action: (id: string) => Promise<unknown>
  ) {
    setBusyId(scheduleId);
    setError(null);
    try {
      await action(scheduleId);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed.");
    } finally {
      setBusyId(null);
    }
  }

  if (loading)
    return <p className="text-[12.5px] text-slate-500">Loading schedules&hellip;</p>;

  const canSubmit =
    !saving &&
    Boolean(sourceId) &&
    versions.length > 0 &&
    (frequency !== "once" || Boolean(runAt)) &&
    (frequency !== "cron" || cron.trim().length > 0);

  return (
    <div className="space-y-4">
      {error && <ErrorNote>{error}</ErrorNote>}

      <Panel
        title="Schedules"
        subtitle="Each schedule runs one saved version, exactly as saved."
      >
        {schedules.length === 0 ? (
          <EmptyState>No schedule yet. Create one below.</EmptyState>
        ) : (
          <ul className="space-y-2">
            {schedules.map((schedule) => (
              <li
                key={schedule.id}
                className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2.5"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-[13px] font-medium text-slate-100">
                        {schedule.description}
                      </p>
                      <StatusBadge
                        status={schedule.is_active ? "active" : schedule.status}
                      />
                    </div>
                    <p className="mt-1 text-[11.5px] text-slate-500">
                      Next run {formatDateTime(schedule.next_run_at)} &middot;{" "}
                      {schedule.run_count} run(s) so far
                      {schedule.cron_expression &&
                        ` · cron ${schedule.cron_expression}`}
                    </p>
                    <p className="mt-0.5 text-[11px] text-slate-600">
                      Credentials from{" "}
                      {schedule.credential_env ?? "no environment variable set"}
                      {schedule.freshness_sla_hours
                        ? ` · freshness SLA ${schedule.freshness_sla_hours}h`
                        : ""}
                    </p>
                    {schedule.last_error && (
                      <p className="mt-1 text-[11px] text-rose-300">
                        Last error: {schedule.last_error}
                      </p>
                    )}
                  </div>

                  <div className="flex shrink-0 flex-wrap gap-1.5">
                    {schedule.status === "active" ? (
                      <button
                        type="button"
                        disabled={busyId === schedule.id}
                        onClick={() => act(schedule.id, pauseSchedule)}
                        className="rounded-md border border-slate-800 px-2 py-1 text-[11.5px] text-slate-300 hover:bg-slate-900 disabled:opacity-50"
                      >
                        Pause
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={busyId === schedule.id}
                        onClick={() => act(schedule.id, resumeSchedule)}
                        className="rounded-md border border-emerald-900/60 px-2 py-1 text-[11.5px] text-emerald-300 hover:bg-emerald-950/40 disabled:opacity-50"
                      >
                        Resume
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={busyId === schedule.id}
                      onClick={() => act(schedule.id, disableSchedule)}
                      className="rounded-md border border-slate-800 px-2 py-1 text-[11.5px] text-slate-400 hover:bg-slate-900 disabled:opacity-50"
                    >
                      Disable
                    </button>
                    <button
                      type="button"
                      disabled={busyId === schedule.id}
                      onClick={() => act(schedule.id, deleteSchedule)}
                      className="rounded-md border border-slate-800 px-2 py-1 text-[11.5px] text-slate-500 hover:bg-slate-900 disabled:opacity-50"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel
        title="New schedule"
        subtitle="Nothing is executed here. A schedule records when to run a saved version."
      >
        {versions.length === 0 ? (
          <EmptyState>
            Save a pipeline version first &mdash; a draft cannot be scheduled.
          </EmptyState>
        ) : (
          <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
            <label className="text-[12px] text-slate-400">
              Frequency
              <select
                value={frequency}
                onChange={(event) =>
                  setFrequency(event.target.value as Frequency)
                }
                className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-[13px] text-slate-200"
              >
                {FREQUENCIES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="text-[12px] text-slate-400">
              Version to run
              <select
                value={versionId}
                onChange={(event) => setVersionId(event.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-[13px] text-slate-200"
              >
                {versions.map((version) => (
                  <option key={version.id} value={version.id}>
                    v{version.version_number}
                  </option>
                ))}
              </select>
            </label>

            {frequency === "cron" && (
              <label className="text-[12px] text-slate-400 sm:col-span-2">
                Cron expression
                <input
                  value={cron}
                  onChange={(event) => setCron(event.target.value)}
                  placeholder="30 6 * * 1-5"
                  className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 font-mono text-[13px] text-slate-200"
                />
                <span className="mt-1 block text-[11px] text-slate-600">
                  minute hour day-of-month month day-of-week. Day-of-week is
                  standard cron: 0 is Sunday, so 1-5 is Monday to Friday.
                </span>
              </label>
            )}

            {frequency === "once" && (
              <label className="text-[12px] text-slate-400 sm:col-span-2">
                Run at
                <input
                  type="datetime-local"
                  value={runAt}
                  onChange={(event) => setRunAt(event.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-[13px] text-slate-200"
                />
              </label>
            )}

            <label className="text-[12px] text-slate-400">
              Data source
              <select
                value={sourceId}
                onChange={(event) => setSourceId(event.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-[13px] text-slate-200"
              >
                {sources.length === 0 && <option value="">No source</option>}
                {sources.map((source) => (
                  <option key={source.id} value={source.id}>
                    {source.source_name}
                  </option>
                ))}
              </select>
            </label>

            <label className="text-[12px] text-slate-400">
              Freshness SLA (hours)
              <input
                type="number"
                min={1}
                max={8760}
                value={slaHours}
                onChange={(event) => setSlaHours(event.target.value)}
                placeholder="24"
                className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-[13px] text-slate-200"
              />
            </label>

            <label className="text-[12px] text-slate-400 sm:col-span-2">
              Credential environment variable
              <input
                value={credentialEnv}
                onChange={(event) => setCredentialEnv(event.target.value)}
                placeholder="MERIDIAN_DW_PASSWORD"
                className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 font-mono text-[13px] text-slate-200"
              />
              <span className="mt-1 block text-[11px] text-slate-600">
                The variable NAME, not the password. Passwords are never stored.
              </span>
            </label>

            <div className="sm:col-span-2">
              <button
                type="submit"
                disabled={!canSubmit}
                className="rounded-lg bg-cyan-600 px-4 py-2 text-[13px] font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-800 disabled:text-slate-500"
              >
                {saving ? "Creating…" : "Create schedule"}
              </button>
            </div>
          </form>
        )}
      </Panel>
    </div>
  );
}
