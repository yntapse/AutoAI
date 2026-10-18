/**
 * AI Pipeline Builder API client.
 *
 * Mirrors backend/routers/plan.py. Uses the shared apiFetch wrapper so error
 * handling and the API base URL stay consistent with the rest of the app.
 *
 * Nothing here executes a pipeline. Compiling a plan writes a PipelineVersion,
 * which the existing deterministic runner in routers/pipelines.py executes.
 */

import { apiFetch } from "@/lib/api";

export type RecommendationKind =
  | "table"
  | "relationship"
  | "join"
  | "cardinality"
  | "transformation"
  | "derived_column"
  | "quality_rule"
  | "filter"
  | "aggregation";

export type Decision = "pending" | "accepted" | "rejected" | "edited";

export interface Recommendation {
  id: string;
  kind: RecommendationKind;
  ref_key: string;
  title: string;
  payload: Record<string, unknown>;
  reason: string;
  alternative_reason: string | null;
  confidence: number;
  expected_impact: string | null;
  risk_level: string;
  origin: string;
  decision: Decision;
  user_payload: Record<string, unknown> | null;
  sort_order: number;
}

export interface Plan {
  id: string;
  data_source_id: string;
  requirement_text: string;
  schema_name: string | null;
  status: string;
  intent: Record<string, unknown>;
  intent_origin: string;
  recommendation_count: number;
  accepted_count: number;
  pipeline_definition_id: string | null;
  created_at: string;
  recommendations: Partial<Record<RecommendationKind, Recommendation[]>>;
}

export interface PlanSummary {
  id: string;
  data_source_id: string;
  requirement_text: string;
  status: string;
  recommendation_count: number;
  accepted_count: number;
  created_at: string;
}

export interface CanvasNode {
  id: string;
  type: string;
  label: string;
  classification: string;
  row_count: number | null;
  column_count: number | null;
  decision: Decision;
  recommendation_id: string;
}

export interface CanvasEdge {
  id: string;
  source: string;
  target: string;
  join_type: string | null;
  cardinality: string | null;
  explosion_risk: string | null;
  requires_pre_aggregation: boolean | null;
  label: string;
  decision: Decision;
}

export interface Canvas {
  plan_id: string;
  anchor_table: string | null;
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  stages: {
    transformations: number;
    quality_rules: number;
    derived_columns: number;
    aggregations: number;
  };
}

export interface PlanPreview {
  plan_id: string;
  tables_selected: number;
  joins_selected: number;
  estimated_rows: number | null;
  estimated_columns: number;
  row_estimate_basis: string;
  join_impact: Array<{
    join: string;
    cardinality: string | null;
    explosion_risk: string | null;
    pre_aggregated: boolean;
  }>;
  duplicate_impact: { dedupe_steps: number };
  null_impact: { fill_steps: number; drop_steps: number };
  quality_impact: { rules: number; blocking_rules: number };
  warnings: string[];
}

export interface CompileResult {
  plan_id: string;
  pipeline_definition_id: string;
  pipeline_version_id: string;
  version_number: number;
  tables: string[];
  cleaning_steps: number;
  quality_rules: number;
  filters: number;
  aggregations: number;
  status: string;
  note: string;
}

export function createPlan(payload: {
  data_source_id: string;
  requirement: string;
  use_model?: boolean;
}): Promise<Plan> {
  return apiFetch<Plan>("/plan", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getPlan(planId: string): Promise<Plan> {
  return apiFetch<Plan>(`/plan/${planId}`);
}

export function listPlans(): Promise<PlanSummary[]> {
  return apiFetch<PlanSummary[]>("/plan");
}

export function decideRecommendation(
  planId: string,
  recommendationId: string,
  body: {
    decision: Exclude<Decision, "pending">;
    user_payload?: Record<string, unknown>;
    decided_by?: string;
  }
): Promise<Recommendation> {
  return apiFetch<Recommendation>(
    `/plan/${planId}/recommendations/${recommendationId}/decide`,
    { method: "POST", body: JSON.stringify(body) }
  );
}

export function getCanvas(planId: string): Promise<Canvas> {
  return apiFetch<Canvas>(`/plan/${planId}/canvas`);
}

export function getPreview(planId: string): Promise<PlanPreview> {
  return apiFetch<PlanPreview>(`/plan/${planId}/preview`);
}

export function compilePlan(
  planId: string,
  body: { pipeline_name: string; project_id: string; description?: string }
): Promise<CompileResult> {
  return apiFetch<CompileResult>(`/plan/${planId}/compile`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
