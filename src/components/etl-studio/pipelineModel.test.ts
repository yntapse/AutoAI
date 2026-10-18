/**
 * Tests for the pipeline graph rules, "needs recalculation" tracking and drafts.
 *   node --test src/components/etl-studio/pipelineModel.test.ts
 */
import test from "node:test";
import assert from "node:assert/strict";
// @ts-expect-error Node needs the explicit .ts extension; the type checker does not.
import * as m from "./pipelineModel.ts";

import type { PipelineNode as N } from "./pipelineModel";

const node = (id: string, type: string, config: Record<string, unknown> = {}): N => ({ id, type: type as N["type"], x: 0, y: 0, config });
const edge = (source: string, target: string, port = "in") => ({ source, target, port });

const baseNodes = [
  node("a", "source", { table_id: "t1" }),
  node("b", "source", { table_id: "t2" }),
  node("j", "join"),
  node("f", "filter"),
  node("o", "output"),
];

test("every node type has metadata and defaults", () => {
  for (const t of m.NODE_TYPES) {
    assert.ok(m.NODE_META[t].label);
    assert.ok(typeof m.defaultConfig(t) === "object");
  }
  assert.deepEqual([...m.inputPorts("join")], ["left", "right"]);
  assert.deepEqual([...m.inputPorts("source")], []);
});

test("new node ids are unique", () => {
  const a = m.makeNode([], "filter", 0, 0);
  const b = m.makeNode([a], "filter", 0, 0);
  assert.notEqual(a.id, b.id);
});

test("connection rules", () => {
  const edges = [edge("a", "j", "left")];
  assert.equal(m.connectionProblem(baseNodes, edges, "b", "j", "right"), null);
  assert.match(m.connectionProblem(baseNodes, edges, "b", "j", "left")!, /already connected/);
  assert.match(m.connectionProblem(baseNodes, edges, "b", "j", "in")!, /inputs are/);
  assert.match(m.connectionProblem(baseNodes, edges, "j", "a", "in")!, /takes no input/);
  assert.match(m.connectionProblem(baseNodes, edges, "o", "f", "in")!, /nothing after/);
  assert.match(m.connectionProblem(baseNodes, edges, "f", "f", "in")!, /itself/);
  assert.match(m.connectionProblem(baseNodes, edges, "zzz", "f", "in")!, /exist/);
});

test("a loop is refused", () => {
  const nodes = [node("f", "filter"), node("g", "filter")];
  assert.match(m.connectionProblem(nodes, [edge("f", "g")], "g", "f", "in")!, /loop/);
});

test("addEdge returns the reason and leaves the graph alone on a bad connection", () => {
  const bad = m.addEdge(baseNodes, [], edge("j", "a"));
  assert.ok(bad.problem);
  assert.deepEqual(bad.edges, []);
  const ok = m.addEdge(baseNodes, [], edge("a", "f"));
  assert.equal(ok.problem, null);
  assert.equal(ok.edges.length, 1);
});

test("removing a node removes only its connections", () => {
  const edges = [edge("a", "j", "left"), edge("b", "j", "right"), edge("j", "o")];
  const out = m.removeNode(baseNodes, edges, "j");
  assert.deepEqual(out.nodes.map((n: N) => n.id), ["a", "b", "f", "o"]);
  assert.deepEqual(out.edges, []);
  const kept = m.removeNode(baseNodes, edges, "o");
  assert.equal(kept.edges.length, 2);
});

test("removeEdge removes one connection", () => {
  const edges = [edge("a", "j", "left"), edge("b", "j", "right")];
  assert.deepEqual(m.removeEdge(edges, edge("a", "j", "left")), [edge("b", "j", "right")]);
});

test("downstream nodes are found through several steps", () => {
  const edges = [edge("a", "f"), edge("f", "o")];
  assert.deepEqual(m.downstreamOf(edges, "a").sort(), ["f", "o"]);
  assert.deepEqual(m.downstreamOf(edges, "o"), []);
});

test("the server payload carries no positions", () => {
  const payload = m.toPayload([{ ...baseNodes[0], x: 123, y: 456 }], []);
  assert.deepEqual(payload.nodes[0], { id: "a", type: "source", config: { table_id: "t1" } });
});

test("type categories match the server's rules", () => {
  const cases: Record<string, string> = {
    INTEGER: "numeric", "numeric(18,2)": "numeric", "double precision": "numeric", money: "numeric",
    varchar: "text", "character varying": "text", nvarchar: "text", text: "text",
    datetime2: "datetime", "timestamp without time zone": "datetime", date: "datetime",
    bit: "boolean", boolean: "boolean", uuid: "other", xml: "other",
  };
  for (const [type, cat] of Object.entries(cases)) assert.equal(m.typeCategory(type), cat, type);
});

test("operators depend on the column type", () => {
  assert.ok(m.operatorsFor("numeric").includes("gt"));
  assert.ok(!m.operatorsFor("text").includes("gt"));
  assert.ok(m.operatorsFor("text").includes("contains"));
  assert.deepEqual(m.operatorsFor("other"), ["is_null", "is_not_null"]);
  assert.equal(m.operatorNeedsValue("is_null"), false);
  assert.equal(m.operatorNeedsValue("eq"), true);
});

// ------------------------------------------------- needs recalculation

test("changing a node changes its signature and everything after it", () => {
  const edges = [edge("a", "f"), edge("f", "o")];
  const nodes = [node("a", "source", { table_id: "t1" }), node("f", "filter", { combine: "AND", conditions: [] }), node("o", "output")];
  const before = m.effectiveSignatures(nodes, edges);
  const changed = nodes.map((n) => (n.id === "f" ? { ...n, config: { combine: "OR", conditions: [] } } : n));
  const after = m.effectiveSignatures(changed, edges);
  assert.equal(before.a, after.a);          // upstream untouched
  assert.notEqual(before.f, after.f);
  assert.notEqual(before.o, after.o);       // downstream is now out of date
});

test("moving a node does not make results out of date", () => {
  const nodes = [node("a", "source", { table_id: "t1" })];
  const edges: never[] = [];
  const before = m.effectiveSignatures(nodes, edges);
  const moved = m.effectiveSignatures([{ ...nodes[0], x: 500, y: 90 }], edges);
  assert.deepEqual(before, moved);
});

test("key order in a config does not matter", () => {
  assert.equal(m.stableStringify({ a: 1, b: [2, { d: 1, c: 2 }] }), m.stableStringify({ b: [2, { c: 2, d: 1 }], a: 1 }));
});

test("outdated nodes are those whose signature moved on", () => {
  const sigs = { a: "S1", f: "S2-new" };
  const runs = {
    a: { state: "successful" as const, signature: "S1" },
    f: { state: "successful" as const, signature: "S2-old" },
    o: { state: "running" as const, signature: "x" },
  };
  assert.deepEqual(m.outdatedNodes(runs, sigs), ["f"]);
});

test("display status", () => {
  const ok = { state: "successful" as const, signature: "S" };
  assert.deepEqual(m.displayStatus("ready", ok, "S"), { status: "successful", outdated: false });
  assert.deepEqual(m.displayStatus("ready", ok, "T"), { status: "ready", outdated: true });
  assert.deepEqual(m.displayStatus("ready", { state: "failed" as const, signature: "S" }, "S"), { status: "failed", outdated: false });
  assert.deepEqual(m.displayStatus("ready", undefined, "S"), { status: "ready", outdated: false });
  assert.deepEqual(m.displayStatus("invalid", ok, "S"), { status: "invalid", outdated: false });
  assert.deepEqual(m.displayStatus("not_configured", undefined, "S"), { status: "not_configured", outdated: false });
  assert.deepEqual(m.displayStatus("ready", { state: "running" as const, signature: "S" }, "S"), { status: "running", outdated: false });
  assert.deepEqual(m.displayStatus(undefined, undefined, undefined), { status: "not_configured", outdated: false });
});

// ------------------------------------------------------------- drafts

class MemoryStorage {
  data = new Map<string, string>();
  getItem(k: string) { return this.data.get(k) ?? null; }
  setItem(k: string, v: string) { this.data.set(k, v); }
  removeItem(k: string) { this.data.delete(k); }
}

test("a draft round-trips", () => {
  const nodes = [{ ...node("a", "source", { table_id: "t1" }), x: 10.4, y: 20 }, node("f", "filter", { combine: "AND", conditions: [{ column: "age", operator: "gt", value: 30 }] })];
  const edges = [edge("a", "f")];
  const store = new MemoryStorage();
  assert.equal(m.saveDraft("src1", nodes, edges, store), true);
  const back = m.loadDraft("src1", store)!;
  assert.equal(back.nodes.length, 2);
  assert.equal(back.nodes[0].x, 10);
  assert.deepEqual(back.nodes[1].config, nodes[1].config);
  assert.deepEqual(back.edges, edges);
});

test("a draft for another source is not loaded", () => {
  const store = new MemoryStorage();
  m.saveDraft("src1", [node("a", "source")], [], store);
  assert.equal(m.loadDraft("src2", store), null);
  store.setItem(m.draftStorageKey("src2"), store.getItem(m.draftStorageKey("src1"))!);
  assert.equal(m.loadDraft("src2", store), null);   // content says src1
});

test("credential-looking keys never reach a saved draft", () => {
  const n = node("a", "source", { table_id: "t1", password: "hunter2", nested: { api_key: "k", token: "t", keep: "yes" }, connectionString: "x" });
  const text = m.serializeDraft("s", [n], []);
  assert.ok(!text.includes("hunter2"));
  assert.ok(!text.includes("api_key"));
  assert.ok(!text.includes("connectionString"));
  assert.ok(text.includes("keep"));
});

test("a hand-edited or damaged draft is sanitised, not trusted", () => {
  const bad = JSON.stringify({
    version: 1,
    sourceId: "s",
    nodes: [
      { id: "a", type: "source", x: 1, y: 2, config: { table_id: "t", password: "p" } },
      { id: "a", type: "source", x: 1, y: 2, config: {} },            // duplicate id
      { id: "x y", type: "source", x: 1, y: 2, config: {} },          // bad id
      { id: "w", type: "run_shell", x: 1, y: 2, config: {} },          // unknown type
      { id: "n", type: "filter", x: "1", y: 2, config: {} },           // bad coordinate
      { id: "f", type: "filter", x: 5, y: 6, config: "oops" },         // bad config
    ],
    edges: [
      { source: "a", target: "f", port: "in" },
      { source: "f", target: "a", port: "in" },                        // into a source: refused
      { source: "a", target: "ghost", port: "in" },
      "nonsense",
    ],
  });
  const d = m.parseDraft(bad, "s")!;
  assert.deepEqual(d.nodes.map((n: N) => n.id), ["a", "f"]);
  assert.ok(!JSON.stringify(d).includes("password"));
  assert.deepEqual(d.nodes[1].config, m.defaultConfig("filter"));
  assert.deepEqual(d.edges, [edge("a", "f")]);
});

test("garbage and oversized drafts are ignored", () => {
  assert.equal(m.parseDraft("{not json", "s"), null);
  assert.equal(m.parseDraft(null, "s"), null);
  assert.equal(m.parseDraft(JSON.stringify({ version: 99, sourceId: "s", nodes: [], edges: [] }), "s"), null);
  assert.equal(m.parseDraft("x".repeat(500_000), "s"), null);
});

test("storage that throws or is missing does not break saving", () => {
  const broken = { getItem() { throw new Error("no"); }, setItem() { throw new Error("full"); }, removeItem() { throw new Error("no"); } };
  assert.equal(m.saveDraft("s", [], [], broken), false);
  assert.equal(m.loadDraft("s", broken), null);
  assert.doesNotThrow(() => m.clearDraft("s", broken));
  assert.equal(m.saveDraft("s", [], [], null), false);
});

test("row counts say whether they are exact", () => {
  assert.equal(m.formatRowCount(1234, false), "1,234 rows (exact)");
  assert.equal(m.formatRowCount(1234, true), "about 1,234 rows (estimate)");
  assert.equal(m.formatRowCount(5, undefined), "about 5 rows (estimate)");
  assert.equal(m.formatRowCount(null, true), "Row count unknown");
});

test("node summaries describe the settings and handle empty ones", () => {
  assert.equal(m.nodeSummary(node("a", "source"), "public.customers"), "public.customers");
  assert.equal(m.nodeSummary(node("a", "source")), "Choose a table");
  assert.equal(m.nodeSummary({ ...node("j", "join"), config: { join_type: "LEFT", conditions: [{}] } }), "LEFT join · 1 condition");
  assert.equal(m.nodeSummary(node("j", "join", { conditions: [] })), "No matching columns yet");
  assert.equal(m.nodeSummary(node("d", "drop_columns", { columns: ["a", "b"] })), "Remove 2 columns");
  assert.equal(m.nodeSummary(node("h", "handle_missing", { strategy: "mean", columns: ["a"] })), "fill with mean · 1 column");
  assert.equal(m.nodeSummary(node("o", "output", { name: "" })), "Unnamed dataset");
});

// ---- Catalog "Build Dataset" prefill

const spotsFor = (count: number) => Array.from({ length: count }, (_, i) => ({ x: 40, y: 40 + i * 152 }));

test("prefill adds one Source node per table and nothing else", () => {
  const out = m.prefillSourceNodes([], ["t1", "t2", "t3"], spotsFor);
  assert.deepEqual(out.added, ["t1", "t2", "t3"]);
  assert.equal(out.nodes.length, 3);
  assert.ok(out.nodes.every((n: N) => n.type === "source"), "no join, filter or output is created");
  assert.equal(new Set(out.nodes.map((n: N) => n.id)).size, 3, "unique ids");
  assert.deepEqual(out.nodes.map((n: N) => n.config.table_id), ["t1", "t2", "t3"]);
});

test("prefill never duplicates a table already on the canvas, even if run twice", () => {
  const first = m.prefillSourceNodes([], ["t1", "t2"], spotsFor);
  const second = m.prefillSourceNodes(first.nodes, ["t1", "t2", "t3"], spotsFor);
  assert.equal(second.nodes.length, 3);
  assert.deepEqual(second.added, ["t3"]);
  assert.deepEqual(second.alreadyThere, ["t1", "t2"]);
  const again = m.prefillSourceNodes(second.nodes, ["t1", "t2", "t3"], spotsFor);
  assert.equal(again.nodes.length, 3);
  assert.deepEqual(again.added, []);
});

test("prefill keeps existing nodes and edges untouched and respects the node limit", () => {
  const existing = Array.from({ length: m.MAX_NODES - 1 }, (_, i) => node(`n${i}`, "filter"));
  const out = m.prefillSourceNodes(existing, ["a", "b", "c"], spotsFor);
  assert.equal(out.nodes.length, m.MAX_NODES);
  assert.deepEqual(out.added, ["a"]);
  assert.deepEqual(out.leftOut, ["b", "c"]);
  assert.equal(out.nodes.slice(0, existing.length).every((n: N, i: number) => n === existing[i]), true);
});

test("a join already on the canvas is recognised, in either orientation", () => {
  const nodes = [
    node("source_1", "source", { table_id: "t1" }),
    node("source_2", "source", { table_id: "t2" }),
    node("join_1", "join", { join_type: "INNER", conditions: [{ left_column: "a", right_column: "b" }] }),
  ];
  const edges = [edge("source_1", "join_1", "left"), edge("source_2", "join_1", "right")];
  const rel = { leftTableId: "t1", rightTableId: "t2", conditions: [{ leftColumn: "a", rightColumn: "b" }] };
  assert.equal(m.findJoinForRelationship(nodes, edges, rel), "join_1");
  assert.equal(
    m.findJoinForRelationship(nodes, edges, { leftTableId: "t2", rightTableId: "t1", conditions: [{ leftColumn: "b", rightColumn: "a" }] }),
    "join_1"
  );
  assert.equal(m.findJoinForRelationship(nodes, edges, { ...rel, conditions: [{ leftColumn: "a", rightColumn: "z" }] }), null);
  assert.equal(m.findJoinForRelationship(nodes, [], rel), null, "an unconnected join does not count");
});

test("prefill records the source on each new Source step when one is given, and nothing extra otherwise", () => {
  const withSource = m.prefillSourceNodes([], ["t1", "t2"], spotsFor, "src-A");
  assert.deepEqual(withSource.nodes.map((n: N) => n.config), [
    { table_id: "t1", source_id: "src-A" },
    { table_id: "t2", source_id: "src-A" },
  ]);
  const without = m.prefillSourceNodes([], ["t1"], spotsFor);
  assert.deepEqual(without.nodes[0].config, { table_id: "t1" });
});
