"""The live 5-row loop: a dataset below MIN_TRAINING_ROWS must still finish.

Observed against real SQL Server:
    validate_dataset     -> "5 rows x 2 columns; no issues found"
    drop_rows_with_nulls -> "5 rows x 2 cols -> 5 rows x 2 cols"
    validate_dataset     -> clean again
    ... 10/10 steps, finish_etl never called.

Root cause: MIN_TRAINING_ROWS is 10, so assess_training_readiness reported
ready=False, the clean-validation flag was gated on it and never set, and the
guard never engaged - while the model was told "no issues found". A row
shortage is not something any cleanup tool can fix.

This suite uses the REAL readiness semantics (reject under 10 rows).
"""
import sys, types, json, pandas as pd
sys.path.insert(0, ".")
MIN_TRAINING_ROWS = 10
_stub = types.ModuleType("main")
def assess_training_readiness(df):
    n = len(df)
    if n < MIN_TRAINING_ROWS:
        return {"ready": False,
                "reason": f"Only {n} rows; at least {MIN_TRAINING_ROWS} are needed to train.",
                "n_rows": n, "n_columns": int(df.shape[1]), "n_folds": 0, "warnings": []}
    return {"ready": True, "reason": None, "n_rows": n,
            "n_columns": int(df.shape[1]), "n_folds": 5, "warnings": []}
_stub.assess_training_readiness = assess_training_readiness
sys.modules["main"] = _stub
import services.etl_agent as A

ok = fail = 0
def check(label, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("  PASS  " + label)
    else: fail += 1; print("  FAIL  " + label + " :: " + str(extra))

def small_session(rows=5):
    """Exactly the live shape: 5 rows x 2 columns, clean, numeric target."""
    s = A.ETLSession("mssql+pyodbc://u:p@h/db", "mssql", "dbo", db=None)
    s.df = pd.DataFrame({"customerid": list(range(1, rows + 1)),
                         "total_spend": [float(100 * i) for i in range(1, rows + 1)]})
    s.register_names("customerid", "total_spend")
    return s

def validate(s, target="total_spend"):
    return A.execute_tool(s, "validate_dataset", {"target_column": target})

print("\n--- 1. a 5-row dataset is marked clean DESPITE failing readiness ---")
s = small_session()
r = validate(s)
check("validation succeeded", r["ok"], r.get("error"))
check("readiness still reports not-ready", r["training_readiness"]["ready"] is False)
check("the row shortage IS reported to the model now",
      any("at least 10" in p for p in r["problems"]), r["problems"])
check("no longer claims 'no issues found'", "no issues found" not in r["summary"], r["summary"])
check("nothing is FIXABLE by cleanup", r["fixable_problems"] == [], r["fixable_problems"])
check("so the dataset counts as cleanly validated", r["validated_clean"] is True,
      r["validated_clean"])
check("session flag set", bool(s.clean_validation), s.clean_validation)

print("\n--- 2. THE LIVE LOOP: the no-op cleanup cannot even be offered ---")
names = [t["function"]["name"] for t in A.get_tools_schema(s)]
check("drop_rows_with_nulls withheld from the schema", "drop_rows_with_nulls" not in names)
check("validate_dataset withheld from the schema", "validate_dataset" not in names)
check("every cleanup tool withheld",
      not any(n in names for n in A.CLEANUP_TOOLS), [n for n in names if n in A.CLEANUP_TOOLS])
check("finish_etl IS offered", "finish_etl" in names, names)
check("reshaping tools still offered",
      "select_columns" in names and "aggregate_rows" in names, names)

print("\n--- 3. and if the model sends one anyway, it is refused ---")
before = s.df.shape
r = A.execute_tool(s, "drop_rows_with_nulls", {"columns": ["*"], "how": "any"})
check("refused at execution too (defence in depth)", r["ok"] is False, r)
check("carries the directive", "Call finish_etl now" in r["error"], r["error"])
check("dataset untouched", s.df.shape == before, s.df.shape)

print("\n--- 4. a NO-OP cleanup must NOT clear the flag ---")
# Prove it at the fingerprint level: 5x2 -> 5x2 with identical content.
s = small_session()
validate(s)
flag = s.clean_validation
fp_before = s.dataset_fingerprint()
import services.transform_mcp_server as T
s.df = T.execute_tool("drop_rows_with_nulls", s.df, {"columns": ["*"], "how": "any"})
check("the no-op really produced the same shape", s.df.shape == (5, 2), s.df.shape)
check("fingerprint identical after a no-op", s.dataset_fingerprint() == fp_before)
check("flag survives a no-op", s.clean_validation == flag, s.clean_validation)
check("still considered clean", A._validation_still_clean(s))

print("\n--- 5. a REAL change still clears it ---")
s = small_session()
validate(s)
r = A.execute_tool(s, "select_columns", {"columns": ["total_spend"]})
check("reshaping allowed", r["ok"], r.get("error"))
check("flag cleared", s.clean_validation is None, s.clean_validation)
names = [t["function"]["name"] for t in A.get_tools_schema(s)]
check("cleanup tools come back", "drop_rows_with_nulls" in names)
check("validate_dataset comes back", "validate_dataset" in names)
r = A.execute_tool(s, "drop_rows_with_nulls", {"columns": ["*"], "how": "any"})
check("and executes normally", r["ok"], r.get("error"))

print("\n--- 6. finish_etl is reachable and never blocked ---")
captured = {}
class FakeDB: pass
def fake_create(db, df, project_name, target_column, source_label):
    captured["rows"], captured["cols"] = len(df), list(df.columns)
    return {"project_id": "p1", "project_name": project_name, "file_id": "f.csv",
            "target_column": target_column, "rows": len(df), "columns": len(df.columns),
            "source": source_label, "dataset_profile_summary": ""}
import routers.datamodel as dm
original = dm._create_project_from_frame
dm._create_project_from_frame = fake_create
try:
    s = small_session(); s.db = FakeDB()
    validate(s)
    check("finish_etl offered while clean", "finish_etl" in
          [t["function"]["name"] for t in A.get_tools_schema(s)])
    r = A.execute_tool(s, "finish_etl",
                       {"project_name": "p", "target_column": "total_spend", "summary": "s"})
    check("finish_etl executed", r["ok"], r.get("error"))
    check("the 5-row dataset really saved", captured.get("rows") == 5, captured)
    check("file_id returned", r.get("file_id") == "f.csv", r.get("file_id"))
finally:
    dm._create_project_from_frame = original

print("\n--- 7. a FIXABLE problem still keeps the guard off ---")
s = A.ETLSession("mssql+pyodbc://u:p@h/db", "mssql", "dbo", db=None)
s.df = pd.DataFrame({"cid": [1, 1, 2, 3, 4], "tgt": [1.0, 1.0, 2.0, 3.0, 4.0]})
s.register_names("cid", "tgt")
r = validate(s, "tgt")
check("duplicates are reported as fixable",
      any("duplicate row" in p for p in r["fixable_problems"]), r["fixable_problems"])
check("flag NOT set while something is fixable", s.clean_validation is None)
names = [t["function"]["name"] for t in A.get_tools_schema(s)]
check("cleanup tools still offered", "drop_duplicates" in names)
r = A.execute_tool(s, "drop_duplicates", {})
check("and drop_duplicates runs", r["ok"], r.get("error"))
r = validate(s, "tgt")
check("now clean, guard engages", bool(s.clean_validation), r["problems"])

print("\n--- 8. a null target keeps the guard off (not fixable-clean) ---")
s = A.ETLSession("mssql+pyodbc://u:p@h/db", "mssql", "dbo", db=None)
s.df = pd.DataFrame({"cid": [1, 2, 3, 4, 5], "tgt": [1.0, None, 3.0, 4.0, 5.0]})
s.register_names("cid", "tgt")
r = validate(s, "tgt")
check("null target reported", any("null value" in p for p in r["problems"]), r["problems"])
check("flag not set", s.clean_validation is None)

print("\n--- 9. END TO END: the exact live trace now reaches finish_etl ---")
class Fn:
    def __init__(s2, n, a): s2.name, s2.arguments = n, json.dumps(a)
class Call:
    def __init__(s2, i, n, a): s2.id, s2.type, s2.function = i, "function", Fn(n, a)
class Msg:
    def __init__(s2, c=None, t=""): s2.tool_calls, s2.content = c, t
OFFERED = []
class FakeClient:
    """Calls whatever the schema still offers - a stand-in for a model that
    cannot invent a tool it was never given."""
    def __init__(self): self.step = 0
    @property
    def chat(self): return self
    @property
    def completions(self): return self
    def create(self, **kw):
        names = [t["function"]["name"] for t in kw["tools"]]
        OFFERED.append(names)
        self.step += 1
        if self.step == 1:
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=Msg([Call("v1", "validate_dataset", {"target_column": "total_spend"})]))])
        # The pathological preference: cleanup, if it is still on the menu.
        if "drop_rows_with_nulls" in names:
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=Msg([Call(f"d{self.step}", "drop_rows_with_nulls",
                                  {"columns": ["*"], "how": "any"})]))])
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=Msg([Call("f1", "finish_etl",
                              {"project_name": "p", "target_column": "total_spend",
                               "summary": "done"})]))])

dm._create_project_from_frame = fake_create
try:
    s = small_session(); s.db = FakeDB()
    original_resolve = A.resolve_llm_client
    A.resolve_llm_client = lambda: (FakeClient(), "m", "openai")
    try:
        payload = A.run_etl_agent(s, "prepare the dataset", max_steps=10)
    finally:
        A.resolve_llm_client = original_resolve
finally:
    dm._create_project_from_frame = original

print(f"        trace: {[(t['tool'], 'ok' if t['ok'] else 'blocked') for t in payload['trace']]}")
print(f"        steps: {payload['steps_used']}/{payload['max_steps']}  status: {payload['status']}")
check("run COMPLETED", payload["status"] == "complete", payload["status"])
check("finish_etl was reached", payload["file_id"] == "f.csv", payload["file_id"])
check("it took 2 steps, not 10", payload["steps_used"] == 2, payload["steps_used"])
check("no cleanup tool was ever executed",
      not any(t["tool"] in A.CLEANUP_TOOLS for t in payload["trace"]), payload["trace"])
check("cleanup was offered at step 1 but withheld at step 2",
      "drop_rows_with_nulls" in OFFERED[0] and "drop_rows_with_nulls" not in OFFERED[1],
      [len(o) for o in OFFERED])

print("\n" + str(ok) + " passed, " + str(fail) + " failed")
sys.exit(1 if fail else 0)
