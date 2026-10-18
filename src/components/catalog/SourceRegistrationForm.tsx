"use client";

import { useState } from "react";
import {
  DbType,
  DataSourceSummary,
  registerSource,
} from "@/services/catalogService";

interface Props {
  onRegistered: (source: DataSourceSummary) => void;
}

const DEFAULT_PORT: Record<DbType, number> = {
  postgresql: 5432,
  mssql: 1433,
};

const DEFAULT_SCHEMA: Record<DbType, string> = {
  postgresql: "public",
  mssql: "dbo",
};

export default function SourceRegistrationForm({ onRegistered }: Props) {
  const [dbType, setDbType] = useState<DbType>("postgresql");
  const [sourceName, setSourceName] = useState("");
  const [host, setHost] = useState("localhost");
  const [port, setPort] = useState<number>(DEFAULT_PORT.postgresql);
  const [database, setDatabase] = useState("");
  const [username, setUsername] = useState("");
  const [schemaName, setSchemaName] = useState(DEFAULT_SCHEMA.postgresql);
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function changeDbType(next: DbType) {
    setDbType(next);
    setPort(DEFAULT_PORT[next]);
    setSchemaName(DEFAULT_SCHEMA[next]);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const source = await registerSource({
        source_name: sourceName.trim(),
        db_type: dbType,
        host: host.trim() || undefined,
        port,
        database: database.trim() || undefined,
        username: username.trim() || undefined,
        schema_name: schemaName.trim() || undefined,
        description: description.trim() || undefined,
      });
      setSourceName("");
      setDatabase("");
      setDescription("");
      onRegistered(source);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to register source.");
    } finally {
      setBusy(false);
    }
  }

  const field =
    "w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-[13px] text-slate-100 placeholder-slate-600 outline-none focus:border-cyan-400/40";
  const label = "mb-1.5 block text-[11px] font-semibold tracking-wide text-slate-400 uppercase";

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5"
    >
      <h3 className="text-[15px] font-semibold text-white">Register a data source</h3>
      <p className="mt-1 text-[12px] text-slate-500">
        Connection details only. The password is never stored &mdash; you supply it
        when you run a scan.
      </p>

      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label className={label} htmlFor="src-name">Source name</label>
          <input
            id="src-name"
            className={field}
            value={sourceName}
            onChange={(e) => setSourceName(e.target.value)}
            placeholder="meridian_dw"
            required
          />
        </div>

        <div>
          <label className={label} htmlFor="src-type">Database type</label>
          <select
            id="src-type"
            className={field}
            value={dbType}
            onChange={(e) => changeDbType(e.target.value as DbType)}
          >
            <option value="postgresql">PostgreSQL</option>
            <option value="mssql">SQL Server</option>
          </select>
        </div>

        <div>
          <label className={label} htmlFor="src-schema">Schema</label>
          <input
            id="src-schema"
            className={field}
            value={schemaName}
            onChange={(e) => setSchemaName(e.target.value)}
          />
        </div>

        <div>
          <label className={label} htmlFor="src-host">Host</label>
          <input
            id="src-host"
            className={field}
            value={host}
            onChange={(e) => setHost(e.target.value)}
          />
        </div>

        <div>
          <label className={label} htmlFor="src-port">Port</label>
          <input
            id="src-port"
            type="number"
            className={field}
            value={port}
            onChange={(e) => setPort(Number(e.target.value))}
          />
        </div>

        <div>
          <label className={label} htmlFor="src-db">Database</label>
          <input
            id="src-db"
            className={field}
            value={database}
            onChange={(e) => setDatabase(e.target.value)}
            placeholder="meridian"
          />
        </div>

        <div>
          <label className={label} htmlFor="src-user">Username</label>
          <input
            id="src-user"
            className={field}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="svc_readonly"
          />
        </div>

        <div className="sm:col-span-2">
          <label className={label} htmlFor="src-desc">Description</label>
          <input
            id="src-desc"
            className={field}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Core banking warehouse"
          />
        </div>
      </div>

      {error && (
        <p className="mt-3 rounded-lg border border-rose-500/25 bg-rose-500/10 px-3 py-2 text-[12px] text-rose-300">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={busy || !sourceName.trim()}
        className="mt-4 rounded-xl bg-gradient-to-r from-cyan-500 to-blue-600 px-4 py-2 text-[13px] font-semibold text-white disabled:opacity-40"
      >
        {busy ? "Registering…" : "Register source"}
      </button>
    </form>
  );
}
