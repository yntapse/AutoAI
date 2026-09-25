const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

export async function createProject(
  projectName: string,
  targetColumn: string,
  file: File
): Promise<unknown> {
  const formData = new FormData();
  formData.append("project_name", projectName);
  formData.append("target_column", targetColumn);
  formData.append("file", file);

  const response = await fetch(`${API_BASE}/projects/create`, {
    method: "POST",
    body: formData,
  });

  const contentType = response.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const message =
      isJson && payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail?: unknown }).detail)
        : typeof payload === "string"
        ? payload
        : "Failed to create project.";

    throw new Error(message);
  }

  return payload;
}


// --- Database-sourced project creation -------------------------------------
//
// Calls the existing /datasource/extract endpoint, which already:
//   - runs the SELECT query against the connected database
//   - writes the result out as a CSV
//   - creates the project record
//   - returns a file_id / target_column compatible with the existing
//     training flow (same shape used by createProject() above)
//
// No new backend endpoint is introduced here.
 
export interface DatabaseExtractParams {
  dbType: string;
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
  /** A single validated SELECT query, e.g. `SELECT * FROM [Customers]` */
  query: string;
  projectName: string;
  targetColumn: string;
}
 
export interface DatabaseExtractResponse {
  project_id: string;
  project_name: string;
  file_id: string;
  target_column: string;
  rows: number;
  columns: number;
}
 
export async function createProjectFromDatabase(
  params: DatabaseExtractParams
): Promise<DatabaseExtractResponse> {
  const response = await fetch(`${API_BASE}/datasource/extract`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      db_type: params.dbType,
      host: params.host,
      port: params.port,
      database: params.database,
      username: params.username,
      password: params.password,
      query: params.query,
      project_name: params.projectName,
      target_column: params.targetColumn,
    }),
  });
 
  const contentType = response.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  const payload = isJson ? await response.json() : await response.text();
 
  if (!response.ok) {
    const message =
      isJson && payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail?: unknown }).detail)
        : typeof payload === "string"
        ? payload
        : "Failed to create project from database.";
 
    throw new Error(message);
  }
 
  return payload as DatabaseExtractResponse;
}
 


// --- Multi-table dataset builder (Sprint 1) --------------------------------
//
// Calls the /datamodel/* endpoints added in the Phase 3 backend router.
// These are stateless: the connection details travel with every request,
// exactly like the existing /datasource/* calls above.
//
// The preview response deliberately carries the same first six fields as
// POST /datasource/preview, so the New Project page can feed the existing
// Step 3 target-column picker from it without any change to Step 3.

export interface DataModelConnectionParams {
  dbType: string;
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
  /** Schema for every selected table. Backend defaults to dbo / public. */
  schemaName?: string | null;
}

export interface DataModelRelationship {
  left_table: string;
  left_column: string;
  right_table: string;
  right_column: string;
  join_type: "inner" | "left";
}

export interface DataModelColumnMeta {
  name: string;
  type: string;
  nullable: boolean;
  primary_key: boolean;
}

export interface DataModelColumnsResponse {
  schema: string;
  tables: { table: string; columns: DataModelColumnMeta[] }[];
}

/** The join specification the user has built. Sent with preview and extract. */
export interface DataModelSpec {
  tables: string[];
  baseTable: string;
  relationships: DataModelRelationship[];
  /** Omit or pass null to select every column of every table. */
  selectedColumns?: Record<string, string[]> | null;
}

export interface DataModelPreviewResponse {
  // --- identical shape to POST /datasource/preview ---
  preview_rows: number;
  has_more: boolean;
  columns: number;
  column_names: string[];
  numeric_columns: string[];
  sample_rows: Record<string, unknown>[];
  // --- additive, read only by the multi-table panel ---
  generated_sql: string;
  estimated_row_count: number | null;
  row_limit: number;
  exceeds_limit: boolean;
  alias_map: Record<string, string>;
  table_aliases: Record<string, string>;
  base_table: string;
  schema: string;
  warnings: string[];
}

/**
 * Shared response unwrapping for the three /datamodel calls below.
 * Same behaviour as the inline blocks in createProject and
 * createProjectFromDatabase: prefer the backend's "detail" string so the
 * user sees a readable message rather than a status code.
 */
async function unwrapDataModelResponse<T>(
  response: Response,
  fallbackMessage: string,
): Promise<T> {
  const contentType = response.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const message =
      isJson && payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail?: unknown }).detail)
        : typeof payload === "string" && payload
          ? payload
          : fallbackMessage;

    throw new Error(message);
  }

  return payload as T;
}

function dataModelConnectionBody(params: DataModelConnectionParams) {
  return {
    db_type: params.dbType,
    host: params.host,
    port: params.port,
    database: params.database,
    username: params.username,
    password: params.password,
    schema_name: params.schemaName ?? null,
  };
}

function dataModelSpecBody(spec: DataModelSpec) {
  return {
    tables: spec.tables,
    base_table: spec.baseTable,
    relationships: spec.relationships,
    selected_columns: spec.selectedColumns ?? null,
  };
}

/** POST /datamodel/columns - column metadata for the selected tables. */
export async function fetchDataModelColumns(
  params: DataModelConnectionParams & { tables: string[] },
): Promise<DataModelColumnsResponse> {
  const response = await fetch(`${API_BASE}/datamodel/columns`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...dataModelConnectionBody(params),
      tables: params.tables,
    }),
  });

  return unwrapDataModelResponse<DataModelColumnsResponse>(
    response,
    "Could not load columns for the selected tables.",
  );
}

/** POST /datamodel/preview - validate the joins and return a 20-row sample. */
export async function previewDataModel(
  params: DataModelConnectionParams & DataModelSpec,
): Promise<DataModelPreviewResponse> {
  const response = await fetch(`${API_BASE}/datamodel/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...dataModelConnectionBody(params),
      ...dataModelSpecBody(params),
    }),
  });

  return unwrapDataModelResponse<DataModelPreviewResponse>(
    response,
    "Could not preview the joined dataset.",
  );
}

/**
 * POST /datamodel/extract - run the join, write the CSV, create the Project.
 *
 * Returns the same shape as createProjectFromDatabase, so the page's submit
 * handler can route all three flows to the same router.push().
 */
export async function createProjectFromDataModel(
  params: DataModelConnectionParams &
    DataModelSpec & {
      projectName: string;
      targetColumn: string;
    },
): Promise<DatabaseExtractResponse> {
  const response = await fetch(`${API_BASE}/datamodel/extract`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...dataModelConnectionBody(params),
      ...dataModelSpecBody(params),
      project_name: params.projectName,
      target_column: params.targetColumn,
    }),
  });

  return unwrapDataModelResponse<DatabaseExtractResponse>(
    response,
    "Failed to create project from the selected tables.",
  );
}


// --- Join Advisor (Sprint 1) ----------------------------------------------
//
// Calls POST /datamodel/analyze-relationship, which profiles ONE candidate
// relationship against the real source data and returns a deterministic
// INNER/LEFT recommendation plus the evidence behind it.
//
// Nothing here decides anything: the endpoint recommends, the user still
// chooses the join type. Field names below mirror the backend's JoinProfile
// and JoinRecommendation dataclasses exactly (snake_case, as asdict() emits).

/** The measured facts about one candidate column pair. */
export interface JoinProfile {
  // --- measured by the database ---
  base_rows: number;
  related_rows: number;
  base_matched_rows: number;
  related_matched_rows: number;
  base_null_keys: number;
  related_null_keys: number;
  base_distinct_keys: number;
  related_distinct_keys: number;
  max_rows_per_key: number;
  // --- derived ---
  base_unmatched_rows: number;
  related_unmatched_rows: number;
  base_key_unique: boolean;
  related_key_unique: boolean;
  match_percentage: number;
  cardinality: "one_to_one" | "one_to_many" | "many_to_one" | "many_to_many";
  inner_result_rows: number;
  left_result_rows: number;
  row_multiplication: boolean;
}

/** The suggestion and the plain-language reasons for it. */
export interface JoinRecommendation {
  /** null when neither join type is sensible (no rows match at all). */
  recommended: "inner" | "left" | null;
  severity: "info" | "warning" | "danger";
  headline: string;
  reasons: string[];
}

export interface RelationshipProfileResponse {
  base_table: string;
  related_table: string;
  profile: JoinProfile;
  recommendation: JoinRecommendation;
}

/**
 * POST /datamodel/analyze-relationship - profile one candidate relationship.
 *
 * Runs ten COUNTs in a single statement against the source database, then
 * applies a fixed rule table. Same numbers always produce the same advice.
 */
export async function analyzeRelationship(
  params: DataModelConnectionParams & {
    baseTable: string;
    baseColumn: string;
    relatedTable: string;
    relatedColumn: string;
  },
): Promise<RelationshipProfileResponse> {
  const response = await fetch(`${API_BASE}/datamodel/analyze-relationship`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...dataModelConnectionBody(params),
      base_table: params.baseTable,
      base_column: params.baseColumn,
      related_table: params.relatedTable,
      related_column: params.relatedColumn,
    }),
  });

  return unwrapDataModelResponse<RelationshipProfileResponse>(
    response,
    "Could not analyse this relationship.",
  );
}