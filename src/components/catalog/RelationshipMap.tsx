"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import {
  CatalogOverview,
  CatalogRelationship,
  CatalogTableDetail,
  DataSourceSummary,
  getTableDetail,
} from "@/services/catalogService";
import {
  CARD_WIDTH,
  HEADER_HEIGHT,
  ROW_HEIGHT,
  Point,
  Rect,
  Viewport,
  WorkspaceJoin,
  addToCanvas,
  anchorY,
  buildEdges,
  cardHeight,
  cardinalityLabel,
  cardinalityText,
  classifyJoin,
  computeLayout,
  defaultCanvas,
  edgeGeometry,
  findFreePosition,
  fitViewport,
  joinLabel,
  offCanvasNeighbours,
  parallelOffset,
  removeFromCanvas,
  resolveOverlaps,
  validateJoin,
  visibleColumns,
  zoomAt,
  ColumnLookup,
  EdgeGeometry,
} from "./relationshipGraph";
import { loadWorkspace, saveWorkspace } from "./canvasWorkspace";
import TableDetailsPanel from "./TableDetailsPanel";
import JoinEditor from "./JoinEditor";
import { buildDataset } from "./buildDataset";

interface Props {
  source: DataSourceSummary;
  overview: CatalogOverview | null;
  relationships: CatalogRelationship[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  /** Called when a live call (sample rows) finds the database session expired. */
  onSessionExpired: () => void;
}

type Selection =
  | { kind: "table"; name: string }
  | { kind: "edge"; id: string }
  | { kind: "join"; id: string }
  | null;

interface Link {
  kind: "declared" | "join";
  id: string;
  fromTable: string;
  fromColumn: string;
  toTable: string;
  toColumn: string;
  label: string;
  cardinality?: string;
}

const toolbarButton =
  "rounded-lg border border-white/10 bg-[#0b1630]/90 px-2.5 py-1.5 text-[12px] font-semibold text-slate-200 transition hover:bg-white/[0.08] disabled:opacity-40";

const newId = () => `j${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;

const AMBER = "#fbbf24";

/**
 * Visual Table Relationship Map.
 *
 * Reads the catalog the Source Explorer has already loaded. Adding or removing
 * a card only changes what is drawn; it never touches the database or the
 * catalog. Database-declared foreign keys (solid blue) are kept visually apart
 * from joins the user defines in this workspace (dashed amber).
 */
export default function RelationshipMap({
  source,
  overview,
  relationships,
  loading,
  error,
  onRefresh,
  onSessionExpired,
}: Props) {
  const tables = useMemo(() => overview?.tables ?? [], [overview]);
  const tableNames = useMemo(() => new Set(tables.map((t) => t.table_name)), [tables]);

  // ---- column details (lazy, cached by table id) -------------------------
  const [details, setDetails] = useState<Record<string, CatalogTableDetail | "error">>({});
  const requested = useRef<Set<string>>(new Set());

  const loadDetail = useCallback(async (id: string) => {
    if (requested.current.has(id)) return;
    requested.current.add(id);
    try {
      const detail = await getTableDetail(id);
      setDetails((prev) => ({ ...prev, [id]: detail }));
    } catch {
      setDetails((prev) => ({ ...prev, [id]: "error" }));
    }
  }, []);

  const columnsByTable: ColumnLookup = useMemo(() => {
    const out: ColumnLookup = {};
    for (const t of tables) {
      const d = details[t.id];
      if (d && d !== "error") out[t.table_name] = d.columns;
    }
    return out;
  }, [tables, details]);

  const { edges, ignoredNotDeclared, ignoredUnknownTable } = useMemo(
    () => buildEdges(tables, relationships, columnsByTable),
    [tables, relationships, columnsByTable]
  );

  // ---- workspace state ----------------------------------------------------
  const [canvas, setCanvas] = useState<string[]>([]);
  const [positions, setPositions] = useState<Record<string, Point>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [joins, setJoins] = useState<WorkspaceJoin[]>([]);
  const [selection, setSelection] = useState<Selection>(null);
  const [joinEditor, setJoinEditor] = useState<
    { editingId: string | null; initial?: Partial<Omit<WorkspaceJoin, "id">> } | null
  >(null);
  const [addOpen, setAddOpen] = useState(false);
  const [addFilter, setAddFilter] = useState("");
  const [confirmClear, setConfirmClear] = useState(false);
  const [layoutKey, setLayoutKey] = useState("");
  const [fitRequest, setFitRequest] = useState(0);
  // Cards ticked for "Build Dataset". Only tables still on the canvas count.
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [buildError, setBuildError] = useState<string | null>(null);

  const canvasSet = useMemo(() => new Set(canvas), [canvas]);
  const pickedOnCanvas = useMemo(() => canvas.filter((n) => picked.has(n)), [canvas, picked]);

  function togglePicked(name: string) {
    setBuildError(null);
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function startBuild(newTab: boolean) {
    const byName = new Map(tables.map((t) => [t.table_name, t.id]));
    const ids = pickedOnCanvas.map((n) => byName.get(n)).filter((id): id is string => Boolean(id));
    // Only joins whose two tables are both ticked are passed on (the helper checks this).
    setBuildError(buildDataset({ sourceId: source.id, tableIds: ids, catalogTables: tables, declared: relationships, workspaceJoins: joins, newTab }));
  }
  const canvasTables = useMemo(() => tables.filter((t) => canvasSet.has(t.table_name)), [tables, canvasSet]);

  // Fetch columns for what is on the canvas and linked, so cardinality and the
  // join editor have real column lists.
  useEffect(() => {
    const names = new Set<string>();
    edges.forEach((e) => {
      if (canvasSet.has(e.childTable) && canvasSet.has(e.parentTable)) {
        names.add(e.childTable);
        names.add(e.parentTable);
      }
    });
    joins.forEach((j) => {
      names.add(j.leftTable);
      names.add(j.rightTable);
    });
    const queue = tables.filter((t) => names.has(t.table_name)).map((t) => t.id);
    const worker = async () => {
      for (let id = queue.shift(); id; id = queue.shift()) await loadDetail(id);
    };
    void Promise.all([worker(), worker(), worker(), worker()]);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `edges` changes as details arrive; ids are de-duplicated by `requested`
  }, [tables, relationships, canvasSet, joins, loadDetail]);

  const edgeColumns = useMemo(() => {
    const out = new Map<string, Set<string>>();
    const add = (t: string, c: string) => {
      if (!out.has(t)) out.set(t, new Set());
      out.get(t)!.add(c);
    };
    edges.forEach((e) => {
      add(e.childTable, e.childColumn);
      add(e.parentTable, e.parentColumn);
    });
    joins.forEach((j) =>
      j.conditions.forEach((c) => {
        add(j.leftTable, c.leftColumn);
        add(j.rightTable, c.rightColumn);
      })
    );
    return out;
  }, [edges, joins]);

  /** What each card shows when expanded. */
  const bodies = useMemo(() => {
    const out: Record<string, ReturnType<typeof visibleColumns> | "loading" | "error"> = {};
    for (const t of tables) {
      const d = details[t.id];
      if (d === "error") out[t.table_name] = "error";
      else if (!d) out[t.table_name] = "loading";
      else out[t.table_name] = visibleColumns(d.columns, edgeColumns.get(t.table_name) ?? new Set());
    }
    return out;
  }, [tables, details, edgeColumns]);

  const heightWith = useCallback(
    (name: string, isExpanded: boolean) => {
      if (!isExpanded) return cardHeight(null);
      const body = bodies[name];
      if (!body || body === "loading" || body === "error") return cardHeight(1);
      return cardHeight(body.columns.length + (body.hiddenCount ? 1 : 0));
    },
    [bodies]
  );
  const heightOf = useCallback((name: string) => heightWith(name, expanded.has(name)), [heightWith, expanded]);

  // (Re)build the workspace when the catalog changes (new scan, refresh,
  // another source). Dragging, expanding, adding and removing never reset it.
  const structureKey = useMemo(
    () =>
      `${source.id}|${overview?.scan_id ?? ""}|` +
      tables.map((t) => t.table_name).join(",") +
      "|" +
      edges.map((e) => e.id).join(","),
    [source.id, overview, tables, edges]
  );
  if (structureKey !== layoutKey) {
    setLayoutKey(structureKey);
    const names = tables.map((t) => t.table_name);
    const saved = loadWorkspace(source.id, tableNames);
    const initialCanvas = saved ? saved.canvas : defaultCanvas(names);
    const inCanvas = new Set(initialCanvas);
    const auto = computeLayout(
      initialCanvas.map((name) => ({ name, height: cardHeight(null) })),
      edges.filter((e) => inCanvas.has(e.childTable) && inCanvas.has(e.parentTable))
    );
    const merged: Record<string, Point> = { ...auto };
    if (saved) for (const [n, p] of Object.entries(saved.positions)) if (inCanvas.has(n)) merged[n] = p;
    setCanvas(initialCanvas);
    setPositions(merged);
    setExpanded(new Set((saved?.expanded ?? []).filter((n) => inCanvas.has(n))));
    setJoins(saved?.joins ?? []);
    setSelection(null);
    setJoinEditor(null);
    setConfirmClear(false);
    setFitRequest((n) => n + 1);
  }

  // Remember the workspace in this browser (table names, positions, joins only).
  useEffect(() => {
    if (!overview || structureKey !== layoutKey) return;
    const timer = setTimeout(
      () => saveWorkspace(source.id, { canvas, positions, expanded: [...expanded], joins }),
      200
    );
    return () => clearTimeout(timer);
  }, [overview, structureKey, layoutKey, source.id, canvas, positions, expanded, joins]);

  const rects: Record<string, Rect> = useMemo(() => {
    const out: Record<string, Rect> = {};
    for (const t of canvasTables) {
      const p = positions[t.table_name];
      if (p) out[t.table_name] = { x: p.x, y: p.y, w: CARD_WIDTH, h: heightOf(t.table_name) };
    }
    return out;
  }, [canvasTables, positions, heightOf]);

  // ---- links to draw ------------------------------------------------------
  const drawnEdges = useMemo(
    () => edges.filter((e) => canvasSet.has(e.childTable) && canvasSet.has(e.parentTable)),
    [edges, canvasSet]
  );
  const drawnJoins = useMemo(
    () => joins.filter((j) => canvasSet.has(j.leftTable) && canvasSet.has(j.rightTable)),
    [joins, canvasSet]
  );
  const joinChecks = useMemo(() => {
    const out: Record<string, { basis: ReturnType<typeof classifyJoin>["basis"]; errors: string[]; warnings: string[] }> = {};
    for (const j of joins) {
      const v = validateJoin(j, columnsByTable, tableNames);
      out[j.id] = { basis: classifyJoin(j, edges).basis, ...v };
    }
    return out;
  }, [joins, edges, columnsByTable, tableNames]);

  const offCanvasByTable = useMemo(() => {
    const out = new Map<string, string[]>();
    for (const name of canvas) {
      const off = offCanvasNeighbours(name, edges, canvasSet);
      if (off.length) out.set(name, off);
    }
    return out;
  }, [canvas, edges, canvasSet]);

  // ---- selection ----------------------------------------------------------
  const connectedTo = useMemo(() => {
    const tablesOn = new Set<string>();
    const linksOn = new Set<string>();
    if (selection?.kind === "table") {
      tablesOn.add(selection.name);
      drawnEdges.forEach((e) => {
        if (e.childTable === selection.name || e.parentTable === selection.name) {
          linksOn.add(e.id);
          tablesOn.add(e.childTable);
          tablesOn.add(e.parentTable);
        }
      });
      drawnJoins.forEach((j) => {
        if (j.leftTable === selection.name || j.rightTable === selection.name) {
          linksOn.add(j.id);
          tablesOn.add(j.leftTable);
          tablesOn.add(j.rightTable);
        }
      });
    } else if (selection?.kind === "edge") {
      const e = edges.find((x) => x.id === selection.id);
      if (e) {
        linksOn.add(e.id);
        tablesOn.add(e.childTable);
        tablesOn.add(e.parentTable);
      }
    } else if (selection?.kind === "join") {
      const j = joins.find((x) => x.id === selection.id);
      if (j) {
        linksOn.add(j.id);
        tablesOn.add(j.leftTable);
        tablesOn.add(j.rightTable);
      }
    }
    return { tablesOn, linksOn };
  }, [selection, edges, joins, drawnEdges, drawnJoins]);
  const hasSelection = selection !== null;

  const selectedEdge = selection?.kind === "edge" ? edges.find((e) => e.id === selection.id) ?? null : null;
  const selectedJoin = selection?.kind === "join" ? joins.find((j) => j.id === selection.id) ?? null : null;
  const selectedTable =
    selection?.kind === "table" ? tables.find((t) => t.table_name === selection.name) ?? null : null;
  const selectedTableId = selectedTable?.id;
  useEffect(() => {
    if (selectedTableId) void loadDetail(selectedTableId);
  }, [selectedTableId, loadDetail]);

  // ---- viewport: pan, zoom, fit ------------------------------------------
  const canvasRef = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<Viewport>({ x: 0, y: 0, k: 1 });
  const [size, setSize] = useState({ w: 0, h: 0 });
  const viewRef = useRef(view);
  useEffect(() => {
    viewRef.current = view;
  }, [view]);

  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    observer.observe(el);
    return () => observer.disconnect();
  }, [overview, loading, error]);

  const fit = useCallback(() => {
    setView(fitViewport(Object.values(rects), size.w, size.h));
  }, [rects, size]);

  const handledFit = useRef(0);
  useEffect(() => {
    if (fitRequest !== handledFit.current && size.w > 0 && Object.keys(rects).length) {
      handledFit.current = fitRequest;
      setView(fitViewport(Object.values(rects), size.w, size.h));
    }
  }, [fitRequest, rects, size]);

  // Wheel zoom needs a non-passive listener so the page does not scroll too.
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const onWheel = (event: WheelEvent) => {
      if ((event.target as HTMLElement).closest("[data-nowheel]")) return;
      event.preventDefault();
      const box = el.getBoundingClientRect();
      const factor = Math.exp(-event.deltaY * 0.0015);
      setView((v) => zoomAt(v, factor, event.clientX - box.left, event.clientY - box.top));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [overview, loading, error]);

  const zoomBy = (factor: number) => setView((v) => zoomAt(v, factor, size.w / 2, size.h / 2));

  // Pan by dragging empty canvas; a click without movement clears the selection.
  const panState = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean } | null>(null);
  const onCanvasDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    if ((event.target as HTMLElement).closest("[data-interactive]")) return;
    panState.current = { x: event.clientX, y: event.clientY, vx: view.x, vy: view.y, moved: false };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onCanvasMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const s = panState.current;
    if (!s) return;
    const dx = event.clientX - s.x;
    const dy = event.clientY - s.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) s.moved = true;
    setView((v) => ({ ...v, x: s.vx + dx, y: s.vy + dy }));
  };
  const onCanvasUp = () => {
    const s = panState.current;
    panState.current = null;
    if (s && !s.moved) setSelection(null);
  };

  // ---- dragging cards -----------------------------------------------------
  const dragState = useRef<{ name: string; px: number; py: number; ox: number; oy: number; moved: boolean } | null>(null);
  const onCardDown = (event: ReactPointerEvent<HTMLDivElement>, name: string) => {
    if (event.button !== 0) return;
    if ((event.target as HTMLElement).closest("button, input, label")) return;
    const p = positions[name];
    dragState.current = { name, px: event.clientX, py: event.clientY, ox: p.x, oy: p.y, moved: false };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onCardMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const s = dragState.current;
    if (!s) return;
    const dx = (event.clientX - s.px) / viewRef.current.k;
    const dy = (event.clientY - s.py) / viewRef.current.k;
    if (Math.abs(dx) + Math.abs(dy) > 2) s.moved = true;
    if (s.moved) setPositions((prev) => ({ ...prev, [s.name]: { x: s.ox + dx, y: s.oy + dy } }));
  };
  const onCardUp = () => {
    const s = dragState.current;
    dragState.current = null;
    if (s && !s.moved) {
      setJoinEditor(null);
      setSelection({ kind: "table", name: s.name });
    }
  };

  // ---- expand / collapse --------------------------------------------------
  const toggleExpanded = (name: string) => {
    const table = tables.find((t) => t.table_name === name);
    const next = new Set(expanded);
    if (next.has(name)) next.delete(name);
    else {
      next.add(name);
      if (table) void loadDetail(table.id);
    }
    const sized: Record<string, Rect> = {};
    for (const t of canvasTables) {
      const p = positions[t.table_name];
      if (p) sized[t.table_name] = { x: p.x, y: p.y, w: CARD_WIDTH, h: heightWith(t.table_name, next.has(t.table_name)) };
    }
    const resolved = resolveOverlaps(sized, name);
    setExpanded(next);
    setPositions((prev) => {
      const out = { ...prev };
      for (const [k, r] of Object.entries(resolved)) out[k] = { x: r.x, y: r.y };
      return out;
    });
  };

  // ---- add / remove / clear (canvas only, never the database) -------------
  const addTable = (name: string) => {
    if (canvasSet.has(name) || !tableNames.has(name)) return;
    const related = edges
      .filter((e) => e.childTable === name || e.parentTable === name)
      .map((e) => (e.childTable === name ? e.parentTable : e.childTable))
      .find((n) => rects[n]);
    const spot = findFreePosition(Object.values(rects), CARD_WIDTH, cardHeight(null), related ? rects[related] : null);
    setCanvas((prev) => addToCanvas(prev, name, tableNames));
    setPositions((prev) => ({ ...prev, [name]: spot }));
    const t = tables.find((x) => x.table_name === name);
    if (t) void loadDetail(t.id);
    setSelection({ kind: "table", name });
  };

  const removeTable = (name: string) => {
    setCanvas((prev) => removeFromCanvas(prev, name));
    setExpanded((prev) => {
      const next = new Set(prev);
      next.delete(name);
      return next;
    });
    setSelection((sel) => (sel && sel.kind === "table" && sel.name === name ? null : sel));
  };

  const clearCanvas = () => {
    setCanvas([]);
    setExpanded(new Set());
    setJoins([]);
    setSelection(null);
    setJoinEditor(null);
    setConfirmClear(false);
  };

  const autoArrange = () => {
    const nodes = canvasTables.map((t) => ({ name: t.table_name, height: heightOf(t.table_name) }));
    setPositions(computeLayout(nodes, drawnEdges));
    setFitRequest((n) => n + 1);
  };

  const saveJoin = (draft: Omit<WorkspaceJoin, "id">, editingId: string | null) => {
    const id = editingId ?? newId();
    setJoins((prev) => (editingId ? prev.map((j) => (j.id === editingId ? { ...draft, id } : j)) : [...prev, { ...draft, id }]));
    setJoinEditor(null);
    setSelection({ kind: "join", id });
  };

  // ---- line geometry ------------------------------------------------------
  const lines = useMemo(() => {
    const links: Link[] = [
      ...drawnEdges.map<Link>((e) => ({
        kind: "declared",
        id: e.id,
        fromTable: e.childTable,
        fromColumn: e.childColumn,
        toTable: e.parentTable,
        toColumn: e.parentColumn,
        label: cardinalityLabel(e.cardinality),
      })),
      ...drawnJoins.map<Link>((j) => ({
        kind: "join",
        id: j.id,
        fromTable: j.leftTable,
        fromColumn: j.conditions[0].leftColumn,
        toTable: j.rightTable,
        toColumn: j.conditions[0].rightColumn,
        label: joinLabel(j),
      })),
    ];
    const pairKey = (l: Link) => [l.fromTable, l.toTable].sort().join("|");
    const pairCount = new Map<string, number>();
    const pairIndex = new Map<string, number>();
    links.forEach((l) => pairCount.set(pairKey(l), (pairCount.get(pairKey(l)) ?? 0) + 1));

    const rowOf = (table: string, column: string): number | null => {
      if (!expanded.has(table)) return null;
      const body = bodies[table];
      if (!body || body === "loading" || body === "error") return null;
      const i = body.columns.findIndex((c) => c.column_name === column);
      return i >= 0 ? i : null;
    };

    return links.flatMap((l): { link: Link; geo: EdgeGeometry }[] => {
      const from = rects[l.fromTable];
      const to = rects[l.toTable];
      if (!from || !to) return [];
      const key = pairKey(l);
      const idx = pairIndex.get(key) ?? 0;
      pairIndex.set(key, idx + 1);
      const off = parallelOffset(idx, pairCount.get(key) ?? 1);
      const fy = anchorY(from, rowOf(l.fromTable, l.fromColumn), off);
      const ty = anchorY(to, rowOf(l.toTable, l.toColumn), off);
      return [{ link: l, geo: edgeGeometry(from, to, fy, ty) }];
    });
  }, [drawnEdges, drawnJoins, rects, expanded, bodies]);

  const showEndLabels = lines.length <= 12;

  // ---- add-table menu -----------------------------------------------------
  const addCandidates = useMemo(() => {
    const q = addFilter.trim().toLowerCase();
    return tables
      .filter((t) => !canvasSet.has(t.table_name) && t.table_name.toLowerCase().includes(q))
      .map((t) => ({
        table: t,
        related: edges.some(
          (e) =>
            (e.childTable === t.table_name && canvasSet.has(e.parentTable)) ||
            (e.parentTable === t.table_name && canvasSet.has(e.childTable))
        ),
      }))
      .sort((a, b) => Number(b.related) - Number(a.related) || a.table.table_name.localeCompare(b.table.table_name));
  }, [tables, canvasSet, edges, addFilter]);

  // Look of one line; used by the line layer and by the label layer above the cards.
  const linkLook = (link: Link) => {
    const isJoin = link.kind === "join";
    const on = connectedTo.linksOn.has(link.id);
    const chosen =
      (selection?.kind === "edge" && selection.id === link.id) ||
      (selection?.kind === "join" && selection.id === link.id);
    return {
      isJoin,
      on,
      dim: hasSelection && !on,
      chosen,
      stroke: isJoin ? AMBER : chosen ? "#22d3ee" : on ? "#67e8f9" : "#38bdf8",
      pick: () => {
        setJoinEditor(null);
        setSelection({ kind: isJoin ? "join" : "edge", id: link.id });
      },
    };
  };

  // ---- render -------------------------------------------------------------
  const noTables = !!overview && overview.table_count === 0;
  const joinTables = canvasTables.map((t) => ({ name: t.table_name, columns: columnsByTable[t.table_name] }));

  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-[15px] font-semibold text-white">Relationship map</h3>
          <p className="mt-1 max-w-2xl text-[12px] leading-relaxed text-slate-500">
            The tables you choose, joined by the foreign keys the database itself declares.
            Add or remove cards freely — that only changes this canvas, never the database.
            Drag cards, scroll to zoom, drag the background to pan, and select a table or a
            line for details.
          </p>
        </div>
        <div className="relative flex flex-wrap items-center justify-end gap-1.5">
          <button className={toolbarButton} onClick={() => zoomBy(1 / 1.25)} aria-label="Zoom out">
            −
          </button>
          <span className="w-12 text-center text-[11.5px] tabular-nums text-slate-500">{Math.round(view.k * 100)}%</span>
          <button className={toolbarButton} onClick={() => zoomBy(1.25)} aria-label="Zoom in">
            +
          </button>
          <button className={toolbarButton} onClick={fit} disabled={!Object.keys(rects).length}>
            Fit to screen
          </button>
          <button className={toolbarButton} onClick={autoArrange} disabled={!canvas.length}>
            Auto-arrange
          </button>
          <button
            className={toolbarButton}
            onClick={() => {
              setAddOpen((o) => !o);
              setAddFilter("");
            }}
            disabled={!overview || tables.length === 0}
            aria-expanded={addOpen}
          >
            Add table ▾
          </button>
          <button
            className={toolbarButton}
            onClick={() => {
              setSelection(null);
              setJoinEditor({ editingId: null });
            }}
            disabled={canvas.length < 2}
            title={canvas.length < 2 ? "Put at least two tables on the canvas" : "Define a join for this workspace"}
          >
            New join
          </button>
          <button className={toolbarButton} onClick={() => setConfirmClear(true)} disabled={!canvas.length && !joins.length}>
            Clear canvas
          </button>
          <button className={toolbarButton} onClick={onRefresh} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
          <span className="mx-1 hidden h-5 w-px bg-white/10 sm:inline-block" aria-hidden />
          <button
            className={toolbarButton}
            onClick={() => setPicked(new Set(canvas))}
            disabled={!canvas.length}
            title="Tick every table on the canvas for Build Dataset"
          >
            Tick all
          </button>
          {pickedOnCanvas.length > 0 && (
            <button className={toolbarButton} onClick={() => setPicked(new Set())}>
              Clear ticks
            </button>
          )}
          <button
            className="rounded-lg bg-gradient-to-r from-cyan-500 to-blue-600 px-3 py-1.5 text-[12px] font-semibold text-white disabled:opacity-40"
            onClick={() => startBuild(false)}
            disabled={pickedOnCanvas.length === 0}
            title={pickedOnCanvas.length === 0 ? "Tick the tables you need on the cards first" : "Open the Visual ETL Builder with the ticked tables"}
            data-testid="map-build-dataset"
          >
            Build Dataset{pickedOnCanvas.length ? ` (${pickedOnCanvas.length})` : ""}
          </button>
          <button
            className={toolbarButton}
            onClick={() => startBuild(true)}
            disabled={pickedOnCanvas.length === 0}
            title="Open the Visual ETL Builder in a new browser tab"
          >
            New tab ↗
          </button>
          {buildError && <p className="basis-full text-right text-[11.5px] text-rose-300">{buildError}</p>}

          {addOpen && (
            <div
              data-nowheel
              className="absolute right-0 top-full z-20 mt-2 w-[320px] rounded-xl border border-white/10 bg-[#0b1630] p-3 shadow-xl shadow-black/50"
            >
              <input
                autoFocus
                value={addFilter}
                onChange={(e) => setAddFilter(e.target.value)}
                placeholder="Find a table to add…"
                className="w-full rounded-lg border border-white/10 bg-[#071127] px-3 py-2 text-[12.5px] text-slate-100 placeholder-slate-600 outline-none focus:border-cyan-400/40"
              />
              <ul className="mt-2 max-h-[280px] space-y-1 overflow-y-auto">
                {addCandidates.map(({ table, related }) => (
                  <li key={table.id}>
                    <button
                      onClick={() => {
                        addTable(table.table_name);
                        setAddOpen(false);
                      }}
                      className="flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12px] text-slate-300 hover:bg-white/[0.06]"
                    >
                      <span className="truncate font-mono">{table.table_name}</span>
                      <span className="shrink-0 text-[10.5px] text-slate-600">
                        {related && <span className="mr-1 rounded border border-cyan-400/30 px-1 text-cyan-300">related</span>}
                        {table.column_count} cols
                      </span>
                    </button>
                  </li>
                ))}
                {addCandidates.length === 0 && (
                  <li className="px-2.5 py-2 text-[12px] text-slate-600">
                    {tables.length === canvas.length ? "Every table is already on the canvas." : "No match."}
                  </li>
                )}
              </ul>
              <p className="mt-2 text-[10.5px] text-slate-600">Adds a card to the canvas only.</p>
            </div>
          )}
        </div>
      </div>

      {confirmClear && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2.5 text-[12.5px] text-amber-100">
          <span>
            Remove all {canvas.length} tables{joins.length ? ` and ${joins.length} workspace join${joins.length > 1 ? "s" : ""}` : ""} from
            the canvas? Database tables, catalog metadata and declared foreign keys are not affected.
          </span>
          <span className="flex gap-2">
            <button className={toolbarButton} onClick={() => setConfirmClear(false)}>
              Cancel
            </button>
            <button
              className="rounded-lg border border-amber-400/40 bg-amber-500/20 px-2.5 py-1.5 text-[12px] font-semibold text-amber-50 hover:bg-amber-500/30"
              onClick={clearCanvas}
            >
              Clear canvas
            </button>
          </span>
        </div>
      )}

      {error && (
        <div className="mt-4 rounded-lg border border-rose-500/25 bg-rose-500/10 px-3 py-3 text-[12.5px] text-rose-300">
          Could not load the catalog for the map: {error}
          <button className="ml-3 underline" onClick={onRefresh}>
            Try again
          </button>
        </div>
      )}

      {!error && loading && !overview && (
        <div className="mt-4 flex h-[360px] items-center justify-center rounded-xl border border-white/[0.06] text-[13px] text-slate-500">
          Loading tables and relationships…
        </div>
      )}

      {!error && (noTables || (overview && overview.scan_id === null)) && (
        <div className="mt-4 rounded-xl border border-dashed border-white/10 px-5 py-10 text-center">
          <p className="text-[13.5px] font-semibold text-slate-200">Nothing to map yet</p>
          <p className="mt-1 text-[12.5px] text-slate-500">
            This source has not been scanned. Connect, run a scan, and the tables will appear here.
          </p>
        </div>
      )}

      {!error && overview && overview.table_count > 0 && (
        <>
          {edges.length === 0 && (
            <div className="mt-4 rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-3 text-[12.5px] leading-relaxed text-amber-200">
              <span className="font-semibold">No declared relationships.</span> The scan found {overview.table_count} tables but
              the database declares no single-column foreign keys between them. Tables are shown without lines. Nothing is
              guessed: composite and cross-schema keys are not read yet. You can still define a workspace join between two tables
              with <span className="font-semibold">New join</span>.
            </div>
          )}

          <div
            ref={canvasRef}
            onPointerDown={onCanvasDown}
            onPointerMove={onCanvasMove}
            onPointerUp={onCanvasUp}
            className="relative mt-4 h-[68vh] min-h-[520px] cursor-grab touch-none select-none overflow-hidden rounded-xl border border-white/[0.06] bg-[#050c1c] active:cursor-grabbing"
            style={{
              backgroundImage: "radial-gradient(rgba(148,163,184,0.12) 1px, transparent 1px)",
              backgroundSize: `${24 * view.k}px ${24 * view.k}px`,
              backgroundPosition: `${view.x}px ${view.y}px`,
            }}
          >
            {canvas.length === 0 && (
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                <div className="pointer-events-auto rounded-xl border border-dashed border-white/10 bg-[#0b1630]/80 px-6 py-6 text-center" data-interactive>
                  <p className="text-[13.5px] font-semibold text-slate-200">The canvas is empty</p>
                  <p className="mt-1 max-w-sm text-[12.5px] text-slate-500">
                    {tables.length} tables are in the catalog. Add the ones you want to explore; nothing in the database changes.
                  </p>
                  <div className="mt-3 flex justify-center gap-2">
                    <button className={toolbarButton} onClick={() => setAddOpen(true)}>
                      Add table ▾
                    </button>
                    {tables.length <= 60 && (
                      <button
                        className={toolbarButton}
                        onClick={() => {
                          const names = tables.map((t) => t.table_name);
                          setCanvas(names);
                          setPositions(
                            computeLayout(
                              names.map((name) => ({ name, height: cardHeight(null) })),
                              edges
                            )
                          );
                          setFitRequest((n) => n + 1);
                        }}
                      >
                        Add all {tables.length} tables
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )}

            <div
              className="absolute left-0 top-0 origin-top-left"
              style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }}
            >
              {/* Lines */}
              <svg className="pointer-events-none absolute left-0 top-0 overflow-visible" width={1} height={1}>
                {lines.map(({ link, geo }) => {
                  const { isJoin, on, dim, chosen, stroke, pick } = linkLook(link);
                  const labels = showEndLabels || on;
                  const endText = (p: Point, side: string, text: string) => (
                    <text
                      x={p.x + (side === "right" ? 8 : -8)}
                      y={p.y - 6}
                      textAnchor={side === "right" ? "start" : "end"}
                      className="fill-slate-300 font-mono"
                      style={{ fontSize: 10.5, paintOrder: "stroke", stroke: "#050c1c", strokeWidth: 3 }}
                    >
                      {text}
                    </text>
                  );
                  return (
                    <g key={`${link.kind}-${link.id}`} opacity={dim ? 0.15 : 1}>
                      <path
                        d={geo.path}
                        fill="none"
                        stroke={stroke}
                        strokeWidth={chosen ? 2.6 : on ? 2.2 : 1.6}
                        strokeDasharray={isJoin ? "7 5" : undefined}
                      />
                      <circle cx={geo.from.x} cy={geo.from.y} r={3.5} fill={stroke} />
                      <circle cx={geo.to.x} cy={geo.to.y} r={3.5} fill="#050c1c" stroke={stroke} strokeWidth={1.6} />
                      {labels && endText(geo.from, geo.fromSide, link.fromColumn)}
                      {labels && endText(geo.to, geo.toSide, link.toColumn)}
                      <path
                        data-interactive
                        d={geo.path}
                        fill="none"
                        stroke="transparent"
                        strokeWidth={14}
                        className="cursor-pointer"
                        style={{ pointerEvents: "stroke" }}
                        onClick={pick}
                      />
                    </g>
                  );
                })}
              </svg>

              {/* Cards */}
              {canvasTables.map((table) => {
                const rect = rects[table.table_name];
                if (!rect) return null;
                const isExpanded = expanded.has(table.table_name);
                const body = bodies[table.table_name];
                const isSelected = selection?.kind === "table" && selection.name === table.table_name;
                const dim = hasSelection && !connectedTo.tablesOn.has(table.table_name);
                const keyCols = edgeColumns.get(table.table_name);
                const off = offCanvasByTable.get(table.table_name);
                return (
                  <div
                    key={table.id}
                    data-interactive
                    onPointerDown={(e) => onCardDown(e, table.table_name)}
                    onPointerMove={onCardMove}
                    onPointerUp={onCardUp}
                    className={`absolute cursor-move overflow-hidden rounded-xl border bg-[#0b1630] shadow-lg shadow-black/40 transition-opacity ${
                      isSelected
                        ? "border-cyan-400/70 ring-1 ring-cyan-400/40"
                        : connectedTo.tablesOn.has(table.table_name)
                          ? "border-cyan-400/30"
                          : "border-white/10"
                    } ${dim ? "opacity-30" : ""}`}
                    style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }}
                  >
                    <div className="flex items-start justify-between gap-2 px-3 pt-2.5" style={{ height: HEADER_HEIGHT }}>
                      <div className="min-w-0">
                        <div className="text-[10px] uppercase tracking-wider text-slate-500">{table.schema_name}</div>
                        <div className="truncate font-mono text-[13px] font-semibold text-white" title={table.table_name}>
                          {table.table_name}
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-500">
                          <span>{table.column_count} cols</span>
                          <span title={table.row_count_is_estimate ? "Estimated from database statistics" : undefined}>
                            {table.row_count !== null
                              ? `${table.row_count_is_estimate ? "~" : ""}${table.row_count.toLocaleString()} rows${table.row_count_is_estimate ? " (est.)" : ""}`
                              : "rows n/a"}
                          </span>
                          {table.has_primary_key ? (
                            <span className="rounded-full border border-cyan-400/30 bg-cyan-500/10 px-1.5 text-[10px] font-semibold text-cyan-300">PK</span>
                          ) : (
                            <span className="rounded-full border border-white/10 px-1.5 text-[10px] text-slate-500">no PK</span>
                          )}
                        </div>
                        {off && (
                          <button
                            onClick={() => {
                              setJoinEditor(null);
                              setSelection({ kind: "table", name: table.table_name });
                            }}
                            className="mt-1 rounded-full border border-amber-400/30 bg-amber-500/10 px-2 text-[10px] font-semibold text-amber-200 hover:bg-amber-500/20"
                            title={`Related, not on the canvas: ${off.join(", ")}`}
                          >
                            ↗ {off.length} related off-canvas
                          </button>
                        )}
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        <input
                          type="checkbox"
                          className="h-3.5 w-3.5 accent-cyan-500"
                          checked={picked.has(table.table_name)}
                          onChange={() => togglePicked(table.table_name)}
                          aria-label={`Tick ${table.table_name} for Build Dataset`}
                          title="Tick to include this table in Build Dataset"
                        />
                        <button
                          onClick={() => toggleExpanded(table.table_name)}
                          aria-expanded={isExpanded}
                          aria-label={`${isExpanded ? "Collapse" : "Expand"} columns of ${table.table_name}`}
                          className="rounded-md border border-white/10 px-1.5 py-0.5 text-[11px] text-slate-300 hover:bg-white/[0.08]"
                        >
                          {isExpanded ? "▾" : "▸"} cols
                        </button>
                        <button
                          onClick={() => removeTable(table.table_name)}
                          aria-label={`Remove ${table.table_name} from the canvas`}
                          title="Remove from the canvas (does not touch the database)"
                          className="rounded-md border border-white/10 px-1.5 py-0.5 text-[11px] text-slate-400 hover:border-rose-400/40 hover:bg-rose-500/10 hover:text-rose-300"
                        >
                          ✕
                        </button>
                      </div>
                    </div>
                    {isExpanded && (
                      <div className="border-t border-white/[0.06] px-3 pt-0" style={{ paddingBottom: 8 }}>
                        {body === "loading" && (
                          <div className="text-[11.5px] text-slate-500" style={{ height: ROW_HEIGHT, lineHeight: `${ROW_HEIGHT}px` }}>
                            Loading columns…
                          </div>
                        )}
                        {body === "error" && (
                          <div className="text-[11.5px] text-rose-300" style={{ height: ROW_HEIGHT, lineHeight: `${ROW_HEIGHT}px` }}>
                            Could not load columns.
                          </div>
                        )}
                        {body && body !== "loading" && body !== "error" && (
                          <>
                            {body.columns.map((c) => {
                              const inLine = keyCols?.has(c.column_name);
                              const isFk =
                                c.is_foreign_key ||
                                edges.some((e) => e.childTable === table.table_name && e.childColumn === c.column_name);
                              return (
                                <div
                                  key={c.column_name}
                                  className={`flex items-center justify-between gap-2 text-[11.5px] ${
                                    c.is_primary_key ? "text-cyan-200" : isFk ? "text-violet-200" : "text-slate-300"
                                  }`}
                                  style={{ height: ROW_HEIGHT }}
                                >
                                  <span className={`truncate font-mono ${inLine ? "underline decoration-white/20 underline-offset-2" : ""}`} title={c.column_name}>
                                    {c.column_name}
                                  </span>
                                  <span className="flex shrink-0 items-center gap-1">
                                    {c.is_primary_key && (
                                      <span className="rounded border border-cyan-400/30 bg-cyan-500/10 px-1 text-[9.5px] font-semibold text-cyan-300">PK</span>
                                    )}
                                    {isFk && (
                                      <span className="rounded border border-violet-400/30 bg-violet-500/10 px-1 text-[9.5px] font-semibold text-violet-300">FK</span>
                                    )}
                                    <span className="font-mono text-[10px] text-slate-600">{c.data_type}</span>
                                  </span>
                                </div>
                              );
                            })}
                            {body.hiddenCount > 0 && (
                              <div className="text-[11px] text-slate-600" style={{ height: ROW_HEIGHT, lineHeight: `${ROW_HEIGHT}px` }}>
                                + {body.hiddenCount} more columns
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}

              {/* Line labels sit above the cards so a line that crosses a card stays clickable */}
              <svg className="pointer-events-none absolute left-0 top-0 overflow-visible" width={1} height={1}>
                {lines.map(({ link, geo }) => {
                  const { isJoin, dim, chosen, stroke, pick } = linkLook(link);
                  const pillW = Math.max(38, link.label.length * 7 + 16);
                  return (
                    <g key={`pill-${link.kind}-${link.id}`} style={{ opacity: dim ? 0.15 : 1 }}>
                      <g data-interactive className="cursor-pointer" style={{ pointerEvents: "all" }} onClick={pick}>
                        <rect
                          x={geo.mid.x - pillW / 2}
                          y={geo.mid.y - 10}
                          width={pillW}
                          height={20}
                          rx={10}
                          fill="#0b1630"
                          stroke={stroke}
                          strokeWidth={chosen ? 1.8 : 1}
                          strokeDasharray={isJoin ? "3 2" : undefined}
                        />
                        <text
                          x={geo.mid.x}
                          y={geo.mid.y + 4}
                          textAnchor="middle"
                          className="fill-slate-100 font-semibold"
                          style={{ fontSize: 10.5 }}
                        >
                          {link.label}
                        </text>
                      </g>
                    </g>
                  );
                })}
              </svg>
            </div>

            {/* Legend */}
            <div
              data-interactive
              className="absolute bottom-3 left-3 space-y-1.5 rounded-lg border border-white/10 bg-[#0b1630]/90 px-3 py-2 text-[11px] text-slate-400"
            >
              <div className="flex items-center gap-2">
                <svg width="30" height="8"><line x1="0" y1="4" x2="30" y2="4" stroke="#38bdf8" strokeWidth="1.8" /></svg>
                Declared foreign key (child → parent)
              </div>
              <div className="flex items-center gap-2">
                <svg width="30" height="8"><line x1="0" y1="4" x2="30" y2="4" stroke={AMBER} strokeWidth="1.8" strokeDasharray="5 3" /></svg>
                Workspace join you defined (not a database key)
              </div>
              <div className="flex items-center gap-2">
                <span className="w-[30px] text-center font-semibold text-slate-100">N : 1</span>
                Many child rows to one parent (derived from keys)
              </div>
              <div className="flex items-center gap-2">
                <span className="w-[30px] text-center font-semibold text-slate-100">FK</span>
                Cardinality not provable from the catalog
              </div>
              <div className="flex items-center gap-2">
                <span className="rounded border border-cyan-400/30 bg-cyan-500/10 px-1 text-[9.5px] font-semibold text-cyan-300">PK</span>
                <span className="rounded border border-violet-400/30 bg-violet-500/10 px-1 text-[9.5px] font-semibold text-violet-300">FK</span>
                Primary / foreign key column
              </div>
              <div className="flex items-center gap-2">
                <span className="rounded-full border border-amber-400/30 bg-amber-500/10 px-1.5 text-[9.5px] font-semibold text-amber-200">↗ n</span>
                Related tables not on the canvas
              </div>
            </div>

            {/* Side panels */}
            {joinEditor && (
              <JoinEditor
                key={joinEditor.editingId ?? "new"}
                tables={joinTables}
                initial={joinEditor.initial}
                title={joinEditor.editingId ? "Edit workspace join" : "New workspace join"}
                onSave={(draft) => saveJoin(draft, joinEditor.editingId)}
                onCancel={() => setJoinEditor(null)}
              />
            )}

            {!joinEditor && selectedTable && (
              <TableDetailsPanel
                key={selectedTable.id}
                table={selectedTable}
                columns={details[selectedTable.id] === "error" ? "error" : (details[selectedTable.id] as CatalogTableDetail | undefined)?.columns}
                source={source}
                declared={edges}
                joins={joins}
                offCanvas={offCanvasNeighbours(selectedTable.table_name, edges, canvasSet)}
                onAddTable={addTable}
                onSelectEdge={(id) => setSelection({ kind: "edge", id })}
                onSelectJoin={(id) => setSelection({ kind: "join", id })}
                onSessionExpired={onSessionExpired}
                onClose={() => setSelection(null)}
              />
            )}

            {!joinEditor && selectedEdge && (
              <div
                data-interactive
                data-nowheel
                className="absolute right-3 top-3 max-h-[92%] w-[320px] overflow-y-auto rounded-xl border border-white/10 bg-[#0b1630]/95 p-4 text-[12px] text-slate-300 shadow-xl shadow-black/40"
              >
                <div className="flex items-start justify-between gap-2">
                  <h4 className="text-[13px] font-semibold text-white">Declared relationship</h4>
                  <button className="text-slate-500 hover:text-white" onClick={() => setSelection(null)} aria-label="Close details">✕</button>
                </div>
                <dl className="mt-3 space-y-2.5">
                  <Detail label="Source (child, holds the key)" value={`${selectedEdge.childTable}.${selectedEdge.childColumn}`} mono />
                  <Detail label="Target (parent, referenced)" value={`${selectedEdge.parentTable}.${selectedEdge.parentColumn}`} mono />
                  <Detail label="Constraint" value={selectedEdge.constraintName || "—"} mono />
                  <Detail label="Defined by" value="The database (a real foreign key). It cannot be changed from this map." />
                  <Detail label="Cardinality" value={cardinalityText(selectedEdge.cardinality)} />
                  <p className="text-[11.5px] leading-relaxed text-slate-500">{selectedEdge.cardinalityBasis}</p>
                  <p className="text-[11px] text-slate-600">This is the declared key only. How many rows actually match is not measured here.</p>
                </dl>
                <button
                  className="mt-3 rounded-lg border border-amber-400/30 px-3 py-1.5 text-[11.5px] font-semibold text-amber-200 hover:bg-amber-500/10"
                  onClick={() =>
                    setJoinEditor({
                      editingId: null,
                      initial: {
                        leftTable: selectedEdge.childTable,
                        rightTable: selectedEdge.parentTable,
                        joinType: "LEFT",
                        conditions: [{ leftColumn: selectedEdge.childColumn, rightColumn: selectedEdge.parentColumn }],
                      },
                    })
                  }
                >
                  Start a workspace join from this key
                </button>
              </div>
            )}

            {!joinEditor && selectedJoin && (
              <div
                data-interactive
                data-nowheel
                className="absolute right-3 top-3 max-h-[92%] w-[320px] overflow-y-auto rounded-xl border border-amber-400/25 bg-[#0b1630]/95 p-4 text-[12px] text-slate-300 shadow-xl shadow-black/40"
              >
                <div className="flex items-start justify-between gap-2">
                  <h4 className="text-[13px] font-semibold text-white">Workspace join</h4>
                  <button className="text-slate-500 hover:text-white" onClick={() => setSelection(null)} aria-label="Close details">✕</button>
                </div>
                <dl className="mt-3 space-y-2.5">
                  <Detail label="Type" value={`${selectedJoin.joinType} JOIN`} />
                  <Detail label="Left table" value={selectedJoin.leftTable} mono />
                  <Detail label="Right table" value={selectedJoin.rightTable} mono />
                  <div>
                    <dt className="text-[10.5px] uppercase tracking-wider text-slate-500">Match on (AND)</dt>
                    <dd className="mt-0.5 space-y-0.5 font-mono text-[11.5px] text-slate-100">
                      {selectedJoin.conditions.map((c) => (
                        <div key={`${c.leftColumn}=${c.rightColumn}`}>
                          {selectedJoin.leftTable}.{c.leftColumn} = {selectedJoin.rightTable}.{c.rightColumn}
                        </div>
                      ))}
                    </dd>
                  </div>
                  <Detail
                    label="Basis"
                    value={
                      joinChecks[selectedJoin.id]?.basis === "declared"
                        ? "Matches a declared foreign key"
                        : joinChecks[selectedJoin.id]?.basis === "partly-declared"
                          ? "Partly matches declared foreign keys"
                          : "Manually configured (no declared foreign key)"
                    }
                  />
                  <p className="text-[11.5px] leading-relaxed text-slate-500">
                    Defined in this workspace only. It does not create or change a database constraint, and it is not verified against the data yet.
                  </p>
                  {joinChecks[selectedJoin.id]?.errors.map((e) => (
                    <p key={e} className="rounded-lg border border-rose-500/25 bg-rose-500/10 px-2.5 py-1.5 text-[11.5px] text-rose-300">{e}</p>
                  ))}
                  {joinChecks[selectedJoin.id]?.warnings.map((w) => (
                    <p key={w} className="rounded-lg border border-amber-500/25 bg-amber-500/10 px-2.5 py-1.5 text-[11.5px] text-amber-200">{w}</p>
                  ))}
                </dl>
                <div className="mt-3 flex gap-2">
                  <button
                    className="rounded-lg border border-white/10 px-3 py-1.5 text-[11.5px] font-semibold text-slate-200 hover:bg-white/[0.06]"
                    onClick={() =>
                      setJoinEditor({
                        editingId: selectedJoin.id,
                        initial: {
                          leftTable: selectedJoin.leftTable,
                          rightTable: selectedJoin.rightTable,
                          joinType: selectedJoin.joinType,
                          conditions: selectedJoin.conditions,
                        },
                      })
                    }
                  >
                    Edit
                  </button>
                  <button
                    className="rounded-lg border border-rose-400/30 px-3 py-1.5 text-[11.5px] font-semibold text-rose-300 hover:bg-rose-500/10"
                    onClick={() => {
                      setJoins((prev) => prev.filter((j) => j.id !== selectedJoin.id));
                      setSelection(null);
                    }}
                  >
                    Delete join
                  </button>
                </div>
              </div>
            )}
          </div>

          <p className="mt-3 text-[11.5px] text-slate-600">
            {canvas.length} of {tables.length} tables on the canvas · {drawnEdges.length} declared relationships shown
            {drawnJoins.length > 0 && ` · ${drawnJoins.length} workspace join${drawnJoins.length > 1 ? "s" : ""}`}
            {ignoredNotDeclared > 0 && ` · ${ignoredNotDeclared} non-declared hidden`}
            {ignoredUnknownTable > 0 && ` · ${ignoredUnknownTable} skipped (table not in catalog)`}. Layout and workspace joins are
            remembered in this browser only.
          </p>
        </>
      )}
    </div>
  );
}

function Detail({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-[10.5px] uppercase tracking-wider text-slate-500">{label}</dt>
      <dd className={`mt-0.5 break-words text-slate-100 ${mono ? "font-mono text-[11.5px]" : ""}`}>{value}</dd>
    </div>
  );
}
