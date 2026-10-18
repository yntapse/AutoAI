/**
 * Tests for saving / loading the map workspace. Run from the project root:
 *   node --test src/components/catalog/canvasWorkspace.test.ts
 */
import test from "node:test";
import assert from "node:assert/strict";
// @ts-expect-error Node needs the explicit .ts extension; the type checker does not.
import * as w from "./canvasWorkspace.ts";
// @ts-expect-error same reason
import * as g from "./relationshipGraph.ts";

const known = new Set(["A", "B", "C"]);
const good = {
  canvas: ["A", "B"],
  positions: { A: { x: 1, y: 2 }, B: { x: 3, y: 4 } },
  expanded: ["A"],
  joins: [
    { id: "j1", leftTable: "A", rightTable: "B", joinType: "LEFT" as const, conditions: [{ leftColumn: "id", rightColumn: "a_id" }] },
  ],
};

test("saved join types match the join types the map offers", () => {
  assert.deepEqual([...w.SAVED_JOIN_TYPES], [...g.JOIN_TYPES]);
});

test("a workspace round-trips", () => {
  const back = w.parseWorkspace(w.serializeWorkspace(good), known)!;
  assert.deepEqual(back.canvas, good.canvas);
  assert.deepEqual(back.positions, good.positions);
  assert.deepEqual(back.joins, good.joins);
});

test("tables that left the catalog are dropped, including their joins", () => {
  const back = w.parseWorkspace(w.serializeWorkspace(good), new Set(["A"]))!;
  assert.deepEqual(back.canvas, ["A"]);
  assert.deepEqual(Object.keys(back.positions), ["A"]);
  assert.equal(back.joins.length, 0);
});

test("garbage, wrong versions and bad values are rejected or cleaned", () => {
  assert.equal(w.parseWorkspace(null, known), null);
  assert.equal(w.parseWorkspace("not json", known), null);
  assert.equal(w.parseWorkspace(JSON.stringify({ version: 99 }), known), null);
  const dirty = JSON.stringify({
    version: 1,
    canvas: ["A", "A", 5, "Ghost"],
    positions: { A: { x: "1", y: 2 }, B: { x: Infinity, y: 0 }, C: { x: 5, y: 6 } },
    expanded: ["A", 7],
    joins: [
      { id: "x", leftTable: "A", rightTable: "B", joinType: "CROSS", conditions: [{ leftColumn: "a", rightColumn: "b" }] },
      { id: "y", leftTable: "A", rightTable: "B", joinType: "INNER", conditions: [] },
      { id: "z", leftTable: "A", rightTable: "B", joinType: "INNER", conditions: [{ leftColumn: "a", rightColumn: 3 }] },
      { id: "ok", leftTable: "A", rightTable: "C", joinType: "FULL", conditions: [{ leftColumn: "a", rightColumn: "c" }] },
    ],
  });
  const back = w.parseWorkspace(dirty, known)!;
  assert.deepEqual(back.canvas, ["A"]);
  assert.deepEqual(back.positions, { C: { x: 5, y: 6 } });
  assert.deepEqual(back.expanded, ["A"]);
  assert.deepEqual(back.joins.map((j: { id: string }) => j.id), ["ok"]);
});

test("the stored text never contains credentials or connection details", () => {
  const text = w.serializeWorkspace(good);
  for (const word of ["password", "host", "token", "secret", "username"]) {
    assert.equal(text.toLowerCase().includes(word), false, word);
  }
  assert.ok(w.storageKey("abc").includes("abc"));
});

test("load/save survive missing browser storage", () => {
  // No `window` in Node: both calls must fail quietly.
  assert.equal(w.loadWorkspace("s", known), null);
  assert.doesNotThrow(() => w.saveWorkspace("s", good));
  assert.doesNotThrow(() => w.clearSavedWorkspace("s"));
});
