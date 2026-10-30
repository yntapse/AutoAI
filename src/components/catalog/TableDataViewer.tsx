"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  CatalogTableDetail,
  DataSourceSummary,
  SessionStatus,
  connectSource,
  getSessionStatus,
  isSessionExpiredError,
  previewTable,
} from "@/services/catalogService";

/**
 * Full-screen, spreadsheet-style viewer for the real rows of one table.
 *
 * Rows arrive in batches from the existing cursor API (POST
 * /catalog/tables/{id}/preview) as the user scrolls; nothing is fetched up
 * front. Only the rows on screen are drawn (fixed-height virtual scrolling), so
 * a long table stays responsive. Search and sort act on the rows loaded in the
 * browser only, and the toolbar says so.
 */

type Row = Record<string, unknown>;
type SortState = { column: string; dir: "asc" | "desc" } | null;
type Phase = "idle" | "loading" | "error" | "expired";

const BATCH = 100; // the API's maximum page size
const ROW_H = 30;
const HEADER_H = 36;
const ROWNUM_W = 64;
const OVERSCAN = 8;
const PREFETCH_ROWS = 40; // start the next batch when this close to the bottom
const MEMORY_STEP = 10_000; // rows held in the browser before asking the user

interface Props {
  source: DataSourceSummary;
  table: CatalogTableDetail;
  onClose: () => void;
  /** The server said the database session is gone. */
  onSessionExpired: () => void;
  /** The user reconnected from inside the viewer. */
  onReconnected: (status: SessionStatus) => void;
}

function cellText(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}

function measureWidths(columns: string[], sample: Row[]): number[] {
  return columns.map((column) => {
    let chars = column.length + 3;
    for (const row of sample.slice(0, 50)) {
      const value = row[column];
      const length = value === null || value === undefined ? 4 : String(value).length;
      if (length > chars) chars = length;
    }
    return Math.min(360, Math.max(96, Math.round(chars * 7.4 + 28)));
  });
}

function compareValues(a: unknown, b: unknown): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  const sa = String(a);
  const sb = String(b);
  const na = sa.trim() === "" ? NaN : Number(sa);
  const nb = sb.trim() === "" ? NaN : Number(sb);
  if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
  return sa.localeCompare(sb, undefined, { numeric: true, sensitivity: "base" });
}

export default function TableDataViewer({
  source,
  table,
  onClose,
  onSessionExpired,
  onReconnected,
}: Props) {
  const [rows, setRows] = useState<Row[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [widths, setWidths] = useState<number[]>([]);
  const [hasMore, setHasMore] = useState(true);
  const [phase, setPhase] = useState<Phase>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [emptyMessage, setEmptyMessage] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortState>(null);
  const [selected, setSelected] = useState<{ row: number; column: string } | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportH, setViewportH] = useState(600);
  const [memoryCap, setMemoryCap] = useState(MEMORY_STEP);
  const [password, setPassword] = useState("");
  const [reconnecting, setReconnecting] = useState(false);
  const [reconnectError, setReconnectError] = useState<string | null>(null);

  // Refs hold what the fetch loop must read without waiting for a re-render, so
  // two quick scroll events can never start two requests for the same cursor.
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const cursorRef = useRef<string | null>(null);
  const hasMoreRef = useRef(true);
  const inflightRef = useRef(false);
  const generationRef = useRef(0);
  const loadedRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Cover the whole screen: lock page scrolling underneath, Esc closes.
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous;
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  // Track the visible height of the grid.
  useEffect(() => {
    const element = scrollerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setViewportH(element.clientHeight));
    observer.observe(element);
    setViewportH(element.clientHeight);
    return () => observer.disconnect();
  }, []);

  const fetchNext = useCallback(async () => {
    if (inflightRef.current || !hasMoreRef.current) return;
    inflightRef.current = true;
    const generation = generationRef.current;
    setPhase("loading");
    setErrorMessage(null);
    try {
      const page = await previewTable(table.id, source, {
        limit: BATCH,
        cursor: cursorRef.current,
      });
      if (generation !== generationRef.current || !mountedRef.current) return;
      // Each batch must continue exactly where the last one ended.
      if (page.start_row !== null && page.start_row !== loadedRef.current + 1) {
        throw new Error("The server returned rows out of order. Reload the table.");
      }
      if (loadedRef.current === 0) {
        setColumns(page.column_names);
        setWidths(measureWidths(page.column_names, page.rows));
        setEmptyMessage(page.message);
      }
      loadedRef.current += page.rows.length;
      cursorRef.current = page.next_cursor;
      hasMoreRef.current = page.has_more && page.next_cursor !== null;
      setHasMore(hasMoreRef.current);
      if (page.rows.length > 0) {
        setRows((current) => current.concat(page.rows));
      }
      setPhase("idle");
    } catch (error) {
      if (generation !== generationRef.current || !mountedRef.current) return;
      if (isSessionExpiredError(error)) {
        setPhase("expired");
        onSessionExpired();
      } else {
        setErrorMessage(error instanceof Error ? error.message : "Failed to load rows.");
        setPhase("error");
      }
    } finally {
      if (generation === generationRef.current) inflightRef.current = false;
    }
  }, [table.id, source, onSessionExpired]);

  const filtering = search.trim() !== "" || sort !== null;

  // Infinite scroll: when the user is near the last loaded row, fetch the next
  // batch. Paused while an error is showing, while searching or sorting (the
  // list would otherwise keep growing to fill a short result), and at the
  // in-memory cap.
  useEffect(() => {
    if (phase !== "idle" || filtering || !hasMore || rows.length >= memoryCap) return;
    const lastVisible = Math.ceil((scrollTop + viewportH) / ROW_H);
    if (rows.length === 0 || lastVisible >= rows.length - PREFETCH_ROWS) {
      void fetchNext();
    }
  }, [phase, filtering, hasMore, rows.length, memoryCap, scrollTop, viewportH, fetchNext]);

  function reload() {
    generationRef.current += 1;
    inflightRef.current = false;
    cursorRef.current = null;
    hasMoreRef.current = true;
    loadedRef.current = 0;
    setRows([]);
    setHasMore(true);
    setErrorMessage(null);
    setEmptyMessage(null);
    setSelected(null);
    setMemoryCap(MEMORY_STEP);
    setScrollTop(0);
    if (scrollerRef.current) scrollerRef.current.scrollTop = 0;
    setPhase("idle");
  }

  function retry() {
    setPhase("idle");
    void fetchNext();
  }

  async function handleReconnect() {
    setReconnecting(true);
    setReconnectError(null);
    try {
      await connectSource(source.id, password);
      setPassword("");
      const status = await getSessionStatus(source.id);
      if (!status.connected) {
        setReconnectError(
          "Connected, but your browser did not keep the session. Open this app " +
            "at the same host name as the API (for example localhost for both), then try again."
        );
        return;
      }
      onReconnected(status);
      setPhase("idle");
      void fetchNext();
    } catch (error) {
      setReconnectError(error instanceof Error ? error.message : "Could not connect.");
    } finally {
      setReconnecting(false);
    }
  }

  function toggleSort(column: string) {
    setSort((current) => {
      if (!current || current.column !== column) return { column, dir: "asc" };
      if (current.dir === "asc") return { column, dir: "desc" };
      return null;
    });
  }

  // Row order after search and sort. null means "as loaded" (no copy made).
  const order = useMemo<number[] | null>(() => {
    const query = search.trim().toLowerCase();
    if (!query && !sort) return null;
    let indices: number[] = [];
    for (let i = 0; i < rows.length; i += 1) {
      if (!query || columns.some((c) => cellText(rows[i][c]).toLowerCase().includes(query))) {
        indices.push(i);
      }
    }
    if (sort) {
      const { column, dir } = sort;
      const sign = dir === "asc" ? 1 : -1;
      indices = indices.sort((x, y) => {
        const a = rows[x][column];
        const b = rows[y][column];
        const aNull = a === null || a === undefined;
        const bNull = b === null || b === undefined;
        if (aNull || bNull) return aNull === bNull ? x - y : aNull ? 1 : -1; // NULLs last
        return sign * compareValues(a, b) || x - y;
      });
    }
    return indices;
  }, [rows, columns, search, sort]);

  const total = order ? order.length : rows.length;
  const first = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN);
  const last = Math.min(total, Math.ceil((scrollTop + viewportH - HEADER_H) / ROW_H) + OVERSCAN);
  const visible: number[] = [];
  for (let i = first; i < last; i += 1) visible.push(i);

  const gridWidth = ROWNUM_W + widths.reduce((sum, w) => sum + w, 0);
  const atCap = hasMore && rows.length >= memoryCap;
  const empty = phase === "idle" && !hasMore && rows.length === 0;
  const selectedText =
    selected && rows[selected.row] ? cellText(rows[selected.row][selected.column]) : null;

  const barButton =
    "rounded-lg border border-white/10 px-3 py-1.5 text-[12px] font-semibold text-slate-200 transition hover:bg-white/[0.06] disabled:opacity-40";

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Data viewer: ${table.schema_name}.${table.table_name}`}
      className="fixed inset-0 z-[100] flex flex-col bg-[#050c1c] text-slate-100"
    >
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3 border-b border-white/[0.08] bg-[#071127] px-4 py-2.5">
        <button onClick={onClose} className={barButton}>
          ← Back to Catalog
        </button>
        <div className="min-w-0">
          <div className="truncate font-mono text-[14px] font-semibold text-white">
            {table.schema_name}.{table.table_name}
          </div>
          <div className="truncate text-[11px] text-slate-500">
            {source.source_name} · {source.db_type === "mssql" ? "SQL Server" : "PostgreSQL"}
            {source.database ? ` · ${source.database}` : ""}
          </div>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="flex flex-col">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search loaded rows…"
              aria-label="Search loaded rows"
              className="w-56 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-[12.5px] text-slate-100 placeholder-slate-600 outline-none focus:border-cyan-400/40"
            />
            <span className="mt-0.5 text-[10.5px] text-slate-600">
              Searches the {rows.length.toLocaleString()} rows loaded so far, not the whole table
            </span>
          </div>
          {sort && (
            <button onClick={() => setSort(null)} className={barButton} title="Clear sorting">
              Sorted by {sort.column} {sort.dir === "asc" ? "▲" : "▼"} (loaded rows only) ✕
            </button>
          )}
          <button onClick={reload} disabled={phase === "loading"} className={barButton}>
            ↻ Reload
          </button>
          <button onClick={onClose} aria-label="Close viewer" className={barButton}>
            ✕
          </button>
        </div>
      </div>

      {/* Session expired */}
      {phase === "expired" && (
        <div className="flex flex-wrap items-center gap-3 border-b border-amber-500/25 bg-amber-500/10 px-4 py-2.5 text-[12.5px] text-amber-200">
          <span>Your database session expired. Enter the password to reconnect and continue where you left off.</span>
          <input
            type="password"
            autoComplete="off"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !reconnecting) void handleReconnect();
            }}
            placeholder="Database password"
            aria-label="Database password"
            className="w-52 rounded-lg border border-white/10 bg-black/30 px-3 py-1.5 text-[12.5px] text-slate-100 placeholder-slate-600 outline-none focus:border-cyan-400/40"
          />
          <button onClick={handleReconnect} disabled={reconnecting} className={barButton}>
            {reconnecting ? "Connecting…" : "Reconnect"}
          </button>
          {reconnectError && <span className="text-rose-300">{reconnectError}</span>}
        </div>
      )}

      {/* Grid */}
      <div
        ref={scrollerRef}
        role="grid"
        aria-rowcount={total + 1}
        aria-colcount={columns.length + 1}
        onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
        className="relative min-h-0 flex-1 overflow-auto"
      >
        {columns.length > 0 && (
          <div style={{ width: gridWidth, minWidth: "100%" }}>
            <div
              role="row"
              className="sticky top-0 z-20 flex border-b border-white/[0.12] bg-[#0b1630]"
              style={{ height: HEADER_H, width: gridWidth }}
            >
              <div
                className="sticky left-0 z-30 flex-none border-r border-white/[0.12] bg-[#0b1630]"
                style={{ width: ROWNUM_W }}
              />
              {columns.map((column, i) => (
                <button
                  key={column}
                  role="columnheader"
                  onClick={() => toggleSort(column)}
                  title={`Sort loaded rows by ${column}`}
                  className="flex flex-none items-center justify-between gap-1 border-r border-white/[0.08] px-3 text-left text-[12px] font-semibold text-slate-200 hover:bg-white/[0.05]"
                  style={{ width: widths[i], minWidth: widths[i] }}
                >
                  <span className="truncate">{column}</span>
                  <span className="text-[10px] text-cyan-300">
                    {sort?.column === column ? (sort.dir === "asc" ? "▲" : "▼") : ""}
                  </span>
                </button>
              ))}
            </div>

            <div className="relative" style={{ height: total * ROW_H, width: gridWidth }}>
              {visible.map((position) => {
                const rowIndex = order ? order[position] : position;
                const row = rows[rowIndex];
                if (!row) return null;
                return (
                  <div
                    key={rowIndex}
                    role="row"
                    aria-rowindex={position + 2}
                    className={`group absolute left-0 flex border-b border-white/[0.05] hover:bg-cyan-500/[0.07] ${
                      position % 2 ? "bg-white/[0.018]" : ""
                    }`}
                    style={{ top: position * ROW_H, height: ROW_H, width: gridWidth }}
                  >
                    <div
                      className="sticky left-0 z-10 flex-none border-r border-white/[0.12] bg-[#0a1428] px-2 text-right font-mono text-[11px] leading-[30px] text-slate-500"
                      style={{ width: ROWNUM_W }}
                    >
                      {(rowIndex + 1).toLocaleString()}
                    </div>
                    {columns.map((column, i) => {
                      const value = row[column];
                      const isNull = value === null || value === undefined;
                      const isSelected = selected?.row === rowIndex && selected.column === column;
                      return (
                        <div
                          key={column}
                          role="gridcell"
                          onClick={() => setSelected({ row: rowIndex, column })}
                          title={isNull ? "NULL" : String(value)}
                          className={`flex-none cursor-cell truncate border-r border-white/[0.05] px-3 text-[12.5px] leading-[30px] ${
                            typeof value === "number" ? "text-right tabular-nums" : ""
                          } ${isSelected ? "outline outline-1 -outline-offset-1 outline-cyan-400" : ""} ${
                            isNull ? "italic text-slate-600" : "text-slate-300"
                          }`}
                          style={{ width: widths[i], minWidth: widths[i] }}
                        >
                          {isNull ? "NULL" : String(value)}
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* States drawn over the empty grid */}
        {phase === "loading" && rows.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center text-[13px] text-cyan-300">
            Loading rows…
          </div>
        )}
        {empty && (
          <div className="absolute inset-0 flex items-center justify-center text-[13px] text-slate-500">
            {emptyMessage ?? "This table is empty."}
          </div>
        )}
        {phase === "error" && rows.length === 0 && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-6 text-center">
            <p className="max-w-xl rounded-lg border border-rose-500/25 bg-rose-500/10 px-4 py-3 text-[13px] text-rose-300">
              {errorMessage}
            </p>
            <button onClick={retry} className={barButton}>
              Retry
            </button>
          </div>
        )}
        {rows.length > 0 && total === 0 && (
          <div className="absolute inset-x-0 top-20 text-center text-[13px] text-slate-500">
            No loaded rows match &ldquo;{search.trim()}&rdquo;.
            {hasMore && " More rows exist in the table; load more to search them."}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-white/[0.08] bg-[#071127] px-4 py-2 text-[12px] text-slate-400">
        <span>
          {rows.length.toLocaleString()} rows loaded
          {table.row_count !== null && ` of ~${table.row_count.toLocaleString()} (count from last scan)`}
          {order && ` · showing ${order.length.toLocaleString()} matching/sorted`}
        </span>
        {phase === "loading" && rows.length > 0 && (
          <span className="text-cyan-300">Loading more rows…</span>
        )}
        {!hasMore && rows.length > 0 && phase === "idle" && (
          <span className="text-emerald-400">All rows loaded</span>
        )}
        {phase === "error" && rows.length > 0 && (
          <span className="flex items-center gap-2 text-rose-300">
            {errorMessage}
            <button onClick={retry} className={barButton}>
              Retry
            </button>
          </span>
        )}
        {atCap && (
          <span className="flex items-center gap-2 text-amber-300">
            {rows.length.toLocaleString()} rows are in memory.
            <button onClick={() => setMemoryCap((c) => c + MEMORY_STEP)} className={barButton}>
              Load {MEMORY_STEP.toLocaleString()} more
            </button>
          </span>
        )}
        {filtering && hasMore && phase === "idle" && !atCap && (
          <span className="flex items-center gap-2 text-slate-500">
            Auto-loading is paused while searching or sorting.
            <button onClick={() => void fetchNext()} className={barButton}>
              Load more rows
            </button>
          </span>
        )}
        {selected && selectedText !== null && (
          <span className="ml-auto max-w-[40%] truncate font-mono text-slate-500" title={selectedText}>
            {selected.column} = {selectedText === "" ? "NULL / empty" : selectedText}
          </span>
        )}
      </div>
    </div>,
    document.body
  );
}
