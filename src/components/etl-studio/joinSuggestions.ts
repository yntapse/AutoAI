/**
 * Deterministic join suggestions for the tables on the pipeline canvas.
 *
 * No AI model is used here, so this works without any provider configuration
 * and gives the same answer every time. It looks only at facts the Catalog
 * already holds: declared foreign keys, column names, data types and key flags.
 *
 * It never invents a score. Each suggestion has a tier that follows directly
 * from the evidence listed with it:
 *   confirmed - a foreign key declared in the database.
 *   strong    - names agree, types agree, and at least one side is a key.
 *   possible  - names agree but there is little else to go on, or types differ.
 * Whether the values really match is measured on the real data (join
 * diagnostics); a suggestion is never described as safe before that.
 *
 * One suggestion is one join, and a join may match on several column pairs
 * (a composite key). The same relationship is never listed twice, whichever
 * source it came from (database foreign key, a join defined on the Catalog's
 * Relationship Map, or a rule).
 */

export interface SuggestColumn {
  name: string;
  dataType: string;
  /** pipelineModel.typeCategory(dataType); supplied by the caller. */
  category: string;
  isPrimaryKey: boolean;
  isForeignKey: boolean;
}

export interface SuggestTable {
  id: string;
  name: string;
  columns: SuggestColumn[];
}

/** A declared foreign key, by table name (as the Catalog stores it). */
export interface DeclaredLink {
  childTable: string;
  childColumn: string;
  parentTable: string;
  parentColumn: string;
  constraintName: string | null;
}

/** A join the user defined on the Catalog's Relationship Map. Not a database constraint. */
export interface UserRelationship {
  leftTableId: string;
  rightTableId: string;
  pairs: { leftColumn: string; rightColumn: string }[];
  /** The join type the user chose on the map, if any. */
  joinType: string | null;
}

export type Tier = "confirmed" | "defined" | "strong" | "possible";
export type JoinKind = "INNER" | "LEFT" | "RIGHT" | "FULL";
export interface ColumnPair {
  leftColumn: string;
  rightColumn: string;
}

export interface JoinSuggestion {
  id: string;
  leftTableId: string;
  rightTableId: string;
  /** First column pair (kept for simple callers); see `pairs` for the full list. */
  leftColumn: string;
  rightColumn: string;
  /** Every column pair of this join; more than one means a composite key. */
  pairs: ColumnPair[];
  /** declared = foreign key in the database; workspace = defined by the user on the map; inferred = rules. */
  origin: "declared" | "workspace" | "inferred";
  tier: Tier;
  evidence: string[];
  warnings: string[];
  /** True when the types differ, so a conversion would be needed first. */
  needsCleaning: boolean;
  /** Always false here: nothing in this module looks at the data. */
  verified: false;
  recommendedJoinType: JoinKind;
  /** Plain-language reason for the recommended join type. */
  joinTypeReason: string;
}

const TIER_ORDER: Record<Tier, number> = { confirmed: 0, defined: 1, strong: 2, possible: 3 };
const MAX_SUGGESTIONS = 25;

/** Column names so common that matching them says nothing. */
const GENERIC_NAMES = new Set([
  "id", "name", "description", "status", "type", "code", "notes", "note", "comment", "comments",
  "createdat", "updatedat", "createdby", "updatedby", "createddate", "updateddate", "modifieddate",
  "isactive", "active", "deleted", "version", "value", "title", "label", "rowversion",
]);

export function normalizeName(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function singular(word: string): string {
  const w = normalizeName(word);
  if (w.endsWith("ies") && w.length > 4) return `${w.slice(0, -3)}y`;
  if (w.endsWith("ses") && w.length > 4) return w.slice(0, -2);
  if (w.endsWith("s") && !w.endsWith("ss") && w.length > 3) return w.slice(0, -1);
  return w;
}

function sameFamily(a: SuggestColumn, b: SuggestColumn): boolean {
  return a.category === b.category;
}

function pairId(lt: string, lc: string, rt: string, rc: string): string {
  return `${lt}.${lc}=${rt}.${rc}`;
}

/** Plain-language meaning of each join type, used by the cards and the join editor. */
export const JOIN_TYPE_HELP: Record<JoinKind, string> = {
  INNER: "Keeps only the rows that have a match on both sides. Rows without a match are dropped from the result.",
  LEFT: "Keeps every row from the left table. Where the right table has no match, its columns are left empty.",
  RIGHT: "Keeps every row from the right table. Where the left table has no match, its columns are left empty.",
  FULL: "Keeps every row from both tables. Where one side has no match, its columns are left empty.",
};
export const JOIN_KINDS: JoinKind[] = ["INNER", "LEFT", "RIGHT", "FULL"];

function isJoinKind(x: string | null): x is JoinKind {
  return x === "INNER" || x === "LEFT" || x === "RIGHT" || x === "FULL";
}

function recommend(leftName: string, rightName: string, userType: string | null): { type: JoinKind; reason: string } {
  if (isJoinKind(userType)) {
    return { type: userType, reason: `You chose ${userType} when you defined this relationship on the Relationship Map.` };
  }
  return {
    type: "LEFT",
    reason: `LEFT keeps every ${leftName} row, even one with no match in ${rightName}. INNER would silently drop those rows; choose INNER only if you want matched rows alone.`,
  };
}

/** Same relationship, whichever way round it is written. */
function canonicalKey(leftId: string, rightId: string, pairs: readonly ColumnPair[]): string {
  const flip = leftId > rightId;
  const [a, b] = flip ? [rightId, leftId] : [leftId, rightId];
  const ps = pairs.map((p) => (flip ? `${p.rightColumn}\u0000${p.leftColumn}` : `${p.leftColumn}\u0000${p.rightColumn}`)).sort();
  return `${a}|${b}|${ps.join("|")}`;
}

function suggestionId(leftId: string, rightId: string, pairs: readonly ColumnPair[]): string {
  return pairs.length === 1
    ? pairId(leftId, pairs[0].leftColumn, rightId, pairs[0].rightColumn)
    : `${leftId}->${rightId}:${pairs.map((p) => `${p.leftColumn}=${p.rightColumn}`).join("+")}`;
}

function describePairs(t1: string, t2: string, pairs: readonly ColumnPair[]): { a: string; b: string } {
  if (pairs.length === 1) return { a: `${t1}.${pairs[0].leftColumn}`, b: `${t2}.${pairs[0].rightColumn}` };
  return { a: `${t1}.(${pairs.map((p) => p.leftColumn).join(", ")})`, b: `${t2}.(${pairs.map((p) => p.rightColumn).join(", ")})` };
}

function declaredSuggestions(a: SuggestTable, b: SuggestTable, links: readonly DeclaredLink[]): JoinSuggestion[] {
  // One foreign key with several columns is one join. Links are grouped by the
  // database's constraint name; a link without a name is never merged.
  const groups = new Map<string, { child: SuggestTable; parent: SuggestTable; name: string | null; links: DeclaredLink[] }>();
  links.forEach((link, index) => {
    let child: SuggestTable | null = null;
    let parent: SuggestTable | null = null;
    if (link.childTable === a.name && link.parentTable === b.name) [child, parent] = [a, b];
    else if (link.childTable === b.name && link.parentTable === a.name) [child, parent] = [b, a];
    if (!child || !parent) return;
    const key = link.constraintName ? `${child.id}>${parent.id}#${link.constraintName}` : `anon${index}`;
    const g = groups.get(key);
    if (g) g.links.push(link);
    else groups.set(key, { child, parent, name: link.constraintName || null, links: [link] });
  });

  const out: JoinSuggestion[] = [];
  for (const g of groups.values()) {
    const pairs: ColumnPair[] = [];
    const warnings: string[] = [];
    let complete = true;
    for (const link of g.links) {
      const cc = g.child.columns.find((c) => c.name === link.childColumn);
      const pc = g.parent.columns.find((c) => c.name === link.parentColumn);
      if (!cc || !pc) {
        complete = false;
        break;
      }
      if (!sameFamily(cc, pc)) warnings.push(`The column types differ (${cc.dataType} and ${pc.dataType}); check the result carefully.`);
      if (!pairs.some((p) => p.leftColumn === cc.name && p.rightColumn === pc.name)) pairs.push({ leftColumn: cc.name, rightColumn: pc.name });
    }
    if (!complete || pairs.length === 0) continue; // never invent a partial key
    const d = describePairs(g.child.name, g.parent.name, pairs);
    const rec = recommend(g.child.name, g.parent.name, null);
    out.push({
      id: suggestionId(g.child.id, g.parent.id, pairs),
      leftTableId: g.child.id,
      rightTableId: g.parent.id,
      leftColumn: pairs[0].leftColumn,
      rightColumn: pairs[0].rightColumn,
      pairs,
      origin: "declared",
      tier: "confirmed",
      evidence: [`Declared foreign key${g.name ? ` ${g.name}` : ""}: ${d.a} references ${d.b}.`],
      warnings: [...new Set(warnings)],
      needsCleaning: false,
      verified: false,
      recommendedJoinType: rec.type,
      joinTypeReason: rec.reason,
    });
  }
  return out;
}

function userSuggestions(tables: readonly SuggestTable[], rels: readonly UserRelationship[]): JoinSuggestion[] {
  const out: JoinSuggestion[] = [];
  for (const r of rels) {
    const left = tables.find((t) => t.id === r.leftTableId);
    const right = tables.find((t) => t.id === r.rightTableId);
    if (!left || !right || r.pairs.length === 0) continue;
    const warnings: string[] = [];
    let complete = true;
    for (const p of r.pairs) {
      const lc = left.columns.find((c) => c.name === p.leftColumn);
      const rc = right.columns.find((c) => c.name === p.rightColumn);
      if (!lc || !rc) {
        complete = false;
        break;
      }
      if (!sameFamily(lc, rc)) warnings.push(`The column types differ (${lc.dataType} and ${rc.dataType}); check the result carefully.`);
    }
    if (!complete) continue;
    const d = describePairs(left.name, right.name, r.pairs);
    const rec = recommend(left.name, right.name, r.joinType);
    out.push({
      id: suggestionId(left.id, right.id, r.pairs),
      leftTableId: left.id,
      rightTableId: right.id,
      leftColumn: r.pairs[0].leftColumn,
      rightColumn: r.pairs[0].rightColumn,
      pairs: r.pairs.map((p) => ({ ...p })),
      origin: "workspace",
      tier: "defined",
      evidence: [`You defined ${d.a} = ${d.b} on the Catalog's Relationship Map. It is not a database constraint.`],
      warnings: [...new Set(warnings)],
      needsCleaning: false,
      verified: false,
      recommendedJoinType: rec.type,
      joinTypeReason: rec.reason,
    });
  }
  return out;
}

function inferredSuggestions(
  a: SuggestTable,
  b: SuggestTable,
  taken: Set<string>,
  links: readonly DeclaredLink[],
  tables: readonly SuggestTable[]
): JoinSuggestion[] {
  const out: JoinSuggestion[] = [];
  const parentOf = (t: SuggestTable, c: SuggestColumn) =>
    links.filter((l) => l.childTable === t.name && l.childColumn === c.name).map((l) => `${l.parentTable}.${l.parentColumn}`);
  for (const ca of a.columns) {
    for (const cb of b.columns) {
      const na = normalizeName(ca.name);
      const nb = normalizeName(cb.name);
      const evidence: string[] = [];
      const warnings: string[] = [];

      const equalNames = na === nb && !GENERIC_NAMES.has(na);
      // customers.id  <-  orders.customer_id   (the "<table>_id" convention)
      const conventionAB = cb.isPrimaryKey && na === `${singular(b.name)}id` && nb !== na;
      const conventionBA = ca.isPrimaryKey && nb === `${singular(a.name)}id` && nb !== na;
      if (!equalNames && !conventionAB && !conventionBA) continue;

      // Two tables that both point at the same parent key ("siblings"). Joining
      // them directly multiplies rows; the safe path goes through the parent.
      const sharedParent = parentOf(a, ca).find((p) => parentOf(b, cb).includes(p));
      if (sharedParent) {
        const parentName = sharedParent.slice(0, sharedParent.indexOf("."));
        if (tables.some((t) => t.name === parentName)) continue; // join each to the parent instead
        warnings.push(
          `Both columns point at the same key (${sharedParent}). Joining these two tables directly can multiply rows (many matches on both sides); joining each to ${parentName} is safer.`
        );
      }

      if (equalNames) evidence.push(`Both columns are called "${ca.name === cb.name ? ca.name : `${ca.name}" and "${cb.name}`}".`);
      if (conventionAB) evidence.push(`${a.name}.${ca.name} follows the "${singular(b.name)}_id" naming pattern for the key of ${b.name}.`);
      if (conventionBA) evidence.push(`${b.name}.${cb.name} follows the "${singular(a.name)}_id" naming pattern for the key of ${a.name}.`);

      const sameType = sameFamily(ca, cb);
      if (sameType) evidence.push(`Compatible types (${ca.dataType} and ${cb.dataType}).`);
      else warnings.push(`The column types differ (${ca.dataType} and ${cb.dataType}). A type conversion would be needed first; that step is not available yet.`);

      const keyNotes: string[] = [];
      if (ca.isPrimaryKey) keyNotes.push(`${a.name}.${ca.name} is a primary key`);
      if (cb.isPrimaryKey) keyNotes.push(`${b.name}.${cb.name} is a primary key`);
      if (ca.isForeignKey) keyNotes.push(`${a.name}.${ca.name} is a foreign key`);
      if (cb.isForeignKey) keyNotes.push(`${b.name}.${cb.name} is a foreign key`);
      if (keyNotes.length) evidence.push(`${keyNotes.join("; ")}.`);
      const hasKey = keyNotes.length > 0;
      if (!hasKey) warnings.push("Neither column is a key, so matching rows may repeat. Measure the join before relying on it.");
      else if (!ca.isPrimaryKey && !cb.isPrimaryKey && !sharedParent) warnings.push("Neither column is a primary key, so matching rows may repeat. Measure the join before relying on it.");

      // The side that is a primary key is the "one" side; keep the other on the left.
      let [left, right, lc, rc] = [a, b, ca, cb];
      if (ca.isPrimaryKey && !cb.isPrimaryKey) [left, right, lc, rc] = [b, a, cb, ca];
      const id = pairId(left.id, lc.name, right.id, rc.name);
      if (taken.has(id) || taken.has(pairId(right.id, rc.name, left.id, lc.name))) continue;

      const tier: Tier = sameType && hasKey && !sharedParent ? "strong" : "possible";
      const rec = recommend(left.name, right.name, null);
      out.push({
        id,
        leftTableId: left.id,
        rightTableId: right.id,
        leftColumn: lc.name,
        rightColumn: rc.name,
        pairs: [{ leftColumn: lc.name, rightColumn: rc.name }],
        origin: "inferred",
        tier,
        evidence,
        warnings,
        needsCleaning: !sameType,
        verified: false,
        recommendedJoinType: rec.type,
        joinTypeReason: rec.reason,
      });
    }
  }
  return out;
}

export function suggestJoins(
  tables: readonly SuggestTable[],
  links: readonly DeclaredLink[],
  userRelationships: readonly UserRelationship[] = []
): JoinSuggestion[] {
  const all: JoinSuggestion[] = [];
  const taken = new Set<string>(); // every column pair already covered by a declared or user-defined join
  const byKey = new Map<string, JoinSuggestion>();

  const claim = (s: JoinSuggestion) => {
    for (const p of s.pairs) {
      taken.add(pairId(s.leftTableId, p.leftColumn, s.rightTableId, p.rightColumn));
      taken.add(pairId(s.rightTableId, p.rightColumn, s.leftTableId, p.leftColumn));
    }
  };

  for (let i = 0; i < tables.length; i += 1) {
    for (let j = i + 1; j < tables.length; j += 1) {
      for (const d of declaredSuggestions(tables[i], tables[j], links)) {
        const key = canonicalKey(d.leftTableId, d.rightTableId, d.pairs);
        if (byKey.has(key)) continue;
        byKey.set(key, d);
        claim(d);
        all.push(d);
      }
    }
  }
  for (const u of userSuggestions(tables, userRelationships)) {
    const key = canonicalKey(u.leftTableId, u.rightTableId, u.pairs);
    const existing = byKey.get(key);
    if (existing) {
      // The map shows the same relationship the database declares: list it once.
      const note = "The same relationship is also defined on the Catalog's Relationship Map.";
      if (!existing.evidence.includes(note)) existing.evidence.push(note);
      continue;
    }
    byKey.set(key, u);
    claim(u);
    all.push(u);
  }
  for (let i = 0; i < tables.length; i += 1) {
    for (let j = i + 1; j < tables.length; j += 1) {
      all.push(...inferredSuggestions(tables[i], tables[j], taken, links, tables));
    }
  }
  const seen = new Set<string>();
  return all
    .filter((s) => (seen.has(s.id) ? false : (seen.add(s.id), true)))
    .sort(
      (x, y) =>
        TIER_ORDER[x.tier] - TIER_ORDER[y.tier] ||
        y.evidence.length - x.evidence.length ||
        x.id.localeCompare(y.id)
    )
    .slice(0, MAX_SUGGESTIONS);
}

export const TIER_LABELS: Record<Tier, string> = {
  confirmed: "Confirmed in the database",
  defined: "Defined by you on the map",
  strong: "Strong inference",
  possible: "Possible match",
};
