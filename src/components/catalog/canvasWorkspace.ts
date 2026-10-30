/**
 * Saving the map workspace (which tables are on the canvas, where the cards
 * are, and the joins the user defined) in THIS browser only.
 *
 * What is stored: table names, card positions, and join definitions.
 * What is never stored: passwords, tokens, host names, or any row data.
 * Nothing here writes to the database or the server.
 *
 * Saved data is untrusted when it is read back: tables that no longer exist in
 * the catalog are dropped, and bad numbers or malformed joins are discarded.
 */

import type {
  JoinCondition,
  JoinType,
  Point,
  WorkspaceJoin,
} from "./relationshipGraph";

// Kept as a plain list (type-only import above) so this file can be tested with
// Node directly. A test asserts it equals JOIN_TYPES in relationshipGraph.ts.
export const SAVED_JOIN_TYPES: readonly string[] = ["INNER", "LEFT", "RIGHT", "FULL"];

export const WORKSPACE_VERSION = 1;

export interface SavedWorkspace {
  version: typeof WORKSPACE_VERSION;
  canvas: string[];
  positions: Record<string, Point>;
  expanded: string[];
  joins: WorkspaceJoin[];
}

export const storageKey = (sourceId: string) => `dios.relationship-map.v${WORKSPACE_VERSION}.${sourceId}`;

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const isFiniteNumber = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function serializeWorkspace(w: Omit<SavedWorkspace, "version">): string {
  const payload: SavedWorkspace = { version: WORKSPACE_VERSION, ...w };
  return JSON.stringify(payload);
}

/** Parse and sanitise. Returns null when nothing usable was saved. */
export function parseWorkspace(
  raw: string | null,
  knownTables: ReadonlySet<string>
): SavedWorkspace | null {
  if (!raw) return null;
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(data) || data.version !== WORKSPACE_VERSION) return null;

  const canvas: string[] = [];
  if (Array.isArray(data.canvas)) {
    for (const n of data.canvas) {
      if (typeof n === "string" && knownTables.has(n) && !canvas.includes(n)) canvas.push(n);
    }
  }

  const positions: Record<string, Point> = {};
  if (isRecord(data.positions)) {
    for (const [name, p] of Object.entries(data.positions)) {
      if (knownTables.has(name) && isRecord(p) && isFiniteNumber(p.x) && isFiniteNumber(p.y)) {
        positions[name] = { x: p.x, y: p.y };
      }
    }
  }

  const expanded = Array.isArray(data.expanded)
    ? data.expanded.filter((n): n is string => typeof n === "string" && knownTables.has(n))
    : [];

  const joins: WorkspaceJoin[] = [];
  if (Array.isArray(data.joins)) {
    for (const j of data.joins) {
      if (!isRecord(j)) continue;
      const { id, leftTable, rightTable, joinType, conditions } = j;
      if (typeof id !== "string" || typeof leftTable !== "string" || typeof rightTable !== "string") continue;
      if (!knownTables.has(leftTable) || !knownTables.has(rightTable)) continue;
      if (typeof joinType !== "string" || !SAVED_JOIN_TYPES.includes(joinType)) continue;
      if (!Array.isArray(conditions)) continue;
      const clean: JoinCondition[] = [];
      for (const c of conditions) {
        if (isRecord(c) && typeof c.leftColumn === "string" && typeof c.rightColumn === "string") {
          clean.push({ leftColumn: c.leftColumn, rightColumn: c.rightColumn });
        }
      }
      if (!clean.length || joins.some((x) => x.id === id)) continue;
      joins.push({ id, leftTable, rightTable, joinType: joinType as JoinType, conditions: clean });
    }
  }

  return { version: WORKSPACE_VERSION, canvas, positions, expanded, joins };
}

/** Browser storage can be missing or blocked; every access is guarded. */
export function loadWorkspace(sourceId: string, knownTables: ReadonlySet<string>): SavedWorkspace | null {
  try {
    return parseWorkspace(window.localStorage.getItem(storageKey(sourceId)), knownTables);
  } catch {
    return null;
  }
}

export function saveWorkspace(sourceId: string, w: Omit<SavedWorkspace, "version">): void {
  try {
    window.localStorage.setItem(storageKey(sourceId), serializeWorkspace(w));
  } catch {
    /* storage unavailable: the workspace simply is not remembered */
  }
}

export function clearSavedWorkspace(sourceId: string): void {
  try {
    window.localStorage.removeItem(storageKey(sourceId));
  } catch {
    /* ignore */
  }
}
