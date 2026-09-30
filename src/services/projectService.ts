const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

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


/**
 * Unwrap an ETL Copilot response.
 *
 * unwrapDataModelResponse does String(payload.detail), which is correct only
 * when detail is a string. The Copilot returns an OBJECT for a CopilotError
 * ({message, issues[]}) and FastAPI returns an ARRAY for a 422 validation
 * error. Both render as "[object Object]". This handles all three shapes.
 */
async function unwrapCopilotResponse<T>(
  response: Response,
  fallbackMessage: string,
): Promise<T> {
  const contentType = response.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    let message = fallbackMessage;
    const detail = (payload as { detail?: unknown })?.detail;

    // Diagnostic 500 from /etl-copilot/preview: {error_type, error, traceback}.
    // It has no `detail`, so surface it before the detail branches run.
    const diag = payload as { error_type?: unknown; error?: unknown };
    if (diag?.error_type || diag?.error) {
      throw new Error(
        [diag.error_type, diag.error].filter(Boolean).map(String).join(": "),
      );
    }

    if (typeof detail === "string" && detail) {
      message = detail;
    } else if (Array.isArray(detail)) {
      // FastAPI 422: [{loc, msg, type}, ...]
      message = detail
        .map((item) => String((item as { msg?: unknown })?.msg ?? item))
        .join("; ");
    } else if (detail && typeof detail === "object") {
      const asObject = detail as {
        message?: unknown;
        error?: unknown;
        hint?: unknown;
        issues?: { message?: unknown }[];
      };
      const parts = [asObject.message, asObject.error, asObject.hint]
        .filter(Boolean)
        .map(String);
      if (Array.isArray(asObject.issues) && asObject.issues.length > 0) {
        parts.push(
          asObject.issues.map((i) => String(i?.message ?? "")).join("; "),
        );
      }
      if (parts.length > 0) message = parts.join(" - ");
    } else if (typeof payload === "string" && payload) {
      message = payload;
    }

    throw new Error(message);
  }

  return payload as T;
}

export interface EtlCopilotSchemaResponse {
  db_type: string;
  schema_name: string;
  table_count: number;
  relationship_count: number;
  usable: boolean;
  notice: string | null;
}

export interface EtlCopilotPreviewResponse {
  blocking: boolean;
  preview_rows: Record<string, unknown>[];
  has_more: boolean;
  sql: string;
  explanation: string[];
  columns: string[];
  errors: unknown[];
  warnings: unknown[];
  spec: unknown;
  lineage?: EtlCopilotLineage | null;
  reconciliation?: EtlCopilotReconciliation | null;
}

export interface EtlCopilotLineage {
  spec_version: number;
  entity_table: string;
  entity_key: string[];
  declared_grain: string[];
  tables_referenced: string[];
  output_columns: string[];
  measures: {
    name: string;
    source: string;
    aggregation: string;
    join_path: string[];
    path_verified: boolean;
    cardinality_per_hop: string[];
    nested_aggregation: boolean;
    zero_when_absent: boolean;
    explanation: string;
  }[];
  join_paths: {
    source_table: string;
    hops: string[];
    verified: boolean;
    discovered_via: string;
    alternatives_considered: number;
  }[];
}

export interface EtlCopilotReconciliation {
  available?: boolean;
  passed?: boolean | null;
  checks?: {
    check: string;
    expected?: unknown;
    actual?: unknown;
    passed?: boolean;
  }[];
  message?: string;
}

export interface EtlCopilotBuildResponse {
  job_id: string;
  status: string;
  poll: string;
}

export interface EtlCopilotBuildResult {
  project_id: string;
  project_name?: string;
  file_id: string;
  target_column: string;
  rows: number;
  columns: number;
  data_quality: {
    total_rows: number;
    total_columns: number;
    missing_value_percentage: number | null;
    duplicate_rows: number;
    quality_score: number;
  };
  column_profiles: {
    column_name: string;
    data_type: string;
    null_count: number;
    distinct_count: number;
    sample_values: unknown[];
  }[];
  reconciliation?: EtlCopilotReconciliation;
  lineage?: EtlCopilotLineage;
}

export interface EtlCopilotJobStatusResponse {
  status: string;
  result?: EtlCopilotBuildResult | null;
  error?: { detail?: unknown } | null;
}


export async function etlCopilotSchema(
  params: DataModelConnectionParams,
): Promise<EtlCopilotSchemaResponse> {
  const response = await fetch(`${API_BASE}/etl-copilot/schema`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dataModelConnectionBody(params)),
  });

  return unwrapCopilotResponse<EtlCopilotSchemaResponse>(
    response,
    "Could not load ETL Copilot schema.",
  );
}


export async function etlCopilotPreview(
  params: DataModelConnectionParams & {
    userRequest: string;
  },
): Promise<EtlCopilotPreviewResponse> {
  const url = `${API_BASE}/etl-copilot/preview`;
  const payload = {
    ...dataModelConnectionBody(params),
    user_request: params.userRequest,
  };

  console.info("[ETL preview trace] API_BASE:", API_BASE);
  console.info("[ETL preview trace] POST URL:", url);
  console.info("[ETL preview trace] payload:", {
    ...payload,
    password: payload.password ? "[REDACTED]" : payload.password,
  });

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    console.error("[ETL preview trace] fetch rejected");
    console.error("URL:", url);
    console.error("Error object:", error);
    const err = error as { name?: string; message?: string; stack?: string };
    console.error("Error name:", err?.name);
    console.error("Error message:", err?.message);
    console.error("Error stack:", err?.stack);
    throw error;
  }

  console.info("[ETL preview trace] response:", {
    url: response.url,
    status: response.status,
    type: response.type,
  });

  return unwrapCopilotResponse<EtlCopilotPreviewResponse>(
    response,
    "Could not generate ETL Copilot preview.",
  );
}



export async function etlCopilotBuild(
  params: DataModelConnectionParams & {
    projectName: string;
    userRequest: string;
  },
): Promise<EtlCopilotBuildResponse> {
  const response = await fetch(`${API_BASE}/etl-copilot/build`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...dataModelConnectionBody(params),
      project_name: params.projectName,
      user_request: params.userRequest,
      background: true,
    }),
  });

  return unwrapCopilotResponse<EtlCopilotBuildResponse>(
    response,
    "Could not build ETL Copilot dataset.",
  );
}


export async function etlCopilotStatus(
  jobId: string,
): Promise<EtlCopilotJobStatusResponse> {
  const response = await fetch(
    `${API_BASE}/etl-copilot/status/${jobId}`,
  );

  return unwrapCopilotResponse<EtlCopilotJobStatusResponse>(
    response,
    "Could not load ETL Copilot job status.",
  );
}