/**
 * Pure logic for the Visual Table Relationship Map.
 *
 * No React and no network calls live here, so everything can be tested on its
 * own. The map only ever draws relationships that the catalog scan recorded as
 * "declared" (real foreign keys read from the database). Nothing is guessed.
 *
 * Direction: the catalog stores a foreign key as left -> right, where the LEFT
 * table holds the foreign-key column (the "child") and the RIGHT table is the
 * table it points at (the "parent").
 */

import type {
  CatalogColumnDetail,
  CatalogRelationship,
  CatalogTableSummary,
} from "@/services/catalogService";

// ---------------------------------------------------------------- constants

export const CARD_WIDTH = 248;
export const HEADER_HEIGHT = 88;
export const ROW_HEIGHT = 22;
export const BODY_PADDING = 8;
/** Columns drawn on an expanded card. Wider tables show the key columns first. */
export const MAX_VISIBLE_COLUMNS = 30;

const LAYER_GAP = 220; // horizontal space between dependency layers (room for lines)
const SUBCOLUMN_GAP = 48;
const ROW_GAP = 40;
const MAX_PER_COLUMN = 8;
const GRID_GAP = 40;

// -------------------------------------------------------------------- types

export interface Point {
  x: number;
  y: number;
}

export interface Rect extends Point {
  w: number;
  h: number;
}

/** Cardinality is derived from key metadata, never stored in the catalog. */
export type Cardinality = "many-to-one" | "one-to-one" | "unknown";

export interface MapEdge {
  id: string;
  /** Table that holds the foreign-key column. */
  childTable: string;
  childColumn: string;
  /** Table that is referenced. */
  parentTable: string;
  parentColumn: string;
  constraintName: string | null;
  cardinality: Cardinality;
  /** Plain-language reason for the cardinality, shown in the details panel. */
  cardinalityBasis: string;
}

export interface EdgeBuildResult {
  edges: MapEdge[];
  /** Relationships left out, with the reason, so the UI can be honest. */
  ignoredNotDeclared: number;
  ignoredUnknownTable: number;
}

export type ColumnLookup = Record<string, CatalogColumnDetail[] | undefined>;

// ---------------------------------------------------------- building edges

function soleKeyColumn(columns: CatalogColumnDetail[] | undefined): string | null {
  if (!columns) return null;
  const keys = columns.filter((c) => c.is_primary_key);
  return keys.length === 1 ? keys[0].column_name : null;
}

/**
 * Turn catalog relationships into map edges.
 *
 * - Only origin === "declared" is drawn. Anything else (for example a future
 *   AI suggestion) is counted in `ignoredNotDeclared` and never drawn.
 * - A relationship whose table is not in the catalog is skipped.
 * - Cardinality is only claimed when key metadata proves it:
 *     parent column is the parent's single-column primary key
 *       -> each child row points at exactly one parent row.
 *     child column is ALSO the child's single-column primary key -> one-to-one.
 *   Otherwise it is "unknown" (the catalog does not record unique constraints).
 */
export function buildEdges(
  tables: Pick<CatalogTableSummary, "table_name">[],
  relationships: CatalogRelationship[],
  columnsByTable: ColumnLookup
): EdgeBuildResult {
  const known = new Set(tables.map((t) => t.table_name));
  const edges: MapEdge[] = [];
  let ignoredNotDeclared = 0;
  let ignoredUnknownTable = 0;

  for (const rel of relationships) {
    if (rel.origin !== "declared") {
      ignoredNotDeclared += 1;
      continue;
    }
    if (!known.has(rel.left_table) || !known.has(rel.right_table)) {
      ignoredUnknownTable += 1;
      continue;
    }

    const parentCols = columnsByTable[rel.right_table];
    const childCols = columnsByTable[rel.left_table];
    let cardinality: Cardinality = "unknown";
    let basis = "Column details are still loading, so the cardinality is not known yet.";

    if (parentCols) {
      if (soleKeyColumn(parentCols) === rel.right_column) {
        if (childCols && soleKeyColumn(childCols) === rel.left_column) {
          cardinality = "one-to-one";
          basis = `Both ${rel.left_column} and ${rel.right_column} are single-column primary keys.`;
        } else if (childCols) {
          cardinality = "many-to-one";
          basis = `${rel.right_column} is the primary key of ${rel.right_table}, so each ${rel.left_table} row points at one ${rel.right_table} row.`;
        } else {
          basis = "The child table's columns are still loading.";
        }
      } else {
        basis = `${rel.right_table}.${rel.right_column} is not recorded as a single-column primary key, and the catalog does not store unique constraints.`;
      }
    }

    edges.push({
      id: rel.id,
      childTable: rel.left_table,
      childColumn: rel.left_column,
      parentTable: rel.right_table,
      parentColumn: rel.right_column,
      constraintName: rel.constraint_name,
      cardinality,
      cardinalityBasis: basis,
    });
  }

  return { edges, ignoredNotDeclared, ignoredUnknownTable };
}

/** Short label for the middle of a line: "N : 1", "1 : 1" or "FK". */
export function cardinalityLabel(c: Cardinality): string {
  if (c === "many-to-one") return "N : 1";
  if (c === "one-to-one") return "1 : 1";
  return "FK";
}

export function cardinalityText(c: Cardinality): string {
  if (c === "many-to-one") return "Many-to-one (child to parent)";
  if (c === "one-to-one") return "One-to-one";
  return "Not determined";
}

// ------------------------------------------------------------ visible rows

export interface VisibleColumns {
  columns: CatalogColumnDetail[];
  hiddenCount: number;
}

/**
 * Columns to draw on an expanded card. Up to MAX_VISIBLE_COLUMNS are shown in
 * their natural order. For wider tables the key columns (primary keys, foreign
 * keys, and columns used by a drawn relationship) are kept first so every line
 * can still land on its column.
 */
export function visibleColumns(
  columns: CatalogColumnDetail[],
  relationshipColumns: ReadonlySet<string>,
  cap: number = MAX_VISIBLE_COLUMNS
): VisibleColumns {
  const ordered = [...columns].sort((a, b) => a.ordinal_position - b.ordinal_position);
  if (ordered.length <= cap) return { columns: ordered, hiddenCount: 0 };

  const isKey = (c: CatalogColumnDetail) =>
    c.is_primary_key || c.is_foreign_key || relationshipColumns.has(c.column_name);
  const keys = ordered.filter(isKey);
  const rest = ordered.filter((c) => !isKey(c));
  const chosen = new Set([...keys, ...rest].slice(0, cap).map((c) => c.column_name));
  return {
    columns: ordered.filter((c) => chosen.has(c.column_name)),
    hiddenCount: ordered.length - chosen.size,
  };
}

/** Height of a card. `bodyRows` is null when collapsed. */
export function cardHeight(bodyRows: number | null): number {
  if (bodyRows === null) return HEADER_HEIGHT;
  return HEADER_HEIGHT + Math.max(bodyRows, 1) * ROW_HEIGHT + BODY_PADDING;
}

// ------------------------------------------------------------------ layout

export interface LayoutNode {
  name: string;
  height: number;
}

/**
 * Place cards in dependency layers: referenced (parent) tables on the left,
 * the tables that point at them to the right. Tables with no declared
 * relationship go in a tidy grid underneath. Pure and deterministic.
 */
export function computeLayout(
  nodes: LayoutNode[],
  edges: Pick<MapEdge, "childTable" | "parentTable">[]
): Record<string, Point> {
  const byName = new Map(nodes.map((n) => [n.name, n]));
  const connected = new Set<string>();
  const parentsOf = new Map<string, Set<string>>();
  const neighbours = new Map<string, Set<string>>();

  for (const e of edges) {
    if (e.childTable === e.parentTable) continue;
    if (!byName.has(e.childTable) || !byName.has(e.parentTable)) continue;
    connected.add(e.childTable);
    connected.add(e.parentTable);
    if (!parentsOf.has(e.childTable)) parentsOf.set(e.childTable, new Set());
    parentsOf.get(e.childTable)!.add(e.parentTable);
    for (const [a, b] of [
      [e.childTable, e.parentTable],
      [e.parentTable, e.childTable],
    ]) {
      if (!neighbours.has(a)) neighbours.set(a, new Set());
      neighbours.get(a)!.add(b);
    }
  }

  const positions: Record<string, Point> = {};
  const sortedConnected = [...connected].sort((a, b) => a.localeCompare(b));

  // Layer = longest chain of parents above the table. Cycles are broken by
  // ignoring a table that is already being visited.
  const layerOf = new Map<string, number>();
  const visiting = new Set<string>();
  const depth = (name: string): number => {
    const known = layerOf.get(name);
    if (known !== undefined) return known;
    if (visiting.has(name)) return 0;
    visiting.add(name);
    let d = 0;
    for (const parent of parentsOf.get(name) ?? []) d = Math.max(d, depth(parent) + 1);
    visiting.delete(name);
    layerOf.set(name, d);
    return d;
  };
  sortedConnected.forEach((n) => depth(n));

  const layerCount = sortedConnected.length
    ? Math.max(...sortedConnected.map((n) => layerOf.get(n) ?? 0)) + 1
    : 0;
  const layers: string[][] = Array.from({ length: layerCount }, () => []);
  sortedConnected.forEach((n) => layers[layerOf.get(n) ?? 0].push(n));

  // Reduce crossings: order each layer by the average slot of its neighbours.
  const slot = new Map<string, number>();
  const refreshSlots = () =>
    layers.forEach((layer) =>
      layer.forEach((n, i) => slot.set(n, layer.length > 1 ? i / (layer.length - 1) : 0.5))
    );
  refreshSlots();
  const sweep = (order: number[]) => {
    for (const li of order) {
      const key = (n: string) => {
        const ns = [...(neighbours.get(n) ?? [])].filter((m) => layerOf.get(m) !== li);
        if (!ns.length) return slot.get(n) ?? 0.5;
        return ns.reduce((sum, m) => sum + (slot.get(m) ?? 0.5), 0) / ns.length;
      };
      layers[li].sort((a, b) => key(a) - key(b) || a.localeCompare(b));
      refreshSlots();
    }
  };
  const forward = layers.map((_, i) => i);
  for (let pass = 0; pass < 3; pass += 1) {
    sweep(forward);
    sweep([...forward].reverse());
  }

  // Coordinates. A layer taller than MAX_PER_COLUMN wraps into sub-columns.
  let x = 0;
  const columnsOfLayer: { names: string[]; x: number }[][] = [];
  layers.forEach((layer, li) => {
    const chunks: string[][] = [];
    for (let i = 0; i < layer.length; i += MAX_PER_COLUMN) {
      chunks.push(layer.slice(i, i + MAX_PER_COLUMN));
    }
    columnsOfLayer[li] = chunks.map((names) => {
      const col = { names, x };
      x += CARD_WIDTH + SUBCOLUMN_GAP;
      return col;
    });
    x += LAYER_GAP - SUBCOLUMN_GAP;
  });

  const colHeight = (names: string[]) =>
    names.reduce((sum, n) => sum + (byName.get(n)?.height ?? HEADER_HEIGHT), 0) +
    ROW_GAP * Math.max(names.length - 1, 0);
  const tallest = Math.max(
    0,
    ...columnsOfLayer.flat().map((c) => colHeight(c.names))
  );
  columnsOfLayer.flat().forEach((col) => {
    let y = (tallest - colHeight(col.names)) / 2;
    col.names.forEach((n) => {
      positions[n] = { x: col.x, y };
      y += (byName.get(n)?.height ?? HEADER_HEIGHT) + ROW_GAP;
    });
  });
  const connectedBottom = tallest;

  // Tables without a relationship: grid below the connected block.
  const isolated = nodes
    .map((n) => n.name)
    .filter((n) => !connected.has(n))
    .sort((a, b) => a.localeCompare(b));
  if (isolated.length) {
    const perRow = Math.min(5, Math.max(1, Math.ceil(Math.sqrt(isolated.length))));
    const startY = connectedBottom > 0 ? connectedBottom + 100 : 0;
    const rowHeights: number[] = [];
    isolated.forEach((n, i) => {
      const r = Math.floor(i / perRow);
      rowHeights[r] = Math.max(rowHeights[r] ?? 0, byName.get(n)?.height ?? HEADER_HEIGHT);
    });
    isolated.forEach((n, i) => {
      const r = Math.floor(i / perRow);
      const c = i % perRow;
      const y =
        startY + rowHeights.slice(0, r).reduce((s, h) => s + h + GRID_GAP, 0);
      positions[n] = { x: c * (CARD_WIDTH + GRID_GAP), y };
    });
  }

  return positions;
}

/**
 * After a card grows (expand), push cards it now overlaps further down. Only
 * cards below the changed one move, and only as far as needed. Returns a new
 * object; the input is not modified.
 */
export function resolveOverlaps(
  rects: Record<string, Rect>,
  changed: string,
  gap: number = 24
): Record<string, Rect> {
  const out: Record<string, Rect> = {};
  for (const [k, r] of Object.entries(rects)) out[k] = { ...r };
  const queue = [changed];
  let guard = 0;
  while (queue.length && guard < 5000) {
    guard += 1;
    const a = out[queue.shift()!];
    if (!a) continue;
    for (const [name, b] of Object.entries(out)) {
      if (b === a) continue;
      const horizontal = a.x < b.x + b.w && b.x < a.x + a.w;
      const vertical = a.y < b.y + b.h + gap && b.y < a.y + a.h + gap;
      if (horizontal && vertical && b.y >= a.y) {
        b.y = a.y + a.h + gap;
        queue.push(name);
      }
    }
  }
  return out;
}

// -------------------------------------------------------------- line paths

export type Side = "left" | "right";

export interface EdgeGeometry {
  path: string;
  from: Point;
  to: Point;
  fromSide: Side;
  toSide: Side;
  mid: Point;
}

/** Y of a column row inside a card, or the header middle when not drawn. */
export function anchorY(rect: Rect, rowIndex: number | null, parallelOffset = 0): number {
  if (rowIndex === null) return rect.y + HEADER_HEIGHT / 2 + parallelOffset;
  return rect.y + HEADER_HEIGHT + rowIndex * ROW_HEIGHT + ROW_HEIGHT / 2;
}

/** Spread lines that join the same two tables so they do not sit on top of each other. */
export function parallelOffset(index: number, total: number): number {
  return (index - (total - 1) / 2) * 10;
}

const bezier = (p0: number, p1: number, p2: number, p3: number, t: number) =>
  (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3;

/**
 * Curve from the child card to the parent card. Lines leave the sides that
 * face each other; cards stacked in the same column connect on their right
 * sides with a loop.
 */
export function edgeGeometry(
  child: Rect,
  parent: Rect,
  childY: number,
  parentY: number
): EdgeGeometry {
  let fromSide: Side;
  let toSide: Side;
  if (child.x + child.w + 30 <= parent.x) {
    fromSide = "right";
    toSide = "left";
  } else if (parent.x + parent.w + 30 <= child.x) {
    fromSide = "left";
    toSide = "right";
  } else {
    fromSide = "right";
    toSide = "right";
  }
  const x1 = fromSide === "right" ? child.x + child.w : child.x;
  const x2 = toSide === "right" ? parent.x + parent.w : parent.x;
  const d1 = fromSide === "right" ? 1 : -1;
  const d2 = toSide === "right" ? 1 : -1;
  const reach =
    fromSide === toSide
      ? 70 + Math.abs(parentY - childY) / 4
      : Math.max(40, Math.min(170, Math.abs(x2 - x1) / 2));
  const c1x = x1 + d1 * reach;
  const c2x = x2 + d2 * reach;
  return {
    path: `M ${x1} ${childY} C ${c1x} ${childY}, ${c2x} ${parentY}, ${x2} ${parentY}`,
    from: { x: x1, y: childY },
    to: { x: x2, y: parentY },
    fromSide,
    toSide,
    mid: {
      x: bezier(x1, c1x, c2x, x2, 0.5),
      y: bezier(childY, childY, parentY, parentY, 0.5),
    },
  };
}

// ------------------------------------------------------------------ viewport

export interface Viewport {
  x: number;
  y: number;
  k: number;
}

export const MIN_ZOOM = 0.15;
export const MAX_ZOOM = 2;

export const clampZoom = (k: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, k));

/** Viewport that fits every rect inside a width x height area. */
export function fitViewport(
  rects: Rect[],
  width: number,
  height: number,
  padding = 48
): Viewport {
  if (!rects.length || width <= 0 || height <= 0) return { x: 0, y: 0, k: 1 };
  const minX = Math.min(...rects.map((r) => r.x));
  const minY = Math.min(...rects.map((r) => r.y));
  const maxX = Math.max(...rects.map((r) => r.x + r.w));
  const maxY = Math.max(...rects.map((r) => r.y + r.h));
  const bw = Math.max(maxX - minX, 1);
  const bh = Math.max(maxY - minY, 1);
  const k = clampZoom(
    Math.min((width - padding * 2) / bw, (height - padding * 2) / bh, 1.1)
  );
  return {
    k,
    x: (width - bw * k) / 2 - minX * k,
    y: (height - bh * k) / 2 - minY * k,
  };
}

/** Zoom by `factor` keeping the screen point (px, py) fixed. */
export function zoomAt(view: Viewport, factor: number, px: number, py: number): Viewport {
  const k = clampZoom(view.k * factor);
  const ratio = k / view.k;
  return { k, x: px - (px - view.x) * ratio, y: py - (py - view.y) * ratio };
}

// =============================================================== canvas (Phase 1)
//
// "Canvas" = the tables the user chose to look at. Adding or removing a card
// changes only this visual workspace. It never touches the database, the
// catalog metadata, or a declared foreign key.

/** Largest schema that is placed on the canvas automatically. */
export const AUTO_CANVAS_LIMIT = 30;

/** Default canvas: every table for a small schema, empty for a big one. */
export function defaultCanvas(tableNames: string[], limit: number = AUTO_CANVAS_LIMIT): string[] {
  return tableNames.length <= limit ? [...tableNames] : [];
}

/** Add a table. Unknown names and duplicates are ignored. Returns a new array. */
export function addToCanvas(canvas: string[], name: string, known: ReadonlySet<string>): string[] {
  if (!known.has(name) || canvas.includes(name)) return canvas;
  return [...canvas, name];
}

export function removeFromCanvas(canvas: string[], name: string): string[] {
  return canvas.filter((n) => n !== name);
}

/**
 * Tables that a card on the canvas is related to (declared FK) but that are not
 * on the canvas. Sorted, no duplicates, self-references ignored.
 */
export function offCanvasNeighbours(
  table: string,
  edges: Pick<MapEdge, "childTable" | "parentTable">[],
  canvas: ReadonlySet<string>
): string[] {
  const out = new Set<string>();
  for (const e of edges) {
    if (e.childTable === table && e.parentTable !== table && !canvas.has(e.parentTable)) {
      out.add(e.parentTable);
    }
    if (e.parentTable === table && e.childTable !== table && !canvas.has(e.childTable)) {
      out.add(e.childTable);
    }
  }
  return [...out].sort((a, b) => a.localeCompare(b));
}

/**
 * First free spot for a new card, so it never lands on top of another one.
 * Starts to the right of `near` (a related card) when given, otherwise to the
 * right of everything; scans downward, then moves one column further right.
 */
export function findFreePosition(
  rects: Rect[],
  width: number,
  height: number,
  near: Rect | null = null,
  gap: number = 40
): Point {
  const free = (x: number, y: number) =>
    rects.every(
      (r) =>
        !(x < r.x + r.w + gap && r.x < x + width + gap && y < r.y + r.h + gap && r.y < y + height + gap)
    );
  let startX = 0;
  let startY = 0;
  if (near) {
    startX = near.x + near.w + 140;
    startY = near.y;
  } else if (rects.length) {
    startX = Math.max(...rects.map((r) => r.x + r.w)) + gap * 2;
    startY = Math.min(...rects.map((r) => r.y));
  }
  const step = Math.max(40, Math.round((height + gap) / 2));
  for (let col = 0; col < 40; col += 1) {
    const x = startX + col * (width + gap);
    for (let k = 0; k < 80; k += 1) {
      const y = startY + k * step;
      if (free(x, y)) return { x, y };
    }
  }
  return { x: startX, y: startY };
}

// ============================================================ workspace joins

export const JOIN_TYPES = ["INNER", "LEFT", "RIGHT", "FULL"] as const;
export type JoinType = (typeof JOIN_TYPES)[number];

export interface JoinCondition {
  leftColumn: string;
  rightColumn: string;
}

/**
 * A join the user defined in this workspace. It is NOT a database constraint
 * and never changes one. The left table is the "from" side of the join.
 */
export interface WorkspaceJoin {
  id: string;
  leftTable: string;
  rightTable: string;
  joinType: JoinType;
  conditions: JoinCondition[];
}

export type JoinBasis = "declared" | "partly-declared" | "manual";

/**
 * Is a workspace join backed by real foreign keys?
 *   declared        every condition equals a declared FK column pair
 *   partly-declared some do, some do not
 *   manual          none do
 * A foreign key matches in either direction (child.col = parent.col).
 */
export function classifyJoin(
  join: Pick<WorkspaceJoin, "leftTable" | "rightTable" | "conditions">,
  declared: MapEdge[]
): { basis: JoinBasis; matchedEdgeIds: string[] } {
  const matched: string[] = [];
  let hits = 0;
  for (const c of join.conditions) {
    const edge = declared.find(
      (e) =>
        (e.childTable === join.leftTable &&
          e.parentTable === join.rightTable &&
          e.childColumn === c.leftColumn &&
          e.parentColumn === c.rightColumn) ||
        (e.parentTable === join.leftTable &&
          e.childTable === join.rightTable &&
          e.parentColumn === c.leftColumn &&
          e.childColumn === c.rightColumn)
    );
    if (edge) {
      hits += 1;
      if (!matched.includes(edge.id)) matched.push(edge.id);
    }
  }
  let basis: JoinBasis = "manual";
  if (join.conditions.length && hits === join.conditions.length) basis = "declared";
  else if (hits > 0) basis = "partly-declared";
  return { basis, matchedEdgeIds: matched };
}

export interface JoinValidation {
  errors: string[];
  warnings: string[];
}

/**
 * Check a join against the catalog's real columns. Types that differ are a
 * warning, not an error (the database may still convert them).
 */
export function validateJoin(
  join: Pick<WorkspaceJoin, "leftTable" | "rightTable" | "joinType" | "conditions">,
  columnsByTable: ColumnLookup,
  knownTables: ReadonlySet<string>
): JoinValidation {
  const errors: string[] = [];
  const warnings: string[] = [];
  if (!knownTables.has(join.leftTable) || !knownTables.has(join.rightTable)) {
    errors.push("Both tables must exist in the catalog.");
  }
  if (join.leftTable === join.rightTable) errors.push("Choose two different tables.");
  if (!(JOIN_TYPES as readonly string[]).includes(join.joinType)) errors.push("Unknown join type.");
  if (!join.conditions.length) errors.push("Add at least one matching column pair.");

  const seen = new Set<string>();
  for (const c of join.conditions) {
    const key = `${c.leftColumn}\u0000${c.rightColumn}`;
    if (!c.leftColumn || !c.rightColumn) {
      errors.push("Every condition needs a column on both sides.");
      continue;
    }
    if (seen.has(key)) errors.push(`Duplicate condition ${c.leftColumn} = ${c.rightColumn}.`);
    seen.add(key);
    const left = columnsByTable[join.leftTable]?.find((x) => x.column_name === c.leftColumn);
    const right = columnsByTable[join.rightTable]?.find((x) => x.column_name === c.rightColumn);
    if (columnsByTable[join.leftTable] && !left) {
      errors.push(`${join.leftTable}.${c.leftColumn} is not in the catalog.`);
    }
    if (columnsByTable[join.rightTable] && !right) {
      errors.push(`${join.rightTable}.${c.rightColumn} is not in the catalog.`);
    }
    if (left && right && left.data_type.toLowerCase() !== right.data_type.toLowerCase()) {
      warnings.push(
        `${c.leftColumn} (${left.data_type}) and ${c.rightColumn} (${right.data_type}) have different types.`
      );
    }
  }
  return { errors, warnings };
}

/** Short pill text for a workspace join line, e.g. "LEFT" or "INNER · 2". */
export function joinLabel(join: Pick<WorkspaceJoin, "joinType" | "conditions">): string {
  return join.conditions.length > 1 ? `${join.joinType} · ${join.conditions.length}` : join.joinType;
}
