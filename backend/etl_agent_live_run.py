"""
ETL Agent live end-to-end validation - Phases 2, 3 and 4.

RUN THIS ON YOUR WINDOWS MACHINE, from backend/, with uvicorn already up:

    cd C:\\Users\\Rishi\\Desktop\\AutoML\\AutoAI-main\\AutoAI-main\\backend
    python etl_agent_live_run.py --database YOUR_DB --username sa --password YOUR_PASSWORD
    # or:  --trusted   for Windows Authentication

Two parts, both using the REAL agent, the REAL SQL Server and the REAL OpenAI
key already configured in the project.

  PART A  Phases 2 and 3. Hits POST /etl/agent/run over HTTP. Nothing is
          bypassed, stubbed or faked: the server runs its own Observe/Think/Act
          loop and this script only reads the response. Then it verifies the
          AutoML handoff by checking that the CSV and the Project row the
          file_id points at really exist.

  PART B  Phase 4. Runs the same agent again with ONLY the OpenAI transport
          wrapped by a recorder, so every outbound message can be inspected.
          The loop, the tools, the database and the model are untouched - the
          recorder just copies what goes over the wire. This is the only way to
          see what the LLM actually received; the HTTP response does not carry
          it. Then it pulls real cell values out of your three tables and
          searches the captured messages for them.

Not committed into backend/ - it is a test harness, not part of the product.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

sys.path.insert(0, ".")

CUSTOMERS = "MLTest_LargeCustomers"
ORDERS = "MLTest_LargeOrders"
ITEMS = "MLTest_LargeOrderItems"

USER_REQUEST = (
    "Build a customer-level dataset I can use to predict each customer's total "
    "spend. Start from MLTest_Customers, attach MLTest_Orders and "
    "MLTest_OrderItems, drop customer names and any free-text columns, remove "
    "duplicate rows, and aggregate back to one row per customer so the grain "
    "is correct."
)

OLD_BUG = "does not exist in"

RESULTS = {}


def banner(text):
    print("\n" + "=" * 72)
    print(text)
    print("=" * 72)


def verdict(key, label, passed, detail=""):
    RESULTS[key] = bool(passed)
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))


def connection_body(args):
    return {
        "db_type": "mssql",
        "host": args.host,
        "port": args.port,
        "database": args.database,
        "username": args.username,
        "password": args.password,
        "trusted_connection": args.trusted,
        "schema_name": args.schema,
    }


def print_trace(payload):
    print(f"\n  status     : {payload['status']}")
    print(f"  message    : {payload['message']}")
    print(f"  steps used : {payload['steps_used']} / {payload['max_steps']}")
    print(f"  provider   : {payload['provider']}  model: {payload['model']}")
    print(f"  file_id    : {payload.get('file_id')}")
    if payload.get("dataset"):
        d = payload["dataset"]
        print(f"  dataset    : {d['rows']} rows x {d['columns']} columns, "
              f"{d['duplicate_rows']} duplicate row(s)")
    if payload.get("build"):
        b = payload["build"]
        print(f"  base table : {b.get('base_table')}")
        print(f"  tables     : {', '.join(b.get('tables') or [])}")
        for rel in b.get("relationships") or []:
            print(f"    join     : {rel['left_table']}.{rel['left_column']} "
                  f"{rel['join_type'].upper()} JOIN "
                  f"{rel['right_table']}.{rel['right_column']}")
        if b.get("join_keys_used_internally"):
            print(f"    keys used internally, left out of output: "
                  f"{', '.join(b['join_keys_used_internally'])}")
        print(f"  columns    : {', '.join(b.get('column_names') or [])}")
    for note in payload.get("notes") or []:
        print(f"  note       : {note}")
    print("\n  --- Observe / Think / Act trace ---")
    for entry in payload.get("trace") or []:
        flag = "ok " if entry["ok"] else "ERR"
        print(f"   step {entry['step']}  {flag}  {entry['tool']}")
        print(f"              args: {json.dumps(entry['arguments'])[:160]}")
        print(f"              -->  {entry['summary'][:200]}")
    for label, applied in enumerate(payload.get("transformations_applied") or [], 1):
        print(f"  transform {label}: {applied}")


# ---------------------------------------------------------------------------
# PART A - the real API
# ---------------------------------------------------------------------------


def part_a(args):
    banner("PART A - POST /etl/agent/run  (Phases 2 and 3)")

    body = connection_body(args)
    body.update({
        "request": USER_REQUEST,
        "tables": [CUSTOMERS, ORDERS, ITEMS],
        "max_steps": 10,
    })

    print(f"  POST {args.api}/etl/agent/run")
    print(f"  tables : {CUSTOMERS}, {ORDERS}, {ITEMS}")
    print(f"  request: {USER_REQUEST}")
    print("\n  running (this takes a minute - the agent makes several LLM round trips)...")

    request = urllib.request.Request(
        f"{args.api}/etl/agent/run",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:600]
        print(f"\n  HTTP {exc.code}: {detail}")
        verdict("api", "POST /etl/agent/run returned 200", False, f"HTTP {exc.code}")
        return None
    except urllib.error.URLError as exc:
        print(f"\n  Could not reach {args.api} - is uvicorn running? ({exc.reason})")
        verdict("api", "POST /etl/agent/run returned 200", False, str(exc.reason))
        return None

    print_trace(payload)

    tools_used = [entry["tool"] for entry in payload.get("trace") or []]
    failures = [entry for entry in payload.get("trace") or [] if not entry["ok"]]

    banner("PHASE 3 CHECKS")
    verdict("api", "POST /etl/agent/run returned 200", True)
    verdict("p3_01", "1. inspected available tables",
            any(t in ("inspect_tables", "inspect_columns", "inspect_table") for t in tools_used))
    verdict("p3_02", "2. inspected required columns",
            any(t in ("inspect_columns", "inspect_table") for t in tools_used))

    analyses = [e for e in payload.get("trace") or []
                if e["tool"] == "analyze_relationship" and e["ok"]]
    pairs = {
        (e["arguments"].get("base_table"), e["arguments"].get("related_table"))
        for e in analyses
    }
    verdict("p3_03", "3+4. understood Customers->Orders and Orders->OrderItems",
            any(CUSTOMERS in p and ORDERS in p for p in pairs)
            and any(ORDERS in p and ITEMS in p for p in pairs),
            f"analysed pairs: {pairs}")
    verdict("p3_05", "5. analysed the relationships with the Join Advisor",
            len(analyses) >= 1, f"{len(analyses)} analyze_relationship call(s)")

    builds = [e for e in payload.get("trace") or [] if e["tool"] == "build_dataset"]
    good_builds = [e for e in builds if e["ok"]]
    verdict("p3_06", "6. built the joined dataset",
            bool(good_builds), f"{len(good_builds)} of {len(builds)} build(s) succeeded")

    after_build = []
    if good_builds:
        first = payload["trace"].index(good_builds[0])
        after_build = payload["trace"][first + 1:]
    verdict("p3_07", "7. continued after build_dataset",
            bool(after_build), f"{len(after_build)} tool call(s) after the build")

    verdict("p3_08", "8. performed the requested transformations",
            bool(payload.get("transformations_applied")),
            "; ".join(payload.get("transformations_applied") or []) or "none")
    verdict("p3_09", "9. validated the dataset",
            any(t == "validate_dataset" for t in tools_used))
    verdict("p3_10", "10. finished the ETL",
            payload["status"] == "complete", payload["status"])
    verdict("p3_11", "11. produced a file_id", bool(payload.get("file_id")),
            str(payload.get("file_id")))

    banner("THE PREVIOUS BUG")
    offenders = [e for e in payload.get("trace") or []
                 if not e["ok"] and OLD_BUG in (e["summary"] or "")]
    verdict("oldbug", "no 'Column X does not exist in T' failure",
            not offenders,
            offenders[0]["summary"] if offenders else "clean")
    repeats = [n for n in payload.get("notes") or [] if "identical" in n]
    verdict("guard", "no identical-failing-call deadlock",
            not repeats, repeats[0] if repeats else "clean")
    if failures:
        print("\n  tool failures during the run (recoverable ones are normal):")
        for entry in failures:
            print(f"    step {entry['step']} {entry['tool']}: {entry['summary'][:160]}")

    return payload


# ---------------------------------------------------------------------------
# PART A2 - AutoML handoff
# ---------------------------------------------------------------------------


def part_a2(payload):
    banner("AUTOML HANDOFF  (Phase 3, item 12)")
    file_id = (payload or {}).get("file_id")
    if not file_id:
        verdict("handoff", "12. result usable by the AutoML pipeline", False, "no file_id")
        return

    import pandas as pd
    from database import SessionLocal
    from main import UPLOAD_DIR, assess_training_readiness
    from models.project import Project

    path = UPLOAD_DIR / file_id
    print(f"  CSV path : {path}")
    verdict("handoff_csv", "the file_id resolves to a real CSV", path.exists())
    if not path.exists():
        verdict("handoff", "12. result usable by the AutoML pipeline", False, "CSV missing")
        return

    frame = pd.read_csv(path)
    print(f"  CSV shape: {frame.shape[0]} rows x {frame.shape[1]} columns")
    print(f"  columns  : {', '.join(map(str, frame.columns))}")

    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.file_id == file_id).first()
    finally:
        db.close()

    verdict("handoff_project", "a Project row exists for that file_id", project is not None)
    if project is not None:
        print(f"  project  : id={project.id} name={project.project_name!r} "
              f"target={project.target_column!r} rows={project.num_rows} "
              f"features={project.num_features}")
        verdict("handoff_target", "the target column is present in the CSV",
                str(project.target_column) in frame.columns, str(project.target_column))

    readiness = assess_training_readiness(frame)
    print(f"  readiness: {readiness}")
    verdict("handoff_ready", "the dataset passes the platform's training gate",
            bool(readiness.get("ready")), readiness.get("reason") or "ready")

    verdict("handoff", "12. result usable by the AutoML pipeline",
            path.exists() and project is not None and bool(readiness.get("ready")))


# ---------------------------------------------------------------------------
# PART B - privacy
# ---------------------------------------------------------------------------


class RecordingCompletions:
    def __init__(self, inner, sink):
        self._inner, self._sink = inner, sink

    def create(self, **kwargs):
        self._sink.append(json.dumps(kwargs.get("messages", []), default=str))
        return self._inner.create(**kwargs)


class RecordingChat:
    def __init__(self, inner, sink):
        self.completions = RecordingCompletions(inner.completions, sink)


class RecordingClient:
    """Wraps ONLY the transport. The agent, its tools and the model are real."""

    def __init__(self, inner, sink):
        self._inner = inner
        self.chat = RecordingChat(inner.chat, sink)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def real_cell_values(connection_string, schema):
    """Pull actual values out of the three tables, to search the messages for."""
    from services.db_connector import extract_to_dataframe, quote_identifier

    corpus = {}
    for table in (CUSTOMERS, ORDERS, ITEMS):
        qualified = (f"{quote_identifier('mssql', schema)}."
                     f"{quote_identifier('mssql', table)}")
        try:
            frame = extract_to_dataframe(
                connection_string, f"SELECT TOP 100 * FROM {qualified}"
            )
        except Exception as exc:
            print(f"  (could not sample {table}: {exc})")
            continue
        for column in frame.columns:
            for value in frame[column].dropna().unique():
                text = str(value).strip()
                # Skip anything too short or purely numeric: a bare "3" would
                # collide with step numbers and counts and prove nothing.
                if len(text) >= 4 and not text.replace(".", "").replace("-", "").isdigit():
                    corpus.setdefault(text, f"{table}.{column}")
    return corpus


def part_b(args):
    banner("PART B - PRIVACY AUDIT  (Phase 4)")
    print("  Running the agent again with the OpenAI transport recorded.")
    print("  The loop, tools, database and model are unmodified.\n")

    from database import SessionLocal
    from services.db_connector import build_connection_string
    import services.etl_agent as agent_module

    connection_string = build_connection_string(
        db_type="mssql", host=args.host, port=args.port, database=args.database,
        username=args.username, password=args.password,
        trusted_connection=args.trusted,
    )

    captured = []
    original_resolve = agent_module.resolve_llm_client

    def recording_resolve():
        client, model, provider = original_resolve()
        return RecordingClient(client, captured), model, provider

        agent_module.resolve_llm_client = recording_resolve
        db = SessionLocal()
        try:
            session = agent_module.ETLSession(
                connection_string=connection_string, db_type="mssql",
                schema=args.schema, db=db, allowed_tables=[CUSTOMERS, ORDERS, ITEMS],
            )
            payload = agent_module.run_etl_agent(session, USER_REQUEST, max_steps=10)
        finally:
            agent_module.resolve_llm_client = original_resolve
            db.close()

        print(f"  status: {payload['status']}  steps: {payload['steps_used']}  "
            f"file_id: {payload.get('file_id')}")
        print(f"  LLM round trips recorded: {len(captured)}")
        blob = "\n".join(captured)
        print(f"  total characters sent to the model: {len(blob)}")

        print("\n  sampling real values from the three tables...")
        corpus = real_cell_values(connection_string, args.schema)
        print(f"  {len(corpus)} distinct non-trivial cell values to search for")

        leaked = {value: source for value, source in corpus.items() if value in blob}

        banner("PHASE 4 RESULT")
        verdict("priv_rows", "no real cell value appears in any LLM message",
                not leaked, f"{len(leaked)} leak(s)")
        if leaked:
            print("\n  *** PRIVACY VIOLATION - values found in the conversation: ***")
            for value, source in list(leaked.items())[:25]:
                print(f"    {source}: {value!r}")

        secrets = {
            "password": args.password or "\x00unset\x00",
            "connection string scheme": "mssql+pyodbc",
            "odbc driver": "ODBC Driver",
        }
        hits = {label: token for label, token in secrets.items() if token and token in blob}
        verdict("priv_secrets", "no password, connection string or driver name sent",
                not hits, str(list(hits)) if hits else "clean")

        import re
        emails = set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", blob))
        verdict("priv_email", "no email address in any message",
                not emails, str(list(emails)[:5]) if emails else "clean")

        print("\n  what the model DID receive (expected and allowed):")
        for token, label in [("column", "column names"), ("row_count", "row counts"),
                            ("null_count", "null counts"), ("distinct_count", "distinct counts"),
                            ("match_percentage", "join match rates"),
                            ("cardinality", "cardinality"), ("dtype", "data types")]:
            if token in blob:
                print(f"    - {label}")

        return payload


# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--database", required=True)
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--trusted", action="store_true")
    parser.add_argument("--schema", default="dbo")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--skip-privacy", action="store_true",
                        help="skip Part B (it costs a second OpenAI run)")
    args = parser.parse_args()

    payload = part_a(args)
    if payload:
        part_a2(payload)
    if not args.skip_privacy:
        try:
            part_b(args)
        except Exception:
            import traceback
            traceback.print_exc()
            verdict("priv_rows", "privacy audit completed", False, "harness error")

    banner("SUMMARY")
    passed = sum(1 for v in RESULTS.values() if v)
    print(f"  {passed} of {len(RESULTS)} checks passed")
    for key, value in RESULTS.items():
        if not value:
            print(f"    FAILED: {key}")
    print("\n  Paste this whole output back into the conversation.")
    return 0 if all(RESULTS.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
