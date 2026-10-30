"""ETL Agent privacy regression suite.

Nothing here is asserted against a hardcoded string. Every sensitive value is
generated into a DataFrame, and the assertions search the ACTUAL outbound
OpenAI payload for the values that frame happens to contain. Change the frame
and the tests still hold - which is the point: the protection has to work for
customer values nobody has written down yet.

Structure:
  A  every tool, every dataset, values from the frame           (per-tool sweep)
  B  the reproduced one-hot leak, end to end through run_etl_agent
  C  persistence: later summaries must not re-leak
  D  secrets
  E  safe metadata must still get through (the boundary must not be a mute)
  F  the boundary itself, unit level
"""
import sys, types, json, random, string, pandas as pd
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

# --- synthetic sensitive data, generated not hardcoded ---------------------
random.seed(20260925)
def _tok(n=7):
    return "".join(random.choice(string.ascii_uppercase) for _ in range(n))

FIRST = [_tok(6).capitalize() for _ in range(6)]
LAST  = [_tok(8).capitalize() for _ in range(6)]
NAMES = [f"{f} {l}" for f, l in zip(FIRST, LAST)]
EMAILS = [f"{f.lower()}.{l.lower()}@{_tok(9).lower()}.co.in" for f, l in zip(FIRST, LAST)]
PHONES = [f"+91 9{random.randint(10**8, 10**9-1)}" for _ in range(6)]
ADDRS  = [f"{random.randint(1,99)} {_tok(8).capitalize()} Road {_tok(6).capitalize()}" for _ in range(6)]
INVOICES = [f"INV-{random.randint(10000,99999)}-{_tok(3)}" for _ in range(6)]
SALARIES = [float(random.randint(400000, 2500000)) for _ in range(6)]

SENSITIVE = NAMES + EMAILS + PHONES + ADDRS + INVOICES + [f"{s:.1f}" for s in SALARIES] \
            + [str(int(s)) for s in SALARIES]

SCHEMA_COLS = ["CustomerName", "Email", "Phone", "Address", "InvoiceRef",
               "Salary", "Spend", "Qty"]

def frame():
    return pd.DataFrame({
        "CustomerName": NAMES, "Email": EMAILS, "Phone": PHONES,
        "Address": ADDRS, "InvoiceRef": INVOICES,
        "Salary": SALARIES[:5] + [None],
        "Spend": [1200.0, 900.0, 1500.0, 900.0, 2200.0, 700.0],
        "Qty": [3, 2, 5, 2, 4, 1],
    })

def built_session(register=True):
    """A session as it looks AFTER build_dataset: schema names registered."""
    s = A.ETLSession("mssql+pyodbc://sa:Sup3rS3cret@dbhost:1433/SalesDB",
                     "mssql", "dbo", db=None, allowed_tables=["MLTest_Customers"])
    s.df = frame()
    if register:
        s.register_names(*SCHEMA_COLS)
    return s

def leaks(payload):
    blob = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    return sorted({v for v in SENSITIVE if v and v in blob})

# ---------------------------------------------------------------- A
print("\n--- A. per-tool sweep: no tool may emit a value from the frame ---")
CASES = [
    ("fill_nulls", {"columns": ["Salary"], "strategy": "mean"}),
    ("fill_nulls", {"columns": ["CustomerName"], "strategy": "mode"}),
    ("fill_nulls", {"columns": ["*"], "strategy": "constant", "fill_value": "0"}),
    ("drop_columns", {"columns": ["Phone"]}),
    ("drop_rows_with_nulls", {"columns": ["Salary"], "how": "any"}),
    ("drop_duplicates", {}),
    ("rename_columns", {"mapping": {"Spend": "TotalSpend"}}),
    ("convert_dtype", {"columns": ["CustomerName"], "target_type": "int"}),
    ("convert_dtype", {"columns": ["Email"], "target_type": "category"}),
    ("convert_dtype", {"columns": ["Salary"], "target_type": "datetime"}),
    ("normalize_columns", {"columns": ["Spend"], "method": "zscore"}),
    ("normalize_columns", {"columns": ["Address"], "method": "minmax"}),
    ("encode_categorical", {"columns": ["CustomerName"], "method": "label"}),
    ("encode_categorical", {"columns": ["CustomerName"], "method": "onehot"}),
    ("encode_categorical", {"columns": ["*"], "method": "onehot"}),
    ("replace_values", {"column": "Email", "old_values": ["x"], "new_value": "y"}),
    ("remove_outliers", {"columns": ["Spend"], "method": "iqr"}),
    ("remove_outliers", {"columns": ["InvoiceRef"], "method": "zscore"}),
    ("filter_rows", {"column": "Spend", "operator": ">", "value": "1000"}),
    ("filter_rows", {"column": "Email", "operator": "contains", "value": "co.in"}),
    ("lowercase_columns", {"columns": ["*"]}),
    ("strip_whitespace", {"columns": ["*"]}),
    ("select_columns", {"columns": ["Spend", "Qty"]}),
    ("select_columns", {"columns": ["NoSuchColumn"]}),
    ("add_calculated_column", {"new_column": "Unit", "left": "Spend",
                               "operation": "divide", "right": "Qty"}),
    ("add_calculated_column", {"new_column": "Bad", "left": "CustomerName",
                               "operation": "add", "right": "1"}),
    ("aggregate_rows", {"group_by": ["CustomerName"],
                        "aggregations": [{"column": "Spend", "function": "sum"}]}),
    ("aggregate_rows", {"group_by": ["Email"],
                        "aggregations": [{"column": "CustomerName", "function": "max"}]}),
    ("aggregate_rows", {"group_by": ["NoSuch"], "aggregations": []}),
    ("aggregate_rows", {"group_by": "not-a-list", "aggregations": []}),
    ("validate_dataset", {}),
    ("validate_dataset", {"target_column": "Spend"}),
    ("validate_dataset", {"target_column": "CustomerName"}),
    ("validate_dataset", {"target_column": "NoSuch"}),
    ("finish_etl", {"project_name": "p", "target_column": "Spend", "summary": "s"}),
    ("inspect_table", {"table_name": "NotAllowed"}),
    ("inspect_columns", {"table_names": []}),
    ("inspect_columns", {"bogus": 1}),
    ("build_dataset", {"base_table": "X", "tables": ["X"], "relationships": []}),
    ("totally_unknown_tool", {"note": "arbitrary"}),
]
offenders = {}
for tool, args in CASES:
    s = built_session()
    raw = A.execute_tool(s, tool, args)
    sent = A.sanitize_for_llm(s, raw)
    found = [v for v in leaks(sent) if v not in json.dumps(args, default=str)]
    if found:
        offenders.setdefault(tool, set()).update(found)
check(f"all {len(CASES)} tool calls emitted no frame value", not offenders, offenders)

print("\n--- A2. the boundary is load-bearing, not decorative ---")
# Fix 2 refuses one-hot, so it can no longer reach the frame through a tool.
# Force the frame into the post-one-hot state directly and confirm that the
# RAW tool output leaks while the SANITIZED output does not. Without this the
# suite could pass simply because Fix 2 fires first.
def onehot_session():
    s = built_session()
    s.df = pd.get_dummies(s.df, columns=["CustomerName", "Email"], drop_first=True)
    return s

raw_leakers, sanitized_leakers = {}, {}
for tool, args in [("validate_dataset", {}), ("drop_duplicates", {}),
                   ("select_columns", {"columns": ["Spend"]}),
                   ("aggregate_rows", {"group_by": ["Spend"],
                                       "aggregations": [{"column": "Qty", "function": "sum"}]})]:
    s = onehot_session()
    raw = A.execute_tool(s, tool, args)
    if leaks(raw): raw_leakers[tool] = sorted(leaks(raw))[:2]
    if leaks(A.sanitize_for_llm(s, raw)): sanitized_leakers[tool] = sorted(leaks(A.sanitize_for_llm(s, raw)))
check("raw tool output DOES leak once a value-derived name exists",
      bool(raw_leakers), "vacuous - nothing leaked raw")
check("the boundary removes every one of those leaks", not sanitized_leakers, sanitized_leakers)
print(f"        raw leakers: {sorted(raw_leakers)}")

# ---------------------------------------------------------------- B
print("\n--- B. the reproduced leak, end to end through run_etl_agent ---")
class Fn:
    def __init__(s,n,a): s.name, s.arguments = n, json.dumps(a)
class Call:
    def __init__(s,i,n,a): s.id, s.type, s.function = i, "function", Fn(n,a)
class Msg:
    def __init__(s,c=None,t=""): s.tool_calls, s.content = c, t
WIRE = []
class SpyClient:
    def __init__(self, script): self.script, self.i = script, 0
    @property
    def chat(self): return self
    @property
    def completions(self): return self
    def create(self, **kw):
        WIRE.append(json.dumps(kw["messages"], default=str))
        m = self.script[min(self.i, len(self.script)-1)]; self.i += 1
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=m)])

def run(script, session):
    WIRE.clear()
    original = A.resolve_llm_client
    A.resolve_llm_client = lambda: (SpyClient(script), "m", "openai")
    try: return A.run_etl_agent(session, "prepare the dataset", max_steps=len(script))
    finally: A.resolve_llm_client = original

payload = run([
    Msg([Call("c1", "encode_categorical", {"columns": ["CustomerName", "Email"],
                                           "method": "onehot"})]),
    Msg([Call("c2", "validate_dataset", {})]),
    Msg([Call("c3", "drop_duplicates", {})]),
    Msg(None, "done"),
], built_session())
wire = "\n".join(WIRE)
check("one-hot refused by the agent", payload["trace"][0]["ok"] is False, payload["trace"][0])
check("refusal explains why", "column names" in payload["trace"][0]["summary"],
      payload["trace"][0]["summary"])
check("NO customer name reached the outbound payload", not leaks(wire), leaks(wire))
check("one-hot is absent from the tool schema offered to the model",
      all("onehot" not in json.dumps(t)
          for t in A.get_tools_schema(built_session())))
check("label encoding is still offered",
      any("label" in json.dumps(t) and "encode_categorical" in json.dumps(t)
          for t in A.get_tools_schema(built_session())))

# ---------------------------------------------------------------- C
print("\n--- C. persistence: a forced one-hot must not leak via later summaries ---")
s = built_session()
s.df = pd.get_dummies(s.df, columns=["CustomerName", "Email"], drop_first=True)
payload = run([
    Msg([Call("d1", "validate_dataset", {})]),
    Msg([Call("d2", "drop_duplicates", {})]),
    Msg([Call("d3", "select_columns", {"columns": ["NoSuch"]})]),
    Msg(None, "done"),
], s)
wire = "\n".join(WIRE)
check("value-derived column names never reach the model", not leaks(wire), leaks(wire))
check("they appear as neutral placeholders instead", "derived_column_" in wire)
check("the run still worked", payload["trace"][0]["ok"] is True, payload["trace"][0])
check("even the error path is scrubbed",
      payload["trace"][2]["ok"] is False and not leaks(payload["trace"][2]["summary"]),
      payload["trace"][2]["summary"])

# ---------------------------------------------------------------- D
print("\n--- D. secrets ---")
for token, label in [("Sup3rS3cret", "password"), ("mssql+pyodbc", "connection string"),
                     ("dbhost", "host"), ("sa:", "username"), ("SalesDB", "database name")]:
    check(f"no {label} in the outbound payload", token not in wire)

# ---------------------------------------------------------------- E
print("\n--- E. the boundary must not be a mute: safe metadata still flows ---")
s = built_session()
sent = A.sanitize_for_llm(s, A.execute_tool(s, "validate_dataset",
                                            {"target_column": "Spend"}))
blob = json.dumps(sent)
for token, label in [("CustomerName", "registered column name"), ("Spend", "target name"),
                     ("float64", "data type"), ("rows", "row count"),
                     ("null_count", "null count"), ("distinct_count", "distinct count"),
                     ("training_readiness", "validation summary")]:
    check(f"{label} still reaches the model", token in blob)
check("row count is the real number", sent["dataset"]["rows"] == 6, sent["dataset"]["rows"])
check("null count is the real number",
      any(c["null_count"] == 1 for c in sent["dataset"]["column_details"]))

# ---------------------------------------------------------------- F
print("\n--- F. the boundary, unit level ---")
s = built_session()
hostile = {
    "ok": True,
    "summary": f"Processed {NAMES[0]} successfully",
    "rows": 6,
    "column_names": ["Spend", f"CustomerName_{NAMES[1]}"],
    "preview": [{"CustomerName": NAMES[2]}],
    "records": NAMES,
    "connection_string": "mssql+pyodbc://sa:Sup3rS3cret@dbhost/db",
    "dataframe": frame(),
    "nested": {"deep": {"value": EMAILS[0]}},
}
# The frame must actually CONTAIN the values, as it would in a real run:
# the scrubber can only remove values present in the dataset it is guarding.
s.df = frame()
s.df[f"CustomerName_{NAMES[1]}"] = 1
clean = A.sanitize_for_llm(s, hostile)
check("unknown keys dropped entirely",
      not any(k in clean for k in ["preview", "records", "connection_string",
                                   "dataframe", "nested"]), list(clean))
check("dropped keys are reported, not silent", "_withheld" in clean, clean.get("_withheld"))
check("value-derived name placeholdered",
      any("derived_column_" in str(c) for c in clean["column_names"]), clean["column_names"])
check("registered name kept", "Spend" in clean["column_names"])
check("free text scrubbed of raw dataset values", not leaks(clean["summary"]), clean["summary"])
check("withheld values marked, not silently removed",
      "withheld" in clean["summary"].lower(), clean["summary"])
check("nothing sensitive survives anywhere", not leaks(clean), leaks(clean))
check("placeholders are stable within a session",
      s.public_name(f"CustomerName_{NAMES[1]}") == s.public_name(f"CustomerName_{NAMES[1]}"))
check("different names get different placeholders",
      s.public_name("alpha_x") != s.public_name("beta_y"))
check("compound of registered parts allowed",
      s.public_name("MLTest_Customers.Spend") == "MLTest_Customers.Spend"
      if "mltest_customers" in s.safe_names else True)

print("\n" + str(ok) + " passed, " + str(fail) + " failed")
sys.exit(1 if fail else 0)
