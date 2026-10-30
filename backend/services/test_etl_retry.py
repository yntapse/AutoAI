"""New tests: the failed-call retry guard."""
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

class Fn:
    def __init__(s, n, a): s.name, s.arguments = n, a if isinstance(a, str) else json.dumps(a)
class Call:
    def __init__(s, i, n, a): s.id, s.type, s.function = i, "function", Fn(n, a)
class Msg:
    def __init__(s, calls=None, content=""): s.tool_calls, s.content = calls, content
class FakeClient:
    def __init__(self, script): self.script, self.calls = list(script), 0
    @property
    def chat(self): return self
    @property
    def completions(self): return self
    def create(self, **kw):
        turn = self.script[min(self.calls, len(self.script) - 1)]; self.calls += 1
        if isinstance(turn, Exception): raise turn
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=turn)])

def with_client(client, fn):
    original = A.resolve_llm_client
    A.resolve_llm_client = lambda: (client, "fake-model", "openai")
    try: return fn()
    finally: A.resolve_llm_client = original

def fresh():
    s = A.ETLSession("mssql+pyodbc://x", "mssql", "dbo", db=None,
                     allowed_tables=["MLTest_Customers", "MLTest_Orders"])
    return s

BAD_BUILD = {"base_table": "MLTest_Customers",
             "tables": ["MLTest_Customers", "NotATable"],
             "relationships": []}

print("\n--- identical failing build_dataset, repeated 8 times ---")
script = [Msg([Call(f"c{i}", "build_dataset", BAD_BUILD)]) for i in range(8)]
r = with_client(FakeClient(script), lambda: A.run_etl_agent(fresh(), "build it", max_steps=8))
check("run stopped early, not at the ceiling", r["steps_used"] == 3, r["steps_used"])
check("status partial", r["status"] == "partial", r["status"])
check("exactly 3 trace entries", len(r["trace"]) == 3, len(r["trace"]))
check("attempt 1 really executed", "not in the table list" in r["trace"][0]["summary"], r["trace"][0])
check("attempt 2 blocked with a DIFFERENT message",
      "already made this exact call" in r["trace"][1]["summary"], r["trace"][1])
check("attempt 2 message differs from attempt 1",
      r["trace"][1]["summary"] != r["trace"][0]["summary"])
check("attempt 3 stops the run", "Blocked" in r["trace"][2]["summary"], r["trace"][2])
check("note names the tool", any("build_dataset" in n for n in r["notes"]), r["notes"])
check("message carries the ORIGINAL error, not the guard's",
      "not in the table list" in r["message"], r["message"])
check("nothing was saved", r["file_id"] is None)

print("\n--- the RETRY guard does NOT fire on successful calls ---")
# select_columns, not validate_dataset: repeating a clean validation is now
# blocked by the separate redundant-loop guard, so it can no longer be used to
# test the retry guard. select_columns is idempotent and unguarded.
s = fresh(); s.df = pd.DataFrame({"A": range(12), "B": range(12)})
good = {"columns": ["A", "B"]}
script = [Msg([Call(f"v{i}", "select_columns", good)]) for i in range(4)]
r = with_client(FakeClient(script), lambda: A.run_etl_agent(s, "check it", max_steps=4))
check("all 4 identical successful calls ran", len(r["trace"]) == 4, len(r["trace"]))
check("all succeeded", all(t["ok"] for t in r["trace"]), r["trace"])
check("ran to the ceiling, not stopped early", r["steps_used"] == 4, r["steps_used"])

print("\n--- the two guards compose: loop guard first, then retry guard ---")
s = fresh(); s.df = pd.DataFrame({"A": [float(i) for i in range(12)], "B": range(12)})
s.register_names("A", "B")
script = [Msg([Call(f"v{i}", "validate_dataset", {"target_column": "A"})]) for i in range(4)]
r = with_client(FakeClient(script), lambda: A.run_etl_agent(s, "check it", max_steps=4))
check("first validation executed", r["trace"][0]["ok"] is True, r["trace"][0])
check("second blocked by the loop guard",
      "Call finish_etl now" in r["trace"][1]["summary"], r["trace"][1]["summary"][:80])
check("a stubborn repeat is then stopped by the retry guard",
      r["status"] == "partial", r["status"])
check("nothing was saved", r["file_id"] is None)

print("\n--- different arguments are a different call ---")
s = fresh(); s.df = pd.DataFrame({"A": range(12)})
script = [Msg([Call("d1", "select_columns", {"columns": ["Nope1"]})]),
          Msg([Call("d2", "select_columns", {"columns": ["Nope2"]})]),
          Msg([Call("d3", "select_columns", {"columns": ["Nope3"]})]),
          Msg(None, "giving up")]
r = with_client(FakeClient(script), lambda: A.run_etl_agent(s, "x", max_steps=4))
check("three DIFFERENT failures are not blocked", len(r["trace"]) == 3, len(r["trace"]))
check("all three genuinely executed",
      all("not in the dataset" in t["summary"] for t in r["trace"]), r["trace"])
check("run ended on the no-tool-call turn", r["status"] == "incomplete", r["status"])

print("\n--- argument ORDER does not defeat the guard ---")
s = fresh(); s.df = pd.DataFrame({"A": range(12)})
script = [Msg([Call("k1", "aggregate_rows", '{"group_by": ["X"], "aggregations": []}')]),
          Msg([Call("k2", "aggregate_rows", '{"aggregations": [], "group_by": ["X"]}')]),
          Msg([Call("k3", "aggregate_rows", '{"group_by": ["X"], "aggregations": []}')])]
r = with_client(FakeClient(script), lambda: A.run_etl_agent(s, "x", max_steps=3))
check("reordered JSON recognised as the same call",
      "already made this exact call" in r["trace"][1]["summary"], r["trace"][1])
check("third attempt stops the run", r["status"] == "partial", r["status"])

print("\n--- a fixed call after a failure still runs ---")
s = fresh(); s.df = pd.DataFrame({"A": range(12), "B": range(12)})
script = [Msg([Call("f1", "select_columns", {"columns": ["Nope"]})]),
          Msg([Call("f2", "select_columns", {"columns": ["A"]})]),
          Msg(None, "done")]
r = with_client(FakeClient(script), lambda: A.run_etl_agent(s, "x", max_steps=3))
check("recovery is not blocked", r["trace"][0]["ok"] is False and r["trace"][1]["ok"] is True, r["trace"])
check("the fix took effect", list(s.df.columns) == ["A"], list(s.df.columns))

print("\n" + str(ok) + " passed, " + str(fail) + " failed")
sys.exit(1 if fail else 0)
