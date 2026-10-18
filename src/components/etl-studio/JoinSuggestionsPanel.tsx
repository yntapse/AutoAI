"use client";

/**
 * Join suggestions for the tables on the canvas. These come from rules applied
 * to Catalog facts (declared foreign keys, names, types, keys). No AI model is
 * involved and no confidence number is invented. Accepting a suggestion only
 * adds a join node to the canvas; the join is measured and checked by the
 * server like any other step.
 */

import { useState } from "react";
import { JOIN_KINDS, JOIN_TYPE_HELP, TIER_LABELS, type JoinKind, type JoinSuggestion } from "./joinSuggestions";

interface Props {
  suggestions: JoinSuggestion[];
  tableName: (tableId: string) => string;
  rejectedCount: number;
  /** True when the Catalog facts produced at least one suggestion (before any were handled). */
  anySuggestions: boolean;
  tablesOnCanvas: number;
  onAccept: (s: JoinSuggestion, joinType: JoinKind) => void;
  onEdit: (s: JoinSuggestion, joinType: JoinKind) => void;
  onReject: (s: JoinSuggestion) => void;
  onRestore: () => void;
  onClose: () => void;
}

const TIER_STYLE = {
  confirmed: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  defined: "border-violet-500/30 bg-violet-500/10 text-violet-300",
  strong: "border-cyan-500/30 bg-cyan-500/10 text-cyan-300",
  possible: "border-amber-500/30 bg-amber-500/10 text-amber-300",
} as const;

const ORIGIN_LABEL = {
  declared: "Declared in the database",
  workspace: "Defined on the Relationship Map",
  inferred: "Inferred by rules",
} as const;

function SuggestionCard({ s, tableName, onAccept, onEdit, onReject }: {
  s: JoinSuggestion;
  tableName: (id: string) => string;
  onAccept: Props["onAccept"];
  onEdit: Props["onEdit"];
  onReject: Props["onReject"];
}) {
  const [joinType, setJoinType] = useState<JoinKind>(s.recommendedJoinType);
  return (
    <li className="rounded-xl border border-white/[0.08] p-3" data-testid="join-suggestion" data-origin={s.origin} data-pairs={s.pairs.length}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full border px-2 py-[1px] text-[10.5px] ${TIER_STYLE[s.tier]}`}>{TIER_LABELS[s.tier]}</span>
        <span className="rounded-full border border-white/[0.1] px-2 py-[1px] text-[10.5px] text-slate-400">{ORIGIN_LABEL[s.origin]}</span>
        <span className="rounded-full border border-amber-500/30 px-2 py-[1px] text-[10.5px] text-amber-300" data-testid="not-verified">
          Not yet verified
        </span>
      </div>
      <div className="mt-1.5 space-y-0.5 text-[12.5px] text-white">
        {s.pairs.map((p) => (
          <div key={`${p.leftColumn}|${p.rightColumn}`}>
            {tableName(s.leftTableId)}.{p.leftColumn} <span className="text-slate-500">=</span> {tableName(s.rightTableId)}.{p.rightColumn}
          </div>
        ))}
      </div>
      {s.pairs.length > 1 && <p className="mt-0.5 text-[11px] text-slate-500">All {s.pairs.length} column pairs must match together (a composite key).</p>}
      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11.5px] text-slate-400">
        {s.evidence.map((e) => (
          <li key={e}>{e}</li>
        ))}
      </ul>
      {s.warnings.length > 0 && (
        <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11.5px] text-amber-300">
          {s.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      <p className="mt-1 text-[11.5px] text-slate-500">How well the values match has not been measured. Add the join, then press &ldquo;Measure this join&rdquo;.</p>
      <div className="mt-2 rounded-lg bg-white/[0.03] p-2">
        <label className="text-[11.5px] text-slate-300">
          Join type{" "}
          <select
            className="ml-1 rounded border border-white/[0.12] bg-[#0b1220] px-1.5 py-0.5 text-[11.5px] text-white"
            value={joinType}
            onChange={(e) => setJoinType(e.target.value as JoinKind)}
            aria-label="Join type for this suggestion"
          >
            {JOIN_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}{k === s.recommendedJoinType ? " (recommended)" : ""}
              </option>
            ))}
          </select>
        </label>
        <p className="mt-1 text-[11.5px] text-slate-400" data-testid="join-type-help">{JOIN_TYPE_HELP[joinType]}</p>
        {joinType === s.recommendedJoinType && <p className="mt-0.5 text-[11.5px] text-slate-500">{s.joinTypeReason}</p>}
      </div>
      <div className="mt-2 flex gap-1.5">
        <button className="rounded-md bg-cyan-500/90 px-2.5 py-1 text-[11.5px] font-medium text-[#04202a] hover:bg-cyan-400" onClick={() => onAccept(s, joinType)}>
          Accept
        </button>
        <button className="rounded-md border border-white/[0.1] px-2.5 py-1 text-[11.5px] text-slate-300 hover:bg-white/[0.06]" onClick={() => onEdit(s, joinType)}>
          Edit
        </button>
        <button className="rounded-md border border-white/[0.1] px-2.5 py-1 text-[11.5px] text-slate-400 hover:bg-white/[0.06]" onClick={() => onReject(s)}>
          Reject
        </button>
      </div>
    </li>
  );
}

export default function JoinSuggestionsPanel(props: Props) {
  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4" data-testid="join-suggestions">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-[14px] font-semibold text-white">Suggested joins</h3>
          <p className="mt-0.5 text-[11.5px] text-slate-500">
            One list for everything the Catalog knows: database foreign keys, joins you drew on the Relationship Map, and rule-based guesses
            from names and types. No AI model is used. Nothing is added until you press Accept or Edit.
          </p>
        </div>
        <button className="text-[12px] text-slate-500 hover:text-slate-200" onClick={props.onClose} aria-label="Close suggestions">
          ✕
        </button>
      </div>

      {props.tablesOnCanvas < 2 && (
        <p className="mt-3 text-[12.5px] text-slate-500">Add at least two tables to the canvas to get suggestions.</p>
      )}
      {props.tablesOnCanvas >= 2 && props.suggestions.length === 0 && (
        <p className="mt-3 text-[12.5px] text-slate-500">
          {props.anySuggestions
            ? "Every suggestion has been accepted or rejected."
            : "No relationship could be supported by the Catalog facts for these tables. You can still add a join and choose the columns yourself."}
        </p>
      )}

      <ul className="mt-3 space-y-2">
        {props.suggestions.map((s) => (
          <SuggestionCard key={s.id} s={s} tableName={props.tableName} onAccept={props.onAccept} onEdit={props.onEdit} onReject={props.onReject} />
        ))}
      </ul>
      {props.rejectedCount > 0 && (
        <button className="mt-3 text-[11.5px] text-slate-500 underline hover:text-slate-200" onClick={props.onRestore}>
          Show {props.rejectedCount} rejected suggestion{props.rejectedCount === 1 ? "" : "s"} again
        </button>
      )}
    </div>
  );
}
