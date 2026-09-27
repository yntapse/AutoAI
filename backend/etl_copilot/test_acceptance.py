"""Acceptance test: Customers -> Orders -> OrderItems, executed for real.

The live SQL Server sits on the developer's Windows localhost, which this
sandbox cannot reach. To prove the ARITHMETIC rather than merely that SQL
parses, this test builds the same 100/1000/3000 fixture in sqlite3 (stdlib)
and EXECUTES the compiler's generated SQL against it.

sqlite accepts [bracket] quoting, CTEs, COALESCE and COUNT(DISTINCT), so the
mssql-dialect output runs unchanged.

Expected:
    total_sales    = 2,701,500
    total_quantity = 18,000
    rows           = 100
"""

import sqlite3
import sys

from etl_copilot.dataset_compiler import DatasetCompiler
from etl_copilot.join_graph import resolve_paths_for_spec
from etl_copilot.spec_models import (
    AttributeSpec,
    Catalog,
    ColumnInfo,
    DatasetSpec,
    JoinHop,
    MeasureSpec,
    TableInfo,
)
from etl_copilot.spec_validator import errors_only, validate_spec

_failures = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print("  [%s] %s%s" % (status, label, (" - " + detail) if detail else ""))
    if not condition:
        _failures.append(label)


# ---------------------------------------------------------------- fixture

def build_fixture():
    """100 customers, 1000 orders, 3000 order items.

    OrderAmount is chosen so the total is exactly 2,701,500:
        order i (1..1000) has amount 1500 + (i * 1200 / 1000 * ...)  -- no.
    Use a clean arithmetic series instead:
        amount(i) = 1200 + 3*i   for i in 1..1000
        sum = 1000*1200 + 3*(1000*1001/2) = 1,200,000 + 1,501,500 = 2,701,500
    Quantity = 6 per item, 3000 items -> 18,000.
    """
    connection = sqlite3.connect(":memory:")
    cursor = connection.cursor()

    cursor.execute(
        "CREATE TABLE Customers ("
        "CustomerID INTEGER PRIMARY KEY, CustomerName TEXT, City TEXT)"
    )
    cursor.execute(
        "CREATE TABLE Orders ("
        "OrderID INTEGER PRIMARY KEY, CustomerID INTEGER, "
        "OrderDate TEXT, OrderAmount NUMERIC)"
    )
    cursor.execute(
        "CREATE TABLE OrderItems ("
        "OrderItemID INTEGER PRIMARY KEY, OrderID INTEGER, "
        "ProductID INTEGER, Quantity INTEGER)"
    )

    cursor.executemany(
        "INSERT INTO Customers VALUES (?, ?, ?)",
        [(c, "Customer %d" % c, "City %d" % (c % 7)) for c in range(1, 101)],
    )

    cursor.executemany(
        "INSERT INTO Orders VALUES (?, ?, ?, ?)",
        [
            (i, ((i - 1) % 100) + 1, "2026-01-01", 1200 + 3 * i)
            for i in range(1, 1001)
        ],
    )

    items = []
    item_id = 1
    for order_id in range(1, 1001):
        for _ in range(3):
            items.append((item_id, order_id, (item_id % 20) + 1, 6))
            item_id += 1

    cursor.executemany("INSERT INTO OrderItems VALUES (?, ?, ?, ?)", items)
    connection.commit()
    return connection


def build_catalog():
    return Catalog(
        db_type="mssql",
        schema_name=None,
        tables=[
            TableInfo(
                name="Customers",
                columns=[
                    ColumnInfo(name="CustomerID", type="INTEGER", nullable=False, primary_key=True),
                    ColumnInfo(name="CustomerName", type="VARCHAR(100)"),
                    ColumnInfo(name="City", type="VARCHAR(50)"),
                ],
            ),
            TableInfo(
                name="Orders",
                columns=[
                    ColumnInfo(name="OrderID", type="INTEGER", nullable=False, primary_key=True),
                    ColumnInfo(name="CustomerID", type="INTEGER"),
                    ColumnInfo(name="OrderDate", type="DATE"),
                    ColumnInfo(name="OrderAmount", type="DECIMAL(10, 2)"),
                ],
            ),
            TableInfo(
                name="OrderItems",
                columns=[
                    ColumnInfo(name="OrderItemID", type="INTEGER", nullable=False, primary_key=True),
                    ColumnInfo(name="OrderID", type="INTEGER"),
                    ColumnInfo(name="ProductID", type="INTEGER"),
                    ColumnInfo(name="Quantity", type="INTEGER"),
                ],
            ),
        ],
        relationships=[
            JoinHop(
                left_table="Customers", left_column="CustomerID",
                right_table="Orders", right_column="CustomerID",
            ),
            JoinHop(
                left_table="Orders", left_column="OrderID",
                right_table="OrderItems", right_column="OrderID",
            ),
        ],
    )


def build_spec():
    return DatasetSpec(
        entity_table="Customers",
        entity_key=["CustomerID"],
        attributes=[
            AttributeSpec(source_table="Customers", source_column="CustomerName", name="CustomerName"),
            AttributeSpec(source_table="Customers", source_column="City", name="City"),
        ],
        measures=[
            MeasureSpec(measure_name="total_sales", source_table="Orders",
                        source_column="OrderAmount", aggregation="SUM"),
            MeasureSpec(measure_name="order_count", source_table="Orders",
                        source_column="OrderID", aggregation="COUNT_DISTINCT"),
            MeasureSpec(measure_name="total_quantity", source_table="OrderItems",
                        source_column="Quantity", aggregation="SUM"),
        ],
        db_type="mssql",
    )


def main():
    print("\n=== ETL Copilot acceptance test ===\n")

    connection = build_fixture()
    catalog = build_catalog()
    spec = build_spec()

    cursor = connection.cursor()
    cursor.execute("SELECT COUNT(*) FROM Customers")
    customers = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM Orders")
    orders = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM OrderItems")
    order_items = cursor.fetchone()[0]

    print("[1] Fixture")
    check("100 customers", customers == 100, str(customers))
    check("1000 orders", orders == 1000, str(orders))
    check("3000 order items", order_items == 3000, str(order_items))

    # ---- join path resolution ----
    print("\n[2] Join path resolution (deterministic, FK-derived)")
    paths, issues = resolve_paths_for_spec(catalog, spec)
    check("no resolution errors", not errors_only(issues),
          "; ".join(i.message for i in errors_only(issues)))
    check("3 paths resolved (Customers self, Orders, OrderItems)",
          len(paths) == 3, str(len(paths)))

    by_source = {p.source_table: p for p in paths}
    check("Orders path is 1 hop", len(by_source["Orders"].hops) == 1)
    check("OrderItems path is 2 hops", len(by_source["OrderItems"].hops) == 2)
    check("OrderItems needs nested aggregation",
          by_source["OrderItems"].requires_nested_aggregation)
    check("paths NOT verified by graph alone",
          all(not p.verified for p in paths if p.hops))

    # ---- verification (simulated: sqlite fixture, not the profiler) ----
    print("\n[3] Verification contract")
    measured = []
    for path in paths:
        hops = [
            h.copy(update={
                "cardinality": "one_to_many",
                "match_rate": 100.0,
                "row_multiplication": True,
            })
            for h in path.hops
        ]
        measured.append(path.copy(update={"hops": hops, "verified": True}))
    spec.join_paths = measured
    check("all paths verified before compile", all(p.verified for p in spec.join_paths))

    # ---- validation ----
    print("\n[4] Validation")
    all_issues = validate_spec(spec, catalog, spec.join_paths)
    errs = errors_only(all_issues)
    check("no validation errors", not errs, "; ".join(e.message for e in errs))
    for issue in all_issues:
        if not issue.is_error:
            print("      warning: %s - %s" % (issue.code, issue.message))

    # ---- compile ----
    print("\n[5] Compile")
    compiler = DatasetCompiler(db_type="mssql", schema="")
    plan = compiler.compile(spec)
    check("SQL starts with WITH", plan.sql.lstrip().upper().startswith("WITH"))
    check("no semicolon", ";" not in plan.sql)
    check("nested CTE emitted (level 2)",
          any(s.level == 2 for s in plan.subqueries))
    check("3 CTEs (orders + 2 nested levels)", len(plan.subqueries) == 3,
          str([s.name for s in plan.subqueries]))
    check("reconciliation queries emitted", len(plan.reconciliation) >= 3,
          str(len(plan.reconciliation)))
    check("lineage for every measure", len(plan.lineage) == 3)

    print("\n--- generated SQL ---")
    print(plan.sql)
    print("--- end SQL ---\n")

    # ---- execute ----
    print("[6] Execution")
    cursor.execute(plan.sql)
    rows = cursor.fetchall()
    columns = [d[0] for d in cursor.description]

    check("output row count = 100 entity rows", len(rows) == 100, str(len(rows)))
    check("no duplicate entity keys",
          len({r[columns.index("CustomerID")] for r in rows}) == len(rows))

    total_sales = sum(r[columns.index("total_sales")] for r in rows)
    total_quantity = sum(r[columns.index("total_quantity")] for r in rows)
    total_orders = sum(r[columns.index("order_count")] for r in rows)

    print("\n[7] Reconciliation (HARD GATE)")
    cursor.execute("SELECT SUM(OrderAmount) FROM Orders")
    expected_sales = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(Quantity) FROM OrderItems")
    expected_quantity = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT CustomerID) FROM Customers")
    expected_rows = cursor.fetchone()[0]

    check("total_sales = 2,701,500", total_sales == 2701500, "{:,}".format(total_sales))
    check("total_sales reconciles to source grain",
          total_sales == expected_sales,
          "output %s vs source %s" % (total_sales, expected_sales))
    check("total_quantity = 18,000", total_quantity == 18000, "{:,}".format(total_quantity))
    check("total_quantity reconciles to source grain",
          total_quantity == expected_quantity,
          "output %s vs source %s" % (total_quantity, expected_quantity))
    check("order_count total = 1000 (no fan-out)", total_orders == 1000, str(total_orders))
    check("row count = distinct entity keys", len(rows) == expected_rows)

    print("\n[8] Fan-out proof")
    cursor.execute(
        "SELECT SUM(o.OrderAmount) FROM Customers c "
        "LEFT JOIN Orders o ON c.CustomerID = o.CustomerID "
        "LEFT JOIN OrderItems oi ON o.OrderID = oi.OrderID"
    )
    naive = cursor.fetchone()[0]
    print("      naive flat join would give: {:,}".format(naive))
    print("      compiler gives:             {:,}".format(total_sales))
    check("compiler avoids the flat-join inflation", total_sales != naive,
          "inflation factor %.1fx avoided" % (naive / float(total_sales)))

    print("\n[9] Lineage")
    for record in plan.lineage:
        print("      %s: %s" % (record.measure_name, record.explanation))
        print("        verified=%s nested=%s cardinality=%s"
              % (record.path_verified, record.nested_aggregation,
                 record.cardinality_per_hop))
    check("OrderItems measure marked nested",
          any(r.nested_aggregation for r in plan.lineage
              if r.measure_name == "total_quantity"))

    print("\n[10] tables_referenced")
    print("      %s" % plan.tables_referenced)
    check("OrderItems present (requested)", "OrderItems" in plan.tables_referenced)

    # ---- unrequested-table proof ----
    print("\n[11] Unrequested measure must not pull in a table")
    lean = build_spec()
    lean.measures = [m for m in lean.measures if m.source_table == "Orders"]
    lean_paths, _ = resolve_paths_for_spec(catalog, lean)
    lean.join_paths = [
        p.copy(update={
            "hops": [h.copy(update={"cardinality": "one_to_many",
                                    "match_rate": 100.0,
                                    "row_multiplication": True})
                     for h in p.hops],
            "verified": True,
        })
        for p in lean_paths
    ]
    lean_plan = DatasetCompiler("mssql", "").compile(lean)
    check("OrderItems absent when not requested",
          "OrderItems" not in lean_plan.tables_referenced and
          "OrderItems" not in lean_plan.sql,
          str(lean_plan.tables_referenced))

    cursor.execute(lean_plan.sql)
    lean_rows = cursor.fetchall()
    lean_cols = [d[0] for d in cursor.description]
    lean_sales = sum(r[lean_cols.index("total_sales")] for r in lean_rows)
    check("lean total_sales still 2,701,500", lean_sales == 2701500,
          "{:,}".format(lean_sales))

    print("\n" + "=" * 62)
    if _failures:
        print("  %d FAILURE(S): %s" % (len(_failures), ", ".join(_failures)))
        return 1
    print("  ALL ACCEPTANCE CHECKS PASSED")
    print("  total_sales    = {:,}".format(total_sales))
    print("  total_quantity = {:,}".format(total_quantity))
    print("  rows           = %d" % len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
