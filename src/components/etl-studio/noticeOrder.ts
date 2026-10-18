/** Ordering rules for the notices area. Pure, so they can be tested without a browser. */

export type NoticeSeverity = "error" | "action" | "warning" | "info";

export interface NoticeItem {
  key: string;
  severity: NoticeSeverity;
  node: unknown;
}

const RANK: Record<NoticeSeverity, number> = { error: 0, action: 1, warning: 2, info: 3 };

/** Sorted by importance (stable inside one level) and with surplus information split off. Pure. */
export function arrangeNotices(items: readonly NoticeItem[], maxInfo: number): { shown: NoticeItem[]; hiddenInfo: NoticeItem[] } {
  const sorted = items.map((it, i) => ({ it, i })).sort((a, b) => RANK[a.it.severity] - RANK[b.it.severity] || a.i - b.i).map((x) => x.it);
  const shown: NoticeItem[] = [];
  const hiddenInfo: NoticeItem[] = [];
  let info = 0;
  for (const it of sorted) {
    if (it.severity === "info") {
      info += 1;
      if (info > maxInfo) {
        hiddenInfo.push(it);
        continue;
      }
    }
    shown.push(it);
  }
  return { shown, hiddenInfo };
}

