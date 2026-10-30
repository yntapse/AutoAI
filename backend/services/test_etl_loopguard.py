"""Redundant-loop guard regression suite.

The live model burned every remaining step on:
    validate_dataset -> no issues
    drop_rows_with_nulls -> no change
    validate_dataset -> no issues
    ... to MAX_STEPS

The guard declines to run a cleanup or validation call that cannot change
anything, and says to call finish_etl. It never calls finish_etl itself.
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

CLEAN_COLS = ["customerid", "total_spend", "order_count"]

def clean_session():
    """A dataset that validates cleanly: no nulls, no duplicates, numeric target."""
    s = A.ETLSession("mssql+pyodbc://u:p@h/db", "mssql", "dbo", db=None)
    s.df = pd.DataFrame({
        "customerid": range(1, 13),
        "total_spend": [float(100 * i) for i in range(1, 13)],
        "order_count": list(range(1, 13)),
    })
    s.register_names(*CLEAN_COLS)
    return s

def validate(s, target="total_spend"):
    return A.execute_tool(s, "validate_dataset", {"target_column": target})

print("\n--- 1. a clean validation sets the flag ---")
s = clean_session()
r = validate(s)
check("validation reports no issues", r["ok"] and r["problems"] == [], r.get("problems"))
check("validated_clean is true in the result", r["validated_clean"] is True, r.get("validated_clean"))
check("session flag set", bool(s.clean_validation), s.clean_validation)

print("\n--- 2. THE OBSERVED LOOP: redundant drop_rows_with_nulls is not executed ---")
before_shape = s.df.shape
r = A.execute_tool(s, "drop_rows_with_nulls", {"columns": ["*"], "how": "any"})
check("not executed", r["ok"] is False, r)
check("carries the exact directive",
      "already passed validation with no issues" in r["error"]
      and "Call finish_etl now" in r["error"], r["error"])
check("names the tool it declined", "drop_rows_with_nulls" in r["error"], r["error"])
check("dataset untouched", s.df.shape == before_shape, s.df.shape)
check("flag still set", bool(s.clean_validation))

print("\n--- 3. redundant validate_dataset is also not executed ---")
r = validate(s)
check("not executed", r["ok"] is False, r)
check("same directive", "Call finish_etl now" in r["error"], r["error"])

print("\n--- 4. every cleanup tool in the family is guarded ---")
for tool, args in [
    ("fill_nulls", {"columns": ["*"], "strategy": "mean"}),
    ("drop_duplicates", {}),
    ("remove_outliers", {"columns": ["total_spend"], "method": "iqr"}),
    ("strip_whitespace", {"columns": ["*"]}),
    ("lowercase_columns", {"columns": ["*"]}),
]:
    r = A.execute_tool(s, tool, args)
    check(f"{tool} guarded", r["ok"] is False and "finish_etl" in r["error"], r)

print("\n--- 5. finish_etl is NEVER guarded ---")
captured = {}
class FakeDB: pass
def fake_create(db, df, project_name, target_column, source_label):
    captured["rows"] = len(df)
    return {"project_id": "p1", "project_name": project_name, "file_id": "f.csv",
            "target_column": target_column, "rows": len(df), "columns": len(df.columns),
            "source": source_label, "dataset_profile_summary": ""}
import routers.datamodel as dm
original = dm._create_project_from_frame
dm._create_project_from_frame = fake_create
try:
    s.db = FakeDB()
    r = A.execute_tool(s, "finish_etl",
                       {"project_name": "p", "target_column": "total_spend", "summary": "s"})
    check("finish_etl executed while the flag is set", r["ok"], r.get("error"))
    check("it really saved", captured.get("rows") == 12, captured)
    check("file_id returned", r.get("file_id") == "f.csv", r.get("file_id"))
finally:
    dm._create_project_from_frame = original

print("\n--- 6. a REAL transformation clears the flag and reopens processing ---")
s = clean_session()
validate(s)
check("flag set before", bool(s.clean_validation))
r = A.execute_tool(s, "select_columns", {"columns": ["customerid", "total_spend"]})
check("reshaping tool is NOT guarded", r["ok"], r.get("error"))
check("flag cleared by the change", s.clean_validation is None, s.clean_validation)
r = A.execute_tool(s, "drop_rows_with_nulls", {"columns": ["*"], "how": "any"})
check("cleanup allowed again after a real change", r["ok"], r.get("error"))
r = validate(s)
check("validation runs normally again", r["ok"] and "problems" in r, r)
check("and re-sets the flag", bool(s.clean_validation))

print("\n--- 7. a value-only change (identical shape) still clears the flag ---")
# This is why the fingerprint hashes CONTENT: normalize_columns rewrites every
# value but leaves the shape and the column names exactly as they were, so a
# shape-only check would wrongly keep treating the dataset as validated.
s = clean_session()
validate(s)
fingerprint = s.clean_validation
shape_before, cols_before = s.df.shape, list(s.df.columns)
r = A.execute_tool(s, "normalize_columns", {"columns": ["total_spend"], "method": "minmax"})
check("normalize_columns is not guarded", r["ok"], r.get("error"))
check("shape and columns are unchanged",
      s.df.shape == shape_before and list(s.df.columns) == cols_before, s.df.shape)
check("but the flag was cleared on content", s.clean_validation is None, s.clean_validation)
check("so a cleanup call is allowed again",
      A.execute_tool(s, "drop_duplicates", {})["ok"])
r = validate(s)
check("re-validation runs and re-sets the flag", r["ok"] and bool(s.clean_validation), r.get("problems"))
check("the new fingerprint differs from the old", s.clean_validation != fingerprint)

print("\n--- 8. validation that FINDS problems does not set the flag ---")
s = A.ETLSession("mssql+pyodbc://u:p@h/db", "mssql", "dbo", db=None)
s.df = pd.DataFrame({"cid": [1, 1, 2] * 4, "tgt": [1.0, 1.0, 2.0] * 4})
s.register_names("cid", "tgt")
r = validate(s, "tgt")
check("duplicates reported", r["ok"] and r["problems"], r.get("problems"))
check("flag NOT set", s.clean_validation is None, s.clean_validation)
check("validated_clean false", r["validated_clean"] is False, r.get("validated_clean"))
r = A.execute_tool(s, "drop_duplicates", {})
check("cleanup allowed when problems exist", r["ok"], r.get("error"))

print("\n--- 9. a non-numeric target never counts as clean ---")
s = A.ETLSession("mssql+pyodbc://u:p@h/db", "mssql", "dbo", db=None)
s.df = pd.DataFrame({"cid": range(1, 13), "label": ["a"] * 12})
s.register_names("cid", "label")
r = validate(s, "label")
check("non-numeric target keeps the flag unset", s.clean_validation is None, r.get("problems"))

print("\n--- 10. validate with NO target does not set the flag ---")
s = clean_session()
r = A.execute_tool(s, "validate_dataset", {})
check("no target -> not marked clean", r["ok"] and s.clean_validation is None, r.get("validated_clean"))
check("so a following cleanup still runs",
      A.execute_tool(s, "drop_duplicates", {})["ok"])

print("\n--- 11. the guard survives the privacy boundary intact ---")
s = clean_session()
validate(s)
r = A.execute_tool(s, "drop_rows_with_nulls", {"columns": ["*"], "how": "any"})
clean = A.sanitize_for_llm(s, r)
check("the directive reaches the model",
      "Call finish_etl now" in clean.get("error", ""), clean.get("error"))
check("validated_clean survives for validate results",
      A.sanitize_for_llm(s, {"ok": True, "validated_clean": True}).get("validated_clean") is True)

print("\n--- 12. end to end through the real loop: the loop is broken ---")
class Fn:
    def __init__(s2, n, a): s2.name, s2.arguments = n, json.dumps(a)
class Call:
    def __init__(s2, i, n, a): s2.id, s2.type, s2.function = i, "function", Fn(n, a)
class Msg:
    def __init__(s2, c=None, t=""): s2.tool_calls, s2.content = c, t
class FakeClient:
    def __init__(self, script): self.script, self.i = script, 0
    @property
    def chat(self): return self
    @property
    def completions(self): return self
    def create(self, **kw):
        turn = self.script[min(self.i, len(self.script) - 1)]; self.i += 1
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=turn)])

# The exact observed pathology, alternating so the retry guard cannot mask it.
script = [
    Msg([Call("v1", "validate_dataset", {"target_column": "total_spend"})]),
    Msg([Call("d1", "drop_rows_with_nulls", {"columns": ["*"], "how": "any"})]),
    Msg([Call("v2", "validate_dataset", {"target_column": "total_spend"})]),
    Msg([Call("d2", "drop_rows_with_nulls", {"columns": ["customerid"], "how": "all"})]),
    Msg([Call("v3", "validate_dataset", {"target_column": "order_count"})]),
    Msg([Call("d3", "fill_nulls", {"columns": ["*"], "strategy": "mean"})]),
    Msg(None, "I will stop here."),
]
s = clean_session()
original = A.resolve_llm_client
A.resolve_llm_client = lambda: (FakeClient(script), "m", "openai")
try:
    payload = A.run_etl_agent(s, "prepare the dataset", max_steps=7)
finally:
    A.resolve_llm_client = original

executed = [t for t in payload["trace"] if t["ok"]]
blocked = [t for t in payload["trace"] if not t["ok"] and "finish_etl now" in t["summary"]]
print(f"        trace: {[(t['tool'], 'ok' if t['ok'] else 'blocked') for t in payload['trace']]}")
check("only the FIRST validation executed", len(executed) == 1, executed)
check("every later cleanup/validation was blocked", len(blocked) == 5, len(blocked))
check("no dataset mutation happened", len(s.df) == 12, len(s.df))
check("finish_etl was NOT called on the model's behalf", payload["file_id"] is None)
check("the run did not silently succeed", payload["status"] in {"incomplete", "partial"},
      payload["status"])

print("\n" + str(ok) + " passed, " + str(fail) + " failed")
sys.exit(1 if fail else 0)
