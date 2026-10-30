/**
 * Visual ETL Pipeline Builder API client.
 *
 * Every call here is read-only on the customer database (one bounded SELECT).
 * Nothing in this file is called when a node is moved, connected or deleted.
 * Credentials: the temporary database session cookie is sent exactly as the
 * Catalog does it; no password is held by this module or put in a pipeline.
 */

import { apiFetch } from "@/lib/api";
import type { DataSourceSummary } from "@/services/catalogService";
import type { GraphPayload } from "@/components/etl-studio/pipelineModel";

const SESSION_REQUEST: RequestInit = {
  credentials: "include",
  headers: { "X-Requested-By": "catalog-ui" },
};

export interface StudioColumn {
  name: string;
  data_type: string;
  category: string;
}

export interface StudioIssue {
  message: string;
  kind: "incomplete" | "invalid" | "blocked" | "warning" | string;
  field: string | null;
}

export interface StudioNodeResult {
  node_id: string;
  node_type: string;
  /** not_configured | invalid | blocked | ready */
  status: string;
  columns: StudioColumn[] | null;
  issues: StudioIssue[];
  notes: string[];
  inputs: Record<string, string>;
  /** The registered data sources this step reads from. */
  source_ids?: string[];
  /** Why this step cannot be previewed on the connected database; null when it can. */
  preview_blocked_reason?: string | null;
}

export interface ValidateResult {
  db_type: string;
  order: string[];
  nodes: StudioNodeResult[];
  downstream: Record<string, string[]>;
}

export interface SqlResult {
  node_id: string;
  sql: string;
  parameters: Record<string, unknown>;
  execution_path: string;
}

export interface PreviewResult {
  node_id: string;
  columns: StudioColumn[];
  rows: unknown[][];
  row_limit: number;
  has_more: boolean;
  execution_path: string;
}

export interface JoinSummary {
  join_type: string;
  left_rows: number;
  right_rows: number;
  left_rows_with_match: number;
  right_rows_with_match: number;
  unmatched_left_rows: number;
  unmatched_right_rows: number;
  matched_pairs: number;
  output_rows: number;
  largest_left_key_group: number;
  largest_right_key_group: number;
  left_null_keys: number;
  right_null_keys: number;
  multiplies_left_rows: boolean;
  multiplies_right_rows: boolean;
  extra_rows_from_multiplication: number;
  main_side: "left" | "right";
  multiplies_main_side: boolean;
}

export interface DiagnosticsResult {
  node_id: string;
  summary: JoinSummary;
  key_pairs: { left: string; right: string }[];
  execution_path: string;
}

function connectionFields(source: DataSourceSummary) {
  return {
    db_type: source.db_type,
    host: source.host ?? "",
    port: source.port ?? undefined,
    database: source.database ?? "",
    username: source.username ?? undefined,
    trusted_connection: source.trusted_connection,
  };
}

function post<T>(path: string, body: unknown, withSession: boolean): Promise<T> {
  return apiFetch<T>(path, {
    ...(withSession ? SESSION_REQUEST : {}),
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Check the pipeline against the Catalog. Does not contact the customer database. */
export function validatePipeline(sourceId: string, graph: GraphPayload): Promise<ValidateResult> {
  return post<ValidateResult>("/catalog/etl-studio/validate", { source_id: sourceId, ...graph }, false);
}

/** The exact SELECT a preview of this node would run. Not executed. */
export function getNodeSql(sourceId: string, graph: GraphPayload, nodeId: string): Promise<SqlResult> {
  return post<SqlResult>("/catalog/etl-studio/sql", { source_id: sourceId, ...graph, node_id: nodeId }, false);
}

/** At most `limit` real rows (1-100) from one node. */
export function previewNode(
  source: DataSourceSummary,
  graph: GraphPayload,
  nodeId: string,
  limit = 25
): Promise<PreviewResult> {
  return post<PreviewResult>(
    "/catalog/etl-studio/preview",
    { ...connectionFields(source), source_id: source.id, ...graph, node_id: nodeId, limit },
    true
  );
}

/** Measure a join on the real data (aggregate counts only). */
export function joinDiagnostics(
  source: DataSourceSummary,
  graph: GraphPayload,
  nodeId: string
): Promise<DiagnosticsResult> {
  return post<DiagnosticsResult>(
    "/catalog/etl-studio/join-diagnostics",
    { ...connectionFields(source), source_id: source.id, ...graph, node_id: nodeId },
    true
  );
}
