"use client";

import { useState } from "react";
import {
  CatalogTableDetail,
  DataSourceSummary,
  SessionStatus,
} from "@/services/catalogService";
import TableDataViewer from "./TableDataViewer";

interface Props {
  source: DataSourceSummary;
  table: CatalogTableDetail;
  /** Called when the server says the database session has expired. */
  onSessionExpired: () => void;
  /** Called when the user reconnects from inside the viewer. */
  onReconnected: (status: SessionStatus) => void;
}

/**
 * The "Data preview" entry on the Catalog page. Rows are not shown here: Load
 * rows opens the full-screen viewer, which streams the table in batches as the
 * user scrolls.
 */
export default function TableDataPreview({
  source,
  table,
  onSessionExpired,
  onReconnected,
}: Props) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-5 border-t border-white/[0.06] pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-[13px] font-semibold text-white">Data preview</h4>
        <button
          onClick={() => setOpen(true)}
          className="rounded-lg border border-white/10 px-3 py-1.5 text-[12px] font-semibold text-slate-200 transition hover:bg-white/[0.06]"
        >
          Load rows
        </button>
      </div>
      <p className="mt-2 text-[12px] text-slate-500">
        Opens a full-screen viewer with the real rows of this table. Scroll to load
        more; no password is needed again while your database connection is active.
      </p>
      {open && (
        <TableDataViewer
          source={source}
          table={table}
          onClose={() => setOpen(false)}
          onSessionExpired={onSessionExpired}
          onReconnected={onReconnected}
        />
      )}
    </div>
  );
}
