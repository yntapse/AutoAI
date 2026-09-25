"""Grain-aware aggregation regression suite.

Fixture mirrors the real MLTest data exactly:

  MLTest_Customers  1 Alpha, 2 Beta (no orders at all), 3 Gamma
  MLTest_Orders     1001 -> cust 1, 56200      1002 -> cust 1, 15000
                    1003 -> cust 3, 9000
  MLTest_OrderItems 1001 has THREE items, 1002 has one, 1003 has two

  Customer 1 correct total = 56200 + 15000 = 71200
  Flat joined frame repeats order 1001 three times, so a naive SUM gives
  56200*3 + 15000 = 183600, and drop_duplicates(customer) gives 56200.

sql_builder is NOT used here (no database); the frame is assembled to match
what build_select would emit, and session.grain is populated the same way
_build_grain does, so the code under test is the real thing.
"""
import sys, types, json, pandas as pd
sys.path.insert(0, ".")
_stub = types.ModuleType("main")
_stub.assess_training_readiness = lambda df: {"ready": True, "reason": None,
    "n_rows": len(df), "n_columns": int(df.shape[1]), "n_folds": 5, "warnings": []}
sys.modules.setdefault("main", _stub)
import services.etl_agent as A

ok = fail = 0
def check(label, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("  PASS  " + label)
    else: fail += 1; print("  FAIL  " + label + " :: " + str(extra))

CUST, ORD, ITEM = "MLTest_Customers", "MLTest_Orders", "MLTest_OrderItems"
C_ID, C_NAME = "mltest_customers_customerid", "customername"
O_ID, O_AMT = "mltest_orders_orderid", "orderamount"
I_ID, I_QTY = "mltest_orderitems_orderitemid", "quantity"

# LEFT JOIN shape: customer 2 has no orders -> NULL order columns.
ROWS = [
    (1, "Alpha", 1001, 56200.0, 1, 2),
    (1, "Alpha", 1001, 56200.0, 2, 1),
    (1, "Alpha", 1001, 56200.0, 3, 4),
    (1, "Alpha", 1002, 15000.0, 4, 1),
    (2, "Beta",  None, None,    None, None),
    (3, "Gamma", 1003,  9000.0, 5, 3),
    (3, "Gamma", 1003,  9000.0, 6, 1),
]

def frame():
    return pd.DataFrame(
        [dict(zip([C_ID, C_NAME, O_ID, O_AMT, I_ID, I_QTY], r)) for r in ROWS]
    )

def session(fanned=True, pk_orders=("OrderID",), include_item_pk=True):
    s = A.ETLSession("mssql+pyodbc://u:p@h/db", "mssql", "dbo", db=None,
                     allowed_tables=[CUST, ORD, ITEM])
    s.df = frame()
    s.register_names(C_ID, C_NAME, O_ID, O_AMT, I_ID, I_QTY, CUST, ORD, ITEM)
    s.internal_columns = {O_ID, I_ID}
    s.primary_keys_by_table = {
        CUST: ["CustomerID"], ORD: list(pk_orders),
        ITEM: ["OrderItemID"] if include_item_pk else [],
    }
    s.grain = {
        "base_table": CUST,
        "tables": [CUST, ORD, ITEM],
        "primary_keys_by_table": dict(s.primary_keys_by_table),
        "column_sources": {
            C_ID: f"{CUST}.CustomerID", C_NAME: f"{CUST}.CustomerName",
            O_ID: f"{ORD}.OrderID", O_AMT: f"{ORD}.OrderAmount",
            I_ID: f"{ITEM}.OrderItemID", I_QTY: f"{ITEM}.Quantity",
        },
        "fanned_out_tables": [ITEM, ORD] if fanned else [],
        "multiplied_tables": [CUST, ORD] if fanned else [],
        "natural_key_aliases": (
            {CUST: [C_ID], ORD: [O_ID], ITEM: [I_ID]} if include_item_pk
            else {CUST: [C_ID], ORD: [O_ID]}
        ),
        "aggregated_to": None,
    }
    if pk_orders != ("OrderID",):
        s.grain["natural_key_aliases"].pop(ORD, None)
    return s

def totals(df):
    return {int(r[C_ID]): r["total_spend"] for _, r in df.iterrows()}

SUM_SPEND = [{"column": O_AMT, "function": "sum", "new_name": "total_spend"}]

print("\n--- 1. THE CORRECTNESS CASE: Customer 1 must total 71,200 ---")
s = session()
r = A.execute_tool(s, "aggregate_rows",
                   {"group_by": [C_ID], "dedupe_on": [O_ID], "aggregations": SUM_SPEND})
check("aggregation succeeded", r["ok"], r.get("error"))
t = totals(s.df)
check("Customer 1 = 71,200", t.get(1) == 71200.0, t.get(1))
check("Customer 3 = 9,000 (its 2 items did not double it)", t.get(3) == 9000.0, t.get(3))
check("de-fan collapsed 7 rows to 4 orders", r["deduped_rows"] == 4, r["deduped_rows"])
print(f"        totals: {t}")

print("\n--- 2. fan-out is what would have broken it ---")
s2 = session(fanned=False)   # guard disabled, so the naive path can be measured
r = A.execute_tool(s2, "aggregate_rows", {"group_by": [C_ID], "aggregations": SUM_SPEND})
naive = totals(s2.df)
check("without dedupe the same data gives 183,600", naive.get(1) == 183600.0, naive.get(1))
check("183,600 is exactly 56,200x3 + 15,000", naive.get(1) == 56200.0 * 3 + 15000.0)
print(f"        naive totals: {naive}  <-- the bug, reproduced")

print("\n--- 3. the guard refuses the unguarded sum on a fanned frame ---")
s = session()
r = A.execute_tool(s, "aggregate_rows", {"group_by": [C_ID], "aggregations": SUM_SPEND})
check("refused", r["ok"] is False, r)
check("error names the column to dedupe on", O_ID in r["error"], r["error"])
check("error warns against drop_duplicates", "drop_duplicates" in r["error"], r["error"])
check("dataset untouched by a refusal", len(s.df) == 7, len(s.df))

print("\n--- 4. multiple orders per customer are all retained ---")
s = session()
A.execute_tool(s, "aggregate_rows",
               {"group_by": [C_ID], "dedupe_on": [O_ID],
                "aggregations": SUM_SPEND + [{"column": O_ID, "function": "nunique",
                                              "new_name": "order_count"}]})
counts = {int(r[C_ID]): r["order_count"] for _, r in s.df.iterrows()}
check("Customer 1 has 2 orders, not 1 and not 4", counts.get(1) == 2, counts)
check("Customer 3 has 1 order", counts.get(3) == 1, counts)

print("\n--- 5. the customer with NO orders survives, with total 0 ---")
s = session()
A.execute_tool(s, "aggregate_rows",
               {"group_by": [C_ID], "dedupe_on": [O_ID], "aggregations": SUM_SPEND})
t = totals(s.df)
check("all 3 customers present", len(s.df) == 3, len(s.df))
check("Customer 2 retained", 2 in t, sorted(t))
check("Customer 2 total is 0, not NaN", t.get(2) == 0.0, t.get(2))
check("its NULL key did not swallow another customer",
      t.get(1) == 71200.0 and t.get(3) == 9000.0, t)

print("\n--- 6. mean stays NaN for the orderless customer (not a false 0) ---")
s = session()
A.execute_tool(s, "aggregate_rows",
               {"group_by": [C_ID], "dedupe_on": [O_ID],
                "aggregations": [{"column": O_AMT, "function": "mean",
                                  "new_name": "avg_order"}]})
row2 = s.df.loc[s.df[C_ID] == 2, "avg_order"].iloc[0]
check("average of no orders is NaN, not 0", pd.isna(row2), row2)

print("\n--- 7. drop_duplicates(CustomerID) is refused on a fanned frame ---")
s = session()
r = A.execute_tool(s, "drop_duplicates", {"columns": [C_ID]})
check("refused", r["ok"] is False, r)
check("explains it deletes real rows", "delete real rows" in r["error"], r["error"])
check("points at aggregate_rows + dedupe_on",
      "aggregate_rows" in r["error"] and "dedupe_on" in r["error"], r["error"])
check("frame untouched", len(s.df) == 7, len(s.df))

print("\n--- 8. the allowed dedupe forms still work ---")
s = session()
r = A.execute_tool(s, "drop_duplicates", {})
check("full-row dedupe allowed (no columns)", r["ok"], r.get("error"))
s = session()
r = A.execute_tool(s, "drop_duplicates", {"columns": ["*"]})
check("full-row dedupe allowed (['*'])", r["ok"], r.get("error"))
s = session()
r = A.execute_tool(s, "drop_duplicates", {"columns": [O_ID]})
check("natural-key dedupe allowed", r["ok"] and len(s.df) == 4, len(s.df))
s = session(fanned=False)
r = A.execute_tool(s, "drop_duplicates", {"columns": [C_ID]})
check("no fan-out recorded -> not refused", r["ok"], r.get("error"))

print("\n--- 9. a missing / bad dedupe key fails clearly ---")
s = session()
r = A.execute_tool(s, "aggregate_rows",
                   {"group_by": [C_ID], "dedupe_on": ["not_a_column"],
                    "aggregations": SUM_SPEND})
check("unknown dedupe column rejected", r["ok"] is False and "not in the dataset" in r["error"], r)
s = session()
r = A.execute_tool(s, "aggregate_rows",
                   {"group_by": [C_ID], "dedupe_on": [], "aggregations": SUM_SPEND})
check("empty dedupe_on rejected", r["ok"] is False and "non-empty" in r["error"], r)
s = session()
r = A.execute_tool(s, "aggregate_rows",
                   {"group_by": [C_ID], "dedupe_on": O_ID, "aggregations": SUM_SPEND})
check("non-list dedupe_on rejected", r["ok"] is False, r)

print("\n--- 10. min/max/nunique need no dedupe; count does ---")
s = session()
r = A.execute_tool(s, "aggregate_rows",
                   {"group_by": [C_ID],
                    "aggregations": [{"column": O_AMT, "function": "max", "new_name": "biggest"}]})
check("max allowed without dedupe", r["ok"], r.get("error"))
check("max is correct", s.df.loc[s.df[C_ID] == 1, "biggest"].iloc[0] == 56200.0)
s = session()
r = A.execute_tool(s, "aggregate_rows",
                   {"group_by": [C_ID],
                    "aggregations": [{"column": O_AMT, "function": "count", "new_name": "n"}]})
check("count refused without dedupe (it would count item rows)", r["ok"] is False, r)

print("\n--- 11. composite and missing natural keys fail clearly, not wrongly ---")
s = session(pk_orders=("OrderID", "LineNo"))
r = A.execute_tool(s, "aggregate_rows", {"group_by": [C_ID], "aggregations": SUM_SPEND})
check("composite key refused", r["ok"] is False and "composite" in r["error"], r["error"])
s = session()
s.grain["natural_key_aliases"].pop(ORD)
r = A.execute_tool(s, "aggregate_rows", {"group_by": [C_ID], "aggregations": SUM_SPEND})
check("no usable key refused", r["ok"] is False and "cannot be de-fanned" in r["error"], r["error"])

print("\n--- 12. measures from two repeated tables are refused, not guessed ---")
s = session()
r = A.execute_tool(s, "aggregate_rows",
                   {"group_by": [C_ID],
                    "aggregations": SUM_SPEND + [{"column": C_NAME, "function": "count",
                                                  "new_name": "n"}]})
check("mixed-grain aggregation refused", r["ok"] is False and "more than one" in r["error"],
      r["error"])

print("\n--- 13. a measure from the DEEPEST table needs no dedupe ---")
s = session()
r = A.execute_tool(s, "aggregate_rows",
                   {"group_by": [C_ID],
                    "aggregations": [{"column": I_QTY, "function": "sum",
                                      "new_name": "total_items"}]})
check("OrderItems measure allowed without dedupe", r["ok"], r.get("error"))
qty = {int(x[C_ID]): x["total_items"] for _, x in s.df.iterrows()}
check("Customer 1 item qty = 2+1+4+1 = 8", qty.get(1) == 8.0, qty)

print("\n--- 14. grain metadata is not stale after aggregating ---")
s = session()
A.execute_tool(s, "aggregate_rows",
               {"group_by": [C_ID], "dedupe_on": [O_ID], "aggregations": SUM_SPEND})
check("fan-out cleared once collapsed", s.grain["multiplied_tables"] == [], s.grain["multiplied_tables"])
check("aggregated_to recorded", s.grain["aggregated_to"] == [C_ID], s.grain["aggregated_to"])
r = A.execute_tool(s, "aggregate_rows",
                   {"group_by": [C_ID],
                    "aggregations": [{"column": "total_spend", "function": "sum",
                                      "new_name": "again"}]})
check("a second aggregation is not refused on stale evidence", r["ok"], r.get("error"))

print("\n--- 15. internal grain keys stay out of the final output ---")
s = session()
A.execute_tool(s, "aggregate_rows",
               {"group_by": [C_ID], "dedupe_on": [O_ID], "aggregations": SUM_SPEND})
check("aggregation already removed them", O_ID not in s.df.columns, list(s.df.columns))

captured = {}
class FakeDB: pass
def fake_create(db, df, project_name, target_column, source_label):
    captured["columns"] = list(df.columns)
    return {"project_id": "p1", "project_name": project_name, "file_id": "f.csv",
            "target_column": target_column, "rows": len(df), "columns": len(df.columns),
            "source": source_label, "dataset_profile_summary": ""}
import routers.datamodel as dm
original = dm._create_project_from_frame
dm._create_project_from_frame = fake_create
try:
    s = session(); s.db = FakeDB()
    r = A.execute_tool(s, "finish_etl",
                       {"project_name": "p", "target_column": O_AMT, "summary": "s"})
    check("finish_etl succeeded", r["ok"], r.get("error"))
    check("internal OrderID NOT written to the CSV", O_ID not in captured["columns"],
          captured["columns"])
    check("internal OrderItemID NOT written either", I_ID not in captured["columns"],
          captured["columns"])
    check("the real columns ARE written",
          C_NAME in captured["columns"] and O_AMT in captured["columns"], captured["columns"])

    s = session(); s.db = FakeDB(); s.internal_columns = {I_ID}   # OrderID explicitly selected
    A.execute_tool(s, "finish_etl", {"project_name": "p", "target_column": O_AMT, "summary": "s"})
    check("an explicitly selected key IS kept", O_ID in captured["columns"], captured["columns"])

    s = session(); s.db = FakeDB()
    r = A.execute_tool(s, "finish_etl",
                       {"project_name": "p", "target_column": O_ID, "summary": "s"})
    check("an internal key cannot be the target", r["ok"] is False and "internal" in r["error"],
          r.get("error"))
finally:
    dm._create_project_from_frame = original

print("\n--- 16. grain metadata survives the privacy boundary ---")
s = session()
raw = {
    "ok": True,
    "column_sources": dict(s.grain["column_sources"]),
    "fanned_out_tables": list(s.grain["fanned_out_tables"]),
    "multiplied_tables": list(s.grain["multiplied_tables"]),
    "natural_key_columns": dict(s.grain["natural_key_aliases"]),
    "join_keys_used_internally": [O_ID, I_ID],
    "deduped_rows": 4,
    "summary": "ok",
}
clean = A.sanitize_for_llm(s, raw)
check("column_sources survives", clean.get("column_sources"), clean.get("_withheld"))
check("its keys are real aliases, not placeholders",
      O_AMT in clean["column_sources"], list(clean["column_sources"]))
check("its values keep Table.column", clean["column_sources"][O_AMT] == f"{ORD}.OrderAmount",
      clean["column_sources"].get(O_AMT))
check("multiplied_tables survives", ORD in clean.get("multiplied_tables", []), clean)
check("fanned_out_tables survives", ITEM in clean.get("fanned_out_tables", []), clean)
check("natural_key_columns survives", clean.get("natural_key_columns"), clean)
check("deduped_rows survives as a number", clean.get("deduped_rows") == 4, clean.get("deduped_rows"))
check("nothing withheld", "_withheld" not in clean, clean.get("_withheld"))

print("\n--- 17. the boundary still placeholders a VALUE-derived name ---")
s = session()
s.df = s.df.copy()
s.df["customername_Alpha"] = 1          # what get_dummies would produce
clean = A.sanitize_for_llm(s, {"ok": True, "column_names": list(s.df.columns)})
check("value-derived name placeholdered",
      any("derived_column_" in c for c in clean["column_names"]), clean["column_names"])
check("real names untouched", C_ID in clean["column_names"], clean["column_names"])
check("the value itself never appears", "Alpha" not in json.dumps(clean), clean)

print("\n" + str(ok) + " passed, " + str(fail) + " failed")
sys.exit(1 if fail else 0)
