"use client";

import { useCallback, useEffect, useState } from "react";
import {
  CatalogOverview,
  CatalogRelationship,
  CatalogTableDetail,
  DataSourceSummary,
  SessionStatus,
  connectSource,
  disconnectSource,
  getOverview,
  getRelationships,
  getSessionStatus,
  getTableDetail,
  isSessionExpiredError,
  scanSource,
} from "@/services/catalogService";
import TableDataPreview from "./TableDataPreview";
import RelationshipMap from "./RelationshipMap";
import { buildDataset } from "./buildDataset";

interface Props {
  source: DataSourceSummary;
}

export default function SourceExplorer({ source }: Props) {
  const [overview, setOverview] = useState<CatalogOverview | null>(null);
  const [relationships, setRelationships] = useState<CatalogRelationship[]>([]);
  const [selected, setSelected] = useState<CatalogTableDetail | null>(null);
  const [filter, setFilter] = useState("");
  // The password lives here only while the user is typing it. It is sent once
  // to open the database session, then cleared. It is never stored.
  const [password, setPassword] = useState("");
  const [session, setSession] = useState<SessionStatus | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  const [includeRowCounts, setIncludeRowCounts] = useState(true);
  const [includeSamples, setIncludeSamples] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"tables" | "map">("tables");
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Tables ticked for "Build Dataset". Separate from `selected`, which is the
  // single table shown in the detail pane.
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [buildError, setBuildError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setLoadError(null);
    setLoadingCatalog(true);
    try {
      const [ov, rels] = await Promise.all([
        getOverview(source.id),
        getRelationships(source.id),
      ]);
      setOverview(ov);
      setRelationships(rels);
      setSelected(null);
      // A rescan can remove tables; keep only ticks that still exist.
      setChecked((prev) => new Set([...prev].filter((id) => ov.tables.some((t) => t.id === id))));
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load the catalog.";
      setError(message);
      setLoadError(message);
    } finally {
      setLoadingCatalog(false);
    }
  }, [source.id]);

  useEffect(() => {
    void load();
  }, [load]);

  // Windows authentication has no password, so there is no session to manage.
  const needsSession = !source.trusted_connection;
  const connected = !needsSession || session?.connected === true;

  const markExpired = useCallback(() => {
    setSession(null);
    setSessionExpired(true);
    setNotice(null);
  }, []);

  // Ask the server whether this browser still has a live session, now and
  // every 30 seconds, so an expiry is noticed before the next click.
  useEffect(() => {
    setSession(null);
    setSessionExpired(false);
    setPassword("");
    if (!needsSession) return;
    let cancelled = false;
    let wasConnected = false;
    async function check() {
      try {
        const status = await getSessionStatus(source.id);
        if (cancelled) return;
        if (status.connected) {
          wasConnected = true;
          setSession(status);
          setSessionExpired(false);
        } else {
          setSession(null);
          if (wasConnected) {
            wasConnected = false;
            setSessionExpired(true);
          }
        }
      } catch {
        /* the status check is best effort; real calls report their own errors */
      }
    }
    void check();
    const timer = setInterval(() => void check(), 30_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [source.id, needsSession]);

  async function handleConnect() {
    setConnecting(true);
    setError(null);
    setNotice(null);
    try {
      await connectSource(source.id, password);
      setPassword("");
      // Confirm the browser kept the session cookie. If it did not (the app
      // and the API are on different hosts), say so instead of failing later.
      const status = await getSessionStatus(source.id);
      if (!status.connected) {
        setError(
          "Connected, but your browser did not keep the session. Open this " +
            "app at the same host name as the API (for example localhost " +
            "for both), then connect again."
        );
        return;
      }
      setSession(status);
      setSessionExpired(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not connect.");
    } finally {
      setConnecting(false);
    }
  }

  async function handleDisconnect() {
    setError(null);
    setNotice(null);
    try {
      await disconnectSource(source.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not disconnect.");
      return;
    }
    setSession(null);
    setSessionExpired(false);
    setSelected(null);
    setNotice("Disconnected. The database password was removed from the server.");
  }

  async function handleScan() {
    setScanning(true);
    setError(null);
    setNotice(null);
    try {
      const result = await scanSource(source.id, {
        db_type: source.db_type,
        host: source.host ?? "",
        port: source.port ?? undefined,
        database: source.database ?? "",
        username: source.username ?? undefined,
        trusted_connection: source.trusted_connection,
        schema_name: source.schema_name ?? undefined,
        include_row_counts: includeRowCounts,
        include_sample_values: includeSamples,
      });
      setNotice(
        `Scanned ${result.table_count} tables, ${result.column_count} columns, ` +
          `${result.relationship_count} declared relationships in ${result.duration_ms ?? 0} ms.`
      );
      await load();
    } catch (err) {
      if (isSessionExpiredError(err)) {
        markExpired();
        return;
      }
      setError(err instanceof Error ? err.message : "Scan failed.");
    } finally {
      setScanning(false);
    }
  }

  async function openTable(tableId: string) {
    setError(null);
    try {
      setSelected(await getTableDetail(tableId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load the table.");
    }
  }

  const tables = (overview?.tables ?? []).filter((table) =>
    table.table_name.toLowerCase().includes(filter.trim().toLowerCase())
  );

  function toggleChecked(tableId: string) {
    setBuildError(null);
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(tableId)) next.delete(tableId);
      else next.add(tableId);
      return next;
    });
  }

  function startBuild(newTab: boolean) {
    if (!overview) return;
    // Keep the order of the Catalog list, and only tables that still exist.
    const ids = overview.tables.filter((t) => checked.has(t.id)).map((t) => t.id);
    setBuildError(buildDataset({ sourceId: source.id, tableIds: ids, catalogTables: overview.tables, declared: relationships, newTab }));
  }

  const card = "rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5";
  const field =
    "w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-[13px] text-slate-100 placeholder-slate-600 outline-none focus:border-cyan-400/40";

  return (
    <div className="space-y-4">
      {/* Scan controls */}
      <div className={card}>
        <div className="flex flex-wrap items-end gap-3">
          {needsSession && !connected && (
            <div className="min-w-[180px] flex-1">
              <label
                className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wide text-slate-400"
                htmlFor="scan-pw"
              >
                Database password
              </label>
              <input
                id="scan-pw"
                type="password"
                autoComplete="off"
                className={field}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !connecting) void handleConnect();
                }}
                placeholder="Kept in server memory for this session only"
              />
            </div>
          )}
          {needsSession && !connected && (
            <button
              onClick={handleConnect}
              disabled={connecting}
              className="rounded-xl bg-gradient-to-r from-cyan-500 to-blue-600 px-4 py-2 text-[13px] font-semibold text-white disabled:opacity-40"
            >
              {connecting ? "Connecting…" : sessionExpired ? "Reconnect" : "Connect"}
            </button>
          )}
          {needsSession && connected && (
            <div className="flex min-w-[180px] flex-1 items-center gap-3 pb-1">
              <span className="inline-flex items-center gap-2 rounded-full border border-emerald-400/25 bg-emerald-500/10 px-3 py-1 text-[12px] font-semibold text-emerald-300">
                <span className="h-2 w-2 rounded-full bg-emerald-400" />
                Connected
                {session?.expires_in_seconds != null &&
                  ` · about ${Math.max(1, Math.round(session.expires_in_seconds / 60))} min left`}
              </span>
              <button
                onClick={handleDisconnect}
                className="rounded-lg border border-white/10 px-3 py-1.5 text-[12px] font-semibold text-slate-200 transition hover:bg-white/[0.06]"
              >
                Disconnect
              </button>
            </div>
          )}
          {!needsSession && (
            <p className="min-w-[180px] flex-1 pb-2 text-[12px] text-slate-400">
              Windows authentication: no password needed.
            </p>
          )}
          <label className="flex items-center gap-2 pb-2 text-[12px] text-slate-400">
            <input
              type="checkbox"
              checked={includeRowCounts}
              onChange={(e) => setIncludeRowCounts(e.target.checked)}
            />
            Row counts
          </label>
          <label className="flex items-center gap-2 pb-2 text-[12px] text-slate-400">
            <input
              type="checkbox"
              checked={includeSamples}
              onChange={(e) => setIncludeSamples(e.target.checked)}
            />
            Sample values
          </label>
          <button
            onClick={handleScan}
            disabled={scanning || !connected}
            className="rounded-xl bg-gradient-to-r from-cyan-500 to-blue-600 px-4 py-2 text-[13px] font-semibold text-white disabled:opacity-40"
          >
            {scanning ? "Scanning…" : "Scan metadata"}
          </button>
        </div>
        {includeSamples && (
          <p className="mt-2 text-[11.5px] text-amber-300/80">
            Sample values are customer row data. They are stored in the catalog and
            excluded from every AI payload.
          </p>
        )}
        {sessionExpired && (
          <p className="mt-2 rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-300">
            Your database session expired. Enter the password and press
            Reconnect to keep exploring.
          </p>
        )}
        {notice && <p className="mt-2 text-[12px] text-emerald-400">{notice}</p>}
        {error && (
          <p className="mt-2 rounded-lg border border-rose-500/25 bg-rose-500/10 px-3 py-2 text-[12px] text-rose-300">
            {error}
          </p>
        )}
      </div>

      {/* Summary */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          ["Tables", overview?.table_count ?? 0],
          ["Columns", overview?.column_count ?? 0],
          ["Relationships", overview?.relationship_count ?? 0],
          ["Schema", overview?.schema_name ?? "—"],
        ].map(([label, value]) => (
          <div key={String(label)} className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3">
            <div className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-500">
              {label}
            </div>
            <div className="mt-1 text-[19px] font-semibold text-white">{value}</div>
          </div>
        ))}
      </div>

      {/* View switch: the existing table browser, or the relationship map */}
      <div className="flex gap-1 rounded-xl border border-white/[0.06] bg-white/[0.02] p-1 text-[12.5px] font-semibold sm:w-fit">
        {(
          [
            ["tables", "Tables & columns"],
            ["map", "Relationship map"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setView(key)}
            aria-pressed={view === key}
            className={`rounded-lg px-4 py-1.5 transition ${
              view === key
                ? "bg-cyan-500/15 text-cyan-200"
                : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-100"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {view === "map" && (
        <RelationshipMap
          source={source}
          onSessionExpired={markExpired}
          overview={overview}
          relationships={relationships}
          loading={loadingCatalog}
          error={loadError}
          onRefresh={() => void load()}
        />
      )}

      {view === "tables" && overview && overview.table_count === 0 && (
        <div className={card}>
          <p className="text-[13px] text-slate-400">
            No metadata yet. Connect above, then run a scan to build the catalog
            for <span className="text-slate-200">{source.source_name}</span>.
          </p>
        </div>
      )}

      {/* Tables + detail */}
      {view === "tables" && overview && overview.table_count > 0 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
          <div className={card}>
            <input
              className={field}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter tables…"
            />
            <div
              className="mt-3 rounded-lg border border-white/[0.08] bg-white/[0.02] p-2.5"
              data-testid="build-bar"
            >
              <div className="flex flex-wrap items-center gap-2 text-[12px]">
                <span className="font-semibold text-slate-200" aria-live="polite" data-testid="selected-count">
                  {checked.size === 0 ? "No tables ticked" : `${checked.size} table${checked.size === 1 ? "" : "s"} selected`}
                </span>
                {checked.size > 0 && (
                  <button className="text-slate-400 underline hover:text-slate-100" onClick={() => setChecked(new Set())}>
                    Clear selection
                  </button>
                )}
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  onClick={() => startBuild(false)}
                  disabled={checked.size === 0}
                  className="rounded-lg bg-gradient-to-r from-cyan-500 to-blue-600 px-3 py-1.5 text-[12.5px] font-semibold text-white disabled:opacity-40"
                >
                  Build Dataset{checked.size > 0 ? ` (${checked.size})` : ""}
                </button>
                <button
                  onClick={() => startBuild(true)}
                  disabled={checked.size === 0}
                  className="rounded-lg border border-white/10 px-3 py-1.5 text-[12px] text-slate-200 hover:bg-white/[0.06] disabled:opacity-40"
                  title="Open the Visual ETL Builder in a new browser tab"
                >
                  New tab ↗
                </button>
              </div>
              <p className="mt-1.5 text-[11px] text-slate-500">
                Tick the tables you need. Opening the Builder only places them on its canvas; nothing is run or changed.
              </p>
              {buildError && <p className="mt-1.5 text-[11.5px] text-rose-300">{buildError}</p>}
            </div>
            <ul className="mt-3 max-h-[420px] space-y-1 overflow-y-auto">
              {tables.map((table) => (
                <li key={table.id} className="flex items-center gap-1">
                  <input
                    type="checkbox"
                    className="ml-1 h-3.5 w-3.5 shrink-0 accent-cyan-500"
                    checked={checked.has(table.id)}
                    onChange={() => toggleChecked(table.id)}
                    aria-label={`Select ${table.table_name} for Build Dataset`}
                  />
                  <button
                    onClick={() => openTable(table.id)}
                    className={`min-w-0 flex-1 rounded-lg px-2 py-2 text-left text-[12.5px] transition ${
                      selected?.id === table.id
                        ? "bg-cyan-500/10 text-white"
                        : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-100"
                    }`}
                  >
                    <span className="font-mono">{table.table_name}</span>
                    <span className="ml-2 text-[11px] text-slate-600">
                      {table.column_count} cols
                      {table.row_count !== null && ` · ${table.row_count.toLocaleString()} rows`}
                    </span>
                  </button>
                </li>
              ))}
              {tables.length === 0 && (
                <li className="px-3 py-2 text-[12px] text-slate-600">No match.</li>
              )}
            </ul>
          </div>

          <div className={card}>
            {!selected && (
              <p className="text-[13px] text-slate-500">
                Select a table to see its columns.
              </p>
            )}
            {selected && (
              <>
                <h3 className="font-mono text-[15px] font-semibold text-white">
                  {selected.schema_name}.{selected.table_name}
                </h3>
                <p className="mt-1 text-[12px] text-slate-500">
                  {selected.column_count} columns
                  {selected.row_count !== null &&
                    ` · ${selected.row_count.toLocaleString()} rows`}
                  {selected.has_primary_key ? " · has a primary key" : " · no primary key"}
                </p>
                <div className="mt-3 max-h-[380px] overflow-auto">
                  <table className="w-full text-[12.5px]">
                    <thead>
                      <tr className="text-[10.5px] uppercase tracking-wider text-slate-500">
                        <th className="px-2 py-2 text-left">Column</th>
                        <th className="px-2 py-2 text-left">Type</th>
                        <th className="px-2 py-2 text-left">Null</th>
                        <th className="px-2 py-2 text-left">Key</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selected.columns.map((column) => (
                        <tr key={column.column_name} className="border-t border-white/[0.05]">
                          <td className="px-2 py-2 font-mono text-slate-200">
                            {column.column_name}
                          </td>
                          <td className="px-2 py-2 font-mono text-slate-500">
                            {column.data_type}
                          </td>
                          <td className="px-2 py-2 text-slate-500">
                            {column.is_nullable ? "yes" : "no"}
                          </td>
                          <td className="px-2 py-2">
                            {column.is_primary_key && (
                              <span className="rounded-full border border-cyan-400/30 bg-cyan-500/10 px-2 py-[1px] text-[10px] font-semibold text-cyan-300">
                                PK
                              </span>
                            )}
                            {column.is_foreign_key && (
                              <span className="ml-1 rounded-full border border-violet-400/30 bg-violet-500/10 px-2 py-[1px] text-[10px] font-semibold text-violet-300">
                                FK
                              </span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <TableDataPreview
                  key={selected.id}
                  source={source}
                  table={selected}
                  onSessionExpired={markExpired}
                  onReconnected={(status) => {
                    setSession(status);
                    setSessionExpired(false);
                  }}
                />
              </>
            )}
          </div>
        </div>
      )}

      {/* Declared relationships */}
      {view === "tables" && relationships.length > 0 && (
        <div className={card}>
          <h3 className="text-[14px] font-semibold text-white">
            Declared relationships
            <span className="ml-2 text-[11px] font-normal text-slate-500">
              {relationships.length} foreign keys found
            </span>
          </h3>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="text-[10.5px] uppercase tracking-wider text-slate-500">
                  <th className="px-2 py-2 text-left">From</th>
                  <th className="px-2 py-2 text-left">To</th>
                  <th className="px-2 py-2 text-left">Constraint</th>
                  <th className="px-2 py-2 text-left">Origin</th>
                </tr>
              </thead>
              <tbody>
                {relationships.map((edge) => (
                  <tr key={edge.id} className="border-t border-white/[0.05]">
                    <td className="px-2 py-2 font-mono text-slate-200">
                      {edge.left_table}.{edge.left_column}
                    </td>
                    <td className="px-2 py-2 font-mono text-slate-200">
                      {edge.right_table}.{edge.right_column}
                    </td>
                    <td className="px-2 py-2 font-mono text-slate-600">
                      {edge.constraint_name ?? "—"}
                    </td>
                    <td className="px-2 py-2">
                      <span className="rounded-full border border-emerald-400/25 bg-emerald-500/10 px-2 py-[1px] text-[10px] font-semibold text-emerald-300">
                        {edge.origin}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-[11.5px] text-slate-600">
            Declared foreign keys only. Inferred relationships and approval arrive in
            Sprint 2; a path is still only verified by join_verifier.
          </p>
        </div>
      )}
    </div>
  );
}
