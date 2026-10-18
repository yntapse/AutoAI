"use client";

/**
 * One place for every message the Builder shows above the canvas.
 *
 * Order is by importance, not by arrival: errors first, then things that need the
 * user to act, then warnings, then plain information. Errors, required actions and
 * warnings are never folded away. Only surplus information is, behind a visible
 * "N more messages" button.
 */

import { useState, type ReactNode } from "react";

import { arrangeNotices, type NoticeItem } from "./noticeOrder";

export type { NoticeItem, NoticeSeverity } from "./noticeOrder";

export default function Notices({ items, maxInfo = 1 }: { items: readonly NoticeItem[]; maxInfo?: number }) {
  const [expanded, setExpanded] = useState(false);
  if (items.length === 0) return null;
  const { shown, hiddenInfo } = arrangeNotices(items, expanded ? Number.POSITIVE_INFINITY : maxInfo);
  return (
    <section className="mb-3 space-y-2" aria-label="Messages" data-testid="notices">
      {shown.map((it) => (
        <div key={it.key} data-severity={it.severity}>
          {it.node as ReactNode}
        </div>
      ))}
      {hiddenInfo.length > 0 && (
        <button
          className="rounded-lg border border-white/[0.1] px-2.5 py-1 text-[12px] text-slate-300 hover:bg-white/[0.06]"
          onClick={() => setExpanded(true)}
          data-testid="notices-more"
        >
          Show {hiddenInfo.length} more message{hiddenInfo.length === 1 ? "" : "s"}
        </button>
      )}
      {expanded && items.filter((i) => i.severity === "info").length > maxInfo && (
        <button className="rounded-lg border border-white/[0.1] px-2.5 py-1 text-[12px] text-slate-300 hover:bg-white/[0.06]" onClick={() => setExpanded(false)}>
          Show fewer
        </button>
      )}
    </section>
  );
}
