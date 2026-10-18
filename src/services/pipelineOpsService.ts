/**
 * Pipeline operations API client: dashboard, run metrics, schedules,
 * monitoring and the AutoML handoff.
 *
 * Version history reuses the EXISTING /pipeline endpoints rather than adding
 * new ones - routers/pipelines.py already serves versions and version compare.
 */

import { apiFetch } from "@/lib/api";

// ---------------------------------------------------------------- run metrics

export interface RunMetrics {
  total_runs: number;
  succeeded: number;
  failed: number;
  running: number;
  success_rate: number | null;
  avg_duration_seconds: number | null;
  p95_duration_seconds: number | null;
  max_duration_seconds: number | null;
  total_rows_processed: number;
  last_row_count: number | null;
  last_status: string | null;
  last_run_at: string | null;
  last_run: PipelineRun | null;
}

export interface PipelineRun {
  id: string;
  pipeline_version_id: string | null;
  project_id: string | null;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  row_count: number | null;
  execution_time: number | null;
  dataset_file_id: string | null;
}

export interface TrendPoint {
  run_id: string | null;
  status: string | null;
  started_at: string | null;
  duration_seconds: number | null;
  row_count: number | null;
  succeeded: boolean;
}

export interface PipelineRow {
  pipeline_id: string;
  pipeline_name: string;
  project_id: string;
  description: string | null;
  status: string;
  created_at: string | null;
  updated_at: string | null;
  version_count: number;
  latest_version: number | null;
  schedule_count: number;
  active_schedules: number;
  next_run_at: string | null;
  open_alerts: number;
  metrics: RunMetrics;
}

export interface PortfolioTotals {
  pipelines: number;
  total_runs: number;
  succeeded: number;
  failed: number;
  running: number;
  total_rows_processed: number;
  pipelines_never_run: number;
  pipelines_failing: number;
  success_rate: number | null;
  avg_duration_seconds: number | null;
}

export interface DashboardResponse {
  totals: PortfolioTotals;
  pipelines: PipelineRow[];
  open_alerts: Alert[];
  generated_at: string;
}

// ---------------------------------------------------------------- schedules

export type Frequency = "once" | "hourly" | "daily" | "weekly" | "monthly" | "cron";

export interface Schedule {
  id: string;
  pipeline_definition_id: string;
  pipeline_version_id: string;
  data_source_id: string;
  frequency: Frequency;
  cron_expression: string | null;
  run_at: string | null;
  status: "active" | "paused" | "disabled";
  is_active: boolean;
  description: string;
  credential_env: string | null;
  target_column: string | null;
  freshness_sla_hours: number | null;
  next_run_at: string | null;
  last_run_at: string | null;
  last_run_status: string | null;
  last_error: string | null;
  run_count: number;
  created_by: string | null;
  created_at: string | null;
}

export interface ScheduleCreatePayload {
  pipeline_definition_id: string;
  pipeline_version_id?: string;
  data_source_id: string;
  frequency: Frequency;
  cron_expression?: string;
  run_at?: string;
  credential_env?: string;
  target_column?: string;
  freshness_sla_hours?: number;
}

// ---------------------------------------------------------------- monitoring

export interface Alert {
  id: string;
  pipeline_definition_id: string | null;
  data_source_id: string | null;
  pipeline_run_id: string | null;
  alert_type: string;
  severity: "info" | "warning" | "critical";
  title: string;
  message: string | null;
  detail: Record<string, unknown> | null;
  status: string;
  fingerprint: string;
  created_at: string | null;
  resolved_at: string | null;
}

export interface PipelineHealth {
  pipeline_id: string;
  pipeline_name: string;
  metrics: RunMetrics;
  freshness_sla_hours: number | null;
  hours_since_last_success: number | null;
  stale: boolean;
  never_succeeded: boolean;
  open_alerts: number;
}

export interface MonitoringOverview {
  generated_at: string;
  pipelines: PipelineHealth[];
  alert_types: string[];
  open_alerts: Alert[];
}

// ---------------------------------------------------------------- automl

export interface TargetCandidate {
  column: string;
  task_type: "classification" | "regression";
  distinct_values: number;
  null_percentage: number;
  score: number;
}

export interface AutoMlReadiness {
  pipeline_id: string;
  pipeline_name: string;
  dataset_ready: boolean;
  reason: string | null;
  run_id: string | null;
  dataset_file_id: string | null;
  project_id?: string | null;
  rows: number | null;
  columns: number | null;
  cv_folds?: number;
  quality_score?: number;
  quality_basis?: string;
  target_candidates: TargetCandidate[];
  warnings: string[];
}

// ------------------------------------------------- existing /pipeline shapes

export interface PipelineVersionRow {
  id: string;
  version_number: number;
  pipeline_json: Record<string, unknown>;
  created_at: string;
}

export interface VersionComparison {
  pipeline_id: string;
  from_version: number;
  to_version: number;
  changes: Record<string, { before: unknown; after: unknown }>;
}

// ---------------------------------------------------------------- calls

export function getDashboard(projectId?: string): Promise<DashboardResponse> {
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  return apiFetch<DashboardResponse>(`/orchestration/dashboard${query}`);
}

export function listPipelines(projectId?: string): Promise<PipelineRow[]> {
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  return apiFetch<PipelineRow[]>(`/orchestration/pipelines${query}`);
}

export function getPipelineMetrics(
  pipelineId: string
): Promise<{ pipeline_id: string; pipeline_name: string; metrics: RunMetrics; trend: TrendPoint[] }> {
  return apiFetch(`/orchestration/pipelines/${pipelineId}/metrics`);
}

export function listRuns(pipelineId: string, limit = 100): Promise<PipelineRun[]> {
  return apiFetch<PipelineRun[]>(
    `/orchestration/pipelines/${pipelineId}/runs?limit=${limit}`
  );
}

export function listSchedules(pipelineId?: string): Promise<Schedule[]> {
  const query = pipelineId
    ? `?pipeline_id=${encodeURIComponent(pipelineId)}`
    : "";
  return apiFetch<Schedule[]>(`/orchestration/schedules${query}`);
}

export function createSchedule(payload: ScheduleCreatePayload): Promise<Schedule> {
  return apiFetch<Schedule>("/orchestration/schedules", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function pauseSchedule(scheduleId: string): Promise<Schedule> {
  return apiFetch<Schedule>(`/orchestration/schedules/${scheduleId}/pause`, {
    method: "POST",
  });
}

export function resumeSchedule(scheduleId: string): Promise<Schedule> {
  return apiFetch<Schedule>(`/orchestration/schedules/${scheduleId}/resume`, {
    method: "POST",
  });
}

export function disableSchedule(scheduleId: string): Promise<Schedule> {
  return apiFetch<Schedule>(`/orchestration/schedules/${scheduleId}/disable`, {
    method: "POST",
  });
}

export async function deleteSchedule(scheduleId: string): Promise<void> {
  await apiFetch(`/orchestration/schedules/${scheduleId}`, { method: "DELETE" });
}

export function getMonitoring(pipelineId?: string): Promise<MonitoringOverview> {
  const query = pipelineId
    ? `?pipeline_id=${encodeURIComponent(pipelineId)}`
    : "";
  return apiFetch<MonitoringOverview>(
    `/orchestration/monitoring/overview${query}`
  );
}

export function listAlerts(
  pipelineId?: string,
  status?: string
): Promise<Alert[]> {
  const params = new URLSearchParams();
  if (pipelineId) params.set("pipeline_id", pipelineId);
  if (status) params.set("status", status);
  const query = params.toString();
  return apiFetch<Alert[]>(`/orchestration/alerts${query ? `?${query}` : ""}`);
}

export function resolveAlert(alertId: string): Promise<Alert> {
  return apiFetch<Alert>(`/orchestration/alerts/${alertId}/resolve`, {
    method: "POST",
  });
}

export function getAutoMlReadiness(pipelineId: string): Promise<AutoMlReadiness> {
  return apiFetch<AutoMlReadiness>(
    `/orchestration/pipelines/${pipelineId}/automl-readiness`
  );
}

// --- existing /pipeline endpoints, reused rather than duplicated ---

export function listVersions(pipelineId: string): Promise<PipelineVersionRow[]> {
  return apiFetch<PipelineVersionRow[]>(`/pipeline/version/${pipelineId}`);
}

export function compareVersions(
  pipelineId: string,
  fromVersion: number,
  toVersion: number
): Promise<VersionComparison> {
  return apiFetch<VersionComparison>(
    `/pipeline/version/${pipelineId}/compare/${fromVersion}/${toVersion}`
  );
}

export function restoreVersion(
  pipelineId: string,
  versionId: string
): Promise<unknown> {
  return apiFetch(`/pipeline/${pipelineId}/restore/${versionId}`, {
    method: "POST",
  });
}


export function renamePipeline(
  pipelineId: string,
  body: { pipeline_name?: string; description?: string }
) {
  return apiFetch(`/pipeline/${pipelineId}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deletePipeline(
  pipelineId: string
): Promise<void> {
  await apiFetch(`/pipeline/${pipelineId}`, {
    method: "DELETE",
  });
}

export function clonePipeline(pipelineId: string) {
  return apiFetch(`/pipeline/${pipelineId}/clone`, {
    method: "POST",
  });
}