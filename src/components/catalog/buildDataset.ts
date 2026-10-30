/**
 * "Build Dataset": save the handoff in this browser, then open the Builder.
 * Browser-only glue; the rules live in datasetHandoff.ts (tested with Node).
 */

import type { CatalogRelationship, CatalogTableSummary } from "@/services/catalogService";
import { composeHandoffRelationships, createHandoff, builderUrl } from "./datasetHandoff";
import { loadWorkspace } from "./canvasWorkspace";
import type { WorkspaceJoin } from "./relationshipGraph";

interface BuildArgs {
  sourceId: string;
  tableIds: readonly string[];
  catalogTables: readonly CatalogTableSummary[];
  declared: readonly CatalogRelationship[];
  /** Joins currently on the Map. When omitted, the saved Map workspace is used. */
  workspaceJoins?: readonly WorkspaceJoin[];
  newTab?: boolean;
}

/** Returns an error message to show the user, or null when the Builder was opened. */
export function buildDataset(args: BuildArgs): string | null {
  let storage: Storage | null = null;
  try {
    storage = window.localStorage;
  } catch {
    storage = null;
  }
  const byId = new Map(args.catalogTables.map((t) => [t.id, t]));
  const tables = args.tableIds
    .map((id) => byId.get(id))
    .filter((t): t is CatalogTableSummary => Boolean(t))
    .map((t) => ({
      id: t.id,
      schemaName: t.schema_name,
      tableName: t.table_name,
      rowCount: t.row_count,
      rowCountIsEstimate: t.row_count_is_estimate !== false,
    }));
  const known = new Set(args.catalogTables.map((t) => t.table_name));
  const joins = args.workspaceJoins ?? loadWorkspace(args.sourceId, known)?.joins ?? [];
  const relationships = composeHandoffRelationships(args.tableIds, args.catalogTables, [...args.declared], [...joins]);

  const made = createHandoff(storage, { sourceId: args.sourceId, tables, relationships });
  if (!made.ok) return made.message;
  const url = builderUrl(args.sourceId, made.key);
  if (args.newTab) {
    const opened = window.open(url, "_blank");
    if (!opened) return "Your browser blocked the new tab. Allow pop-ups for this site, or use Build Dataset to open it in this tab.";
    return null;
  }
  window.location.assign(url);
  return null;
}
