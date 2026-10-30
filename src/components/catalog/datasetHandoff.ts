/**
 * "Build Dataset" handoff: Data Catalog -> Visual ETL Builder.
 *
 * The Catalog saves a small, temporary note in THIS browser (localStorage) and
 * opens the Builder with only an opaque key in the address. The Builder reads
 * the note once and deletes it.
 *
 * What the note holds: the source id, the chosen tables (id, schema, name, row
 * count) and the relationships (declared foreign keys and joins the user
 * defined on the map). It is built field by field from an allow-list, so a
 * password, token or any other extra property can never be copied into it.
 * Nothing sensitive goes in the address either.
 *
 * Everything read back is untrusted: it is parsed defensively and then checked
 * against the current Catalog before the Builder uses it.
 *
 * This file has no imports at run time so Node can test it directly.
 */

import type { CatalogRelationship, CatalogTableSummary } from "@/services/catalogService";
import type { WorkspaceJoin } from "./relationshipGraph";

export const HANDOFF_VERSION = 1;
export const HANDOFF_PREFIX = `dios.handoff.v${HANDOFF_VERSION}.`;
/** Ten minutes. */
export const HANDOFF_TTL_MS = 10 * 60 * 1000;
export const MAX_HANDOFF_TABLES = 60;
export const MAX_HANDOFF_RELATIONSHIPS = 200;
export const MAX_CONDITIONS = 20;

/** Matches what Storage offers; lets tests use a plain in-memory object. */
export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
  readonly length: number;
  key(index: number): string | null;
}

export type HandoffJoinType = "INNER" | "LEFT" | "RIGHT" | "FULL";
const JOIN_TYPES: readonly string[] = ["INNER", "LEFT", "RIGHT", "FULL"];

export interface HandoffTable {
  id: string;
  schemaName: string;
  tableName: string;
  rowCount: number | null;
  rowCountIsEstimate: boolean;
}

/**
 * kind "declared": a foreign key that exists in the database (the left table
 * holds the key). kind "workspace": a join the user drew on the Relationship
 * Map. It is NOT a database constraint and has not been checked against data.
 */
export interface HandoffRelationship {
  id: string;
  kind: "declared" | "workspace";
  leftTableId: string;
  rightTableId: string;
  joinType: HandoffJoinType | null;
  conditions: { leftColumn: string; rightColumn: string }[];
  constraintName: string | null;
}

export interface HandoffPayload {
  version: typeof HANDOFF_VERSION;
  createdAt: number;
  expiresAt: number;
  sourceId: string;
  tables: HandoffTable[];
  relationships: HandoffRelationship[];
}

export type HandoffFailure = "missing" | "expired" | "malformed";

export type ConsumeResult =
  | { ok: true; payload: HandoffPayload }
  | { ok: false; reason: HandoffFailure; message: string };

export const FAILURE_MESSAGES: Record<HandoffFailure, string> = {
  missing:
    "The table selection from the Data Catalog could not be found. It may already have been used or this browser may have cleared it. Choose your tables again in the Catalog, or add them below.",
  expired:
    "The table selection from the Data Catalog has expired (it is only kept for 10 minutes). Choose your tables again in the Catalog, or add them below.",
  malformed:
    "The table selection from the Data Catalog could not be read. Choose your tables again in the Catalog, or add them below.",
};

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);
const isText = (v: unknown, max = 300): v is string => typeof v === "string" && v.length > 0 && v.length <= max;

/** 128 bits of randomness as 32 hex characters. */
export function randomKey(bytes: (n: number) => Uint8Array = defaultBytes): string {
  return Array.from(bytes(16), (b) => b.toString(16).padStart(2, "0")).join("");
}

function defaultBytes(n: number): Uint8Array {
  const out = new Uint8Array(n);
  globalThis.crypto.getRandomValues(out);
  return out;
}

export function handoffStorageKey(key: string): string {
  return HANDOFF_PREFIX + key;
}

/** A key from the address bar is only ever used if it looks like one we made. */
export function isValidKey(key: unknown): key is string {
  return typeof key === "string" && /^[0-9a-f]{32}$/.test(key);
}

// --------------------------------------------------------------- creating

export interface HandoffInput {
  sourceId: string;
  tables: readonly HandoffTable[];
  relationships: readonly HandoffRelationship[];
}

/** Copy only the allowed fields. Anything else on the input is dropped. */
export function buildPayload(input: HandoffInput, now: number): HandoffPayload {
  const tables: HandoffTable[] = [];
  const seen = new Set<string>();
  for (const t of input.tables) {
    if (!isText(t.id) || seen.has(t.id) || tables.length >= MAX_HANDOFF_TABLES) continue;
    seen.add(t.id);
    tables.push({
      id: t.id,
      schemaName: String(t.schemaName ?? ""),
      tableName: String(t.tableName ?? ""),
      rowCount: typeof t.rowCount === "number" && Number.isFinite(t.rowCount) ? t.rowCount : null,
      rowCountIsEstimate: t.rowCountIsEstimate !== false,
    });
  }
  const relationships: HandoffRelationship[] = [];
  const relIds = new Set<string>();
  for (const r of input.relationships) {
    if (relationships.length >= MAX_HANDOFF_RELATIONSHIPS) break;
    if (!isText(r.id) || relIds.has(r.id)) continue;
    if (r.kind !== "declared" && r.kind !== "workspace") continue;
    if (!seen.has(r.leftTableId) || !seen.has(r.rightTableId)) continue;
    const conditions = (Array.isArray(r.conditions) ? r.conditions : [])
      .filter((c) => isRecord(c) && isText(c.leftColumn) && isText(c.rightColumn))
      .slice(0, MAX_CONDITIONS)
      .map((c) => ({ leftColumn: c.leftColumn, rightColumn: c.rightColumn }));
    if (conditions.length === 0) continue;
    relIds.add(r.id);
    relationships.push({
      id: r.id,
      kind: r.kind,
      leftTableId: r.leftTableId,
      rightTableId: r.rightTableId,
      joinType: r.joinType && JOIN_TYPES.includes(r.joinType) ? r.joinType : null,
      conditions,
      constraintName: typeof r.constraintName === "string" ? r.constraintName : null,
    });
  }
  return {
    version: HANDOFF_VERSION,
    createdAt: now,
    expiresAt: now + HANDOFF_TTL_MS,
    sourceId: input.sourceId,
    tables,
    relationships,
  };
}

export type CreateResult = { ok: true; key: string; payload: HandoffPayload } | { ok: false; message: string };

/** Save a new handoff and return its opaque key. Also clears old expired ones. */
export function createHandoff(
  storage: StorageLike | null,
  input: HandoffInput,
  now: number = Date.now(),
  bytes: (n: number) => Uint8Array = defaultBytes
): CreateResult {
  if (!storage) return { ok: false, message: "This browser does not allow the temporary note needed to open the Builder." };
  if (!isText(input.sourceId)) return { ok: false, message: "No data source is selected." };
  const payload = buildPayload(input, now);
  if (payload.tables.length === 0) return { ok: false, message: "Select at least one table first." };
  try {
    purgeExpired(storage, now);
    const key = randomKey(bytes);
    storage.setItem(handoffStorageKey(key), JSON.stringify(payload));
    return { ok: true, key, payload };
  } catch {
    return { ok: false, message: "This browser would not save the temporary note needed to open the Builder." };
  }
}

/** Remove every handoff that has expired or cannot be read. */
export function purgeExpired(storage: StorageLike, now: number = Date.now()): number {
  const doomed: string[] = [];
  for (let i = 0; i < storage.length; i += 1) {
    const name = storage.key(i);
    if (!name || !name.startsWith(HANDOFF_PREFIX)) continue;
    const parsed = parsePayload(storage.getItem(name));
    if (!parsed || parsed.expiresAt <= now) doomed.push(name);
  }
  for (const name of doomed) storage.removeItem(name);
  return doomed.length;
}

// -------------------------------------------------------------- consuming

/** Parse and sanitise stored text. Returns null for anything unusable. */
export function parsePayload(raw: string | null): HandoffPayload | null {
  if (!raw) return null;
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(data) || data.version !== HANDOFF_VERSION) return null;
  if (typeof data.createdAt !== "number" || typeof data.expiresAt !== "number") return null;
  if (!Number.isFinite(data.createdAt) || !Number.isFinite(data.expiresAt)) return null;
  if (!isText(data.sourceId) || !Array.isArray(data.tables) || !Array.isArray(data.relationships)) return null;
  const input: HandoffInput = {
    sourceId: data.sourceId,
    tables: data.tables.filter(isRecord).map((t) => ({
      id: t.id as string,
      schemaName: t.schemaName as string,
      tableName: t.tableName as string,
      rowCount: t.rowCount as number | null,
      rowCountIsEstimate: t.rowCountIsEstimate as boolean,
    })),
    relationships: data.relationships.filter(isRecord).map((r) => ({
      id: r.id as string,
      kind: r.kind as HandoffRelationship["kind"],
      leftTableId: r.leftTableId as string,
      rightTableId: r.rightTableId as string,
      joinType: r.joinType as HandoffJoinType | null,
      conditions: r.conditions as HandoffRelationship["conditions"],
      constraintName: r.constraintName as string | null,
    })),
  };
  const clean = buildPayload(input, data.createdAt);
  if (clean.tables.length === 0) return null;
  // buildPayload recalculates expiry from createdAt; keep the stored value
  // only when it is not later than that, so a note cannot extend its own life.
  clean.expiresAt = Math.min(data.expiresAt, data.createdAt + HANDOFF_TTL_MS);
  return clean;
}

/**
 * Read the handoff once. The stored note is removed in every case (used,
 * expired or unreadable), so a key can never be used a second time.
 */
export function consumeHandoff(
  storage: StorageLike | null,
  key: string | null | undefined,
  now: number = Date.now()
): ConsumeResult {
  const fail = (reason: HandoffFailure): ConsumeResult => ({ ok: false, reason, message: FAILURE_MESSAGES[reason] });
  if (!storage || !isValidKey(key)) return fail(key ? "malformed" : "missing");
  const name = handoffStorageKey(key);
  let raw: string | null;
  try {
    raw = storage.getItem(name);
    if (raw !== null) storage.removeItem(name);
  } catch {
    return fail("missing");
  }
  if (raw === null) return fail("missing");
  const payload = parsePayload(raw);
  if (!payload) return fail("malformed");
  if (payload.expiresAt <= now) return fail("expired");
  return { ok: true, payload };
}

// ------------------------------------------------- checking against Catalog

export interface CatalogTableRef {
  id: string;
  schema_name: string;
  table_name: string;
  row_count?: number | null;
  row_count_is_estimate?: boolean;
}

export interface HandoffCheck {
  /** Tables that still exist in the Catalog for this source (current names). */
  tables: HandoffTable[];
  /** Relationships whose two tables both survived. */
  relationships: HandoffRelationship[];
  /** Names (as saved) of tables that are no longer in the Catalog. */
  droppedTables: string[];
  droppedRelationships: number;
  /** Set when nothing usable remains or the source does not match. */
  problem: string | null;
  /** Set when some, but not all, of the selection is still valid. */
  warning: string | null;
}

/**
 * The note is only a request. The Catalog decides what is real: names and row
 * counts come from the current Catalog, not from the note.
 */
export function checkAgainstCatalog(
  payload: HandoffPayload,
  currentSourceId: string,
  catalogTables: readonly CatalogTableRef[]
): HandoffCheck {
  const empty: HandoffCheck = { tables: [], relationships: [], droppedTables: [], droppedRelationships: 0, problem: null, warning: null };
  if (payload.sourceId !== currentSourceId) {
    return { ...empty, problem: "The table selection belongs to a different data source, so it was not used. Choose your tables again in the Catalog." };
  }
  const byId = new Map(catalogTables.map((t) => [t.id, t]));
  const tables: HandoffTable[] = [];
  const dropped: string[] = [];
  for (const t of payload.tables) {
    const live = byId.get(t.id);
    if (!live) {
      dropped.push(t.tableName || t.id);
      continue;
    }
    tables.push({
      id: live.id,
      schemaName: live.schema_name,
      tableName: live.table_name,
      rowCount: typeof live.row_count === "number" ? live.row_count : null,
      rowCountIsEstimate: live.row_count_is_estimate !== false,
    });
  }
  if (tables.length === 0) {
    return {
      ...empty,
      droppedTables: dropped,
      problem: "None of the selected tables exist in the Catalog any more (it may have been scanned again). Choose your tables again in the Catalog.",
    };
  }
  const kept = new Set(tables.map((t) => t.id));
  const relationships = payload.relationships.filter((r) => kept.has(r.leftTableId) && kept.has(r.rightTableId));
  const droppedRelationships = payload.relationships.length - relationships.length;
  const warning = dropped.length
    ? `${dropped.length} selected table${dropped.length === 1 ? " is" : "s are"} no longer in the Catalog and ${dropped.length === 1 ? "was" : "were"} left out: ${dropped.join(", ")}.`
    : null;
  return { tables, relationships, droppedTables: dropped, droppedRelationships, problem: null, warning };
}

// ----------------------------------------------------------------- the URL

/** The address carries only the source id and the opaque key. */
export function builderUrl(sourceId: string, key: string): string {
  return `/etl-studio?source=${encodeURIComponent(sourceId)}&handoff=${encodeURIComponent(key)}`;
}

// ---------------------------------------------------------------- layout

export interface Spot {
  x: number;
  y: number;
}

/**
 * Positions for `count` new Source nodes: a tidy grid (at most `perColumn`
 * nodes tall) that starts right of nothing and below nothing already placed.
 * Spots never overlap each other or anything in `occupied`.
 */
export function gridSpots(
  count: number,
  occupied: readonly Spot[],
  nodeW = 232,
  nodeH = 108,
  perColumn = 4,
  startX = 40,
  startY = 40
): Spot[] {
  const gapX = 60;
  const gapY = 44;
  const clash = (x: number, y: number) =>
    occupied.some((o) => Math.abs(o.x - x) < nodeW + 24 && Math.abs(o.y - y) < nodeH + 24);
  const out: Spot[] = [];
  let col = 0;
  let row = 0;
  let guard = 0;
  while (out.length < count && guard < 5000) {
    guard += 1;
    const x = startX + col * (nodeW + gapX);
    const y = startY + row * (nodeH + gapY);
    row += 1;
    if (row >= perColumn) {
      row = 0;
      col += 1;
    }
    if (clash(x, y)) continue;
    out.push({ x, y });
  }
  return out;
}

// ------------------------------------------------- gathering relationships

/**
 * Build the relationship list for the chosen tables. Both ends must be chosen.
 * Declared foreign keys come from the Catalog; workspace joins are the ones the
 * user drew on the Relationship Map (not database constraints, not verified).
 */
export function composeHandoffRelationships(
  tableIds: readonly string[],
  catalogTables: readonly Pick<CatalogTableSummary, "id" | "table_name">[],
  declared: readonly CatalogRelationship[],
  workspaceJoins: readonly WorkspaceJoin[]
): HandoffRelationship[] {
  const chosen = new Set(tableIds);
  const idByName = new Map<string, string>();
  for (const t of catalogTables) if (chosen.has(t.id)) idByName.set(t.table_name, t.id);
  const out: HandoffRelationship[] = [];
  for (const r of declared) {
    if (r.origin !== "declared") continue;
    const left = idByName.get(r.left_table);
    const right = idByName.get(r.right_table);
    if (!left || !right) continue;
    // Some databases report no constraint name (an empty string); those rows are
    // separate relationships, never merged.
    const name = r.constraint_name ? r.constraint_name : null;
    const existing = name
      ? out.find((x) => x.kind === "declared" && x.leftTableId === left && x.rightTableId === right && x.constraintName === name)
      : undefined;
    const condition = { leftColumn: r.left_column, rightColumn: r.right_column };
    if (existing) {
      // One foreign key with several columns arrives as several rows.
      existing.conditions.push(condition);
      continue;
    }
    out.push({
      id: `fk:${name ?? r.id}`,
      kind: "declared",
      leftTableId: left,
      rightTableId: right,
      joinType: null,
      conditions: [condition],
      constraintName: name,
    });
  }
  for (const j of workspaceJoins) {
    const left = idByName.get(j.leftTable);
    const right = idByName.get(j.rightTable);
    if (!left || !right) continue;
    out.push({
      id: `ws:${j.id}`,
      kind: "workspace",
      leftTableId: left,
      rightTableId: right,
      joinType: j.joinType,
      conditions: j.conditions.map((c) => ({ leftColumn: c.leftColumn, rightColumn: c.rightColumn })),
      constraintName: null,
    });
  }
  return out;
}
