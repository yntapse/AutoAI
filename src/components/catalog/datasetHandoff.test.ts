/**
 * Tests for the Catalog -> Builder handoff.
 *   node --test src/components/catalog/datasetHandoff.test.ts
 */
import test from "node:test";
import assert from "node:assert/strict";
// @ts-expect-error Node needs the explicit .ts extension; the type checker does not.
import * as handoffModule from "./datasetHandoff.ts";

import type { HandoffInput, StorageLike } from "./datasetHandoff";

// The tests poke at results loosely (e.g. reading .key after checking .ok).
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const h: any = handoffModule;

class MemoryStorage implements StorageLike {
  private map = new Map<string, string>();
  get length() {
    return this.map.size;
  }
  key(i: number) {
    return [...this.map.keys()][i] ?? null;
  }
  getItem(k: string) {
    return this.map.has(k) ? (this.map.get(k) as string) : null;
  }
  setItem(k: string, v: string) {
    this.map.set(k, v);
  }
  removeItem(k: string) {
    this.map.delete(k);
  }
}

const T0 = 1_000_000;
const fixedBytes = (seed: number) => (n: number) => Uint8Array.from({ length: n }, (_, i) => (i * 7 + seed) % 256);

const input = (): { sourceId: string; tables: HandoffInput["tables"][number][]; relationships: HandoffInput["relationships"][number][] } => ({
  sourceId: "src-1",
  tables: [
    { id: "t-orders", schemaName: "public", tableName: "orders", rowCount: 120, rowCountIsEstimate: false },
    { id: "t-customers", schemaName: "public", tableName: "customers", rowCount: null, rowCountIsEstimate: true },
  ],
  relationships: [
    {
      id: "fk-1",
      kind: "declared",
      leftTableId: "t-orders",
      rightTableId: "t-customers",
      joinType: null,
      conditions: [{ leftColumn: "customer_id", rightColumn: "id" }],
      constraintName: "orders_customer_fk",
    },
    {
      id: "ws-1",
      kind: "workspace",
      leftTableId: "t-orders",
      rightTableId: "t-customers",
      joinType: "LEFT",
      conditions: [{ leftColumn: "email", rightColumn: "email" }],
      constraintName: null,
    },
  ],
});

const catalog = [
  { id: "t-orders", schema_name: "public", table_name: "orders", row_count: 130, row_count_is_estimate: false },
  { id: "t-customers", schema_name: "public", table_name: "customers", row_count: 50, row_count_is_estimate: true },
  { id: "t-other", schema_name: "public", table_name: "other" },
];

test("a created handoff can be consumed once, then it is gone", () => {
  const s = new MemoryStorage();
  const made = h.createHandoff(s, input(), T0, fixedBytes(1));
  assert.ok(made.ok);
  assert.match(made.key, /^[0-9a-f]{32}$/);
  assert.equal(s.length, 1);

  const first = h.consumeHandoff(s, made.key, T0 + 1000);
  assert.ok(first.ok);
  assert.equal(first.payload.sourceId, "src-1");
  assert.equal(first.payload.tables.length, 2);
  assert.equal(s.length, 0, "removed after consumption");

  const second = h.consumeHandoff(s, made.key, T0 + 2000);
  assert.equal(second.ok, false);
  assert.equal(second.reason, "missing");
  assert.ok(second.message.length > 20);
});

test("keys are unpredictable: different random bytes give different keys", () => {
  const s = new MemoryStorage();
  const a = h.createHandoff(s, input(), T0, fixedBytes(1));
  const b = h.createHandoff(s, input(), T0, fixedBytes(2));
  assert.notEqual(a.key, b.key);
  assert.equal(h.isValidKey(a.key), true);
  assert.equal(h.isValidKey("../../x"), false);
  assert.equal(h.isValidKey("abc"), false);
  assert.equal(h.isValidKey(undefined), false);
  // the real generator yields 32 hex characters and is not constant
  assert.notEqual(h.randomKey(), h.randomKey());
  assert.match(h.randomKey(), /^[0-9a-f]{32}$/);
});

test("expires after about ten minutes, and the expired note is removed", () => {
  const s = new MemoryStorage();
  const made = h.createHandoff(s, input(), T0, fixedBytes(3));
  assert.equal(made.payload.expiresAt - made.payload.createdAt, 10 * 60 * 1000);
  const late = h.consumeHandoff(s, made.key, T0 + 10 * 60 * 1000 + 1);
  assert.equal(late.ok, false);
  assert.equal(late.reason, "expired");
  assert.match(late.message, /expired/i);
  assert.equal(s.length, 0);
});

test("just inside the expiry still works", () => {
  const s = new MemoryStorage();
  const made = h.createHandoff(s, input(), T0, fixedBytes(4));
  assert.ok(h.consumeHandoff(s, made.key, T0 + 10 * 60 * 1000 - 1).ok);
});

test("a note cannot extend its own life", () => {
  const s = new MemoryStorage();
  const made = h.createHandoff(s, input(), T0, fixedBytes(5));
  const name = h.handoffStorageKey(made.key);
  const edited = JSON.parse(s.getItem(name) as string);
  edited.expiresAt = T0 + 24 * 60 * 60 * 1000;
  s.setItem(name, JSON.stringify(edited));
  const out = h.consumeHandoff(s, made.key, T0 + 11 * 60 * 1000);
  assert.equal(out.reason, "expired");
});

test("missing, malformed and wrongly shaped notes give clear failures", () => {
  const s = new MemoryStorage();
  assert.equal(h.consumeHandoff(s, null).reason, "missing");
  assert.equal(h.consumeHandoff(s, "0".repeat(32)).reason, "missing");
  assert.equal(h.consumeHandoff(s, "not-a-key").reason, "malformed");
  assert.equal(h.consumeHandoff(null, "0".repeat(32)).ok, false);

  const bad = "a".repeat(32);
  for (const text of ["{not json", "[]", "null", JSON.stringify({ version: 99 }), JSON.stringify({ version: 1, createdAt: 1, expiresAt: 2, sourceId: "s", tables: [], relationships: [] })]) {
    s.setItem(h.handoffStorageKey(bad), text);
    const r = h.consumeHandoff(s, bad, T0);
    assert.equal(r.ok, false);
    assert.equal(r.reason, "malformed");
    assert.equal(s.length, 0, "a bad note is removed too");
  }
});

test("credentials and other extra fields are never stored", () => {
  const s = new MemoryStorage();
  const dirty = input() as unknown as Record<string, unknown>;
  dirty.password = "hunter2";
  dirty.apiKey = "sk-secret";
  (dirty.tables as Record<string, unknown>[])[0].password = "hunter2";
  (dirty.tables as Record<string, unknown>[])[0].connectionString = "Server=x;Password=hunter2";
  (dirty.relationships as Record<string, unknown>[])[0].token = "abc";
  const made = h.createHandoff(s, dirty as unknown as HandoffInput, T0, fixedBytes(6));
  const stored = s.getItem(h.handoffStorageKey(made.key)) as string;
  for (const secret of ["hunter2", "sk-secret", "connectionString", "apiKey", "token", "password"]) {
    assert.equal(stored.includes(secret), false, `${secret} must not be stored`);
  }
  assert.deepEqual(Object.keys(JSON.parse(stored)).sort(), ["createdAt", "expiresAt", "relationships", "sourceId", "tables", "version"]);
});

test("extra fields smuggled into a stored note are dropped when read", () => {
  const s = new MemoryStorage();
  const made = h.createHandoff(s, input(), T0, fixedBytes(7));
  const name = h.handoffStorageKey(made.key);
  const edited = JSON.parse(s.getItem(name) as string);
  edited.password = "x";
  edited.tables[0].password = "x";
  s.setItem(name, JSON.stringify(edited));
  const out = h.consumeHandoff(s, made.key, T0 + 1);
  assert.ok(out.ok);
  assert.equal(JSON.stringify(out.payload).includes("password"), false);
});

test("the address carries only the source id and the opaque key", () => {
  const key = "ab".repeat(16);
  const url = h.builderUrl("src-1", key);
  assert.equal(url, `/etl-studio?source=src-1&handoff=${key}`);
  assert.equal(/table|orders|customer|password/i.test(url), false);
});

test("empty selection and missing storage are refused with a message", () => {
  const s = new MemoryStorage();
  assert.equal(h.createHandoff(s, { ...input(), tables: [] }, T0).ok, false);
  assert.equal(h.createHandoff(s, { ...input(), sourceId: "" }, T0).ok, false);
  assert.equal(h.createHandoff(null, input(), T0).ok, false);
  assert.equal(s.length, 0);
});

test("storage that throws is handled", () => {
  const s = new MemoryStorage();
  s.setItem = () => {
    throw new Error("quota");
  };
  const r = h.createHandoff(s, input(), T0);
  assert.equal(r.ok, false);
});

test("duplicate tables and relationships are removed; relationships need both tables", () => {
  const i = input();
  i.tables.push({ ...i.tables[0] });
  i.relationships.push({ ...i.relationships[0] });
  i.relationships.push({ ...i.relationships[0], id: "dangling", rightTableId: "t-not-selected" });
  const p = h.buildPayload(i, T0);
  assert.equal(p.tables.length, 2);
  assert.deepEqual(p.relationships.map((r: { id: string }) => r.id), ["fk-1", "ws-1"]);
});

test("declared and workspace relationships keep their kind and join type", () => {
  const s = new MemoryStorage();
  const made = h.createHandoff(s, input(), T0, fixedBytes(8));
  const out = h.consumeHandoff(s, made.key, T0 + 1);
  const [fk, ws] = out.payload.relationships;
  assert.equal(fk.kind, "declared");
  assert.equal(fk.constraintName, "orders_customer_fk");
  assert.equal(fk.joinType, null, "a declared key does not choose a join type");
  assert.equal(ws.kind, "workspace");
  assert.equal(ws.joinType, "LEFT");
  assert.deepEqual(ws.conditions, [{ leftColumn: "email", rightColumn: "email" }]);
});

test("an unknown join type is dropped rather than trusted", () => {
  const i = input();
  (i.relationships[1] as { joinType: string }).joinType = "DROP TABLE";
  assert.equal(h.buildPayload(i, T0).relationships[1].joinType, null);
});

test("table count is capped", () => {
  const many = Array.from({ length: 100 }, (_, n) => ({ id: `t${n}`, schemaName: "s", tableName: `n${n}`, rowCount: 1, rowCountIsEstimate: true }));
  const p = h.buildPayload({ sourceId: "s", tables: many, relationships: [] }, T0);
  assert.equal(p.tables.length, h.MAX_HANDOFF_TABLES);
});

test("creating a handoff clears expired and unreadable ones, not live ones", () => {
  const s = new MemoryStorage();
  const old = h.createHandoff(s, input(), T0, fixedBytes(9));
  s.setItem(h.handoffStorageKey("c".repeat(32)), "garbage");
  s.setItem("unrelated.key", "keep me");
  const live = h.createHandoff(s, input(), T0 + 11 * 60 * 1000, fixedBytes(10));
  assert.equal(s.getItem(h.handoffStorageKey(old.key)), null);
  assert.equal(s.getItem(h.handoffStorageKey("c".repeat(32))), null);
  assert.equal(s.getItem("unrelated.key"), "keep me");
  assert.ok(s.getItem(h.handoffStorageKey(live.key)));
});

test("checking against the Catalog: valid selection uses current names and counts", () => {
  const p = h.buildPayload(input(), T0);
  const c = h.checkAgainstCatalog(p, "src-1", catalog);
  assert.equal(c.problem, null);
  assert.equal(c.warning, null);
  assert.equal(c.tables.length, 2);
  assert.equal(c.tables.find((t: { id: string }) => t.id === "t-orders").rowCount, 130, "row count comes from the Catalog, not the note");
  assert.equal(c.relationships.length, 2);
});

test("checking against the Catalog: a different source is refused", () => {
  const c = h.checkAgainstCatalog(h.buildPayload(input(), T0), "src-2", catalog);
  assert.ok(c.problem);
  assert.equal(c.tables.length, 0);
});

test("checking against the Catalog: stale tables are left out with a warning", () => {
  const p = h.buildPayload(input(), T0);
  const c = h.checkAgainstCatalog(p, "src-1", catalog.filter((t) => t.id !== "t-customers"));
  assert.equal(c.problem, null);
  assert.deepEqual(c.tables.map((t: { id: string }) => t.id), ["t-orders"]);
  assert.match(c.warning, /customers/);
  assert.equal(c.relationships.length, 0, "relationships need both tables");
  assert.equal(c.droppedRelationships, 2);
});

test("checking against the Catalog: nothing left is a problem", () => {
  const c = h.checkAgainstCatalog(h.buildPayload(input(), T0), "src-1", [{ id: "zzz", schema_name: "p", table_name: "z" }]);
  assert.ok(c.problem);
  assert.equal(c.tables.length, 0);
});

test("grid spots never overlap each other or occupied spots", () => {
  const occupied = [{ x: 40, y: 40 }, { x: 40, y: 192 }];
  const spots = h.gridSpots(10, occupied);
  assert.equal(spots.length, 10);
  const all = [...occupied, ...spots];
  for (let i = 0; i < all.length; i += 1) {
    for (let j = i + 1; j < all.length; j += 1) {
      const apart = Math.abs(all[i].x - all[j].x) >= 232 || Math.abs(all[i].y - all[j].y) >= 108;
      assert.ok(apart, `spots ${i} and ${j} overlap`);
    }
  }
  assert.equal(h.gridSpots(0, []).length, 0);
});

test("relationships for the chosen tables: declared keys and workspace joins stay distinct", () => {
  const tables = [{ id: "t-orders", table_name: "orders" }, { id: "t-customers", table_name: "customers" }, { id: "t-other", table_name: "other" }];
  const declared = [
    { id: "r1", left_table: "orders", left_column: "customer_id", right_table: "customers", right_column: "id", constraint_name: "fk_a", origin: "declared", confidence: 1, approved: null },
    { id: "r2", left_table: "orders", left_column: "x", right_table: "other", right_column: "id", constraint_name: "fk_b", origin: "declared", confidence: 1, approved: null },
    { id: "r3", left_table: "orders", left_column: "c2", right_table: "customers", right_column: "id2", constraint_name: "fk_a", origin: "declared", confidence: 1, approved: null },
    { id: "r4", left_table: "orders", left_column: "guess", right_table: "customers", right_column: "g", constraint_name: null, origin: "inferred", confidence: 0.4, approved: null },
  ];
  const joins = [
    { id: "j1", leftTable: "orders", rightTable: "customers", joinType: "LEFT", conditions: [{ leftColumn: "email", rightColumn: "email" }] },
    { id: "j2", leftTable: "orders", rightTable: "other", joinType: "INNER", conditions: [{ leftColumn: "a", rightColumn: "b" }] },
  ];
  const out = h.composeHandoffRelationships(["t-orders", "t-customers"], tables, declared, joins);
  assert.equal(out.length, 2, "relationships to an unchosen table and non-declared origins are left out");
  const fk = out.find((r: { kind: string }) => r.kind === "declared");
  assert.deepEqual(fk.conditions.length, 2, "a multi-column key is one relationship");
  const ws = out.find((r: { kind: string }) => r.kind === "workspace");
  assert.equal(ws.joinType, "LEFT");
  assert.equal(ws.id, "ws:j1");
  // never marked as database-declared
  assert.notEqual(ws.kind, "declared");
});

test("foreign keys with no constraint name stay separate relationships with unique ids", () => {
  const tables = [{ id: "t-o", table_name: "orders" }, { id: "t-c", table_name: "customers" }];
  const mk = (id: string, lc: string, rc: string) => ({ id, left_table: "orders", left_column: lc, right_table: "customers", right_column: rc, constraint_name: "", origin: "declared", confidence: 1, approved: null });
  const out = h.composeHandoffRelationships(["t-o", "t-c"], tables, [mk("r1", "a", "x"), mk("r2", "b", "y")], []);
  assert.equal(out.length, 2);
  assert.notEqual(out[0].id, out[1].id);
  assert.equal(out[0].constraintName, null);
});
