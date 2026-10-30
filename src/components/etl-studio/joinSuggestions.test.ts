/**
 * Tests for the deterministic join suggestions.
 *   node --test src/components/etl-studio/joinSuggestions.test.ts
 */
import test from "node:test";
import assert from "node:assert/strict";
// @ts-expect-error Node needs the explicit .ts extension; the type checker does not.
import * as s from "./joinSuggestions.ts";

const cat = (t: string) => (/int|decimal/.test(t) ? "numeric" : "text");
const col = (name: string, dataType: string, isPrimaryKey = false, isForeignKey = false) => ({ name, dataType, category: cat(dataType), isPrimaryKey, isForeignKey });

const customers = { id: "T1", name: "customers", columns: [col("customer_id", "int", true), col("full_name", "varchar"), col("region", "varchar")] };
const orders = { id: "T2", name: "orders", columns: [col("order_id", "int", true), col("customer_id", "int", false, true), col("region", "varchar"), col("amount", "decimal")] };
const loans = { id: "T3", name: "loans", columns: [col("loan_id", "int", true), col("customer_id", "varchar")] };
const FK = { childTable: "orders", childColumn: "customer_id", parentTable: "customers", parentColumn: "customer_id", constraintName: "fk_orders_customer" };

test("a declared foreign key is confirmed, child on the left", () => {
  const out = s.suggestJoins([customers, orders], [FK]);
  const d = out.find((x: { origin: string }) => x.origin === "declared")!;
  assert.equal(d.tier, "confirmed");
  assert.equal(d.leftTableId, "T2");
  assert.equal(d.rightTableId, "T1");
  assert.equal(d.leftColumn, "customer_id");
  assert.match(d.evidence[0], /fk_orders_customer/);
  assert.equal(out[0], d, "confirmed suggestions rank first");
});

test("an inferred match is never reported as confirmed", () => {
  const out = s.suggestJoins([customers, orders], []);
  const inferred = out.find((x: { leftColumn: string }) => x.leftColumn === "customer_id");
  assert.ok(inferred);
  assert.equal(inferred.origin, "inferred");
  assert.notEqual(inferred.tier, "confirmed");
  assert.equal(inferred.tier, "strong");  // same name, same type, one side is a primary key
});

test("the declared pair is not repeated as an inferred one", () => {
  const out = s.suggestJoins([customers, orders], [FK]);
  const same = out.filter((x: { leftColumn: string; rightColumn: string }) => x.leftColumn === "customer_id" && x.rightColumn === "customer_id");
  assert.equal(same.length, 1);
});

test("columns that only share a vague name are not suggested", () => {
  const a = { id: "A", name: "a", columns: [col("id", "int", true), col("name", "varchar"), col("status", "varchar")] };
  const b = { id: "B", name: "b", columns: [col("id", "int", true), col("name", "varchar"), col("status", "varchar")] };
  assert.deepEqual(s.suggestJoins([a, b], []), []);
});

test("same name without a key is only a possible match, with a repeat warning", () => {
  const out = s.suggestJoins([customers, orders], []);
  const region = out.find((x: { leftColumn: string }) => x.leftColumn === "region")!;
  assert.equal(region.tier, "possible");
  assert.ok(region.warnings.some((w: string) => /may repeat/.test(w)));
});

test("different types are flagged as needing cleaning, never strong", () => {
  const out = s.suggestJoins([customers, loans], []);
  const m = out.find((x: { rightTableId: string; leftTableId: string }) => [x.leftTableId, x.rightTableId].includes("T3"))!;
  assert.equal(m.tier, "possible");
  assert.equal(m.needsCleaning, true);
  assert.ok(m.warnings.some((w: string) => /types differ/.test(w)));
});

test("the <table>_id naming convention is recognised and explained", () => {
  const people = { id: "P", name: "people", columns: [col("id", "int", true), col("full_name", "varchar")] };
  const visits = { id: "V", name: "visits", columns: [col("visit_id", "int", true), col("person_id", "int")] };
  assert.deepEqual(s.suggestJoins([people, visits], []), [], "people -> person is not a plain plural, so no guess");
  const customersTbl = { id: "C", name: "customers", columns: [col("id", "int", true), col("full_name", "varchar")] };
  const sales = { id: "S", name: "sales", columns: [col("sale_id", "int", true), col("customer_id", "int")] };
  const out = s.suggestJoins([customersTbl, sales], []);
  assert.equal(out.length, 1);
  assert.equal(out[0].origin, "inferred");
  assert.equal(out[0].leftTableId, "S");          // the primary-key side (customers) is on the right
  assert.equal(out[0].rightColumn, "id");
  assert.match(out[0].evidence.join(" "), /naming pattern/);
});

test("no suggestion carries an invented score", () => {
  for (const x of s.suggestJoins([customers, orders, loans], [FK])) {
    assert.deepEqual(Object.keys(x).sort(), [
      "evidence", "id", "joinTypeReason", "leftColumn", "leftTableId", "needsCleaning", "origin", "pairs", "recommendedJoinType",
      "rightColumn", "rightTableId", "tier", "verified", "warnings",
    ]);
    assert.equal(x.verified, false, "nothing here has looked at the data");
    assert.ok(x.evidence.length > 0, "every suggestion explains itself");
  }
});

test("results are deterministic and capped", () => {
  const many = Array.from({ length: 12 }, (_, i) => ({
    id: `X${i}`, name: `t${i}`, columns: Array.from({ length: 6 }, (_, c) => col(`shared_${c}`, "int")),
  }));
  const one = s.suggestJoins(many, []);
  const two = s.suggestJoins(many, []);
  assert.deepEqual(one, two);
  assert.ok(one.length <= 25);
});

test("a declared link whose columns are missing is ignored, not invented", () => {
  const bad = { ...FK, childColumn: "nope" };
  assert.equal(s.suggestJoins([customers, orders], [bad]).some((x: { origin: string }) => x.origin === "declared"), false);
});

// ---------------------------------------------------------------- Step B

test("a composite foreign key is ONE suggestion with every column pair", () => {
  const a = { id: "A", name: "line_items", columns: [col("order_id", "int", false, true), col("sku", "varchar", false, true), col("qty", "int")] };
  const b = { id: "B", name: "products", columns: [col("order_id", "int", true), col("sku", "varchar", true), col("title", "varchar")] };
  const links = [
    { childTable: "line_items", childColumn: "order_id", parentTable: "products", parentColumn: "order_id", constraintName: "fk_li" },
    { childTable: "line_items", childColumn: "sku", parentTable: "products", parentColumn: "sku", constraintName: "fk_li" },
  ];
  const out = s.suggestJoins([a, b], links);
  assert.equal(out.length, 1, JSON.stringify(out.map((x: { id: string }) => x.id)));
  assert.equal(out[0].pairs.length, 2);
  assert.equal(out[0].tier, "confirmed");
  assert.match(out[0].evidence[0], /\(order_id, sku\)/);
});

test("two foreign keys WITHOUT a constraint name are never merged", () => {
  const a = { id: "A", name: "loans", columns: [col("borrower_id", "int", false, true), col("guarantor_id", "int", false, true)] };
  const b = { id: "B", name: "people", columns: [col("person_id", "int", true)] };
  const links = [
    { childTable: "loans", childColumn: "borrower_id", parentTable: "people", parentColumn: "person_id", constraintName: "" },
    { childTable: "loans", childColumn: "guarantor_id", parentTable: "people", parentColumn: "person_id", constraintName: "" },
  ];
  const out = s.suggestJoins([a, b], links).filter((x: { origin: string }) => x.origin === "declared");
  assert.equal(out.length, 2);
  assert.ok(out.every((x: { pairs: unknown[] }) => x.pairs.length === 1));
});

test("a composite key with one missing column is dropped, not shortened", () => {
  const a = { id: "A", name: "line_items", columns: [col("order_id", "int")] };
  const b = { id: "B", name: "products", columns: [col("order_id", "int", true), col("sku", "varchar", true)] };
  const links = [
    { childTable: "line_items", childColumn: "order_id", parentTable: "products", parentColumn: "order_id", constraintName: "fk_li" },
    { childTable: "line_items", childColumn: "sku", parentTable: "products", parentColumn: "sku", constraintName: "fk_li" },
  ];
  assert.equal(s.suggestJoins([a, b], links).some((x: { origin: string }) => x.origin === "declared"), false);
});

test("sibling tables that share a parent key are not joined directly when the parent is present", () => {
  const pay = { id: "P", name: "payments", columns: [col("payment_id", "int", true), col("customer_id", "int", false, true)] };
  const withParent = s.suggestJoins([customers, orders, pay], [FK, { ...FK, childTable: "payments", constraintName: "fk_pay" }]);
  assert.equal(withParent.some((x: { leftTableId: string; rightTableId: string }) => [x.leftTableId, x.rightTableId].sort().join() === "P,T2"), false);
  assert.equal(withParent.filter((x: { origin: string }) => x.origin === "declared").length, 2);
});

test("sibling tables without the parent on the canvas get a many-to-many warning and stay only possible", () => {
  const pay = { id: "P", name: "payments", columns: [col("payment_id", "int", true), col("customer_id", "int", false, true)] };
  const out = s.suggestJoins([orders, pay], [FK, { ...FK, childTable: "payments", constraintName: "fk_pay" }]);
  assert.equal(out.length, 1);
  assert.equal(out[0].tier, "possible");
  assert.ok(out[0].warnings.some((w: string) => /multiply rows/.test(w)));
});

test("a Relationship Map join is its own kind, never 'confirmed', and keeps the type the user chose", () => {
  const rel = { leftTableId: "T2", rightTableId: "T1", pairs: [{ leftColumn: "region", rightColumn: "region" }], joinType: "FULL" };
  const out = s.suggestJoins([customers, orders], [], [rel]);
  const u = out.find((x: { origin: string }) => x.origin === "workspace")!;
  assert.equal(u.tier, "defined");
  assert.equal(u.recommendedJoinType, "FULL");
  assert.match(u.joinTypeReason, /You chose FULL/);
  assert.equal(out.filter((x: { leftColumn: string }) => x.leftColumn === "region").length, 1, "the rule-based region match is not repeated");
});

test("the same relationship declared AND drawn on the map is listed once, declared wins", () => {
  const rel = { leftTableId: "T1", rightTableId: "T2", pairs: [{ leftColumn: "customer_id", rightColumn: "customer_id" }], joinType: "INNER" };
  const out = s.suggestJoins([customers, orders], [FK], [rel]);
  const same = out.filter((x: { leftColumn: string; rightColumn: string }) => x.leftColumn === "customer_id" && x.rightColumn === "customer_id");
  assert.equal(same.length, 1);
  assert.equal(same[0].origin, "declared");
  assert.ok(same[0].evidence.some((e: string) => /also defined on the Catalog/.test(e)));
});

test("a map join with a column that no longer exists is ignored", () => {
  const rel = { leftTableId: "T2", rightTableId: "T1", pairs: [{ leftColumn: "gone", rightColumn: "region" }], joinType: null };
  assert.equal(s.suggestJoins([customers, orders], [], [rel]).some((x: { origin: string }) => x.origin === "workspace"), false);
});

test("the recommended type is LEFT with a plain reason, and every join type has help text", () => {
  const out = s.suggestJoins([customers, orders], [FK]);
  assert.equal(out[0].recommendedJoinType, "LEFT");
  assert.match(out[0].joinTypeReason, /orders row/);
  assert.match(out[0].joinTypeReason, /INNER would silently drop/);
  assert.deepEqual(Object.keys(s.JOIN_TYPE_HELP).sort(), ["FULL", "INNER", "LEFT", "RIGHT"]);
});
