"use client";

/**
 * Visual ETL Pipeline Builder - the workspace.
 *
 * What runs where:
 *  - Moving, connecting, editing or deleting nodes changes only the picture in
 *    this browser (and the browser-local draft).
 *  - Validation asks the server to check the pipeline against the Catalog. The
 *    customer database is not contacted.
 *  - "Preview" and "Measure this join" each run one read-only, row-limited
 *    SELECT in the source database through the existing credential session.
 *  - Nothing here writes data, publishes a dataset or starts model training.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  connectSource,
  getOverview,
  getRelationships,
  getSessionStatus,
  getTableDetail,
  isSessionExpiredError,
  listSources,
  type CatalogOverview,
  type CatalogRelationship,
  type CatalogTableSummary,
  type CatalogTableDetail,
  type DataSourceSummary,
} from "@/services/catalogService";
import {
  getNodeSql,
  joinDiagnostics,
  previewNode,
  validatePipeline,
  type DiagnosticsResult,
  type PreviewResult,
  type SqlResult,
  type ValidateResult,
} from "@/services/etlStudioService";
import {
  checkAgainstCatalog,
  consumeHandoff,
  gridSpots,
  type ConsumeResult,
  type HandoffCheck,
  type HandoffPayload,
  type HandoffRelationship,
} from "@/components/catalog/datasetHandoff";
import GuidedStrip from "./GuidedStrip";
import { computeStages, currentStage, joinChecks, type JoinMeasurement, type StageId } from "./guidedStages";
import JoinSuggestionsPanel from "./JoinSuggestionsPanel";
import NodeConfigPanel from "./NodeConfigPanel";
import PreviewPanel from "./PreviewPanel";
import StudioCanvas, { NODE_H, NODE_W, type NodeView } from "./StudioCanvas";
import { layoutNodes } from "./layout";
import Notices, { type NoticeItem } from "./Notices";
import { suggestJoins, type DeclaredLink, type JoinKind, type JoinSuggestion, type SuggestTable, type UserRelationship } from "./joinSuggestions";
import {
  MAX_NODES,
  NODE_META,
  TRANSFORM_TYPES,
  addEdge,
  applyPositions,
  clearDraft,
  clearDraftBackup,
  displayStatus,
  effectiveSignatures,
  findJoinForRelationship,
  formatRowCount,
  inputsOf,
  loadDraft,
  loadDraftBackup,
  makeNode,
  nodeSummary,
  outdatedNodes,
  prefillSourceNodes,
  relinkStaleTables,
  removeEdge,
  removeNode,
  saveDraft,
  saveDraftBackup,
  toPayload,
  typeCategory,
  type Config,
  type GraphPayload,
  type NodeType,
  type PipelineEdge,
  type PipelineNode,
  type RunRecord,
} from "./pipelineModel";

type SessionState = "unknown" | "connected" | "needed" | "not_required";

// On wide screens the two side columns stay in view and scroll on their own, so a
// long table list or suggestion list never pushes the controls off screen. On
// narrower screens everything simply flows down the page (no nested scrollbars).
const SIDE_COLUMN = "xl:sticky xl:top-0 xl:max-h-[calc(100vh-7rem)] xl:self-start xl:overflow-y-auto xl:pr-1";

const btn = "rounded-md border border-white/[0.1] px-2.5 py-1 text-[12px] text-slate-300 hover:bg-white/[0.06] disabled:opacity-40";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}

/** First spot on a loose grid that does not sit on top of another node. */
function freeSpot(nodes: PipelineNode[], startX: number, startY: number): { x: number; y: number } {
  const clash = (x: number, y: number) =>
    nodes.some((n) => Math.abs(n.x - x) < NODE_W + 24 && Math.abs(n.y - y) < NODE_H + 36);
  const x = startX;
  let y = startY;
  for (let i = 0; i < 200 && clash(x, y); i += 1) y += NODE_H + 44;
  return { x, y };
}

// ------------------------------------------------ Catalog "Build Dataset"

interface HandoffRequest {
  sourceId: string | null;
  result: ConsumeResult | null;
}

type HandoffNotice = { tone: "info" | "warning" | "error"; text: string };

// A handoff note can be read only once. React may run an initializer twice in
// development, so the first answer for a key is remembered here (memory only,
// no secrets in it) rather than reading the note a second time.
const takenHandoffs = new Map<string, ConsumeResult>();

function readHandoffRequest(): HandoffRequest | null {
  if (typeof window === "undefined") return null;
  const params = new URLSearchParams(window.location.search);
  const key = params.get("handoff");
  const sourceId = params.get("source");
  if (!key && !sourceId) return null;
  if (!key) return { sourceId, result: null };
  let result = takenHandoffs.get(key);
  if (!result) {
    let storage: Storage | null = null;
    try {
      storage = window.localStorage;
    } catch {
      storage = null;
    }
    result = consumeHandoff(storage, key);
    takenHandoffs.set(key, result);
  }
  return { sourceId, result };
}

/** What happens to the canvas when Catalog tables are handed over. Pure: nothing is set or run here. */
function planHandoff(base: PipelineNode[], check: Pick<HandoffCheck, "tables" | "relationships" | "warning">, sourceId: string) {
  const out = prefillSourceNodes(base, check.tables.map((t) => t.id), (count, occupied) => gridSpots(count, occupied, NODE_W, NODE_H), sourceId);
  const names = check.tables.filter((t) => out.added.includes(t.id)).map((t) => t.tableName);
  const parts = [
    out.added.length
      ? `Opened with ${out.added.length} table${out.added.length === 1 ? "" : "s"} from the Data Catalog: ${names.join(", ")}. Each is a Source step on the canvas. Nothing has been joined, changed or run.`
      : "The tables you chose are already on the canvas. Nothing was added.",
  ];
  if (out.added.length && out.alreadyThere.length) parts.push(`${out.alreadyThere.length} more ${out.alreadyThere.length === 1 ? "was" : "were"} already on your canvas.`);
  if (out.leftOut.length) parts.push(`${out.leftOut.length} could not be added because a pipeline can have at most ${MAX_NODES} nodes.`);
  if (check.warning) parts.push(check.warning);
  const notice: HandoffNotice = { tone: check.warning || out.leftOut.length ? "warning" : "info", text: parts.join(" ") };
  return { nodes: out.nodes, relationships: check.relationships, showSuggestions: check.relationships.length > 0, notice };
}

const NOTICE_STYLE: Record<HandoffNotice["tone"], string> = {
  info: "border-cyan-500/20 bg-cyan-500/10 text-cyan-100",
  warning: "border-amber-500/25 bg-amber-500/10 text-amber-200",
  error: "border-rose-500/25 bg-rose-500/10 text-rose-200",
};

/** Names the exact pipeline state and measured numbers an acknowledgement belongs to. */
function fanOutKey(signature: string, d: DiagnosticsResult): string {
  const m = d.summary;
  return [signature, m.join_type, m.left_rows, m.right_rows, m.matched_pairs, m.output_rows, m.extra_rows_from_multiplication].join("|");
}

export default function EtlStudio() {
  const [sources, setSources] = useState<DataSourceSummary[]>([]);
  const [source, setSource] = useState<DataSourceSummary | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [overview, setOverview] = useState<CatalogOverview | null>(null);
  const [relationships, setRelationships] = useState<CatalogRelationship[]>([]);
  const [details, setDetails] = useState<Record<string, CatalogTableDetail>>({});

  const [nodes, setNodes] = useState<PipelineNode[]>([]);
  const [edges, setEdges] = useState<PipelineEdge[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draftReady, setDraftReady] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const [validation, setValidation] = useState<ValidateResult | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [runs, setRuns] = useState<Record<string, RunRecord>>({});
  const [previews, setPreviews] = useState<Record<string, PreviewResult>>({});
  const [sqlById, setSqlById] = useState<Record<string, { signature: string; sql: SqlResult }>>({});
  const [sqlError, setSqlError] = useState<string | null>(null);
  const [limit, setLimit] = useState(25);
  const [diagnostics, setDiagnostics] = useState<Record<string, DiagnosticsResult>>({});
  // The pipeline signature each measurement was taken under (so a changed pipeline marks it out of date).
  const [measuredAt, setMeasuredAt] = useState<Record<string, string>>({});
  // Repeated-rows acknowledgements: join id -> a key naming the exact configuration and measurement acknowledged.
  const [acks, setAcks] = useState<Record<string, string>>({});
  const [diagnosticsError, setDiagnosticsError] = useState<string | null>(null);
  const [measuring, setMeasuring] = useState(false);

  const [session, setSession] = useState<SessionState>("unknown");
  const [password, setPassword] = useState("");
  const [connecting, setConnecting] = useState(false);

  const [tableFilter, setTableFilter] = useState("");
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [rejected, setRejected] = useState<Set<string>>(new Set());
  // Guided workflow: which stage the user is looking at (null = follow the first one that needs work).
  const [pickedStage, setPickedStage] = useState<StageId | null>(null);
  const [guideCollapsed, setGuideCollapsed] = useState(false);

  const validationRequest = useRef(0);
  // Bumped every time the main source changes. Answers that arrive for an earlier source are ignored.
  const epochRef = useRef(0);

  // Tables of the other registered sources: Catalog metadata only, loaded when the user expands a source.
  const [otherOverviews, setOtherOverviews] = useState<Record<string, { state: "loading" | "ready" | "error"; tables: CatalogTableSummary[]; error?: string }>>({});
  const [openOthers, setOpenOthers] = useState<Record<string, boolean>>({});

  // Tables and relationships chosen in the Data Catalog (browser-local, read once).
  const [handoffRequest] = useState<HandoffRequest | null>(readHandoffRequest);
  const handoffApplied = useRef(false);
  const [handoffRels, setHandoffRels] = useState<HandoffRelationship[]>([]);
  const [handoffNotice, setHandoffNotice] = useState<HandoffNotice | null>(null);
  // Catalog tables waiting for the user's choice: a saved draft already exists, so nothing is added silently.
  const [pendingHandoff, setPendingHandoff] = useState<Pick<HandoffCheck, "tables" | "relationships" | "warning"> | null>(null);
  // Size of a previous draft kept by "Start a new pipeline" (null = none kept).
  const [backupSteps, setBackupSteps] = useState<number | null>(null);
  // Positions before the last "Tidy up", so it can be undone; and a counter that asks the canvas to fit the view.
  const [preTidy, setPreTidy] = useState<Record<string, { x: number; y: number }> | null>(null);
  const [fitToken, setFitToken] = useState(0);

  // The address only ever held the source id and an opaque key. Drop the key
  // (it is already used up) and keep the source id, so a refresh reopens the
  // same source and its draft instead of looking like a second attempt.
  useEffect(() => {
    if (!handoffRequest) return;
    const url = new URL(window.location.href);
    url.searchParams.delete("handoff");
    window.history.replaceState(null, "", url.pathname + url.search);
  }, [handoffRequest]);

  // ------------------------------------------------------------- loading
  useEffect(() => {
    let cancelled = false;
    async function run() {
      try {
        const rows = await listSources();
        if (!cancelled) setSources(rows);
      } catch (err) {
        if (!cancelled) setLoadError(errorText(err));
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, []);

  const openSource = useCallback(async (next: DataSourceSummary | null, handoff?: HandoffPayload | null) => {
    const epoch = (epochRef.current += 1);
    const stale = () => epochRef.current !== epoch;
    setSource(next);
    setOtherOverviews({});
    setOpenOthers({});
    setMeasuring(false);
    setDiagnosticsError(null);
    setSqlError(null);
    setHandoffRels([]);
    setHandoffNotice(null);
    setPendingHandoff(null);
    setPreTidy(null);
    setBackupSteps(next ? (loadDraftBackup(next.id)?.nodes.length ?? null) : null);
    setOverview(null);
    setRelationships([]);
    setDetails({});
    setValidation(null);
    setValidationError(null);
    setRuns({});
    setPreviews({});
    setSqlById({});
    setDiagnostics({});
    setMeasuredAt({});
    setAcks({});
    setRejected(new Set());
    setPickedStage(null);
    setSelectedId(null);
    setLoadError(null);
    setDraftReady(false);
    setSession("unknown");
    if (!next) {
      setNodes([]);
      setEdges([]);
      setNotice(null);
      return;
    }
    const draft = loadDraft(next.id);
    const base = draft?.nodes ?? [];
    setNodes(base);
    setEdges(draft?.edges ?? []);
    setNotice(draft && draft.nodes.length ? "Restored your draft. It is stored in this browser only." : null);
    setDraftReady(true);

    try {
      const [ov, rel] = await Promise.all([getOverview(next.id), getRelationships(next.id)]);
      if (stale()) return;
      setOverview(ov);
      setRelationships(rel);
      if (handoff) {
        // The note is only a request: the current Catalog decides what is real.
        const check = checkAgainstCatalog(handoff, next.id, ov.tables);
        if (check.problem) {
          setHandoffNotice({ tone: "error", text: check.problem });
        } else {
          const fresh = check.tables.some((t) => !base.some((n) => n.type === "source" && n.config.table_id === t.id));
          if (base.length > 0 && fresh) {
            // A saved draft exists: ask before touching it.
            setPendingHandoff(check);
          } else {
            const plan = planHandoff(base, check, next.id);
            setNodes(plan.nodes);
            setHandoffRels(plan.relationships);
            if (plan.showSuggestions) setShowSuggestions(true);
            setHandoffNotice(plan.notice);
          }
        }
      }
    } catch (err) {
      if (stale()) return;
      setLoadError(errorText(err));
      if (handoff) setHandoffNotice({ tone: "error", text: "The Catalog could not be loaded, so the table selection could not be checked. Choose tables below instead." });
    }
    if (stale()) return;
    if (next.trusted_connection) {
      setSession("not_required");
    } else {
      try {
        const status = await getSessionStatus(next.id);
        if (!stale()) setSession(status.connected ? "connected" : "needed");
      } catch {
        if (!stale()) setSession("needed");
      }
    }
  }, []);

  const requestedSourceMissing = Boolean(
    handoffRequest?.sourceId && sources.length > 0 && !source && !sources.some((s) => s.id === handoffRequest.sourceId)
  );

  // Open the source named in the address once the list of sources is known. If
  // the note is missing, expired or unreadable the Builder still opens that
  // source, so the existing "add tables" flow keeps working.
  useEffect(() => {
    if (!handoffRequest || handoffApplied.current || sources.length === 0) return;
    handoffApplied.current = true;
    const target = sources.find((s) => s.id === handoffRequest.sourceId) ?? null;
    if (!target) return; // reported by `requestedSourceMissing` below
    const result = handoffRequest.result;
    // Deferred one tick: opening a source sets several pieces of state.
    queueMicrotask(() => {
      void openSource(target, result && result.ok ? result.payload : null).then(() => {
        if (result && !result.ok) setHandoffNotice({ tone: "error", text: result.message });
      });
    });
  }, [handoffRequest, sources, openSource]);

  // Keep a browser-local copy of the draft. Never contains a credential.
  useEffect(() => {
    if (!source || !draftReady) return undefined;
    const timer = setTimeout(() => {
      // An empty canvas has nothing worth keeping; this also keeps "Clear canvas" final.
      if (nodes.length === 0) clearDraft(source.id);
      else saveDraft(source.id, nodes, edges);
    }, 300);
    return () => clearTimeout(timer);
  }, [source, draftReady, nodes, edges]);

  // Check the pipeline against the Catalog shortly after a change to its steps or
  // connections. The key leaves out node positions, so dragging a node never
  // triggers a check.
  const graphKey = useMemo(() => JSON.stringify(toPayload(nodes, edges)), [nodes, edges]);
  useEffect(() => {
    if (!source) return undefined;
    const ticket = (validationRequest.current += 1);
    const timer = setTimeout(async () => {
      const graph = JSON.parse(graphKey) as GraphPayload;
      if (graph.nodes.length === 0) {
        setValidation(null);
        setValidationError(null);
        return;
      }
      try {
        const result = await validatePipeline(source.id, graph);
        if (ticket === validationRequest.current) {
          setValidation(result);
          setValidationError(null);
        }
      } catch (err) {
        if (ticket === validationRequest.current) setValidationError(errorText(err));
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [source, graphKey]);

  // Column details for the tables on the canvas (key flags feed the suggestions).
  const canvasTableIds = useMemo(
    () => [...new Set(nodes.filter((n) => n.type === "source" && typeof n.config.table_id === "string").map((n) => n.config.table_id as string))],
    [nodes]
  );
  useEffect(() => {
    const missing = canvasTableIds.filter((id) => !details[id]);
    if (missing.length === 0) return undefined;
    let cancelled = false;
    async function run() {
      for (const id of missing) {
        try {
          const detail = await getTableDetail(id);
          if (!cancelled) setDetails((d) => ({ ...d, [id]: detail }));
        } catch {
          /* an unknown table shows up as a validation error on its node */
        }
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, [canvasTableIds, details]);

  // ----------------------------------------------------------- derived data
  // Every signature starts with the main source's id, so a preview, measurement, SQL text or
  // acknowledgement made under another main source can never match the current pipeline.
  const signatures = useMemo(() => {
    const base = effectiveSignatures(nodes, edges);
    const prefix = source?.id ?? "";
    return Object.fromEntries(Object.entries(base).map(([id, sig]) => [id, `${prefix}|${sig}`])) as typeof base;
  }, [nodes, edges, source]);
  const results = useMemo(() => {
    const map: Record<string, ValidateResult["nodes"][number] | undefined> = {};
    for (const n of validation?.nodes ?? []) map[n.node_id] = n;
    return map;
  }, [validation]);
  const tables = useMemo(() => overview?.tables ?? [], [overview]);
  // Which data source owns each known table. Main-source tables first; other sources only once expanded.
  const tableSource = useMemo(() => {
    const map = new Map<string, string>();
    for (const [sid, o] of Object.entries(otherOverviews)) for (const t of o.tables) map.set(t.id, sid);
    for (const t of tables) if (source) map.set(t.id, source.id);
    return map;
  }, [otherOverviews, tables, source]);
  const otherTables = useMemo(() => Object.values(otherOverviews).flatMap((o) => o.tables), [otherOverviews]);
  const tableById = useMemo(() => new Map([...otherTables, ...tables].map((t) => [t.id, t])), [tables, otherTables]);
  const sourceLabel = useCallback(
    (id: string) => {
      const s = sources.find((x) => x.id === id);
      return s ? `${s.source_name} · ${s.db_type}` : "Unknown data source";
    },
    [sources]
  );
  /** The data source a Source step reads from. Steps without a recorded source read from the main source. */
  const nodeSourceId = useCallback(
    (n: PipelineNode) => (typeof n.config.source_id === "string" && n.config.source_id ? n.config.source_id : source?.id ?? ""),
    [source]
  );
  const outdated = useMemo(() => new Set(outdatedNodes(runs, signatures)), [runs, signatures]);

  // A table the current Catalog list no longer shows (an earlier scan) is still named from its own details.
  const lookupTable = useCallback((id: string) => tableById.get(id) ?? details[id], [tableById, details]);

  // Source steps of the main source that point at a table of an EARLIER Catalog scan.
  const staleScan = useMemo(() => {
    const out = { ids: [] as string[], mapping: {} as Record<string, string>, gone: [] as string[] };
    if (!overview || !source) return out;
    const currentIds = new Set(tables.map((t) => t.id));
    const byName = new Map(tables.map((t) => [`${t.schema_name}.${t.table_name}`.toLowerCase(), t.id]));
    for (const n of nodes) {
      const tid = n.type === "source" && typeof n.config.table_id === "string" ? n.config.table_id : "";
      if (!tid || currentIds.has(tid)) continue;
      if (typeof n.config.source_id === "string" && n.config.source_id && n.config.source_id !== source.id) continue;
      const d = details[tid];
      if (!d) continue;
      out.ids.push(n.id);
      const now = byName.get(`${d.schema_name}.${d.table_name}`.toLowerCase());
      if (now) out.mapping[tid] = now;
      else out.gone.push(`${d.schema_name}.${d.table_name}`);
    }
    return out;
  }, [overview, source, tables, nodes, details]);

  const nodeLabel = useCallback(
    (id: string) => {
      const n = nodes.find((x) => x.id === id);
      if (!n) return id;
      if (n.type === "source") {
        const t = lookupTable(String(n.config.table_id ?? ""));
        return t ? `${t.table_name} (${id})` : id;
      }
      return `${NODE_META[n.type].label} (${id})`;
    },
    [nodes, lookupTable]
  );

  const views = useMemo(() => {
    const out: Record<string, NodeView> = {};
    for (const n of nodes) {
      const res = results[n.id];
      const t = n.type === "source" ? lookupTable(String(n.config.table_id ?? "")) : undefined;
      const st = displayStatus(res?.status, runs[n.id], signatures[n.id]);
      let badge: string | undefined;
      let badgeIsMain = true;
      if (n.type === "source" && source) {
        const sid = nodeSourceId(n);
        badgeIsMain = sid === source.id;
        badge = `${badgeIsMain ? "Source" : "Other source"}: ${sourceLabel(sid)}`;
      } else if (source) {
        const others = (res?.source_ids ?? []).filter((sid) => sid !== source.id);
        if (others.length) {
          badgeIsMain = false;
          badge = `Other source: ${others.map(sourceLabel).join(", ")}`;
        }
      }
      out[n.id] = {
        badge,
        badgeIsMain,
        title: t ? t.table_name : NODE_META[n.type].label,
        subtitle: nodeSummary(n, t ? `${t.schema_name}.${t.table_name}` : undefined),
        detail: t
          ? `${formatRowCount(t.row_count, t.row_count_is_estimate)}${staleScan.ids.includes(n.id) ? " · earlier scan" : ""}`
          : res?.columns
            ? `${res.columns.length} columns`
            : undefined,
        status: st.status,
        outdated: st.outdated,
      };
    }
    return out;
  }, [nodes, results, runs, signatures, lookupTable, staleScan, source, nodeSourceId, sourceLabel]);

  const selected = nodes.find((n) => n.id === selectedId) ?? null;

  const suggestTables: SuggestTable[] = useMemo(
    () =>
      canvasTableIds
        .map((id) => details[id])
        .filter((d): d is CatalogTableDetail => Boolean(d))
        .map((d) => ({
          id: d.id,
          name: d.table_name,
          columns: d.columns.map((c) => ({
            name: c.column_name,
            dataType: c.data_type,
            category: typeCategory(c.data_type),
            isPrimaryKey: c.is_primary_key,
            isForeignKey: c.is_foreign_key,
          })),
        })),
    [canvasTableIds, details]
  );
  const suggestions = useMemo(() => {
    const links: DeclaredLink[] = relationships
      .filter((r) => r.origin === "declared")
      .map((r) => ({
        childTable: r.left_table,
        childColumn: r.left_column,
        parentTable: r.right_table,
        parentColumn: r.right_column,
        constraintName: r.constraint_name,
      }));
    // Joins drawn on the Catalog's Relationship Map join the same list. Declared
    // ones are already in `relationships`, so only the user-defined ones are added.
    const mine: UserRelationship[] = handoffRels
      .filter((r) => r.kind === "workspace")
      .map((r) => ({
        leftTableId: r.leftTableId,
        rightTableId: r.rightTableId,
        pairs: r.conditions.map((c) => ({ leftColumn: c.leftColumn, rightColumn: c.rightColumn })),
        joinType: r.joinType,
      }));
    // Tables of different data sources are never suggested as a join: they cannot be run together.
    return suggestJoins(suggestTables, links, mine).filter((s) => tableSource.get(s.leftTableId) === tableSource.get(s.rightTableId));
  }, [suggestTables, relationships, handoffRels, tableSource]);
  // A suggestion disappears once the canvas already holds that join, however it got there.
  const visibleSuggestions = suggestions.filter(
    (s) =>
      !rejected.has(s.id) &&
      !findJoinForRelationship(nodes, edges, { leftTableId: s.leftTableId, rightTableId: s.rightTableId, conditions: s.pairs })
  );
  const tableNameById = useCallback((id: string) => details[id]?.table_name ?? tableById.get(id)?.table_name ?? id, [details, tableById]);

  // --------------------------------------------------------------- editing
  function addTable(tableId: string, sourceId?: string) {
    if (!source) return;
    if (nodes.length >= MAX_NODES) return setNotice(`A pipeline can have at most ${MAX_NODES} nodes.`);
    const sourceCount = nodes.filter((n) => n.type === "source").length;
    const spot = freeSpot(nodes, 40, 40 + (sourceCount % 4) * (NODE_H + 44));
    const node = makeNode(nodes, "source", spot.x, spot.y, { table_id: tableId, source_id: sourceId ?? source.id });
    setNodes([...nodes, node]);
    setSelectedId(node.id);
  }

  function toggleOtherSource(id: string) {
    const opening = !openOthers[id];
    setOpenOthers((o) => ({ ...o, [id]: opening }));
    if (!opening || otherOverviews[id]?.state === "ready" || otherOverviews[id]?.state === "loading") return;
    const epoch = epochRef.current;
    setOtherOverviews((o) => ({ ...o, [id]: { state: "loading", tables: [] } }));
    getOverview(id)
      .then((ov) => {
        if (epochRef.current === epoch) setOtherOverviews((o) => ({ ...o, [id]: { state: "ready", tables: ov.tables } }));
      })
      .catch((err) => {
        if (epochRef.current === epoch) setOtherOverviews((o) => ({ ...o, [id]: { state: "error", tables: [], error: errorText(err) } }));
      });
  }

  // Re-link steps that still point at tables of an earlier scan. Only the table changes; wiring and settings stay.
  function updateToCurrentScan() {
    const out = relinkStaleTables(nodes, edges, staleScan.mapping);
    if (out.relinked.length === 0 && out.removed.length === 0) return;
    setNodes(out.nodes);
    setEdges(out.edges);
    if (selectedId && out.removed.includes(selectedId)) setSelectedId(null);
    setRuns((r) => Object.fromEntries(Object.entries(r).filter(([id]) => !out.removed.includes(id))));
    const parts: string[] = [];
    if (out.relinked.length) parts.push(`${out.relinked.length} step${out.relinked.length === 1 ? "" : "s"} now use the current scan (${out.relinked.join(", ")})`);
    if (out.removed.length) parts.push(`removed ${out.removed.length} unconnected duplicate${out.removed.length === 1 ? "" : "s"} (${out.removed.join(", ")})`);
    setNotice(`${parts.join("; ")}. Connections and settings were kept. Previews and join measurements that depended on them need to be run again. No data was changed.`);
  }

  // Arrange every step left to right. Only positions change; "Undo tidy up" puts them back.
  function tidyUp() {
    if (nodes.length === 0) return;
    const pos = layoutNodes(nodes, edges, { nodeW: NODE_W, nodeH: NODE_H });
    setPreTidy(Object.fromEntries(nodes.map((n) => [n.id, { x: n.x, y: n.y }])));
    setNodes(applyPositions(nodes, pos));
    setFitToken((t) => t + 1);
    setNotice("Steps were arranged left to right. Only their positions changed. Use “Undo tidy up” to put them back.");
  }
  function undoTidy() {
    if (!preTidy) return;
    setNodes(applyPositions(nodes, preTidy));
    setPreTidy(null);
    setFitToken((t) => t + 1);
    setNotice("Positions restored.");
  }

  // ------------------------------------------------ Catalog tables waiting for a choice
  function resetResults() {
    setRuns({});
    setPreviews({});
    setSqlById({});
    setDiagnostics({});
    setMeasuredAt({});
    setAcks({});
    setSelectedId(null);
    setPreTidy(null);
  }
  function chooseAddToDraft() {
    if (!pendingHandoff || !source) return;
    const plan = planHandoff(nodes, pendingHandoff, source.id);
    setNodes(plan.nodes);
    setHandoffRels(plan.relationships);
    if (plan.showSuggestions) setShowSuggestions(true);
    setHandoffNotice(plan.notice);
    setPendingHandoff(null);
  }
  function chooseStartNew() {
    if (!pendingHandoff || !source) return;
    // The draft being replaced is kept, so this can be undone.
    if (saveDraftBackup(source.id, nodes, edges)) setBackupSteps(nodes.length);
    resetResults();
    setEdges([]);
    const plan = planHandoff([], pendingHandoff, source.id);
    setNodes(plan.nodes);
    setHandoffRels(plan.relationships);
    if (plan.showSuggestions) setShowSuggestions(true);
    setHandoffNotice(plan.notice);
    setNotice(null);
    setPendingHandoff(null);
  }
  function chooseSkipHandoff() {
    setPendingHandoff(null);
    setHandoffNotice({ tone: "info", text: "The Catalog tables were not added. Your saved draft is unchanged." });
  }
  // Swap the canvas with the kept draft (the current canvas becomes the kept one, so nothing is lost).
  function swapWithPreviousDraft() {
    if (!source) return;
    const kept = loadDraftBackup(source.id);
    if (!kept) return;
    saveDraftBackup(source.id, nodes, edges);
    setBackupSteps(nodes.length);
    resetResults();
    setNodes(kept.nodes);
    setEdges(kept.edges);
    setNotice("Swapped to your previous draft. The pipeline you just left is kept the same way.");
  }
  function discardPreviousDraft() {
    if (!source) return;
    clearDraftBackup(source.id);
    setBackupSteps(null);
  }

  function addStep(type: NodeType) {
    if (nodes.length >= MAX_NODES) return setNotice(`A pipeline can have at most ${MAX_NODES} nodes.`);
    const right = nodes.length ? Math.max(...nodes.map((n) => n.x)) + NODE_W + 80 : 40;
    const spot = freeSpot(nodes, right, 40);
    const node = makeNode(nodes, type, spot.x, spot.y);
    setNodes([...nodes, node]);
    setSelectedId(node.id);
    if (type === "join") setShowSuggestions(true);
  }

  function moveNode(id: string, x: number, y: number) {
    setPreTidy(null); // a hand-made arrangement replaces the undo point
    setNodes((current) => current.map((n) => (n.id === id ? { ...n, x, y } : n)));
  }

  function changeConfig(id: string, config: Config) {
    setNodes((current) => current.map((n) => (n.id === id ? { ...n, config } : n)));
  }

  function connect(sourceId: string, targetId: string, port: string): string | null {
    const out = addEdge(nodes, edges, { source: sourceId, target: targetId, port });
    if (out.problem) {
      setNotice(`Connection not made: ${out.problem}`);
      return out.problem;
    }
    setEdges(out.edges);
    setNotice(null);
    return null;
  }

  function disconnect(edge: PipelineEdge) {
    setEdges((current) => removeEdge(current, edge));
  }

  function deleteNode(id: string) {
    const next = removeNode(nodes, edges, id);
    setNodes(next.nodes);
    setEdges(next.edges);
    setSelectedId(null);
    setRuns((r) => Object.fromEntries(Object.entries(r).filter(([key]) => key !== id)));
    setNotice(`Removed ${id} from the canvas. No data was changed.`);
  }

  function clearCanvas() {
    setNodes([]);
    setEdges([]);
    setSelectedId(null);
    setRuns({});
    setPreviews({});
    setSqlById({});
    setDiagnostics({});
    setMeasuredAt({});
    setAcks({});
    if (source) clearDraft(source.id);
    setNotice("Canvas cleared. Your saved browser draft was removed too. No data was changed.");
  }

  // ------------------------------------------------------------ suggestions
  /** Add a join step between two Source nodes already on the canvas. Runs nothing. */
  function addJoinNode(
    leftTableId: string,
    rightTableId: string,
    joinType: string,
    conditions: { left_column: string; right_column: string }[]
  ): PipelineNode | null {
    const left = nodes.find((n) => n.type === "source" && n.config.table_id === leftTableId);
    const right = nodes.find((n) => n.type === "source" && n.config.table_id === rightTableId);
    if (!left || !right) {
      setNotice("Both tables need to be on the canvas first.");
      return null;
    }
    if (nodes.length >= MAX_NODES) {
      setNotice(`A pipeline can have at most ${MAX_NODES} nodes.`);
      return null;
    }
    const spot = freeSpot(nodes, Math.max(left.x, right.x) + NODE_W + 80, Math.round((left.y + right.y) / 2));
    const join = makeNode(nodes, "join", spot.x, spot.y, { join_type: joinType, conditions });
    const withJoin = [...nodes, join];
    const first = addEdge(withJoin, edges, { source: left.id, target: join.id, port: "left" });
    const second = addEdge(withJoin, first.edges, { source: right.id, target: join.id, port: "right" });
    setNodes(withJoin);
    setEdges(second.edges);
    return join;
  }

  function acceptSuggestion(s: JoinSuggestion, joinType: JoinKind, select: boolean) {
    const join = addJoinNode(
      s.leftTableId,
      s.rightTableId,
      joinType,
      s.pairs.map((p) => ({ left_column: p.leftColumn, right_column: p.rightColumn }))
    );
    if (!join) return;
    if (select) setSelectedId(join.id);
    setNotice(
      `Added join ${join.id} (${joinType}${joinType === s.recommendedJoinType ? ", the recommended type" : ""}). It has not been measured on the real data yet; check it and press "Measure this join".`
    );
  }

  // ------------------------------------------------------ database actions
  async function runPreview(nodeId: string) {
    if (!source) return;
    const blocked = results[nodeId]?.preview_blocked_reason;
    if (blocked) return setNotice(blocked);
    const epoch = epochRef.current;
    const signature = signatures[nodeId];
    setRuns((r) => ({ ...r, [nodeId]: { state: "running", signature } }));
    try {
      const data = await previewNode(source, toPayload(nodes, edges), nodeId, limit);
      if (epochRef.current !== epoch) return;
      setPreviews((p) => ({ ...p, [nodeId]: data }));
      setRuns((r) => ({ ...r, [nodeId]: { state: "successful", signature, rowsShown: data.rows.length, hasMore: data.has_more } }));
    } catch (err) {
      if (epochRef.current !== epoch) return;
      if (isSessionExpiredError(err)) setSession("needed");
      const message =
        err instanceof ApiError && err.status === 401
          ? "Your database session has ended. Enter the password above and try again."
          : errorText(err);
      setRuns((r) => ({ ...r, [nodeId]: { state: "failed", signature, error: message } }));
    }
  }

  async function showSql(nodeId: string) {
    if (!source) return;
    setSqlError(null);
    const epoch = epochRef.current;
    try {
      const sql = await getNodeSql(source.id, toPayload(nodes, edges), nodeId);
      if (epochRef.current !== epoch) return;
      setSqlById((s) => ({ ...s, [nodeId]: { signature: signatures[nodeId], sql } }));
    } catch (err) {
      if (epochRef.current === epoch) setSqlError(errorText(err));
    }
  }

  async function measureJoin(nodeId: string) {
    if (!source) return;
    const blocked = results[nodeId]?.preview_blocked_reason;
    if (blocked) return setNotice(blocked);
    const epoch = epochRef.current;
    setMeasuring(true);
    setDiagnosticsError(null);
    const signature = signatures[nodeId];
    try {
      const out = await joinDiagnostics(source, toPayload(nodes, edges), nodeId);
      if (epochRef.current !== epoch) return;
      setDiagnostics((d) => ({ ...d, [nodeId]: out }));
      setMeasuredAt((m) => ({ ...m, [nodeId]: signature }));
    } catch (err) {
      if (epochRef.current !== epoch) return;
      if (isSessionExpiredError(err)) setSession("needed");
      setDiagnosticsError(errorText(err));
    } finally {
      if (epochRef.current === epoch) setMeasuring(false);
    }
  }

  async function connectDatabase() {
    if (!source || !password) return;
    setConnecting(true);
    try {
      await connectSource(source.id, password);
      setSession("connected");
      setNotice("Connected. The password is held on the server for a limited time and is not stored in this browser.");
    } catch (err) {
      setNotice(errorText(err));
    } finally {
      setPassword("");
      setConnecting(false);
    }
  }

  // --------------------------------------------------- preview panel inputs
  const selectedResult = selected ? results[selected.id] : undefined;
  const selectedStatus = selected ? views[selected.id] : undefined;
  const blockedReason: string | null = !selected
    ? null
    : selectedResult?.preview_blocked_reason
      ? selectedResult.preview_blocked_reason
      : session === "needed"
      ? "Enter the database password above to run a preview."
      : session === "unknown"
        ? "Checking the database connection…"
        : selectedResult?.status !== "ready"
          ? "Fix the problems listed for this step before previewing."
          : null;
  const selectedInputs = selected
    ? Object.entries(inputsOf(edges, selected.id)).map(([port, nodeId]) => ({ port, nodeId, label: nodeLabel(nodeId) }))
    : [];
  const selectedSql = selected && sqlById[selected.id]?.signature === signatures[selected.id] ? sqlById[selected.id].sql : undefined;
  const filteredTables = tables
    .filter((t) => `${t.schema_name}.${t.table_name}`.toLowerCase().includes(tableFilter.trim().toLowerCase()))
    .slice(0, 200);
  const outdatedNames = [...outdated].map(nodeLabel);

  function acknowledgeFanOut(id: string) {
    const d = diagnostics[id];
    if (!d || !d.summary.multiplies_main_side || measuredAt[id] !== signatures[id]) return;
    setAcks((a) => ({ ...a, [id]: fanOutKey(signatures[id], d) }));
  }
  function withdrawFanOut(id: string) {
    setAcks((a) => {
      const next = { ...a };
      delete next[id];
      return next;
    });
  }

  // A join counts as measured only while the pipeline still matches what was measured.
  const measurements = useMemo(() => {
    const out: Record<string, JoinMeasurement | undefined> = {};
    for (const [id, d] of Object.entries(diagnostics)) {
      out[id] = {
        outdated: measuredAt[id] !== signatures[id],
        multipliesMainSide: d.summary.multiplies_main_side,
        extraRows: d.summary.extra_rows_from_multiplication,
        // Acknowledged only if the pipeline AND the measured numbers are the ones the user looked at.
        acknowledged: acks[id] !== undefined && acks[id] === fanOutKey(signatures[id], d),
        details: {
          joinType: d.summary.join_type,
          mainSide: d.summary.main_side,
          leftRows: d.summary.left_rows,
          rightRows: d.summary.right_rows,
          matchedPairs: d.summary.matched_pairs,
          outputRows: d.summary.output_rows,
          largestLeftGroup: d.summary.largest_left_key_group,
          largestRightGroup: d.summary.largest_right_key_group,
        },
      };
    }
    return out;
  }, [diagnostics, measuredAt, acks, signatures]);

  // Which data source each Source step reads from (shown in the Select and Join stages).
  const sourceLabels = useMemo(() => {
    const out: Record<string, string> = {};
    for (const n of nodes) if (n.type === "source") out[n.id] = sourceLabel(nodeSourceId(n));
    return out;
  }, [nodes, nodeSourceId, sourceLabel]);

  // Guided stages are recomputed from the real pipeline on every render of these inputs.
  const stages = useMemo(() => {
    const previewedRows: Record<string, { rowsShown: number; hasMore: boolean } | undefined> = {};
    for (const [id, run] of Object.entries(runs)) {
      if (run.state === "successful" && run.rowsShown !== undefined) previewedRows[id] = { rowsShown: run.rowsShown, hasMore: Boolean(run.hasMore) };
    }
    return computeStages({
      nodes,
      edges,
      views,
      previewedRows,
      measurements,
      checked: validation !== null && !validationError,
      sourceLabels,
    });
  }, [nodes, edges, views, runs, measurements, validation, validationError, sourceLabels]);
  const joinStates = useMemo(() => joinChecks({ nodes, edges, views, measurements }), [nodes, edges, views, measurements]);
  const focusStage: StageId = pickedStage ?? currentStage(stages);
  const outputNode = nodes.find((n) => n.type === "output") ?? null;
  const guidePreviewBlocked: string | null = !outputNode
    ? null
    : results[outputNode.id]?.preview_blocked_reason
      ? results[outputNode.id]?.preview_blocked_reason ?? null
      : session === "needed"
      ? "Enter the database password above to run a preview."
      : session === "unknown"
        ? "Checking the database connection…"
        : results[outputNode.id]?.status !== "ready"
          ? "The Output step is not ready yet; see the problems listed."
          : null;

  // ----------------------------------------------------------------- messages
  // Everything the Builder says above the canvas goes through one list, ordered by importance.
  const noticeItems: NoticeItem[] = [];
  if (loadError) {
    noticeItems.push({
      key: "load-error",
      severity: "error",
      node: <p className="rounded-lg border border-rose-500/25 bg-rose-500/10 px-3 py-2 text-[12px] text-rose-300">{loadError}</p>,
    });
  }
  if (requestedSourceMissing) {
    noticeItems.push({
      key: "source-missing",
      severity: "error",
      node: (
        <div role="status" data-testid="handoff-notice" data-tone="error" className={`rounded-lg border px-3 py-2 text-[12px] ${NOTICE_STYLE.error}`}>
          The data source named by the Data Catalog is not available. Choose a source above.
        </div>
      ),
    });
  }
  if (source && validationError) {
    noticeItems.push({
      key: "validation-error",
      severity: "error",
      node: (
        <p role="alert" data-testid="validation-error" className="rounded-lg border border-rose-500/25 bg-rose-500/10 px-3 py-2 text-[12px] text-rose-300">
          Validation unavailable: {validationError}
        </p>
      ),
    });
  }
  if (handoffNotice) {
    noticeItems.push({
      key: "handoff-notice",
      severity: handoffNotice.tone === "error" ? "error" : handoffNotice.tone === "warning" ? "warning" : "info",
      node: (
        <div
          role="status"
          data-testid="handoff-notice"
          data-tone={handoffNotice.tone}
          className={`flex items-start justify-between gap-3 rounded-lg border px-3 py-2 text-[12px] ${NOTICE_STYLE[handoffNotice.tone]}`}
        >
          <span>{handoffNotice.text}</span>
          <button className="shrink-0 opacity-70 hover:opacity-100" onClick={() => setHandoffNotice(null)} aria-label="Dismiss message">
            ✕
          </button>
        </div>
      ),
    });
  }
  if (pendingHandoff) {
    noticeItems.push({
      key: "handoff-choice",
      severity: "action",
      node: (
        <div role="group" aria-label="Catalog tables waiting" data-testid="handoff-choice" className="rounded-lg border border-cyan-500/25 bg-cyan-500/10 px-3 py-3 text-[12.5px] text-cyan-100">
          <div className="font-semibold text-white">
            You chose {pendingHandoff.tables.length} table{pendingHandoff.tables.length === 1 ? "" : "s"} in the Data Catalog: {pendingHandoff.tables.map((t) => t.tableName).join(", ")}.
          </div>
          <p className="mt-1 text-[12px] text-cyan-100/80">
            This source already has a saved draft with {nodes.length} step{nodes.length === 1 ? "" : "s"}. Nothing has been changed yet. What should happen to the new tables?
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button className={btn} data-testid="handoff-add" onClick={chooseAddToDraft}>
              Add to current draft
            </button>
            <button className={btn} data-testid="handoff-new" onClick={chooseStartNew} title="Your current draft is kept so you can swap back to it">
              Start new pipeline
            </button>
            <button className={btn} data-testid="handoff-skip" onClick={chooseSkipHandoff}>
              Don’t add them
            </button>
          </div>
          <p className="mt-2 text-[11px] text-cyan-100/60">“Start new pipeline” keeps your current draft aside; you can swap back to it.</p>
        </div>
      ),
    });
  }
  if (source && staleScan.ids.length > 0) {
    noticeItems.push({
      key: "stale-scan",
      severity: "action",
      node: (
        <div data-testid="stale-scan" className="rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-100">
              <div className="font-semibold">
                {staleScan.ids.length} Source step{staleScan.ids.length === 1 ? "" : "s"} use{staleScan.ids.length === 1 ? "s" : ""} a table from an earlier Catalog scan ({staleScan.ids.join(", ")}).
              </div>
              <p className="mt-1 text-amber-100/80">
                The source was scanned again since these steps were added, so the Catalog list now has newer copies of the same tables. The steps still run, but their
                column list may be out of date and they can appear next to a duplicate of the same table.
                {staleScan.gone.length > 0 && ` Not in the current scan any more: ${staleScan.gone.join(", ")}; choose another table or remove those steps.`}
              </p>
              {Object.keys(staleScan.mapping).length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <button className={btn} data-testid="stale-update" onClick={updateToCurrentScan}>
                    Update to the current scan
                  </button>
                  <span className="text-[11px] text-amber-100/70">
                    Points each step at the same table in the latest scan. Connections and settings are kept; unconnected duplicates are removed. Earlier previews and measurements will need to be run again.
                  </span>
                </div>
              )}
            </div>
      ),
    });
  }
  if (source && outdatedNames.length > 0) {
    noticeItems.push({
      key: "outdated",
      severity: "warning",
      node: (
        <p className="rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-200" data-testid="outdated-banner">
          These steps show results from earlier settings and need recalculating: {outdatedNames.join(", ")}.
        </p>
      ),
    });
  }
  if (backupSteps !== null) {
    noticeItems.push({
      key: "draft-backup",
      severity: "info",
      node: (
        <div data-testid="draft-backup" className="flex flex-wrap items-center gap-3 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[12px] text-slate-300">
          <span>A previous draft ({backupSteps} step{backupSteps === 1 ? "" : "s"}) is kept in this browser.</span>
          <button className={btn} onClick={swapWithPreviousDraft}>
            Swap back to it
          </button>
          <button className={btn} onClick={discardPreviousDraft}>
            Discard it
          </button>
        </div>
      ),
    });
  }
  if (notice) {
    const needsAttention = /not made|cannot|could not|at most|failed|error|do not match/i.test(notice);
    noticeItems.push({
      key: "notice",
      severity: needsAttention ? "warning" : "info",
      node: (
        <div role="status" className={`flex items-start justify-between gap-3 rounded-lg border px-3 py-2 text-[12px] ${needsAttention ? "border-amber-500/25 bg-amber-500/10 text-amber-100" : "border-cyan-500/20 bg-cyan-500/10 text-cyan-100"}`}>
          <span>{notice}</span>
          <button className="shrink-0 opacity-70 hover:opacity-100" onClick={() => setNotice(null)} aria-label="Dismiss message">
            ✕
          </button>
        </div>
      ),
    });
  }

  // ----------------------------------------------------------------- render
  return (
    // The page's own scroll area: the dashboard layout clips anything taller than the screen.
    <div className="flex-1 overflow-auto px-6 py-6" data-testid="studio-scroll">
      <header className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight text-white">Visual ETL Pipeline Builder</h1>
          <p className="mt-1 max-w-3xl text-[13px] text-slate-500">
            Connect Catalog tables, join them, clean and reshape the result, and preview real rows at every step. Previews are read-only
            and limited to {limit} rows; nothing is published and no model training is started.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="rounded-lg border border-white/[0.1] bg-[#0b1018] px-2 py-1.5 text-[12.5px] text-slate-100"
            aria-label="Data source"
            value={source?.id ?? ""}
            onChange={(e) => void openSource(sources.find((s) => s.id === e.target.value) ?? null)}
          >
            <option value="">Choose a data source…</option>
            {sources.map((s) => (
              <option key={s.id} value={s.id}>
                {s.source_name} · {s.db_type}
              </option>
            ))}
          </select>
        </div>
      </header>

      <Notices items={noticeItems} maxInfo={2} />

      {!source && (
        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5 text-[13px] text-slate-500">
          Choose a registered source. Sources and their tables come from the Data Catalog; register and scan one there first if the list is empty.
        </div>
      )}

      {source && (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-2 text-[12px]">
            <span className="rounded-full border border-white/[0.1] px-2.5 py-1 text-slate-400">
              One pipeline uses tables from one source: {source.source_name}
            </span>
            <span className="rounded-full border border-amber-500/25 bg-amber-500/10 px-2.5 py-1 text-amber-200" title="Saved automatically in this browser. Not shared, not backed up, not on the server.">
              Draft saved in this browser only
            </span>
            <span className="text-slate-600">{!validationError && nodes.length ? "Pipeline checked against the Catalog" : ""}</span>
            <button className={`${btn} ml-auto`} onClick={clearCanvas} disabled={nodes.length === 0} title="Clears the canvas and the browser draft. No data is changed.">
              Clear canvas
            </button>
          </div>

          {session === "needed" && (
            <form
              className="mb-2 flex flex-wrap items-center gap-2 rounded-xl border border-white/[0.06] bg-white/[0.02] p-2"
              onSubmit={(e) => {
                e.preventDefault();
                void connectDatabase();
              }}
            >
              <span className="text-[12.5px] text-slate-300">Database password (needed for previews only)</span>
              <input
                type="password"
                autoComplete="off"
                className="rounded-lg border border-white/[0.1] bg-[#0b1018] px-2 py-1 text-[12.5px] text-slate-100"
                aria-label="Database password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <button className={btn} type="submit" disabled={!password || connecting}>
                {connecting ? "Connecting…" : "Connect"}
              </button>
              <span className="text-[11px] text-slate-600">Sent once, kept in server memory for a limited time, never saved in the pipeline.</span>
            </form>
          )}

          <GuidedStrip
            stages={stages}
            focus={focusStage}
            onFocus={setPickedStage}
            collapsed={guideCollapsed}
            onToggle={() => setGuideCollapsed((c) => !c)}
            hasOutput={outputNode !== null}
            sourceCount={nodes.filter((n) => n.type === "source").length}
            joinCount={nodes.filter((n) => n.type === "join").length}
            onPreviewOutput={() => {
              if (!outputNode) return;
              setSelectedId(outputNode.id);
              void runPreview(outputNode.id);
            }}
            previewBlockedReason={guidePreviewBlocked}
            previewing={outputNode ? runs[outputNode.id]?.state === "running" : false}
            joinStates={joinStates}
            onOpenJoin={(id) => setSelectedId(id)}
            measureBlockedReason={(id) =>
              results[id]?.preview_blocked_reason
                ? results[id]?.preview_blocked_reason ?? null
                : session === "needed"
                ? "Enter the database password above to measure."
                : session === "unknown"
                  ? "Checking the database connection…"
                  : results[id]?.status !== "ready"
                    ? "This join is not ready yet."
                    : null
            }
            measuring={measuring}
            onAcknowledge={acknowledgeFanOut}
            onWithdraw={withdrawFanOut}
          />

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[250px_minmax(0,1fr)_360px]">
            <aside className={`space-y-3 ${SIDE_COLUMN}`} data-testid="left-column">
              <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-3">
                <h3 className="text-[13px] font-semibold text-white">Steps</h3>
                <div className="mt-2 grid grid-cols-1 gap-1.5">
                  <button className={`${btn} text-left`} onClick={() => addStep("join")}>
                    New join…
                  </button>
                  {TRANSFORM_TYPES.map((t) => (
                    <button key={t} className={`${btn} text-left`} title={NODE_META[t].hint} onClick={() => addStep(t)}>
                      {NODE_META[t].label}
                    </button>
                  ))}
                  <button className={`${btn} text-left`} title={NODE_META.output.hint} onClick={() => addStep("output")}>
                    Output dataset
                  </button>
                </div>
              </div>
              <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-3">
                <h3 className="text-[13px] font-semibold text-white">
                  Catalog tables <span className="ml-1 text-[11px] font-normal text-slate-500">{tables.length}</span>
                </h3>
                <input
                  className="mt-2 w-full rounded-lg border border-white/[0.1] bg-[#0b1018] px-2 py-1.5 text-[12.5px] text-slate-100"
                  placeholder="Filter tables"
                  aria-label="Filter tables"
                  value={tableFilter}
                  onChange={(e) => setTableFilter(e.target.value)}
                />
                <ul className="mt-2 max-h-80 space-y-1 overflow-auto xl:max-h-none xl:overflow-visible" aria-label="Catalog tables">
                  {filteredTables.map((t) => (
                    <li key={t.id} className="flex items-center gap-2 rounded-lg px-1.5 py-1 hover:bg-white/[0.04]">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[12.5px] text-slate-200">{t.table_name}</div>
                        <div className="truncate text-[10.5px] text-slate-600">
                          {t.schema_name} · {formatRowCount(t.row_count, t.row_count_is_estimate)}
                        </div>
                      </div>
                      <button className={btn} onClick={() => addTable(t.id)} aria-label={`Add ${t.table_name}`}>
                        Add
                      </button>
                    </li>
                  ))}
                  {overview && tables.length === 0 && (
                    <li className="text-[12px] text-slate-500">This source has not been scanned. Scan it in the Data Catalog first.</li>
                  )}
                  {!overview && !loadError && <li className="text-[12px] text-slate-600">Loading…</li>}
                </ul>
              </div>
              {source && sources.length > 1 && (
                <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-3" data-testid="other-sources">
                  <h3 className="text-[13px] font-semibold text-white">Add tables from other sources</h3>
                  <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
                    A pipeline reads from one data source at a time. Tables of another source can be added and checked here, but they are not
                    previewed or measured until that source is opened as the main source, and they cannot be joined to tables of a different source.
                  </p>
                  {sources
                    .filter((s) => s.id !== source.id)
                    .map((s) => {
                      const o = otherOverviews[s.id];
                      return (
                        <div key={s.id} className="mt-2" data-testid="other-source" data-source-id={s.id}>
                          <button
                            className={`${btn} w-full text-left`}
                            aria-expanded={Boolean(openOthers[s.id])}
                            onClick={() => toggleOtherSource(s.id)}
                          >
                            {openOthers[s.id] ? "▾" : "▸"} {s.source_name} · {s.db_type}
                          </button>
                          {openOthers[s.id] && (
                            <ul className="mt-1 space-y-1" aria-label={`Tables of ${s.source_name}`}>
                              {o?.state === "loading" && <li className="text-[12px] text-slate-600">Loading…</li>}
                              {o?.state === "error" && <li className="text-[12px] text-rose-300">{o.error}</li>}
                              {o?.state === "ready" && o.tables.length === 0 && (
                                <li className="text-[12px] text-slate-500">This source has not been scanned in the Data Catalog.</li>
                              )}
                              {o?.state === "ready" &&
                                o.tables.slice(0, 200).map((t) => (
                                  <li key={t.id} className="flex items-center gap-2 rounded-lg px-1.5 py-1 hover:bg-white/[0.04]">
                                    <div className="min-w-0 flex-1">
                                      <div className="truncate text-[12.5px] text-slate-200">{t.table_name}</div>
                                      <div className="truncate text-[10.5px] text-slate-600">{t.schema_name}</div>
                                    </div>
                                    <button className={btn} onClick={() => addTable(t.id, s.id)} aria-label={`Add ${t.table_name} from ${s.source_name}`}>
                                      Add
                                    </button>
                                  </li>
                                ))}
                            </ul>
                          )}
                        </div>
                      );
                    })}
                </div>
              )}
            </aside>

            <section className="min-w-0 space-y-4">
              <div className="h-[clamp(320px,55vh,620px)]">
                <StudioCanvas
                  nodes={nodes}
                  edges={edges}
                  views={views}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  onMove={moveNode}
                  onConnect={connect}
                  onRemoveEdge={disconnect}
                  onTidy={tidyUp}
                  onUndoTidy={preTidy ? undoTidy : undefined}
                  fitToken={fitToken}
                />
              </div>
              <PreviewPanel
                node={selected}
                nodeName={selected ? nodeLabel(selected.id) : ""}
                result={selectedResult}
                status={selectedStatus?.status ?? "not_configured"}
                outdated={selectedStatus?.outdated ?? false}
                run={selected ? runs[selected.id] : undefined}
                data={selected ? previews[selected.id] : undefined}
                sql={selectedSql}
                sqlError={sqlError}
                limit={limit}
                onLimit={setLimit}
                previewing={selected ? runs[selected.id]?.state === "running" : false}
                blockedReason={blockedReason}
                onPreview={() => selected && void runPreview(selected.id)}
                onShowSql={() => selected && void showSql(selected.id)}
                inputs={selectedInputs}
                onPreviewInput={(id) => void runPreview(id)}
              />
            </section>

            <aside className={`space-y-4 ${SIDE_COLUMN}`} data-testid="right-column">
              {showSuggestions && (
                <JoinSuggestionsPanel
                  suggestions={visibleSuggestions}
                  tableName={tableNameById}
                  rejectedCount={rejected.size}
                  anySuggestions={suggestions.length > 0}
                  tablesOnCanvas={canvasTableIds.length}
                  onAccept={(s, t) => acceptSuggestion(s, t, false)}
                  onEdit={(s, t) => acceptSuggestion(s, t, true)}
                  onReject={(s) => setRejected((r) => new Set(r).add(s.id))}
                  onRestore={() => setRejected(new Set())}
                  onClose={() => setShowSuggestions(false)}
                />
              )}
              {!showSuggestions && (
                <button className={btn} onClick={() => setShowSuggestions(true)}>
                  Suggest joins for the tables on the canvas
                </button>
              )}
              {selected ? (
                <NodeConfigPanel
                  key={selected.id}
                  node={selected}
                  nodes={nodes}
                  edges={edges}
                  results={results}
                  tables={tables}
                  nodeLabel={nodeLabel}
                  onConfig={(config) => changeConfig(selected.id, config)}
                  onConnect={connect}
                  onDisconnect={disconnect}
                  onRemove={() => deleteNode(selected.id)}
                  diagnostics={diagnostics[selected.id]}
                  diagnosticsOutdated={measurements[selected.id]?.outdated ?? false}
                  diagnosticsError={diagnosticsError}
                  measuring={measuring}
                  canMeasure={(session === "connected" || session === "not_required") && !selectedResult?.preview_blocked_reason}
                  otherTables={otherTables}
                  measureBlockedReason={selectedResult?.preview_blocked_reason ?? null}
                  onMeasure={() => void measureJoin(selected.id)}
                />
              ) : (
                <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4 text-[12.5px] text-slate-500">
                  Select a node on the canvas to edit its settings.
                </div>
              )}
              <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4 text-[11.5px] leading-relaxed text-slate-500">
                <div className="mb-1 text-[12px] font-semibold text-slate-300">How steps are run</div>
                Every step is turned into one read-only SELECT that runs inside your source database, with the row limit applied there.
                Median, most-common-value fills, type changes, derived columns, split/combine and group-by need a different engine and are
                not available yet. Data-quality checks and reconciliation are not part of this version.
              </div>
            </aside>
          </div>
        </>
      )}
    </div>
  );
}
