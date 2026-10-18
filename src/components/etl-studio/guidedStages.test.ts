/**
 * Tests for the guided stages.
 *   node --test src/components/etl-studio/guidedStages.test.ts
 */
import test from "node:test";
import assert from "node:assert/strict";
// @ts-expect-error Node needs the explicit .ts extension; the type checker does not.
import * as gm from "./guidedStages.ts";
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const g: any = gm;

type V = { status: string; outdated: boolean };
type Stage = { id: string; label: string; status: string; summary: string; problems: string[]; notes: string[] };
const src = (id: string, table = "T") => ({ id, type: "source", config: { table_id: table } });
const node = (id: string, type: string) => ({ id, type, config: {} });
const e = (source: string, target: string) => ({ source, target });
const ready: V = { status: "ready", outdated: false };
const done: V = { status: "successful", outdated: false };

function base0() { return { nodes: [], edges: [], views: {}, measurements: {} }; }
function run(over: Record<string, unknown>) {
  const base = { nodes: [], edges: [], views: {}, previewedRows: {}, measurements: {}, checked: true };
  return g.computeStages({ ...base, ...over }) as Stage[];
}
const by = (stages: Stage[], id: string) => stages.find((s) => s.id === id)!;

test("an empty canvas: everything is incomplete and Select Tables is current", () => {
  const s = run({});
  assert.deepEqual(s.map((x) => x.id), ["select", "join", "transform", "validate", "final"]);
  assert.equal(by(s, "select").status, "incomplete");
  assert.equal(g.currentStage(s), "select");
});

test("one table: join is not needed, transform optional, validate waits for an output", () => {
  const s = run({ nodes: [src("s1")], views: { s1: ready } });
  assert.equal(by(s, "select").status, "complete");
  assert.equal(by(s, "join").status, "not_needed");
  assert.equal(by(s, "transform").status, "optional");
  assert.equal(by(s, "validate").status, "incomplete");
  assert.match(by(s, "validate").problems.join(" "), /Add an Output/);
  assert.equal(g.currentStage(s), "validate");
});

test("two tables with no join are incomplete and say so", () => {
  const s = run({ nodes: [src("s1"), src("s2")], views: { s1: ready, s2: ready } });
  assert.equal(by(s, "join").status, "incomplete");
  assert.match(by(s, "join").summary, /2 tables not joined/);
  assert.equal(g.currentStage(s), "join");
});

test("a table that feeds no join is named", () => {
  const nodes = [src("s1"), src("s2"), src("s3"), node("j1", "join")];
  const s = run({ nodes, edges: [e("s1", "j1"), e("s2", "j1")], views: { s1: ready, s2: ready, s3: ready, j1: ready } });
  assert.equal(by(s, "join").status, "incomplete");
  assert.match(by(s, "join").problems.join(" "), /s3 is not joined/);
});

test("a join with errors makes the join stage invalid", () => {
  const nodes = [src("s1"), src("s2"), node("j1", "join")];
  const s = run({ nodes, edges: [e("s1", "j1"), e("s2", "j1")], views: { s1: ready, s2: ready, j1: { status: "invalid", outdated: false } } });
  assert.equal(by(s, "join").status, "invalid");
});

const JOINED = {
  nodes: [src("s1"), src("s2"), node("j1", "join"), node("o1", "output")],
  edges: [e("s1", "j1"), e("s2", "j1"), e("j1", "o1")],
  views: { s1: ready, s2: ready, j1: ready, o1: ready },
};
const fresh = { outdated: false, multipliesMainSide: false, extraRows: 0 };

test("a configured join that was never measured needs 'Join measurement required' (not 'Not validated')", () => {
  const s = run({ ...JOINED });
  assert.equal(by(s, "join").status, "complete", "Configured is the Join stage's job");
  assert.equal(by(s, "validate").status, "measurement_required");
  assert.match(by(s, "validate").summary, /Join measurement required for j1/);
  assert.equal(g.STATUS_TEXT.measurement_required, "Join measurement required");
  assert.equal(by(s, "final").status, "incomplete");
  assert.equal(g.currentStage(s), "validate");
  assert.deepEqual(g.joinChecks({ ...base0(), ...JOINED }).map((c: { state: string }) => c.state), ["configured"]);
});

test("measured but never previewed is 'Not validated'", () => {
  const s = run({ ...JOINED, measurements: { j1: fresh } });
  assert.equal(by(s, "validate").status, "not_validated");
  assert.equal(by(s, "join").summary, "1 join configured, 1 measured.");
});

test("a measurement that is out of date must be run again, and blocks Validate even after a preview", () => {
  const s = run({ ...JOINED, views: { ...JOINED.views, o1: done }, measurements: { j1: { ...fresh, outdated: true } }, previewedRows: { o1: { rowsShown: 6, hasMore: false } } });
  assert.equal(by(s, "validate").status, "measurement_outdated");
  assert.match(by(s, "validate").problems.join(" "), /Measure again/);
  assert.equal(by(s, "final").status, "incomplete");
});

test("an unmeasured join blocks Validate even when the final preview succeeded", () => {
  const s = run({ ...JOINED, views: { ...JOINED.views, o1: done }, previewedRows: { o1: { rowsShown: 6, hasMore: false } } });
  assert.equal(by(s, "validate").status, "measurement_required");
  assert.notEqual(by(s, "final").status, "complete");
});

test("measured joins + a current successful preview => Validated and Final complete", () => {
  const s = run({ ...JOINED, views: { ...JOINED.views, o1: done }, measurements: { j1: fresh }, previewedRows: { o1: { rowsShown: 6, hasMore: false } } });
  assert.equal(by(s, "validate").status, "complete");
  assert.match(by(s, "validate").summary, /Joins measured\. The final preview returned 6 rows/);
  assert.equal(by(s, "final").status, "complete");
});

const details = { joinType: "LEFT", mainSide: "left", leftRows: 6, rightRows: 5, matchedPairs: 18, outputRows: 18, largestLeftGroup: 1, largestRightGroup: 3 };
const fan = (over = {}) => ({ outdated: false, multipliesMainSide: true, extraRows: 12, details, ...over });
const previewed = { views: { ...JOINED.views, o1: done }, previewedRows: { o1: { rowsShown: 18, hasMore: false } } };

test("repeated rows: Validate asks for a review (a warning), Final is not fully approved", () => {
  const s = run({ ...JOINED, ...previewed, measurements: { j1: fan() } });
  assert.equal(by(s, "validate").status, "review_required");
  assert.notEqual(by(s, "validate").status, "invalid", "a warning is not an error");
  assert.equal(by(s, "final").status, "incomplete");
  assert.match(by(s, "final").summary, /Not fully approved/);
  assert.match(by(s, "validate").summary, /18 rows/, "the technical result is still reported");
  assert.match(by(s, "validate").problems.join(" "), /j1: acknowledge the repeated-rows warning/);
  assert.equal(g.currentStage(s), "validate");
});

test("the evidence shows the join, the measured row counts and key groups, with nothing invented", () => {
  const [c] = g.joinChecks({ ...base0(), ...JOINED, measurements: { j1: fan() } });
  assert.equal(c.state, "measured");
  const t = c.fanOut.evidence.join(" | ");
  assert.match(t, /j1 repeats rows of its left side: 12 extra rows/);
  assert.match(t, /LEFT join: 6 left rows and 5 right rows give 18 matching pairs and 18 result rows/);
  assert.match(t, /3 on the right/);
  assert.match(g.FAN_OUT_EXPLANATION, /can be correct/);
  assert.match(g.FAN_OUT_EXPLANATION, /not an error/);
});

test("acknowledging the warning lets Validate and Final complete, and the acknowledgement is noted", () => {
  const s = run({ ...JOINED, ...previewed, measurements: { j1: fan({ acknowledged: true }) } });
  assert.equal(by(s, "validate").status, "complete");
  assert.equal(by(s, "final").status, "complete");
  assert.match(by(s, "validate").notes.join(" "), /j1: repeated rows acknowledged by you/);
});

test("an acknowledgement for an outdated measurement does not count: the measurement must be run again", () => {
  const s = run({ ...JOINED, ...previewed, measurements: { j1: fan({ acknowledged: true, outdated: true }) } });
  assert.equal(by(s, "validate").status, "measurement_outdated");
  assert.equal(by(s, "final").status, "incomplete");
});

test("a measurement without repeated rows needs no acknowledgement and completes normally", () => {
  const clean = { outdated: false, multipliesMainSide: false, extraRows: 0, details };
  const s = run({ ...JOINED, ...previewed, measurements: { j1: clean } });
  assert.equal(by(s, "validate").status, "complete");
  assert.equal(by(s, "final").status, "complete");
  assert.equal(g.joinChecks({ ...base0(), ...JOINED, measurements: { j1: clean } })[0].fanOut, null);
  assert.deepEqual(by(s, "validate").notes, []);
});

test("one acknowledged and one unacknowledged fan-out: still needs review for the second only", () => {
  const nodes = [src("s1"), src("s2"), src("s3"), node("j1", "join"), node("j2", "join"), node("o1", "output")];
  const edges = [e("s1", "j1"), e("s2", "j1"), e("j1", "j2"), e("s3", "j2"), e("j2", "o1")];
  const views = { s1: ready, s2: ready, s3: ready, j1: ready, j2: ready, o1: done };
  const s = run({ nodes, edges, views, previewedRows: { o1: { rowsShown: 9, hasMore: false } }, measurements: { j1: fan({ acknowledged: true }), j2: fan() } });
  assert.equal(by(s, "validate").status, "review_required");
  assert.match(by(s, "validate").summary, /in j2 before approving/);
  assert.doesNotMatch(by(s, "validate").summary, /j1 before/);
});

test("fan-out never overrides a real error: a failed preview stays an error", () => {
  const s = run({ ...JOINED, views: { ...JOINED.views, o1: { status: "failed", outdated: false } }, measurements: { j1: fan() } });
  assert.equal(by(s, "validate").status, "invalid");
});

test("a join that does not feed the output is not required to be measured", () => {
  const nodes = [src("s1"), src("s2"), src("s3"), node("j1", "join"), node("j2", "join"), node("o1", "output")];
  const edges = [e("s1", "j1"), e("s2", "j1"), e("j1", "o1"), e("s2", "j2"), e("s3", "j2")];
  const views = { s1: ready, s2: ready, s3: ready, j1: ready, j2: ready, o1: ready };
  const checks = g.joinChecks({ ...base0(), nodes, edges, views, measurements: {} });
  assert.deepEqual(checks.map((c: { id: string }) => c.id), ["j1"]);
});

test("an unconfigured join is 'not configured', never 'measurement required'", () => {
  const checks = g.joinChecks({ ...base0(), ...JOINED, views: { ...JOINED.views, j1: { status: "invalid", outdated: false } } });
  assert.equal(checks[0].state, "not_configured");
});

test("a real, current preview of the output completes Validate and Final", () => {
  const nodes = [src("s1"), node("o1", "output")];
  const s = run({
    nodes, edges: [e("s1", "o1")], views: { s1: ready, o1: done }, previewedRows: { o1: { rowsShown: 25, hasMore: true } },
  });
  assert.equal(by(s, "validate").status, "complete");
  assert.match(by(s, "validate").summary, /25 rows .*more exist/);
  assert.equal(by(s, "final").status, "complete");
  assert.equal(g.currentStage(s), "final");
});

test("changing a setting after the preview makes Validate outdated, and Final incomplete again", () => {
  const nodes = [src("s1"), node("o1", "output")];
  const s = run({ nodes, edges: [e("s1", "o1")], views: { s1: ready, o1: { status: "ready", outdated: true } } });
  assert.equal(by(s, "validate").status, "outdated");
  assert.equal(by(s, "final").status, "incomplete");
});

test("a failed preview is shown as an error, never as complete", () => {
  const nodes = [src("s1"), node("o1", "output")];
  const s = run({ nodes, edges: [e("s1", "o1")], views: { s1: ready, o1: { status: "failed", outdated: false } } });
  assert.equal(by(s, "validate").status, "invalid");
  assert.notEqual(by(s, "final").status, "complete");
});

test("an unfinished earlier stage keeps Validate waiting, even if an output exists", () => {
  const nodes = [src("s1"), src("s2"), node("o1", "output")];
  const s = run({ nodes, edges: [e("s1", "o1")], views: { s1: ready, s2: ready, o1: ready } });
  assert.equal(by(s, "validate").status, "incomplete");
  assert.match(by(s, "validate").summary, /earlier stages/);
});

test("transform steps: errors and unfinished steps are reported; finished ones count", () => {
  const base = { nodes: [src("s1"), node("f1", "filter"), node("o1", "output")], edges: [e("s1", "f1"), e("f1", "o1")] };
  assert.equal(by(run({ ...base, views: { s1: ready, f1: { status: "not_configured", outdated: false }, o1: ready } }), "transform").status, "incomplete");
  assert.equal(by(run({ ...base, views: { s1: ready, f1: { status: "invalid", outdated: false }, o1: ready } }), "transform").status, "invalid");
  const ok = by(run({ ...base, views: { s1: ready, f1: ready, o1: ready } }), "transform");
  assert.equal(ok.status, "complete");
  assert.match(ok.summary, /1 step set up/);
});

test("when the server check has not answered, that is said and nothing is called complete beyond structure", () => {
  const s = run({ nodes: [src("s1")], checked: false });
  assert.match(by(s, "select").problems.join(" "), /server has not checked/);
  assert.equal(by(s, "validate").status, "incomplete");
});

test("stage status is derived: the same input always gives the same stages", () => {
  const input = { nodes: [src("s1"), node("o1", "output")], edges: [e("s1", "o1")], views: { s1: ready, o1: ready } };
  assert.deepEqual(run(input), run(input));
});

test("tables from several data sources: Select says so and notes that they cannot run together", () => {
  const nodes = [src("s1"), src("s2")];
  const s = run({ nodes, views: { s1: ready, s2: ready }, sourceLabels: { s1: "Sales · postgresql", s2: "HR · mssql" } });
  assert.equal(by(s, "select").status, "complete");
  assert.match(by(s, "select").summary, /2 tables from 2 data sources/);
  assert.match(by(s, "select").notes.join(" "), /cannot be joined or run together/);
});

test("tables of one data source: wording is unchanged", () => {
  const s = run({ nodes: [src("s1"), src("s2")], views: { s1: ready, s2: ready }, sourceLabels: { s1: "Sales · postgresql", s2: "Sales · postgresql" } });
  assert.equal(by(s, "select").summary, "2 tables on the canvas.");
  assert.deepEqual(by(s, "select").notes, []);
});

test("a join the server marked invalid, with tables from two sources, gets the alternatives in Join", () => {
  const nodes = [src("s1"), src("s2"), node("j1", "join")];
  const s = run({
    nodes,
    edges: [e("s1", "j1"), e("s2", "j1")],
    views: { s1: ready, s2: ready, j1: { status: "invalid", outdated: false } },
    sourceLabels: { s1: "A · postgresql", s2: "B · mssql" },
  });
  assert.equal(by(s, "join").status, "invalid");
  assert.match(by(s, "join").problems.join(" "), /different data sources cannot be joined/);
  assert.match(by(s, "join").problems.join(" "), /view or a copy/);
  assert.notEqual(by(s, "final").status, "complete");
});
