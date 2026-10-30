/**
 * node --test src/components/etl-studio/guidance.test.ts
 */
import test from "node:test";
import assert from "node:assert/strict";
// @ts-expect-error Node needs the explicit .ts extension; the type checker does not.
import * as G from "./guidance.ts";
// @ts-expect-error Node needs the explicit .ts extension; the type checker does not.
import * as S from "./guidedStages.ts";
/* eslint-disable @typescript-eslint/no-explicit-any */
const g: any = G;
const s: any = S;

const st = (id: string, status: string, summary = "") => ({ id, label: id, status, summary, problems: [], notes: [] });
const ctx = (o: Record<string, unknown> = {}) => ({ hasOutput: false, sourceCount: 0, joinCount: 0, ...o });

test("every stage and status gives exactly one non-empty sentence", () => {
  const statuses = ["complete", "incomplete", "invalid", "measurement_required", "measurement_outdated", "review_required", "not_validated", "outdated", "optional", "not_needed"];
  for (const id of s.STAGE_ORDER) for (const status of statuses) for (const hasOutput of [false, true]) {
    const t = g.instructionFor(st(id, status), ctx({ hasOutput, sourceCount: 2, joinCount: 1 }));
    assert.equal(typeof t, "string");
    assert.ok(t.length > 10, `${id}/${status}`);
    assert.ok(!t.includes("\n"));
  }
});

test("select: nothing yet, empty source, error, complete", () => {
  assert.match(g.instructionFor(st("select", "incomplete"), ctx()), /Pick the tables/);
  assert.match(g.instructionFor(st("select", "incomplete"), ctx({ sourceCount: 1 })), /empty Source step/);
  assert.match(g.instructionFor(st("select", "invalid"), ctx({ sourceCount: 1 })), /problem/);
  assert.match(g.instructionFor(st("select", "complete"), ctx({ sourceCount: 2 })), /Continue to Join/);
});

test("join: first join vs finishing a join vs errors vs done", () => {
  assert.match(g.instructionFor(st("join", "incomplete"), ctx({ sourceCount: 2 })), /Suggested joins panel.*New join/);
  assert.match(g.instructionFor(st("join", "incomplete"), ctx({ sourceCount: 2, joinCount: 1 })), /connect both inputs/);
  assert.match(g.instructionFor(st("join", "invalid"), ctx({ joinCount: 1 })), /errors/);
  assert.match(g.instructionFor(st("join", "complete"), ctx({ joinCount: 1 })), /measure each one/);
  assert.match(g.instructionFor(st("join", "not_needed"), ctx({ sourceCount: 1 })), /needs no join/);
});

test("validate: the sentence matches the real status and never claims validation early", () => {
  assert.match(g.instructionFor(st("validate", "incomplete"), ctx()), /Add an Output/);
  assert.match(g.instructionFor(st("validate", "incomplete", "Finish the earlier stages first."), ctx({ hasOutput: true })), /earlier stages/);
  assert.match(g.instructionFor(st("validate", "incomplete", "The Output step is not connected to the flow yet."), ctx({ hasOutput: true })), /Connect the last step/);
  assert.match(g.instructionFor(st("validate", "measurement_required"), ctx({ hasOutput: true })), /Measure each join/);
  assert.match(g.instructionFor(st("validate", "measurement_outdated"), ctx({ hasOutput: true })), /measure it again/);
  assert.match(g.instructionFor(st("validate", "not_validated"), ctx({ hasOutput: true })), /Preview the final dataset/);
  assert.match(g.instructionFor(st("validate", "outdated"), ctx({ hasOutput: true })), /again/);
  assert.match(g.instructionFor(st("validate", "review_required"), ctx({ hasOutput: true })), /repeated-rows warning/);
  assert.match(g.instructionFor(st("validate", "complete"), ctx({ hasOutput: true })), /Validated on real data/);
  for (const status of ["measurement_required", "not_validated", "review_required", "outdated"]) {
    assert.doesNotMatch(g.instructionFor(st("validate", status), ctx({ hasOutput: true })), /Validated on real data/);
  }
});

test("final does not promise features that are not built", () => {
  const done = g.instructionFor(st("final", "complete"), ctx());
  assert.match(done, /not available yet/);
  assert.match(g.instructionFor(st("final", "incomplete"), ctx()), /Finish Validate/);
});

// ---- notices ordering
// @ts-expect-error Node needs the explicit .ts extension; the type checker does not.
import * as N from "./noticeOrder.ts";
const n: any = N;
const it = (key: string, severity: string) => ({ key, severity, node: null });

test("notices: errors first, then required actions, warnings, information; stable inside a level", () => {
  const { shown } = n.arrangeNotices([it("i1", "info"), it("w1", "warning"), it("e1", "error"), it("a1", "action"), it("e2", "error")], 5);
  assert.deepEqual(shown.map((x: any) => x.key), ["e1", "e2", "a1", "w1", "i1"]);
});

test("notices: only surplus information is folded; errors, actions and warnings never are", () => {
  const items = [it("i1", "info"), it("i2", "info"), it("i3", "info"), it("e1", "error"), it("a1", "action"), it("w1", "warning"), it("w2", "warning")];
  const { shown, hiddenInfo } = n.arrangeNotices(items, 1);
  assert.deepEqual(hiddenInfo.map((x: any) => x.key), ["i2", "i3"]);
  for (const k of ["e1", "a1", "w1", "w2", "i1"]) assert.ok(shown.some((x: any) => x.key === k), k);
  assert.equal(n.arrangeNotices(items, 0).shown.filter((x: any) => x.severity !== "info").length, 4);
});
