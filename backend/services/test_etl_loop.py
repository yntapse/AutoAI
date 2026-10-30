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
    def __init__(s, n, a): s.name, s.arguments = n, json.dumps(a) if isinstance(a, dict) else a
class Call:
    def __init__(s, i, n, a): s.id, s.type, s.function = i, "function", Fn(n, a)
class Msg:
    def __init__(s, calls=None, content=""): s.tool_calls, s.content = calls, content
class Comp:
    def __init__(s, m): s.choices = [types.SimpleNamespace(message=m)]

class FakeClient:
    """Replays a scripted list of assistant turns."""
    def __init__(self, script): self.script, self.calls = list(script), 0
    @property
    def chat(self): return self
    @property
    def completions(self): return self
    def create(self, **kw):
        turn = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(turn, Exception): raise turn
        self.seen_tools = [t["function"]["name"] for t in kw["tools"]]
        return Comp(turn)

def session_with_data():
    s = A.ETLSession("mssql+pyodbc://x", "mssql", "dbo", db=None,
                     allowed_tables=["MLTest_Customers", "MLTest_Orders"])
    s.df = pd.DataFrame({"C_Id": range(12), "C_Churn": [0, 1] * 6,
                         "O_Total": [float(i) for i in range(12)]})
    return s

def with_client(client, fn):
    original = A.resolve_llm_client
    A.resolve_llm_client = lambda: (client, "fake-model", "openai")
    try: return fn()
    finally: A.resolve_llm_client = original

print("\n--- loop: model answers without calling a tool ---")
r = with_client(FakeClient([Msg(None, "I think you should join them.")]),
                lambda: A.run_etl_agent(session_with_data(), "prep churn data"))
check("status incomplete", r["status"] == "incomplete", r["status"])
check("assistant text preserved", "join them" in r["assistant_text"], r["assistant_text"])
check("no file_id", r["file_id"] is None)

print("\n--- loop: invalid tool name, then recovery, then step ceiling ---")
script = [Msg([Call("c1", "make_me_a_sandwich", {})]),
          Msg([Call("c2", "validate_dataset", {"target_column": "C_Churn"})]),
          Msg([Call("c3", "select_columns", {"columns": ["C_Churn", "O_Total"]})]),
          Msg([Call("c4", "validate_dataset", {})])]
r = with_client(FakeClient(script), lambda: A.run_etl_agent(session_with_data(), "clean it", max_steps=4))
check("status partial after ceiling", r["status"] == "partial", r["status"])
check("steps_used == 4", r["steps_used"] == 4, r["steps_used"])
check("invalid tool recorded as failed, run continued",
      r["trace"][0]["ok"] is False and len(r["trace"]) == 4, r["trace"][0])
check("later valid tools succeeded", all(t["ok"] for t in r["trace"][1:]), r["trace"])
check("transformations logged", r["transformations_applied"], r["transformations_applied"])
check("nothing saved on partial", r["file_id"] is None)

print("\n--- loop: malformed tool arguments ---")
r = with_client(FakeClient([Msg([Call("c1", "validate_dataset", "{not json")])]),
                lambda: A.run_etl_agent(session_with_data(), "x", max_steps=1))
check("malformed JSON args handled", r["trace"][0]["ok"] is False
      and "valid JSON object" in r["trace"][0]["summary"], r["trace"][0])

print("\n--- loop: more than 3 tool calls in one step ---")
many = [Call(f"c{i}", "validate_dataset", {}) for i in range(5)]
r = with_client(FakeClient([Msg(many)]), lambda: A.run_etl_agent(session_with_data(), "x", max_steps=1))
check("only 3 tool calls executed", len(r["trace"]) == 3, len(r["trace"]))
check("truncation noted", any("only the first 3" in n for n in r["notes"]), r["notes"])

print("\n--- loop: provider failure ---")
r = with_client(FakeClient([RuntimeError("connection reset")]),
                lambda: A.run_etl_agent(session_with_data(), "x"))
check("status error on provider failure", r["status"] == "error", r["status"])

print("\n--- loop: max_steps is clamped to the hard ceiling ---")
r = with_client(FakeClient([Msg(None, "done")]),
                lambda: A.run_etl_agent(session_with_data(), "x", max_steps=999))
check("max_steps clamped to the ceiling (10)", r["max_steps"] == 10, r["max_steps"])

print("\n--- privacy: the conversation never carries a cell value ---")
captured = {}
class SpyClient(FakeClient):
    def create(self, **kw):
        captured["messages"] = captured.get("messages","") + json.dumps(kw["messages"], default=str)
        return super().create(**kw)
s = session_with_data(); s.df["C_Email"] = [f"user{i}@example.com" for i in range(12)]
with_client(SpyClient([Msg([Call("c1", "validate_dataset", {})]), Msg(None, "done")]),
            lambda: A.run_etl_agent(s, "prepare churn data", max_steps=2))
check("no email value reached the model", "user3@example.com" not in captured["messages"])
check("no connection string reached the model", "pyodbc" not in captured["messages"])
# Post-boundary contract: a name is shown only if its provenance is known.
# This session never ran build_dataset, so no name is registered and every
# one is placeholdered - the boundary is fail-closed.
check("unregistered column name withheld", "C_Email" not in captured["messages"])
check("withheld names replaced by a neutral placeholder",
      "derived_column_" in captured["messages"])
s.register_names("C_Email", "C_Id", "C_Churn", "O_Total")
captured.clear()
with_client(SpyClient([Msg([Call("c9","validate_dataset",{})]), Msg(None,"done")]),
            lambda: A.run_etl_agent(s, "check it", max_steps=2))
check("a REGISTERED column name does reach the model", "C_Email" in captured["messages"])
check("its values still do not", "user3@example.com" not in captured["messages"])

print("\n" + str(ok) + " passed, " + str(fail) + " failed")
sys.exit(1 if fail else 0)
