/**
 * Metadata Catalog API client - Sprint 1 of the Data Intelligence OS.
 *
 * Uses the shared apiFetch wrapper in src/lib/api.ts so error handling and the
 * API base URL stay consistent with the rest of the app.
 *
 * Credentials are never stored by the backend: a scan sends them in the request
 * body for that call only.
 */

import { ApiError, apiFetch } from "@/lib/api";

export type DbType = "postgresql" | "mssql";

export interface DataSourceSummary {
  id: string;
  source_name: string;
  db_type: DbType;
  host: string | null;
  port: number | null;
  database: string | null;
  username: string | null;
  schema_name: string | null;
  trusted_connection: boolean;
  description: string | null;
  status: string;
  last_scanned_at: string | null;
  created_at: string;
}

export interface RegisterSourcePayload {
  source_name: string;
  db_type: DbType;
  host?: string;
  port?: number;
  database?: string;
  username?: string;
  schema_name?: string;
  trusted_connection?: boolean;
  odbc_driver?: string;
  description?: string;
}

export interface ScanCredentials {
  db_type: DbType;
  host: string;
  port?: number;
  database: string;
  username?: string;
  password?: string;
  trusted_connection?: boolean;
  odbc_driver?: string;
  schema_name?: string;
  include_row_counts?: boolean;
  include_sample_values?: boolean;
  max_tables?: number;
}

export interface ScanResult {
  scan_id: string;
  data_source_id: string;
  schema_name: string;
  status: string;
  table_count: number;
  column_count: number;
  relationship_count: number;
  duration_ms: number | null;
  created_at: string;
}

export interface CatalogTableSummary {
  id: string;
  table_name: string;
  schema_name: string;
  column_count: number;
  row_count: number | null;
  /** True when the count came from statistics, not an exact COUNT(*). */
  row_count_is_estimate?: boolean;
  has_primary_key: boolean;
  business_description?: string | null;
}

export interface CatalogColumnDetail {
  column_name: string;
  data_type: string;
  ordinal_position: number;
  is_nullable: boolean;
  is_primary_key: boolean;
  is_foreign_key: boolean;
  normalized_name: string | null;
  sample_values: unknown[] | null;
  business_description?: string | null;
}

export interface CatalogTableDetail extends CatalogTableSummary {
  columns: CatalogColumnDetail[];
}

export interface CatalogOverview {
  data_source_id: string;
  source_name: string;
  scan_id: string | null;
  scanned_at: string | null;
  schema_name: string | null;
  table_count: number;
  column_count: number;
  relationship_count: number;
  tables: CatalogTableSummary[];
}

export interface CatalogRelationship {
  id: string;
  left_table: string;
  left_column: string;
  right_table: string;
  right_column: string;
  constraint_name: string | null;
  origin: string;
  confidence: number;
  approved: boolean | null;
}

export interface CatalogSearchHit {
  match_type: "table" | "column";
  table_id: string;
  table_name: string;
  schema_name: string;
  column_name: string | null;
  data_type: string | null;
}

export function listSources(): Promise<DataSourceSummary[]> {
  return apiFetch<DataSourceSummary[]>("/catalog/sources");
}

export function registerSource(
  payload: RegisterSourcePayload
): Promise<DataSourceSummary> {
  return apiFetch<DataSourceSummary>("/catalog/sources", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteSource(sourceId: string): Promise<unknown> {
  return apiFetch(`/catalog/sources/${sourceId}`, { method: "DELETE" });
}

export function testSourceConnection(
  sourceId: string,
  credentials: ScanCredentials
): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>(`/catalog/sources/${sourceId}/test`, {
    method: "POST",
    body: JSON.stringify(credentials),
  });
}

/**
 * Session calls carry the HttpOnly session cookie (credentials: "include") and a
 * custom header that a page on another site cannot add (CSRF protection).
 */
const SESSION_REQUEST: RequestInit = {
  credentials: "include",
  headers: { "X-Requested-By": "catalog-ui" },
};

/**
 * Scan using the temporary database session (see connectSource). Do not pass a
 * password: the server uses the one held for the session and answers 401 if
 * the session has expired.
 */
export function scanSource(
  sourceId: string,
  credentials: ScanCredentials
): Promise<ScanResult> {
  return apiFetch<ScanResult>(`/catalog/sources/${sourceId}/scan`, {
    ...SESSION_REQUEST,
    method: "POST",
    body: JSON.stringify(credentials),
  });
}

export interface SessionStatus {
  source_id: string;
  connected: boolean;
  expires_in_seconds: number | null;
  idle_timeout_seconds: number;
  max_lifetime_seconds: number;
}

/**
 * Open a temporary database session. The password is sent once, in the request
 * body, and is kept only in the server's memory for a limited time. It is never
 * returned, stored in the browser, or put in a URL.
 */
export function connectSource(
  sourceId: string,
  password: string,
  odbcDriver?: string
): Promise<SessionStatus> {
  return apiFetch<SessionStatus>(`/catalog/sources/${sourceId}/session`, {
    ...SESSION_REQUEST,
    method: "POST",
    body: JSON.stringify({ password, odbc_driver: odbcDriver || undefined }),
  });
}

/** Is there a live session? Checking never extends it. */
export function getSessionStatus(sourceId: string): Promise<SessionStatus> {
  return apiFetch<SessionStatus>(`/catalog/sources/${sourceId}/session`, {
    credentials: "include",
  });
}

/** End the session now; the server drops the stored password immediately. */
export function disconnectSource(sourceId: string): Promise<SessionStatus> {
  return apiFetch<SessionStatus>(`/catalog/sources/${sourceId}/session`, {
    ...SESSION_REQUEST,
    method: "DELETE",
  });
}

/** True when the server says the database session is missing or has expired. */
export function isSessionExpiredError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

export function getOverview(sourceId: string): Promise<CatalogOverview> {
  return apiFetch<CatalogOverview>(`/catalog/sources/${sourceId}/overview`);
}

export function getTableDetail(tableId: string): Promise<CatalogTableDetail> {
  return apiFetch<CatalogTableDetail>(`/catalog/tables/${tableId}`);
}

export function getRelationships(
  sourceId: string
): Promise<CatalogRelationship[]> {
  return apiFetch<CatalogRelationship[]>(
    `/catalog/sources/${sourceId}/relationships`
  );
}

export function searchCatalog(
  query: string,
  sourceId?: string
): Promise<CatalogSearchHit[]> {
  const params = new URLSearchParams({ q: query });
  if (sourceId) params.set("source_id", sourceId);
  return apiFetch<CatalogSearchHit[]>(`/catalog/search?${params.toString()}`);
}

/** One page of real rows from a catalogued table. */
export interface TablePreviewPage {
  table_name: string;
  schema_name: string;
  column_names: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  page_size: number;
  has_more: boolean;
  /** Send this back as `cursor` to get the next page; null on the last page. */
  next_cursor: string | null;
  /** "keyset" = ordered by primary key; "offset" = position based. */
  pagination_mode: "keyset" | "offset";
  /** False when the row order cannot be fully guaranteed between requests. */
  stable_order: boolean;
  start_row: number | null;
  message: string | null;
}

/**
 * Fetch one page of table rows using the temporary database session. The
 * connection details must match the registered source; no password is sent.
 * The schema and table are taken from the catalog on the server.
 */
export function previewTable(
  tableId: string,
  source: DataSourceSummary,
  options: { limit?: number; cursor?: string | null } = {}
): Promise<TablePreviewPage> {
  return apiFetch<TablePreviewPage>(`/catalog/tables/${tableId}/preview`, {
    ...SESSION_REQUEST,
    method: "POST",
    body: JSON.stringify({
      db_type: source.db_type,
      host: source.host ?? "",
      port: source.port ?? undefined,
      database: source.database ?? "",
      username: source.username ?? undefined,
      trusted_connection: source.trusted_connection,
      limit: options.limit ?? 20,
      cursor: options.cursor ?? undefined,
    }),
  });
}
