"""New tests: join keys used internally without becoming output columns.

sql_builder runs FOR REAL here - it is what regressed - while list_columns and
extract_to_dataframe are faked, so no database is needed.
"""
import re, sys, types, pandas as pd
sys.path.insert(0, ".")
_stub = types.ModuleType("main"); _stub.assess_training_readiness = lambda df: {"ready": True}
sys.modules.setdefault("main", _stub)

import services.etl_agent as A

ok = fail = 0
def check(label, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("  PASS  " + label)
    else: fail += 1; print("  FAIL  " + label + " :: " + str(extra))

SCHEMA = {
    "MLTest_Customers": ["CustomerID", "CustomerName", "City"],
    "MLTest_Orders":    ["OrderID", "CustomerID", "OrderAmount", "OrderDate"],
}
CAPTURED = {}

def fake_list_columns(conn, tables, schema=None):
    return {t: [{"name": c, "type": "INTEGER", "nullable": True, "primary_key": False}
                for c in SCHEMA[t]] for t in tables if t in SCHEMA}

def fake_extract(conn, sql, **kw):
    CAPTURED["sql"] = sql
    if re.search(r"COUNT\(\*\)\s+AS", sql, re.I):
        return pd.DataFrame({"row_count": [5]})
    aliases = re.findall(r"\sAS\s[\[\"]([^\]\"]+)[\]\"]", sql)
    return pd.DataFrame({a: list(range(5)) for a in aliases})

A.list_columns = fake_list_columns
A.extract_to_dataframe = fake_extract

def new_session():
    return A.ETLSession("mssql+pyodbc://x", "mssql", "dbo", db=None,
                        allowed_tables=list(SCHEMA))

REL = [{"left_table": "MLTest_Customers", "left_column": "CustomerID",
        "right_table": "MLTest_Orders", "right_column": "CustomerID",
        "join_type": "inner"}]

print("\n--- the exact call that failed in the live run ---")
s = new_session()
r = A.execute_tool(s, "build_dataset", {
    "base_table": "MLTest_Customers",
    "tables": ["MLTest_Customers", "MLTest_Orders"],
    "relationships": REL,
    "selected_columns": {"MLTest_Customers": ["CustomerID", "CustomerName", "City"],
                         "MLTest_Orders": ["OrderAmount"]},
})
check("build_dataset now succeeds", r["ok"], r.get("error"))
check("join key reached the SQL", "CustomerID" in CAPTURED["sql"])
cols = list(s.df.columns)
check("selected columns present, plus the internal join key",
      len(cols) == 5, cols)
low = [c.lower() for c in cols]
check("Orders.CustomerID is KEPT internally, not projected as a deliverable",
      any("mltest_orders" in c and c.endswith("customerid") for c in low)
      and len(r["join_keys_used_internally"]) == 1, r["join_keys_used_internally"])
check("Customers.CustomerID IS kept - it was selected",
      any(c.endswith("customerid") for c in low), cols)
check("OrderAmount is present", any("orderamount" in c for c in low), cols)
check("dropped key reported back to the agent",
      len(r["join_keys_used_internally"]) == 1, r["join_keys_used_internally"])
check("summary explains why it is kept and that it is not saved",
      "de-fan" in r["summary"] and "removed from the saved file" in r["summary"],
      r["summary"])
check("column_names matches the real frame", r["column_names"] == cols, r["column_names"])
check("alias_map has no entry for the dropped key",
      len(s.build_info["alias_map"]) == len(cols), s.build_info["alias_map"])

print("\n--- a key that IS selected stays in the output ---")
s = new_session()
r = A.execute_tool(s, "build_dataset", {
    "base_table": "MLTest_Customers",
    "tables": ["MLTest_Customers", "MLTest_Orders"],
    "relationships": REL,
    "selected_columns": {"MLTest_Customers": ["CustomerID"],
                         "MLTest_Orders": ["CustomerID", "OrderAmount"]},
})
check("both keys kept when both selected", r["ok"] and len(s.df.columns) == 3, list(s.df.columns))
check("nothing reported as dropped", r["join_keys_used_internally"] == [], r["join_keys_used_internally"])

print("\n--- no selected_columns at all: unchanged behaviour ---")
s = new_session()
r = A.execute_tool(s, "build_dataset", {
    "base_table": "MLTest_Customers",
    "tables": ["MLTest_Customers", "MLTest_Orders"],
    "relationships": REL,
})
check("every column projected", r["ok"] and len(s.df.columns) == 7, list(s.df.columns))
check("nothing dropped", r["join_keys_used_internally"] == [], r["join_keys_used_internally"])

print("\n--- a genuinely wrong join column still fails, and says what exists ---")
s = new_session()
r = A.execute_tool(s, "build_dataset", {
    "base_table": "MLTest_Customers",
    "tables": ["MLTest_Customers", "MLTest_Orders"],
    "relationships": [{"left_table": "MLTest_Customers", "left_column": "CustomerID",
                       "right_table": "MLTest_Orders", "right_column": "CustId",
                       "join_type": "inner"}],
    "selected_columns": {"MLTest_Orders": ["OrderAmount"]},
})
check("misspelled key rejected", r["ok"] is False, r)
check("error lists the real columns", "OrderDate" in r["error"] and "CustomerID" in r["error"], r["error"])
check("error does not claim the projection is the table",
      "does not exist in 'MLTest_Orders'" in r["error"] and "Its columns are" in r["error"], r["error"])

print("\n--- selecting nothing but join keys is refused ---")
s = new_session()
r = A.execute_tool(s, "build_dataset", {
    "base_table": "MLTest_Customers",
    "tables": ["MLTest_Customers", "MLTest_Orders"],
    "relationships": REL,
    "selected_columns": {"MLTest_Customers": [], "MLTest_Orders": []},
})
# A keys-only projection is now legitimate DURING processing - the keys carry
# the grain. finish_etl is what refuses to save a file made only of them.
check("keys-only build allowed, both keys marked internal",
      r["ok"] and len(r["join_keys_used_internally"]) == 2,
      r.get("join_keys_used_internally"))

print("\n" + str(ok) + " passed, " + str(fail) + " failed")
sys.exit(1 if fail else 0)
