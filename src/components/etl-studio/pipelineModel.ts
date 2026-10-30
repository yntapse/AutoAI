/**
 * Pure logic for the Visual ETL Pipeline Builder.
 *
 * No React and no network calls live here, so everything can be tested on its
 * own. A pipeline is a set of nodes and the connections between them. Moving,
 * connecting or deleting a node only changes this in-memory picture; nothing
 * reaches a database until the person presses Validate or Preview.
 *
 * The server is the authority on whether a pipeline is valid (it checks against
 * the catalog and builds the SQL). The checks here exist so the canvas can
 * refuse an impossible connection immediately and show what is out of date.
 */

// ------------------------------------------------------------------- types

export type NodeType =
  | "source"
  | "join"
  | "filter"
  | "drop_columns"
  | "rename_columns"
  | "remove_duplicates"
  | "handle_missing"
  | "aggregate"
  | "change_type"
  | "derive_column"
  | "output";

export const NODE_TYPES: readonly NodeType[] = [
  "source",
  "join",
  "filter",
  "drop_columns",
  "rename_columns",
  "remove_duplicates",
  "handle_missing",
  "aggregate",
  "change_type",
  "derive_column",
  "output",
];

/** Transformation node types, in the order the palette shows them. */
export const TRANSFORM_TYPES: readonly NodeType[] = [
  "filter",
  "drop_columns",
  "rename_columns",
  "remove_duplicates",
  "handle_missing",
  "aggregate",
  "change_type",
  "derive_column",
];

export interface NodeMeta {
  label: string;
  hint: string;
  /** Input ports, in display order. A source has none. */
  ports: readonly string[];
}

export const NODE_META: Record<NodeType, NodeMeta> = {
  source: { label: "Source table", hint: "A table from the Catalog", ports: [] },
  join: { label: "Join", hint: "Combine two datasets on matching columns", ports: ["left", "right"] },
  filter: { label: "Filter rows", hint: "Keep only rows that meet conditions", ports: ["in"] },
  drop_columns: { label: "Remove columns", hint: "Leave selected columns out", ports: ["in"] },
  rename_columns: { label: "Rename columns", hint: "Give columns new names", ports: ["in"] },
  remove_duplicates: { label: "Remove duplicates", hint: "Keep one row per duplicate group", ports: ["in"] },
  handle_missing: { label: "Handle missing values", hint: "Drop rows or fill empty values", ports: ["in"] },
  aggregate: { label: "Group & summarise", hint: "Sum, average, count, min or max per group", ports: ["in"] },
  change_type: { label: "Change column type", hint: "Convert columns to number, decimal, text or date", ports: ["in"] },
  derive_column: { label: "Calculate new column", hint: "Add, subtract, multiply or divide to make a new column", ports: ["in"] },
  output: { label: "Output dataset", hint: "The result of the pipeline", ports: ["in"] },
};

export type Config = Record<string, unknown>;

export interface PipelineNode {
  id: string;
  type: NodeType;
  x: number;
  y: number;
  config: Config;
}

export interface PipelineEdge {
  source: string;
  target: string;
  port: string;
}

/** What the server accepts. Positions never leave the browser. */
export interface GraphPayload {
  nodes: { id: string; type: NodeType; config: Config }[];
  edges: PipelineEdge[];
}

export const MAX_NODES = 60;

// ---------------------------------------------------------- column helpers

export type TypeCategory = "numeric" | "text" | "datetime" | "boolean" | "other";

/** Same rules as the server's type_category, so the UI and API agree. */
export function typeCategory(dataType: string): TypeCategory {
  const t = (dataType || "").trim().toLowerCase();
  if (
    ["datetime", "timestamp", "date", "time"].some((k) => t.includes(k)) &&
    !t.includes("interval")
  ) {
    return "datetime";
  }
  if (t === "bit" || t === "bool" || t === "boolean") return "boolean";
  if (["int", "decimal", "numeric", "float", "real", "double", "money", "serial"].some((k) => t.includes(k))) {
    return "numeric";
  }
  if (["char", "text", "string", "clob"].some((k) => t.includes(k))) return "text";
  return "other";
}

const NUMERIC_OPS = ["eq", "ne", "gt", "gte", "lt", "lte"];
const TEXT_OPS = ["eq", "ne", "contains", "starts_with", "ends_with"];
const NULL_OPS = ["is_null", "is_not_null"];

/** Filter operators that make sense for a column type (mirrors the server). */
export function operatorsFor(category: string): string[] {
  if (category === "numeric" || category === "datetime") return [...NUMERIC_OPS, ...NULL_OPS];
  if (category === "text") return [...TEXT_OPS, ...NULL_OPS];
  if (category === "boolean") return ["eq", "ne", ...NULL_OPS];
  return [...NULL_OPS];
}

export const OPERATOR_LABELS: Record<string, string> = {
  eq: "equals",
  ne: "does not equal",
  gt: "is greater than",
  gte: "is at least",
  lt: "is less than",
  lte: "is at most",
  contains: "contains",
  starts_with: "starts with",
  ends_with: "ends with",
  is_null: "is empty",
  is_not_null: "is not empty",
};

export function operatorNeedsValue(operator: string): boolean {
  return !NULL_OPS.includes(operator);
}

// ------------------------------------------------------------- node factory

export function defaultConfig(type: NodeType): Config {
  switch (type) {
    case "source":
      return { table_id: null };
    case "join":
      return { join_type: "INNER", conditions: [] };
    case "filter":
      return { combine: "AND", conditions: [] };
    case "drop_columns":
      return { columns: [] };
    case "rename_columns":
      return { mappings: [] };
    case "remove_duplicates":
      return { columns: [] };
    case "handle_missing":
      return { strategy: "drop_rows", columns: [], value: "" };
    case "aggregate":
      return { group_by: [], measures: [] };
    case "change_type":
      return { mappings: [] };
    case "derive_column":
      return { name: "", operation: "multiply", left: { column: "" }, right: { value: "" } };
    case "output":
      return { name: "" };
  }
}

export function newNodeId(nodes: readonly PipelineNode[], type: NodeType): string {
  const taken = new Set(nodes.map((n) => n.id));
  const base = type === "drop_columns" ? "drop" : type === "rename_columns" ? "rename" : type === "remove_duplicates" ? "dedupe" : type === "handle_missing" ? "missing" : type === "change_type" ? "convert" : type === "derive_column" ? "calc" : type;
  for (let i = 1; i < 1000; i += 1) {
    const id = `${base}_${i}`;
    if (!taken.has(id)) return id;
  }
  return `${base}_${Date.now()}`;
}

export function makeNode(
  nodes: readonly PipelineNode[],
  type: NodeType,
  x: number,
  y: number,
  config?: Config
): PipelineNode {
  return { id: newNodeId(nodes, type), type, x, y, config: config ?? defaultConfig(type) };
}

// ------------------------------------------------------------ display text

/** Row count wording that never hides whether the number is exact. */
export function formatRowCount(count: number | null | undefined, isEstimate: boolean | undefined): string {
  if (count === null || count === undefined) return "Row count unknown";
  const n = count.toLocaleString("en-US");
  return isEstimate === false ? `${n} rows (exact)` : `about ${n} rows (estimate)`;
}

function list(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

/** One short line describing a node's settings, for the card on the canvas. */
export function nodeSummary(node: PipelineNode, tableLabel?: string): string {
  const c = node.config;
  switch (node.type) {
    case "source":
      return tableLabel ?? "Choose a table";
    case "join": {
      const n = Array.isArray(c.conditions) ? c.conditions.length : 0;
      return n === 0 ? "No matching columns yet" : `${String(c.join_type ?? "INNER")} join · ${n} condition${n === 1 ? "" : "s"}`;
    }
    case "filter": {
      const n = Array.isArray(c.conditions) ? c.conditions.length : 0;
      return n === 0 ? "No conditions yet" : `${n} condition${n === 1 ? "" : "s"} (${String(c.combine ?? "AND")})`;
    }
    case "drop_columns": {
      const n = list(c.columns).length;
      return n === 0 ? "No columns chosen" : `Remove ${n} column${n === 1 ? "" : "s"}`;
    }
    case "rename_columns": {
      const n = Array.isArray(c.mappings) ? c.mappings.length : 0;
      return n === 0 ? "No renames yet" : `${n} rename${n === 1 ? "" : "s"}`;
    }
    case "remove_duplicates": {
      const n = list(c.columns).length;
      return n === 0 ? "Compare every column" : `Compare ${n} column${n === 1 ? "" : "s"}`;
    }
    case "handle_missing": {
      const n = list(c.columns).length;
      const how = c.strategy === "fixed" ? "fill with value" : c.strategy === "mean" ? "fill with mean" : "drop rows";
      return n === 0 ? "No columns chosen" : `${how} · ${n} column${n === 1 ? "" : "s"}`;
    }
    case "aggregate": {
      const n = Array.isArray(c.measures) ? c.measures.length : 0;
      const g = list(c.group_by).length;
      if (n === 0) return "No measures yet";
      return `${n} measure${n === 1 ? "" : "s"} · ${g === 0 ? "one row overall" : `by ${g} column${g === 1 ? "" : "s"}`}`;
    }
    case "change_type": {
      const n = Array.isArray(c.mappings) ? c.mappings.length : 0;
      return n === 0 ? "No conversions yet" : `${n} conversion${n === 1 ? "" : "s"}`;
    }
    case "derive_column":
      return typeof c.name === "string" && c.name ? `New column: ${c.name}` : "No new column yet";
    case "output":
      return typeof c.name === "string" && c.name ? c.name : "Unnamed dataset";
  }
}

// -------------------------------------------------------------- connections

export function inputPorts(type: NodeType): readonly string[] {
  return NODE_META[type].ports;
}

/**
 * Why a connection is not allowed, or null when it is. The server runs the same
 * rules again; this lets the canvas refuse a bad drop straight away.
 */
export function connectionProblem(
  nodes: readonly PipelineNode[],
  edges: readonly PipelineEdge[],
  source: string,
  target: string,
  port: string
): string | null {
  const from = nodes.find((n) => n.id === source);
  const to = nodes.find((n) => n.id === target);
  if (!from || !to) return "Both nodes must exist.";
  if (source === target) return "A node cannot connect to itself.";
  if (from.type === "output") return "An output node has nothing after it.";
  const ports = inputPorts(to.type);
  if (ports.length === 0) return "A source node takes no input.";
  if (!ports.includes(port)) return `This node's inputs are: ${ports.join(", ")}.`;
  if (edges.some((e) => e.target === target && e.port === port)) {
    return "That input is already connected. Remove the existing connection first.";
  }
  const forward = new Map<string, string[]>();
  for (const e of edges) forward.set(e.source, [...(forward.get(e.source) ?? []), e.target]);
  const stack = [target];
  const seen = new Set<string>();
  while (stack.length) {
    const cur = stack.pop() as string;
    if (cur === source) return "That connection would create a loop.";
    if (seen.has(cur)) continue;
    seen.add(cur);
    stack.push(...(forward.get(cur) ?? []));
  }
  return null;
}

export function addEdge(
  nodes: readonly PipelineNode[],
  edges: readonly PipelineEdge[],
  edge: PipelineEdge
): { edges: PipelineEdge[]; problem: string | null } {
  const problem = connectionProblem(nodes, edges, edge.source, edge.target, edge.port);
  return problem ? { edges: [...edges], problem } : { edges: [...edges, edge], problem: null };
}

export function removeEdge(edges: readonly PipelineEdge[], edge: PipelineEdge): PipelineEdge[] {
  return edges.filter(
    (e) => !(e.source === edge.source && e.target === edge.target && e.port === edge.port)
  );
}

/** Removing a node removes its connections too. Nothing else is touched. */
export function removeNode(
  nodes: readonly PipelineNode[],
  edges: readonly PipelineEdge[],
  id: string
): { nodes: PipelineNode[]; edges: PipelineEdge[] } {
  return {
    nodes: nodes.filter((n) => n.id !== id),
    edges: edges.filter((e) => e.source !== id && e.target !== id),
  };
}

/** Input node ids by port for one node. */
export function inputsOf(edges: readonly PipelineEdge[], id: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const e of edges) if (e.target === id) out[e.port] = e.source;
  return out;
}

/** Every node that sits after `id`, in no particular order. */
export function downstreamOf(edges: readonly PipelineEdge[], id: string): string[] {
  const seen = new Set<string>();
  const stack = [id];
  while (stack.length) {
    const cur = stack.pop() as string;
    for (const e of edges) {
      if (e.source === cur && !seen.has(e.target)) {
        seen.add(e.target);
        stack.push(e.target);
      }
    }
  }
  return [...seen];
}

export function toPayload(nodes: readonly PipelineNode[], edges: readonly PipelineEdge[]): GraphPayload {
  return {
    nodes: nodes.map((n) => ({ id: n.id, type: n.type, config: n.config })),
    edges: edges.map((e) => ({ source: e.source, target: e.target, port: e.port })),
  };
}

// ------------------------------------------------- "needs recalculation" logic

/**
 * A node's signature covers its own settings and everything upstream of it. If
 * the signature differs from the one saved when the node last ran, the earlier
 * result is out of date. Moving a node does not change it.
 */
export function effectiveSignatures(
  nodes: readonly PipelineNode[],
  edges: readonly PipelineEdge[]
): Record<string, string> {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const memo = new Map<string, string>();
  const visiting = new Set<string>();

  const sign = (id: string): string => {
    const cached = memo.get(id);
    if (cached !== undefined) return cached;
    const node = byId.get(id);
    if (!node) return "missing";
    if (visiting.has(id)) return "loop";
    visiting.add(id);
    const ins = edges
      .filter((e) => e.target === id)
      .sort((a, b) => a.port.localeCompare(b.port))
      .map((e) => `${e.port}=${sign(e.source)}`);
    visiting.delete(id);
    const text = `${node.type}:${stableStringify(node.config)}|${ins.join(",")}`;
    memo.set(id, `(${text})`);
    return `(${text})`;
  };

  const out: Record<string, string> = {};
  for (const n of nodes) out[n.id] = sign(n.id);
  return out;
}

export function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  const obj = value as Record<string, unknown>;
  return `{${Object.keys(obj)
    .sort()
    .map((k) => `${JSON.stringify(k)}:${stableStringify(obj[k])}`)
    .join(",")}}`;
}

export type RunState = "running" | "successful" | "failed";

export interface RunRecord {
  state: RunState;
  /** effectiveSignatures() value when the run started. */
  signature: string;
  /** Rows shown (at most the preview limit); not a table total. */
  rowsShown?: number;
  hasMore?: boolean;
  error?: string;
}

/** Node ids whose last successful or failed run no longer matches the settings. */
export function outdatedNodes(
  runs: Record<string, RunRecord | undefined>,
  signatures: Record<string, string>
): string[] {
  return Object.keys(runs).filter((id) => {
    const run = runs[id];
    return run !== undefined && run.state !== "running" && signatures[id] !== undefined && run.signature !== signatures[id];
  });
}

export type DisplayStatus =
  | "not_configured"
  | "invalid"
  | "blocked"
  | "ready"
  | "running"
  | "successful"
  | "failed";

export const STATUS_LABELS: Record<DisplayStatus, string> = {
  not_configured: "Not configured",
  invalid: "Has errors",
  blocked: "Waiting on an earlier step",
  ready: "Ready",
  running: "Running",
  successful: "Successful",
  failed: "Failed",
};

/**
 * What to show on a node. A validation problem wins over an old run result; a
 * run result counts only while its signature still matches.
 */
export function displayStatus(
  validation: string | undefined,
  run: RunRecord | undefined,
  signature: string | undefined
): { status: DisplayStatus; outdated: boolean } {
  if (run?.state === "running") return { status: "running", outdated: false };
  const v = (validation ?? "not_configured") as DisplayStatus;
  const known: DisplayStatus[] = ["not_configured", "invalid", "blocked", "ready"];
  const base: DisplayStatus = known.includes(v) ? v : "not_configured";
  if (base !== "ready" || !run) return { status: base, outdated: false };
  const outdated = signature !== undefined && run.signature !== signature;
  if (outdated) return { status: "ready", outdated: true };
  return { status: run.state === "failed" ? "failed" : "successful", outdated: false };
}

// ------------------------------------------------------------------- drafts
//
// A draft is saved in THIS browser only (localStorage). It is not shared, not
// backed up and not tied to a login. It holds names, ids, settings and
// positions, never a credential.

export const DRAFT_VERSION = 1;
const MAX_DRAFT_CHARS = 400_000;
const SECRET_KEY = /pass(word|wd)?|secret|token|credential|api[_-]?key|connection[_-]?string/i;

export interface PipelineDraft {
  version: number;
  sourceId: string;
  nodes: PipelineNode[];
  edges: PipelineEdge[];
}

export function draftStorageKey(sourceId: string): string {
  return `dios.etl-studio.draft.v1.${sourceId}`;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Copy a value, dropping any key that looks like a credential. */
export function stripSecrets(value: unknown, depth = 0): unknown {
  if (depth > 8) return null;
  if (Array.isArray(value)) return value.slice(0, 200).map((v) => stripSecrets(v, depth + 1));
  if (isPlainObject(value)) {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) {
      if (SECRET_KEY.test(k)) continue;
      out[k] = stripSecrets(v, depth + 1);
    }
    return out;
  }
  if (typeof value === "string") return value.slice(0, 2000);
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "boolean" || value === null) return value;
  return null;
}

export function serializeDraft(
  sourceId: string,
  nodes: readonly PipelineNode[],
  edges: readonly PipelineEdge[]
): string {
  const draft: PipelineDraft = {
    version: DRAFT_VERSION,
    sourceId,
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.type,
      x: Math.round(n.x),
      y: Math.round(n.y),
      config: stripSecrets(n.config) as Config,
    })),
    edges: edges.map((e) => ({ source: e.source, target: e.target, port: e.port })),
  };
  return JSON.stringify(draft);
}

/** Read a saved draft defensively. Anything unexpected is dropped, never trusted. */
export function parseDraft(text: string | null | undefined, sourceId: string): PipelineDraft | null {
  if (!text || text.length > MAX_DRAFT_CHARS) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return null;
  }
  if (!isPlainObject(raw) || raw.version !== DRAFT_VERSION || raw.sourceId !== sourceId) return null;
  if (!Array.isArray(raw.nodes) || !Array.isArray(raw.edges)) return null;

  const nodes: PipelineNode[] = [];
  const ids = new Set<string>();
  for (const item of raw.nodes.slice(0, MAX_NODES)) {
    if (!isPlainObject(item)) continue;
    const { id, type, x, y, config } = item;
    if (typeof id !== "string" || !/^[A-Za-z0-9_-]{1,40}$/.test(id) || ids.has(id)) continue;
    if (typeof type !== "string" || !NODE_TYPES.includes(type as NodeType)) continue;
    if (typeof x !== "number" || typeof y !== "number" || !Number.isFinite(x) || !Number.isFinite(y)) continue;
    const clean = isPlainObject(config) ? (stripSecrets(config) as Config) : defaultConfig(type as NodeType);
    ids.add(id);
    nodes.push({ id, type: type as NodeType, x, y, config: clean });
  }

  const edges: PipelineEdge[] = [];
  for (const item of raw.edges) {
    if (!isPlainObject(item)) continue;
    const { source, target, port } = item;
    if (typeof source !== "string" || typeof target !== "string" || typeof port !== "string") continue;
    // Re-apply the connection rules so a hand-edited draft cannot smuggle in a bad graph.
    if (connectionProblem(nodes, edges, source, target, port) === null) edges.push({ source, target, port });
  }
  return { version: DRAFT_VERSION, sourceId, nodes, edges };
}

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

function browserStorage(): StorageLike | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function saveDraft(
  sourceId: string,
  nodes: readonly PipelineNode[],
  edges: readonly PipelineEdge[],
  storage: StorageLike | null = browserStorage()
): boolean {
  if (!storage) return false;
  try {
    storage.setItem(draftStorageKey(sourceId), serializeDraft(sourceId, nodes, edges));
    return true;
  } catch {
    return false;
  }
}

export function loadDraft(
  sourceId: string,
  storage: StorageLike | null = browserStorage()
): PipelineDraft | null {
  if (!storage) return null;
  try {
    return parseDraft(storage.getItem(draftStorageKey(sourceId)), sourceId);
  } catch {
    return null;
  }
}

export function clearDraft(sourceId: string, storage: StorageLike | null = browserStorage()): void {
  try {
    storage?.removeItem(draftStorageKey(sourceId));
  } catch {
    /* nothing to do */
  }
}

// ------------------------------------------------- Catalog "Build Dataset"

export interface Spot {
  x: number;
  y: number;
}

/**
 * Add one Source node for each table that is not already on the canvas.
 * Nothing else is created: no joins, no filters, nothing is run. A table that
 * is already on the canvas is skipped, so opening the Builder twice with the
 * same selection never duplicates a node.
 */
export function prefillSourceNodes(
  existing: readonly PipelineNode[],
  tableIds: readonly string[],
  spotsFor: (count: number, occupied: readonly Spot[]) => Spot[],
  sourceId?: string
): { nodes: PipelineNode[]; added: string[]; alreadyThere: string[]; leftOut: string[] } {
  const onCanvas = new Set(
    existing.filter((n) => n.type === "source" && typeof n.config.table_id === "string").map((n) => n.config.table_id as string)
  );
  const fresh: string[] = [];
  const alreadyThere: string[] = [];
  for (const id of tableIds) {
    if (onCanvas.has(id) || fresh.includes(id)) {
      if (!fresh.includes(id)) alreadyThere.push(id);
      continue;
    }
    fresh.push(id);
  }
  const room = Math.max(0, MAX_NODES - existing.length);
  const take = fresh.slice(0, room);
  const leftOut = fresh.slice(room);
  const spots = spotsFor(take.length, existing);
  const nodes = [...existing];
  take.forEach((tableId, i) => {
    nodes.push(makeNode(nodes, "source", spots[i].x, spots[i].y, sourceId ? { table_id: tableId, source_id: sourceId } : { table_id: tableId }));
  });
  return { nodes, added: take, alreadyThere, leftOut };
}

export interface RelationshipLike {
  leftTableId: string;
  rightTableId: string;
  conditions: readonly { leftColumn: string; rightColumn: string }[];
}

/**
 * The id of a join node that already implements this relationship (same two
 * tables and the same column pairs, in either orientation), or null.
 */
export function findJoinForRelationship(
  nodes: readonly PipelineNode[],
  edges: readonly PipelineEdge[],
  rel: RelationshipLike
): string | null {
  const tableOf = (id: string | undefined): string | null => {
    const n = nodes.find((x) => x.id === id);
    return n && n.type === "source" && typeof n.config.table_id === "string" ? n.config.table_id : null;
  };
  const wanted = new Set(rel.conditions.map((c) => `${c.leftColumn}\u0000${c.rightColumn}`));
  const flipped = new Set(rel.conditions.map((c) => `${c.rightColumn}\u0000${c.leftColumn}`));
  for (const n of nodes) {
    if (n.type !== "join") continue;
    const inputs = inputsOf(edges, n.id);
    const left = tableOf(inputs.left);
    const right = tableOf(inputs.right);
    if (!left || !right) continue;
    const raw = Array.isArray(n.config.conditions) ? (n.config.conditions as unknown[]) : [];
    const have = new Set(
      raw
        .filter((c): c is Record<string, unknown> => typeof c === "object" && c !== null)
        .map((c) => `${String(c.left_column ?? "")}\u0000${String(c.right_column ?? "")}`)
    );
    const same = (a: Set<string>, b: Set<string>) => a.size === b.size && [...a].every((x) => b.has(x));
    if (left === rel.leftTableId && right === rel.rightTableId && same(have, wanted)) return n.id;
    if (left === rel.rightTableId && right === rel.leftTableId && same(have, flipped)) return n.id;
  }
  return null;
}

// ------------------------------------------------- tables from an earlier scan

/**
 * Every Catalog scan creates new table ids. A Source step saved before a re-scan
 * still points at the old id: the server still finds it, but the Catalog list no
 * longer shows it. `mapping` says which current table each old id corresponds to
 * (same schema and table name), decided by the caller from Catalog data.
 *
 * Only Source steps listed in `mapping` change, and only their table. Wiring and
 * every other setting stay. A step that would only duplicate a table already on
 * the canvas and is not connected to anything is removed instead of re-linked.
 */
export function relinkStaleTables(
  nodes: readonly PipelineNode[],
  edges: readonly PipelineEdge[],
  mapping: Readonly<Record<string, string>>
): { nodes: PipelineNode[]; edges: PipelineEdge[]; relinked: string[]; removed: string[] } {
  const wired = (id: string) => edges.some((e) => e.source === id || e.target === id);
  const isStale = (n: PipelineNode) => n.type === "source" && typeof n.config.table_id === "string" && mapping[n.config.table_id as string] !== undefined;
  const removed = new Set<string>();
  const relinked: string[] = [];
  const target = (n: PipelineNode) => mapping[n.config.table_id as string];

  for (const s of nodes.filter(isStale)) {
    const t = target(s);
    const others = nodes.filter((n) => n !== s && n.type === "source" && n.config.table_id === t && !removed.has(n.id));
    if (others.length === 0) continue;
    if (!wired(s.id)) removed.add(s.id);
    else for (const o of others) if (!wired(o.id)) removed.add(o.id);
  }
  const out = nodes
    .filter((n) => !removed.has(n.id))
    .map((n) => {
      if (!isStale(n)) return n;
      relinked.push(n.id);
      return { ...n, config: { ...n.config, table_id: target(n) } };
    });
  return { nodes: out, edges: edges.filter((e) => !removed.has(e.source) && !removed.has(e.target)), relinked, removed: [...removed] };
}

/** Apply positions from the layout. Nothing but x and y changes. */
export function applyPositions(nodes: readonly PipelineNode[], pos: Readonly<Record<string, { x: number; y: number }>>): PipelineNode[] {
  return nodes.map((n) => (pos[n.id] ? { ...n, x: pos[n.id].x, y: pos[n.id].y } : n));
}

// ---------------------------------------------------- previous-draft backup
//
// "Start a new pipeline" replaces the canvas. The draft it replaces is kept in a
// second slot (same rules as a draft: this browser only, no credentials) until the
// user restores or discards it.

export function draftBackupKey(sourceId: string): string {
  return `dios.etl-studio.draft-backup.v1.${sourceId}`;
}

export function saveDraftBackup(
  sourceId: string,
  nodes: readonly PipelineNode[],
  edges: readonly PipelineEdge[],
  storage: StorageLike | null = browserStorage()
): boolean {
  if (!storage) return false;
  try {
    storage.setItem(draftBackupKey(sourceId), serializeDraft(sourceId, nodes, edges));
    return true;
  } catch {
    return false;
  }
}

export function loadDraftBackup(sourceId: string, storage: StorageLike | null = browserStorage()): PipelineDraft | null {
  if (!storage) return null;
  try {
    return parseDraft(storage.getItem(draftBackupKey(sourceId)), sourceId);
  } catch {
    return null;
  }
}

export function clearDraftBackup(sourceId: string, storage: StorageLike | null = browserStorage()): void {
  try {
    storage?.removeItem(draftBackupKey(sourceId));
  } catch {
    /* nothing to do */
  }
}
