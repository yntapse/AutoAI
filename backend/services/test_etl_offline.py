import sys, json, types, pandas as pd
sys.path.insert(0, ".")

# main.py connects to Postgres at import time (Base.metadata.create_all), which
# is unreachable from this sandbox. validate_dataset imports one function from
# it (assess_training_readiness) at call time; stub that ONE function so the
# rest of the tool layer can be exercised offline.
_stub = types.ModuleType("main")
def assess_training_readiness(df):
    n = len(df)
    return {"ready": n >= 10, "reason": None if n >= 10 else "Too few rows to train.",
            "n_rows": n, "n_columns": int(df.shape[1]),
            "n_folds": 5 if n >= 30 else (max(2, min(5, n // 2)) if n >= 10 else 0),
            "warnings": []}
_stub.assess_training_readiness = assess_training_readiness
sys.modules.setdefault("main", _stub)

from services.etl_agent import ETLSession, execute_tool, get_tools_schema

ok = fail = 0
def check(label, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("  PASS  " + label)
    else: fail += 1; print("  FAIL  " + label + " :: " + str(extra))

s = ETLSession("mssql+pyodbc://x", "mssql", "dbo", db=None,
               allowed_tables=["MLTest_Customers", "MLTest_Orders"])

print("\n--- failure cases (no DB contact) ---")
r = execute_tool(s, "definitely_not_a_tool", {})
check("invalid tool name -> controlled error", r["ok"] is False and "not a registered tool" in r["error"], r)
r = execute_tool(s, "inspect_table", {"table_name": "SomeOtherTable"})
check("unknown table -> blocked by allow-list", r["ok"] is False and "not in the table list" in r["error"], r)
r = execute_tool(s, "inspect_columns", {"wrong_arg": 1})
check("malformed arguments -> controlled error", r["ok"] is False and "Wrong arguments" in r["error"], r)
r = execute_tool(s, "inspect_columns", {"table_names": []})
check("empty table_names -> controlled error", r["ok"] is False, r)
r = execute_tool(s, "fill_nulls", {"columns": ["*"], "strategy": "mean"})
check("transform before build -> guarded", r["ok"] is False and "build_dataset first" in r["error"], r)
r = execute_tool(s, "validate_dataset", {})
check("validate before build -> guarded", r["ok"] is False, r)
r = execute_tool(s, "analyze_relationship", {"base_table": "MLTest_Customers", "base_column": "A",
                                             "related_table": "MLTest_Customers", "related_column": "A"})
check("self-join rejected by join_profiler", r["ok"] is False, r)
r = execute_tool(s, "analyze_relationship", {"base_table": "MLTest_Customers", "base_column": "A; DROP TABLE x",
                                             "related_table": "MLTest_Orders", "related_column": "B"})
check("unsafe identifier rejected before any query", r["ok"] is False, r)
r = execute_tool(s, "build_dataset", {"base_table": "MLTest_Customers",
                                      "tables": ["MLTest_Customers", "Nope"], "relationships": []})
check("build with unlisted table -> blocked", r["ok"] is False and "not in the table list" in r["error"], r)
r = execute_tool(s, "finish_etl", {"project_name": "x", "target_column": "y", "summary": "z"})
check("finish before build -> guarded", r["ok"] is False and "build_dataset first" in r["error"], r)

print("\n--- schema gating ---")
check("no transform tools before a dataset exists",
      all(t["function"]["name"] not in ("fill_nulls", "drop_duplicates") for t in get_tools_schema(s)))
check("6 discovery/build tools offered initially", len(get_tools_schema(s)) == 6, len(get_tools_schema(s)))

print("\n--- dataset present: transforms, aggregation, validation ---")
s.df = pd.DataFrame({
    "C_CustomerID": [1, 2, 3, 3, 4, 5, 6, 7, 8, 9, 10, 11],
    "C_Name": list("abccdefghijk"),
    "O_Amount": [10.0, 20.0, 30.0, 30.0, None, 5.0, 6.0, 7.0, 8.0, 9.0, 11.0, 12.0],
    "O_Qty": [1, 2, 3, 3, 4, 1, 1, 1, 2, 2, 2, 3],
    "C_Churn": [0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 1, 0],
    "Constant": [7] * 12,
})
names = [t["function"]["name"] for t in get_tools_schema(s)]
check("transform tools appear once a dataset exists", "fill_nulls" in names and "drop_duplicates" in names)
check("24 tools offered with a dataset", len(names) == 24, len(names))
r = execute_tool(s, "select_columns", {"columns": ["C_CustomerID", "Nope"]})
check("unknown column -> controlled error", r["ok"] is False and "not in the dataset" in r["error"], r)
r = execute_tool(s, "drop_duplicates", {})
check("reused transform: drop_duplicates", r["ok"] and r["dataset"]["rows"] == 11, r.get("summary"))
r = execute_tool(s, "fill_nulls", {"columns": ["O_Amount"], "strategy": "mean"})
check("reused transform: fill_nulls", r["ok"] and s.df["O_Amount"].isnull().sum() == 0, r.get("summary"))
r = execute_tool(s, "drop_columns", {"columns": ["C_Name", "Constant"]})
check("reused transform: drop_columns", r["ok"] and "C_Name" not in s.df.columns, r.get("summary"))
r = execute_tool(s, "add_calculated_column", {"new_column": "UnitPrice", "left": "O_Amount",
                                              "operation": "divide", "right": "O_Qty"})
check("calculated column created", r["ok"] and "UnitPrice" in s.df.columns, r)
r = execute_tool(s, "add_calculated_column", {"new_column": "Dup", "left": "NotThere",
                                              "operation": "add", "right": "1"})
check("unknown operand rejected", r["ok"] is False and "neither a column" in r["error"], r)
r = execute_tool(s, "aggregate_rows", {"group_by": ["C_CustomerID", "C_Churn"],
                                       "aggregations": [{"column": "O_Amount", "function": "sum", "new_name": "TotalSpend"},
                                                        {"column": "O_Qty", "function": "count", "new_name": "OrderCount"}]})
check("aggregation collapses to base grain", r["ok"] and r["dataset"]["rows"] == 11
      and set(s.df.columns) == {"C_CustomerID", "C_Churn", "TotalSpend", "OrderCount"}, r)
r = execute_tool(s, "aggregate_rows", {"group_by": ["C_CustomerID"],
                                       "aggregations": [{"column": "TotalSpend", "function": "bogus"}]})
check("unsupported aggregation function rejected", r["ok"] is False, r)
r = execute_tool(s, "validate_dataset", {"target_column": "C_Churn"})
check("validate reports shape", r["ok"] and r["dataset"]["rows"] == 11 and r["dataset"]["columns"] == 4, r.get("summary"))
check("validate reports numeric target", r["target"]["numeric"] is True, r.get("target"))
check("validate reports training readiness", r["training_readiness"]["ready"] is True, r.get("training_readiness"))
# The previous validation was clean, so the redundant-loop guard would now
# (correctly) decline a second one. Clear the flag to test validation itself.
s.clean_validation = None
r = execute_tool(s, "validate_dataset", {"target_column": "NotAColumn"})
check("unknown target reported as a problem, report still returned",
      r["ok"] and any("not in the dataset" in p for p in r["problems"]), r.get("problems"))
check("all summary numbers are JSON-native (no numpy scalars)",
      all(type(c["null_percentage"]) is float for c in r["dataset"]["column_details"]),
      [type(c["null_percentage"]) for c in r["dataset"]["column_details"]])

print("\n--- non-numeric target is caught before finish_etl ---")
s.df["C_Segment"] = ["x"] * 11
r = execute_tool(s, "validate_dataset", {"target_column": "C_Segment"})
check("non-numeric target flagged as a problem",
      any("not numeric" in p for p in r["problems"]), r.get("problems"))

print("\n--- privacy: no cell values in tool output ---")
s.clean_validation = None
blob = json.dumps(execute_tool(s, "validate_dataset", {"target_column": "C_Churn"}), default=str)
check("column names present, cell values absent",
      "TotalSpend" in blob and '"x"' not in blob and "138" not in blob, blob[:200])

print("\n" + str(ok) + " passed, " + str(fail) + " failed")
sys.exit(1 if fail else 0)
