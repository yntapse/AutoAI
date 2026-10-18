/**
 * Tests for the relationship-graph logic. Run from the project root with:
 *   node --test src/components/catalog/relationshipGraph.test.ts
 * (Node 22+ runs TypeScript directly; no extra test library is needed.)
 */
import test from "node:test";
import assert from "node:assert/strict";
// @ts-expect-error Node needs the explicit .ts extension; the type checker does not.
import * as m from "./relationshipGraph.ts";

type Col = {
  column_name: string;
  data_type: string;
  ordinal_position: number;
  is_nullable: boolean;
  is_primary_key: boolean;
  is_foreign_key: boolean;
  normalized_name: string | null;
  sample_values: unknown[] | null;
};

const col = (name: string, pos: number, pk = false, fk = false): Col => ({
  column_name: name,
  data_type: "int",
  ordinal_position: pos,
  is_nullable: !pk,
  is_primary_key: pk,
  is_foreign_key: fk,
  normalized_name: null,
  sample_values: null,
});

const rel = (id: string, l: string, lc: string, r: string, rc: string, origin = "declared") => ({
  id,
  left_table: l,
  left_column: lc,
  right_table: r,
  right_column: rc,
  constraint_name: `FK_${id}`,
  origin,
  confidence: 1,
  approved: null,
});

const tables = ["A", "B", "C", "Lonely"].map((n) => ({ table_name: n }));
const cols = {
  A: [col("id", 1, true)],
  B: [col("id", 1, true), col("a_id", 2, false, true)],
  C: [col("id", 1, true), col("b_id", 2, false, true)],
};

test("only declared relationships become edges", () => {
  const r = m.buildEdges(tables, [rel("1", "B", "a_id", "A", "id"), rel("2", "C", "b_id", "B", "id", "inferred")], cols);
  assert.equal(r.edges.length, 1);
  assert.equal(r.ignoredNotDeclared, 1);
});

test("relationships to tables outside the catalog are skipped, not invented", () => {
  const r = m.buildEdges(tables, [rel("1", "B", "a_id", "Ghost", "id")], cols);
  assert.equal(r.edges.length, 0);
  assert.equal(r.ignoredUnknownTable, 1);
});

test("no relationships means no edges", () => {
  assert.equal(m.buildEdges(tables, [], cols).edges.length, 0);
});

test("child holds the foreign key, parent is referenced", () => {
  const [e] = m.buildEdges(tables, [rel("1", "B", "a_id", "A", "id")], cols).edges;
  assert.equal(e.childTable, "B");
  assert.equal(e.childColumn, "a_id");
  assert.equal(e.parentTable, "A");
  assert.equal(e.parentColumn, "id");
});

test("cardinality: many-to-one when the parent column is its primary key", () => {
  const [e] = m.buildEdges(tables, [rel("1", "B", "a_id", "A", "id")], cols).edges;
  assert.equal(e.cardinality, "many-to-one");
});

test("cardinality: one-to-one when both sides are single primary keys", () => {
  const [e] = m.buildEdges(tables, [rel("1", "C", "id", "B", "id")], cols).edges;
  assert.equal(e.cardinality, "one-to-one");
});

test("cardinality stays unknown without column details or without a PK target", () => {
  const [a] = m.buildEdges(tables, [rel("1", "B", "a_id", "A", "id")], {}).edges;
  assert.equal(a.cardinality, "unknown");
  const [b] = m.buildEdges(tables, [rel("1", "C", "b_id", "B", "a_id")], cols).edges;
  assert.equal(b.cardinality, "unknown");
});

test("composite primary keys never prove cardinality", () => {
  const composite = { ...cols, A: [col("id", 1, true), col("region", 2, true)] };
  const [e] = m.buildEdges(tables, [rel("1", "B", "a_id", "A", "id")], composite).edges;
  assert.equal(e.cardinality, "unknown");
});

test("layout puts parents left of children and isolated tables below", () => {
  const edges = m.buildEdges(tables, [rel("1", "B", "a_id", "A", "id"), rel("2", "C", "b_id", "B", "id")], cols).edges;
  const nodes = tables.map((t: { table_name: string }) => ({ name: t.table_name, height: m.HEADER_HEIGHT }));
  const pos = m.computeLayout(nodes, edges);
  assert.ok(pos.A.x < pos.B.x && pos.B.x < pos.C.x);
  assert.ok(pos.Lonely.y > pos.C.y);
});

test("layout survives cycles and self references", () => {
  const nodes = [{ name: "X", height: 76 }, { name: "Y", height: 76 }];
  const pos = m.computeLayout(nodes, [
    { childTable: "X", parentTable: "Y" },
    { childTable: "Y", parentTable: "X" },
    { childTable: "X", parentTable: "X" },
  ]);
  assert.ok(Number.isFinite(pos.X.x) && Number.isFinite(pos.Y.x));
});

test("a big schema produces no overlapping cards", () => {
  const names = Array.from({ length: 40 }, (_, i) => `T${String(i).padStart(2, "0")}`);
  const nodes = names.map((name) => ({ name, height: m.HEADER_HEIGHT }));
  const edges = names.slice(1).map((n, i) => ({ childTable: n, parentTable: names[Math.floor(i / 3)] }));
  const pos = m.computeLayout(nodes, edges);
  const rects = names.map((n) => ({ ...pos[n], w: m.CARD_WIDTH, h: m.HEADER_HEIGHT }));
  for (let i = 0; i < rects.length; i += 1) {
    for (let j = i + 1; j < rects.length; j += 1) {
      const a = rects[i];
      const b = rects[j];
      const overlap = a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
      assert.equal(overlap, false, `${names[i]} overlaps ${names[j]}`);
    }
  }
});

test("expanding a card pushes the card below it down", () => {
  const rects = {
    a: { x: 0, y: 0, w: 248, h: 76 },
    b: { x: 0, y: 116, w: 248, h: 76 },
    far: { x: 600, y: 116, w: 248, h: 76 },
  };
  const grown = { ...rects, a: { ...rects.a, h: 300 } };
  const out = m.resolveOverlaps(grown, "a");
  assert.ok(out.b.y >= 300);
  assert.equal(out.far.y, 116);
  assert.equal(rects.b.y, 116, "input is not modified");
});

test("visibleColumns keeps key columns when a table is wider than the cap", () => {
  const wide = Array.from({ length: 50 }, (_, i) => col(`c${i}`, i + 1));
  wide[45] = col("customer_id", 46, false, true);
  const v = m.visibleColumns(wide, new Set(["customer_id"]), 30);
  assert.equal(v.columns.length, 30);
  assert.equal(v.hiddenCount, 20);
  assert.ok(v.columns.some((c: Col) => c.column_name === "customer_id"));
});

test("edge geometry leaves the facing sides and ends on the right cards", () => {
  const child = { x: 400, y: 0, w: 248, h: 76 };
  const parent = { x: 0, y: 0, w: 248, h: 76 };
  const g = m.edgeGeometry(child, parent, 38, 38);
  assert.equal(g.fromSide, "left");
  assert.equal(g.toSide, "right");
  assert.equal(g.from.x, 400);
  assert.equal(g.to.x, 248);
});

test("fit and zoom behave", () => {
  const v = m.fitViewport([{ x: 0, y: 0, w: 1000, h: 500 }], 500, 400);
  assert.ok(v.k < 1 && v.k >= m.MIN_ZOOM);
  const z = m.zoomAt({ x: 0, y: 0, k: 1 }, 2, 100, 100);
  assert.equal(z.k, 2);
  assert.equal(z.x, -100);
  assert.equal(m.zoomAt({ x: 0, y: 0, k: 2 }, 5, 0, 0).k, m.MAX_ZOOM);
});

// ----------------------------------------------------------- Phase 1: canvas

test("default canvas holds every table for a small schema and none for a huge one", () => {
  assert.deepEqual(m.defaultCanvas(["A", "B"]), ["A", "B"]);
  const many = Array.from({ length: 31 }, (_, i) => `T${i}`);
  assert.deepEqual(m.defaultCanvas(many), []);
});

test("adding a table twice, or an unknown table, changes nothing", () => {
  const known = new Set(["A", "B"]);
  const one = m.addToCanvas([], "A", known);
  assert.deepEqual(one, ["A"]);
  assert.equal(m.addToCanvas(one, "A", known), one);
  assert.equal(m.addToCanvas(one, "Ghost", known), one);
  assert.deepEqual(m.addToCanvas(one, "B", known), ["A", "B"]);
});

test("removing a table only edits the canvas list", () => {
  const canvas = ["A", "B", "C"];
  assert.deepEqual(m.removeFromCanvas(canvas, "B"), ["A", "C"]);
  assert.deepEqual(canvas, ["A", "B", "C"], "input untouched");
  assert.deepEqual(m.removeFromCanvas(canvas, "Zed"), canvas);
});

test("off-canvas neighbours name the related tables that are not on the canvas", () => {
  const edges = m.buildEdges(tables, [rel("1", "B", "a_id", "A", "id"), rel("2", "C", "b_id", "B", "id")], cols).edges;
  assert.deepEqual(m.offCanvasNeighbours("B", edges, new Set(["B"])), ["A", "C"]);
  assert.deepEqual(m.offCanvasNeighbours("B", edges, new Set(["A", "B"])), ["C"]);
  assert.deepEqual(m.offCanvasNeighbours("A", edges, new Set(["A", "B", "C"])), []);
  assert.deepEqual(m.offCanvasNeighbours("Lonely", edges, new Set(["Lonely"])), []);
});

test("a new card is placed in free space, never on top of another", () => {
  const rects = [
    { x: 0, y: 0, w: m.CARD_WIDTH, h: 90 },
    { x: 400, y: 0, w: m.CARD_WIDTH, h: 90 },
    { x: 400, y: 130, w: m.CARD_WIDTH, h: 90 },
  ];
  for (const near of [null, rects[0], rects[1]]) {
    const p = m.findFreePosition(rects, m.CARD_WIDTH, 90, near);
    const n = { x: p.x, y: p.y, w: m.CARD_WIDTH, h: 90 };
    const hit = rects.some((r) => n.x < r.x + r.w && r.x < n.x + n.w && n.y < r.y + r.h && r.y < n.y + n.h);
    assert.equal(hit, false);
  }
  assert.deepEqual(m.findFreePosition([], m.CARD_WIDTH, 90), { x: 0, y: 0 });
});

// ------------------------------------------------ Phase 1: workspace joins

const declared = () =>
  m.buildEdges(tables, [rel("1", "B", "a_id", "A", "id"), rel("2", "C", "b_id", "B", "id")], cols).edges;
const join = (l: string, r: string, conds: [string, string][], joinType = "LEFT") => ({
  id: "j1",
  leftTable: l,
  rightTable: r,
  joinType: joinType as "INNER", // typed loosely so tests can pass an invalid type
  conditions: conds.map(([leftColumn, rightColumn]) => ({ leftColumn, rightColumn })),
});

test("a join equal to a declared FK is classified declared, in either direction", () => {
  assert.equal(m.classifyJoin(join("B", "A", [["a_id", "id"]]), declared()).basis, "declared");
  assert.equal(m.classifyJoin(join("A", "B", [["id", "a_id"]]), declared()).basis, "declared");
});

test("a join on columns the database does not link is manual", () => {
  assert.equal(m.classifyJoin(join("A", "C", [["id", "id"]]), declared()).basis, "manual");
  assert.equal(m.classifyJoin(join("B", "A", [["id", "id"]]), declared()).basis, "manual");
});

test("a mixed join is partly declared and never reported as fully declared", () => {
  const r = m.classifyJoin(join("B", "A", [["a_id", "id"], ["id", "id"]]), declared());
  assert.equal(r.basis, "partly-declared");
  assert.deepEqual(r.matchedEdgeIds, ["1"]);
});

test("a join never changes the declared edges", () => {
  const edges = declared();
  const snapshot = JSON.stringify(edges);
  m.classifyJoin(join("B", "A", [["a_id", "id"]]), edges);
  assert.equal(JSON.stringify(edges), snapshot);
});

test("validateJoin rejects bad input and warns on type differences", () => {
  const known = new Set(["A", "B", "C"]);
  const ok = m.validateJoin(join("B", "A", [["a_id", "id"]]), cols, known);
  assert.deepEqual(ok.errors, []);
  assert.ok(m.validateJoin(join("B", "B", [["id", "id"]]), cols, known).errors.length > 0);
  assert.ok(m.validateJoin(join("B", "A", []), cols, known).errors.length > 0);
  assert.ok(m.validateJoin(join("B", "A", [["nope", "id"]]), cols, known).errors.some((e: string) => e.includes("nope")));
  assert.ok(m.validateJoin(join("B", "Ghost", [["a_id", "id"]]), cols, known).errors.length > 0);
  assert.ok(m.validateJoin(join("B", "A", [["a_id", "id"], ["a_id", "id"]]), cols, known).errors.some((e: string) => e.includes("Duplicate")));
  assert.ok(m.validateJoin(join("B", "A", [["a_id", "id"]], "CROSS"), cols, known).errors.length > 0);
  const typed = { ...cols, B: [col("id", 1, true), { ...col("a_id", 2, false, true), data_type: "varchar" }] };
  assert.equal(m.validateJoin(join("B", "A", [["a_id", "id"]]), typed, known).warnings.length, 1);
});

test("join labels and the join type list", () => {
  assert.equal(m.joinLabel({ joinType: "LEFT", conditions: [{ leftColumn: "a", rightColumn: "b" }] }), "LEFT");
  assert.equal(m.joinLabel({ joinType: "INNER", conditions: [{ leftColumn: "a", rightColumn: "b" }, { leftColumn: "c", rightColumn: "d" }] }), "INNER · 2");
  assert.deepEqual([...m.JOIN_TYPES], ["INNER", "LEFT", "RIGHT", "FULL"]);
});
