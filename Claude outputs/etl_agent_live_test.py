"""
ETL Agent live test - run this ON YOUR WINDOWS MACHINE, from the backend/ folder.

    cd C:\\Users\\Rishi\\Desktop\\AutoML\\AutoAI-main\\AutoAI-main\\backend
    python etl_agent_live_test.py --host localhost --database YOUR_DB --username sa --password YOUR_PASSWORD

    # or Windows Authentication:
    python etl_agent_live_test.py --host localhost --database YOUR_DB --trusted

This file is NOT part of the repo - it was not committed into backend/. Save it
wherever you like; it only imports, it changes nothing.

It drives the ETL tool layer DIRECTLY, without the LLM, so a failure points at
the backend rather than at the model. The last section runs the real agent over
HTTP if your server is up.
"""

import argparse
import sys
import traceback

sys.path.insert(0, ".")

PASSED = []
FAILED = []


def step(label):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            print(f"\n=== {label} ===")
            try:
                result = fn(*args, **kwargs)
                PASSED.append(label)
                return result
            except AssertionError as exc:
                FAILED.append((label, str(exc)))
                print(f"  FAIL: {exc}")
            except Exception:
                FAILED.append((label, "exception"))
                traceback.print_exc()
            return None
        return wrapper
    return decorator


def show(result):
    print("   ok:", result.get("ok"), "|", result.get("summary") or result.get("error"))
    return result


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
    parser.add_argument("--skip-agent", action="store_true")
    args = parser.parse_args()

    from database import SessionLocal
    from services.db_connector import build_connection_string
    from services.etl_agent import ETLSession, execute_tool

    connection_string = build_connection_string(
        db_type="mssql",
        host=args.host,
        port=args.port,
        database=args.database,
        username=args.username,
        password=args.password,
        trusted_connection=args.trusted,
    )

    CUSTOMERS, ORDERS, ITEMS, PRODUCTS = (
        "MLTest_Customers", "MLTest_Orders", "MLTest_OrderItems", "MLTest_Products",
    )

    db = SessionLocal()
    session = ETLSession(
        connection_string=connection_string,
        db_type="mssql",
        schema=args.schema,
        db=db,
        allowed_tables=[CUSTOMERS, ORDERS, ITEMS, PRODUCTS],
    )

    # ---------------------------------------------------------------- 1
    @step("1. inspect_tables")
    def t1():
        r = show(execute_tool(session, "inspect_tables", {}))
        assert r["ok"], r.get("error")
        assert CUSTOMERS in r["tables"], f"{CUSTOMERS} not found in {r['tables']}"
    t1()

    @step("2. inspect_table MLTest_Customers")
    def t2():
        r = show(execute_tool(session, "inspect_table", {"table_name": CUSTOMERS}))
        assert r["ok"], r.get("error")
        assert r["row_count"] > 0, "no rows"
        print("   candidate keys:", r["candidate_key_columns"])
        print("   columns:", [c["name"] for c in r["columns"]])
        return r
    customers = t2()

    # ---------------------------------------------------------------- 3
    @step("3. find_declared_relationships Customers/Orders/OrderItems")
    def t3():
        r = show(execute_tool(session, "find_declared_relationships",
                              {"table_names": [CUSTOMERS, ORDERS, ITEMS]}))
        assert r["ok"], r.get("error")
        for rel in r["relationships"]:
            print("   ", rel)
        return r
    declared = t3()

    # Work out the key columns: prefer a declared FK, else guess CustomerID.
    def key_pair(left, right, fallback_left, fallback_right):
        for rel in (declared or {}).get("relationships", []):
            pair = {rel["left_table"], rel["right_table"]}
            if pair == {left, right}:
                if rel["left_table"] == left:
                    return rel["left_column"], rel["right_column"]
                return rel["right_column"], rel["left_column"]
        return fallback_left, fallback_right

    cust_key, order_cust_key = key_pair(CUSTOMERS, ORDERS, "CustomerID", "CustomerID")
    order_key, item_order_key = key_pair(ORDERS, ITEMS, "OrderID", "OrderID")
    print(f"\n   using {CUSTOMERS}.{cust_key} = {ORDERS}.{order_cust_key}")
    print(f"   using {ORDERS}.{order_key} = {ITEMS}.{item_order_key}")

    # ---------------------------------------------------------------- 4
    @step("4. analyze_relationship Customers -> Orders")
    def t4():
        r = show(execute_tool(session, "analyze_relationship", {
            "base_table": CUSTOMERS, "base_column": cust_key,
            "related_table": ORDERS, "related_column": order_cust_key,
        }))
        assert r["ok"], r.get("error")
        p = r["profile"]
        print(f"   match {p['match_percentage']}% | cardinality {p['cardinality']} "
              f"| inner {p['inner_result_rows']} rows | left {p['left_result_rows']} rows "
              f"| row_multiplication {p['row_multiplication']}")
        print("   recommended:", r["recommendation"]["recommended"])
        for reason in r["recommendation"]["reasons"]:
            print("     -", reason)
        return r
    analysis = t4()
    recommended = ((analysis or {}).get("recommendation") or {}).get("recommended") or "inner"

    # ---------------------------------------------------------------- 5
    @step("5. build_dataset Customers + Orders")
    def t5():
        r = show(execute_tool(session, "build_dataset", {
            "base_table": CUSTOMERS,
            "tables": [CUSTOMERS, ORDERS],
            "relationships": [{
                "left_table": CUSTOMERS, "left_column": cust_key,
                "right_table": ORDERS, "right_column": order_cust_key,
                "join_type": recommended,
            }],
        }))
        assert r["ok"], r.get("error")
        print("   columns:", r["column_names"])
        return r
    t5()

    # ---------------------------------------------------------------- 6
    @step("6. build_dataset Customers + Orders + OrderItems (3 tables = the cap)")
    def t6():
        r = show(execute_tool(session, "build_dataset", {
            "base_table": CUSTOMERS,
            "tables": [CUSTOMERS, ORDERS, ITEMS],
            "relationships": [
                {"left_table": CUSTOMERS, "left_column": cust_key,
                 "right_table": ORDERS, "right_column": order_cust_key,
                 "join_type": recommended},
                {"left_table": ORDERS, "left_column": order_key,
                 "right_table": ITEMS, "right_column": item_order_key,
                 "join_type": "inner"},
            ],
        }))
        assert r["ok"], r.get("error")
        print("   columns:", r["column_names"])
        return r
    built = t6()
    if built is None:
        print("\n   3-table build failed; continuing with the 2-table dataset.")

    columns = list(session.df.columns) if session.df is not None else []
    print("\n   working dataset columns:", columns)

    # ---------------------------------------------------------------- 7
    @step("7a. transformation: drop_duplicates")
    def t7a():
        r = show(execute_tool(session, "drop_duplicates", {}))
        assert r["ok"], r.get("error")
    t7a()

    @step("7b. transformation: fill_nulls on every column")
    def t7b():
        r = show(execute_tool(session, "fill_nulls",
                              {"columns": ["*"], "strategy": "constant", "fill_value": "0"}))
        assert r["ok"], r.get("error")
    t7b()

    # Pick a numeric target and collapse to one row per customer.
    import pandas as pd
    numeric = [c for c in session.df.columns
               if pd.api.types.is_numeric_dtype(session.df[c])]
    print("\n   numeric columns:", numeric)
    grain = next((c for c in session.df.columns if c.endswith(cust_key)), None)
    measure = next((c for c in numeric if c != grain), None)

    @step("7c. transformation: aggregate_rows back to one row per customer")
    def t7c():
        assert grain and measure, f"could not pick grain/measure from {numeric}"
        r = show(execute_tool(session, "aggregate_rows", {
            "group_by": [grain],
            "aggregations": [
                {"column": measure, "function": "sum", "new_name": "TotalMeasure"},
                {"column": measure, "function": "count", "new_name": "RecordCount"},
            ],
        }))
        assert r["ok"], r.get("error")
    t7c()

    # ---------------------------------------------------------------- 8
    @step("8. validate_dataset")
    def t8():
        target = "TotalMeasure" if "TotalMeasure" in session.df.columns else measure
        r = show(execute_tool(session, "validate_dataset", {"target_column": target}))
        assert r["ok"], r.get("error")
        print("   readiness:", r["training_readiness"])
        for problem in r["problems"]:
            print("   problem:", problem)
        return target
    target = t8()

    # ---------------------------------------------------------------- 9
    @step("9. finish_etl -> file_id")
    def t9():
        r = show(execute_tool(session, "finish_etl", {
            "project_name": "ETL Agent live test",
            "target_column": target,
            "summary": "Live test of the ETL agent tool layer.",
        }))
        assert r["ok"], r.get("error")
        print("   file_id   :", r["file_id"])
        print("   project_id:", r["project_id"])
        print("   ->  hand this file_id to the existing AutoML flow.")
    t9()

    # --------------------------------------------------------- failures
    print("\n\n########## FAILURE CASES ##########")

    @step("F1. unknown table")
    def f1():
        r = show(execute_tool(session, "inspect_table", {"table_name": "NoSuchTable"}))
        assert r["ok"] is False and "not in the table list" in r["error"]
    f1()

    @step("F2. real-but-unlisted table reaches the database and fails cleanly")
    def f2():
        loose = ETLSession(connection_string, "mssql", args.schema, db=None)
        r = show(execute_tool(loose, "inspect_table", {"table_name": "NoSuchTable"}))
        assert r["ok"] is False, "should not have succeeded"
    f2()

    @step("F3. unknown column in a relationship")
    def f3():
        r = show(execute_tool(session, "analyze_relationship", {
            "base_table": CUSTOMERS, "base_column": "NotAColumn",
            "related_table": ORDERS, "related_column": order_cust_key,
        }))
        assert r["ok"] is False, "should not have succeeded"
    f3()

    @step("F4. invalid relationship (join graph does not connect)")
    def f4():
        r = show(execute_tool(session, "build_dataset", {
            "base_table": CUSTOMERS,
            "tables": [CUSTOMERS, ORDERS, ITEMS],
            "relationships": [],
        }))
        assert r["ok"] is False, "should not have succeeded"
    f4()

    @step("F5. 4 tables exceeds the 3-table cap")
    def f5():
        r = show(execute_tool(session, "build_dataset", {
            "base_table": CUSTOMERS,
            "tables": [CUSTOMERS, ORDERS, ITEMS, PRODUCTS],
            "relationships": [],
        }))
        assert r["ok"] is False and "3 tables" in r["error"], r
    f5()

    @step("F6. invalid tool name")
    def f6():
        r = show(execute_tool(session, "drop_the_database", {}))
        assert r["ok"] is False and "not a registered tool" in r["error"]
    f6()

    @step("F7. malformed tool arguments")
    def f7():
        r = show(execute_tool(session, "aggregate_rows", {"group_by": "not-a-list"}))
        assert r["ok"] is False, r
    f7()

    db.close()

    # ------------------------------------------------------- real agent
    if not args.skip_agent:
        print("\n\n########## FULL AGENT RUN (needs the server up + OPENAI_API_KEY) ##########")

        @step("A1. POST /etl/agent/run")
        def a1():
            import json
            import urllib.request
            body = {
                "db_type": "mssql", "host": args.host, "port": args.port,
                "database": args.database, "username": args.username,
                "password": args.password, "trusted_connection": args.trusted,
                "schema_name": args.schema,
                "request": (
                    "Build a customer-level dataset for churn analysis: one row "
                    "per customer, with their order totals attached. Drop "
                    "identifier and name columns. Predict the total order value."
                ),
                "tables": [CUSTOMERS, ORDERS, ITEMS],
                "max_steps": 8,
            }
            request = urllib.request.Request(
                f"{args.api}/etl/agent/run",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=300) as response:
                payload = json.load(response)
            print("   status :", payload["status"])
            print("   steps  :", payload["steps_used"], "/", payload["max_steps"])
            print("   file_id:", payload["file_id"])
            for entry in payload["trace"]:
                flag = "ok " if entry["ok"] else "ERR"
                print(f"   [{entry['step']}] {flag} {entry['tool']}: {entry['summary'][:110]}")
            assert payload["status"] in {"complete", "partial"}, payload["message"]
        a1()

    # ------------------------------------------------------------ report
    print("\n\n########## RESULT ##########")
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    for label, reason in FAILED:
        print(f"  FAILED: {label} ({reason})")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
