"use client";

import { useEffect, useState } from "react";
import SourceExplorer from "@/components/catalog/SourceExplorer";
import SourceRegistrationForm from "@/components/catalog/SourceRegistrationForm";
import { DataSourceSummary, listSources } from "@/services/catalogService";

export default function CatalogPage() {
  const [sources, setSources] = useState<DataSourceSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setError(null);
    try {
      const rows = await listSources();
      setSources(rows);
      setActiveId((current) => current ?? rows[0]?.id ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load sources.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  const active = sources.find((source) => source.id === activeId) ?? null;

  return (
    // flex-1 + overflow-auto: the dashboard layout clips its content, so each page
    // supplies its own scroll area (as the other dashboard pages do).
    <div className="flex-1 overflow-auto px-6 py-6">
      <header className="mb-6">
        <h1 className="text-[22px] font-semibold tracking-tight text-white">
          Metadata Catalog
        </h1>
        <p className="mt-1 text-[13px] text-slate-500">
          Register a source, scan its metadata, and explore every table and column
          the platform knows about.
        </p>
      </header>

      {error && (
        <p className="mb-4 rounded-lg border border-rose-500/25 bg-rose-500/10 px-3 py-2 text-[12px] text-rose-300">
          {error}
        </p>
      )}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-4">
          <SourceRegistrationForm
            onRegistered={(source) => {
              setSources((current) => [source, ...current]);
              setActiveId(source.id);
            }}
          />

          <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
            <h3 className="text-[14px] font-semibold text-white">
              Registered sources
              <span className="ml-2 text-[11px] font-normal text-slate-500">
                {sources.length}
              </span>
            </h3>
            {loading && (
              <p className="mt-3 text-[12.5px] text-slate-600">Loading…</p>
            )}
            {!loading && sources.length === 0 && (
              <p className="mt-3 text-[12.5px] text-slate-600">
                None yet. Register one above.
              </p>
            )}
            <ul className="mt-3 space-y-1">
              {sources.map((source) => (
                <li key={source.id}>
                  <button
                    onClick={() => setActiveId(source.id)}
                    className={`w-full rounded-lg px-3 py-2 text-left transition ${
                      activeId === source.id
                        ? "bg-cyan-500/10 text-white"
                        : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-100"
                    }`}
                  >
                    <div className="text-[13px] font-medium">{source.source_name}</div>
                    <div className="text-[11px] text-slate-600">
                      {source.db_type} · {source.database ?? "—"} ·{" "}
                      {source.status}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div>
          {active ? (
            <SourceExplorer key={active.id} source={active} />
          ) : (
            <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
              <p className="text-[13px] text-slate-500">
                Register a data source to begin building the catalog.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
