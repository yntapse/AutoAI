/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Tests for the automatic layout and for re-linking tables from an earlier scan.
 *   node --test src/components/etl-studio/layout.test.ts
 */
import test from "node:test";
import assert from "node:assert/strict";
// @ts-expect-error Node needs the explicit .ts extension; the type checker does not.
import * as L from "./layout.ts";
// @ts-expect-error Node needs the explicit .ts extension; the type checker does not.
import * as M from "./pipelineModel.ts";
const l: any = L; const m: any = M;

const src = (id: string, table = id, x = 0, y = 0) => ({ id, type: "source", x, y, config: { table_id: table } as Record<string, unknown> });
const nd = (id: string, type: string, x = 0, y = 0) => ({ id, type, x, y, config: {} as Record<string, unknown> });
const e = (source: string, target: string, port = "in") => ({ source, target, port });
const opts = { nodeW: 232, nodeH: 108 };

test("columns: tables first, then each step one column right of its deepest input", () => {
  const nodes = [src("s1"), src("s2"), src("s3"), nd("j1", "join"), nd("j2", "join"), nd("o1", "output")];
  const edges = [e("s1", "j1", "left"), e("s2", "j1", "right"), e("j1", "j2", "left"), e("s3", "j2", "right"), e("j2", "o1")];
  const c = l.columnsOf(nodes, edges);
  assert.deepEqual([c.s1, c.s2, c.s3, c.j1, c.j2, c.o1], [0, 0, 0, 1, 2, 3]);
});

test("layout puts tables in one column, joins to their right, output last, with no overlaps", () => {
  const nodes = [src("s1"), src("s2"), src("s3"), nd("j1", "join"), nd("j2", "join"), nd("o1", "output")];
  const edges = [e("s1", "j1", "left"), e("s2", "j1", "right"), e("j1", "j2", "left"), e("s3", "j2", "right"), e("j2", "o1")];
  const p = l.layoutNodes(nodes, edges, opts);
  assert.equal(p.s1.x, p.s2.x); assert.equal(p.s2.x, p.s3.x);
  assert.ok(p.j1.x > p.s1.x && p.j2.x > p.j1.x && p.o1.x > p.j2.x);
  const ids = Object.keys(p);
  for (let i = 0; i < ids.length; i += 1) for (let j = i + 1; j < ids.length; j += 1) {
    const a = p[ids[i]], b = p[ids[j]];
    const apart = Math.abs(a.x - b.x) >= opts.nodeW || Math.abs(a.y - b.y) >= opts.nodeH;
    assert.ok(apart, `${ids[i]} and ${ids[j]} overlap`);
  }
});

test("layout removes crossings that a bad order creates", () => {
  // s1 and s3 feed j1; s2 and s4 feed j2. Creation order interleaves them.
  const nodes = [src("s1"), src("s2"), src("s3"), src("s4"), nd("j1", "join"), nd("j2", "join")];
  const edges = [e("s1", "j1", "left"), e("s3", "j1", "right"), e("s2", "j2", "left"), e("s4", "j2", "right")];
  const naive: Record<string, { x: number; y: number }> = { s1: { x: 0, y: 0 }, s2: { x: 0, y: 152 }, s3: { x: 0, y: 304 }, s4: { x: 0, y: 456 }, j1: { x: 312, y: 100 }, j2: { x: 312, y: 400 } };
  assert.ok(l.crossings(nodes, edges, naive) > 0, "the naive placement really crosses");
  assert.equal(l.crossings(nodes, edges, l.layoutNodes(nodes, edges, opts)), 0);
});

test("layout is deterministic and never changes anything but positions", () => {
  const nodes = [src("s1", "t1", 500, 500), src("s2", "t2", 10, 10), nd("j1", "join", 99, 99)];
  const edges = [e("s1", "j1", "left"), e("s2", "j1", "right")];
  const before = JSON.stringify({ nodes, edges });
  const a = l.layoutNodes(nodes, edges, opts);
  const b = l.layoutNodes(nodes, edges, opts);
  assert.deepEqual(a, b);
  assert.equal(JSON.stringify({ nodes, edges }), before, "inputs untouched");
  const moved = m.applyPositions(nodes, a);
  assert.deepEqual(moved.map((n: any) => n.config), nodes.map((n) => n.config));
  assert.deepEqual(moved.map((n: any) => n.id), nodes.map((n) => n.id));
  assert.equal(moved[0].x, a.s1.x);
});

test("unconnected tables are stacked and a loop or an empty canvas does not crash", () => {
  const p = l.layoutNodes([src("a"), src("b"), src("c")], [], opts);
  assert.deepEqual([p.a.x, p.b.x, p.c.x], [40, 40, 40]);
  assert.deepEqual([p.a.y, p.b.y, p.c.y], [40, 192, 344]);
  assert.deepEqual(l.layoutNodes([], [], opts), {});
  assert.ok(l.layoutNodes([nd("x", "join"), nd("y", "join")], [e("x", "y", "left"), e("y", "x", "left")], opts).x);
});

test("a join sits level with the middle of its two tables", () => {
  const nodes = [src("s1"), src("s2"), nd("j1", "join")];
  const p = l.layoutNodes(nodes, [e("s1", "j1", "left"), e("s2", "j1", "right")], opts);
  assert.equal(p.j1.y, Math.round((p.s1.y + p.s2.y) / 2));
});

// ---- tables from an earlier scan
test("re-linking changes only the table of the listed Source steps and keeps wiring", () => {
  const nodes = [src("s1", "old1", 5, 6), src("s2", "keep"), nd("j1", "join")];
  const edges = [e("s1", "j1", "left"), e("s2", "j1", "right")];
  const out = m.relinkStaleTables(nodes, edges, { old1: "new1" });
  assert.deepEqual(out.relinked, ["s1"]);
  assert.equal(out.nodes[0].config.table_id, "new1");
  assert.equal(out.nodes[0].x, 5); assert.equal(out.nodes[0].y, 6);
  assert.equal(out.nodes[1].config.table_id, "keep");
  assert.deepEqual(out.edges, edges);
  assert.equal(nodes[0].config.table_id, "old1", "input not mutated");
});

test("a stale step that only duplicates an unwired current table is removed; a wired one is kept", () => {
  const nodes = [src("s1", "old1"), src("s2", "new1")];
  const dup = m.relinkStaleTables(nodes, [], { old1: "new1" });
  assert.deepEqual(dup.removed, ["s1"]);
  assert.deepEqual(dup.nodes.map((n: any) => n.id), ["s2"]);

  const nodes2 = [src("s1", "old1"), src("s2", "new1"), nd("j1", "join")];
  const edges2 = [e("s1", "j1", "left")];
  const wired = m.relinkStaleTables(nodes2, edges2, { old1: "new1" });
  assert.deepEqual(wired.removed, ["s2"], "the unwired duplicate goes, the wired step stays");
  assert.equal(wired.nodes.find((n: any) => n.id === "s1").config.table_id, "new1");
  assert.deepEqual(wired.edges, edges2);
});

test("nothing is relinked when the mapping is empty", () => {
  const nodes = [src("s1", "old1")];
  const out = m.relinkStaleTables(nodes, [], {});
  assert.deepEqual(out.nodes, nodes); assert.deepEqual(out.removed, []); assert.deepEqual(out.relinked, []);
});

test("the previous-draft backup round-trips, is separate from the live draft and carries no secrets", () => {
  const mem = new Map<string, string>();
  const storage = { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => void mem.set(k, v), removeItem: (k: string) => void mem.delete(k) };
  const nodes = [{ id: "s1", type: "source", x: 1, y: 2, config: { table_id: "t1", password: "hunter2" } }];
  assert.equal(m.saveDraftBackup("A", nodes, [], storage), true);
  assert.equal(mem.has(m.draftStorageKey("A")), false, "the live draft slot is untouched");
  const back = m.loadDraftBackup("A", storage);
  assert.equal(back.nodes.length, 1);
  assert.equal(back.nodes[0].x, 1);
  assert.ok(!JSON.stringify([...mem.values()]).includes("hunter2"), "no secret is stored");
  assert.equal(m.loadDraftBackup("B", storage), null, "another source cannot read it");
  m.clearDraftBackup("A", storage);
  assert.equal(m.loadDraftBackup("A", storage), null);
});

test("the table feeding a join's left input is placed above the one feeding its right input", () => {
  const nodes = [src("s1"), src("s2"), nd("j1", "join")];
  const p = l.layoutNodes(nodes, [e("s2", "j1", "left"), e("s1", "j1", "right")], opts);
  assert.ok(p.s2.y < p.s1.y);
  assert.equal(l.crossings(nodes, [e("s2", "j1", "left"), e("s1", "j1", "right")], p), 0);
});
