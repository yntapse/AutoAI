"""
Unit tests for services/sql_builder.py

No database, no network, no fixtures. Run from backend/:

    python -m pytest services/test_sql_builder.py -v
    python -m services.test_sql_builder        # plain run, no pytest needed
"""

from services.sql_builder import (
    INNER,
    LEFT,
    BuildResult,
    JoinEdge,
    SqlBuildError,
    build_count_select,
    build_select,
    edges_from_dicts,
)

# ---------------------------------------------------------------------------
# Test data - deliberately nasty on purpose
# ---------------------------------------------------------------------------

COLUMNS = {
    "Customers": ["id", "name", "segment", "created_at"],
    "Orders": ["id", "customer_id", "product_id", "total", "created_at"],
    "Products": ["id", "name", "price"],
}

E_ORDERS_CUSTOMERS = JoinEdge("Orders", "customer_id", "Customers", "id", INNER)
E_ORDERS_PRODUCTS = JoinEdge("Orders", "product_id", "Products", "id", INNER)

_failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}   {detail}")
        _failures.append(label)


def expect_error(label, fn, must_contain=None):
    try:
        fn()
        print(f"  FAIL  {label}   (no SqlBuildError raised)")
        _failures.append(label)
    except SqlBuildError as exc:
        if must_contain and must_contain.lower() not in str(exc).lower():
            print(f"  FAIL  {label}   (message missing '{must_contain}'): {exc}")
            _failures.append(label)
        else:
            print(f"  PASS  {label}   -> {exc}")
    except Exception as exc:
        print(f"  FAIL  {label}   (wrong type {type(exc).__name__}): {exc}")
        _failures.append(label)


# ---------------------------------------------------------------------------
# 1. Duplicate column names
# ---------------------------------------------------------------------------


def test_duplicate_column_aliasing():
    print("\n[1] Duplicate column names across tables")
    r = build_select(
        db_type="postgresql",
        base_table="Customers",
        tables=["Customers", "Orders"],
        relationships=[E_ORDERS_CUSTOMERS],
        columns_by_table={k: COLUMNS[k] for k in ("Customers", "Orders")},
    )
    names = r.column_names
    print(f"        aliases: {names}")

    check("all aliases unique", len(names) == len(set(names)), names)
    check("id collision prefixed", "customers_id" in names and "orders_id" in names)
    check(
        "created_at collision prefixed",
        "customers_created_at" in names and "orders_created_at" in names,
    )
    check("unique name kept bare", "segment" in names and "total" in names)
    check("no bare 'id'", "id" not in names)
    check("alias_map resolves", r.alias_map["customers_id"] == "Customers.id")
    check(
        "column count matches input",
        len(names) == len(COLUMNS["Customers"]) + len(COLUMNS["Orders"]),
    )


def test_three_way_aliasing():
    print("\n[1b] Three tables, 'name' collides in two of them")
    r = build_select(
        db_type="postgresql",
        base_table="Orders",
        tables=["Orders", "Customers", "Products"],
        relationships=[E_ORDERS_CUSTOMERS, E_ORDERS_PRODUCTS],
        columns_by_table=COLUMNS,
    )
    names = r.column_names
    print(f"        aliases: {names}")
    check("all unique", len(names) == len(set(names)), names)
    check("customers_name present", "customers_name" in names)
    check("products_name present", "products_name" in names)
    check("price stays bare", "price" in names)
    check("three table aliases", set(r.table_aliases.values()) == {"t0", "t1", "t2"})
    check("base is t0", r.table_aliases["Orders"] == "t0")


# ---------------------------------------------------------------------------
# 2. Awkward identifiers
# ---------------------------------------------------------------------------


def test_awkward_identifiers_mssql():
    print("\n[2] Spaces and reserved words - SQL Server")
    cols = {
        "Order Details": ["Order ID", "order", "Unit Price"],
        "Products": ["id", "name"],
    }
    edge = JoinEdge("Order Details", "order", "Products", "id", INNER)
    r = build_select(
        db_type="mssql",
        base_table="Order Details",
        tables=["Order Details", "Products"],
        relationships=[edge],
        columns_by_table=cols,
        schema="dbo",
    )
    print("        " + r.sql.replace("\n", "\n        "))
    check("bracket-quoted table", "[dbo].[Order Details]" in r.sql)
    check("bracket-quoted column", "[Unit Price]" in r.sql)
    check("space -> underscore in alias", "unit_price" in r.column_names)
    check("reserved word aliased safely", "order" in r.column_names)
    check("no double-space alias", "__" not in "".join(r.column_names))


def test_awkward_identifiers_postgres():
    print("\n[2b] Same shape - PostgreSQL quoting")
    cols = {"order details": ["order id", "order"], "products": ["id"]}
    edge = JoinEdge("order details", "order", "products", "id", INNER)
    r = build_select(
        db_type="postgresql",
        base_table="order details",
        tables=["order details", "products"],
        relationships=[edge],
        columns_by_table=cols,
        schema="public",
    )
    print("        " + r.sql.replace("\n", "\n        "))
    check("double-quoted table", '"public"."order details"' in r.sql)
    check("double-quoted column", '"order id"' in r.sql)
    check("no brackets in pg output", "[" not in r.sql)


def test_alias_edge_cases():
    print("\n[2c] Alias sanitisation edge cases")
    cols = {"T": ["2024_total", "!!!", "a-b-c"], "U": ["id"]}
    edge = JoinEdge("T", "id", "U", "id", INNER)
    # T has no 'id' column -> should be rejected
    expect_error(
        "column existence enforced",
        lambda: build_select("postgresql", "T", ["T", "U"], [edge], cols),
        "does not exist",
    )

    cols2 = {"T": ["2024_total", "!!!", "a-b-c", "id"], "U": ["id"]}
    r = build_select("postgresql", "T", ["T", "U"], [edge], cols2)
    names = r.column_names
    print(f"        aliases: {names}")
    check("leading digit prefixed", "c_2024_total" in names)
    check("symbol-only -> col", "col" in names)
    check("hyphens -> underscores", "a_b_c" in names)
    check("all unique", len(names) == len(set(names)))


# ---------------------------------------------------------------------------
# 3. Dialect row limiting
# ---------------------------------------------------------------------------


def test_limit_dialects():
    print("\n[3] TOP vs LIMIT")
    args = dict(
        base_table="Customers",
        tables=["Customers", "Orders"],
        relationships=[E_ORDERS_CUSTOMERS],
        columns_by_table={k: COLUMNS[k] for k in ("Customers", "Orders")},
    )
    ms = build_select(db_type="mssql", limit=20, **args)
    pg = build_select(db_type="postgresql", limit=20, **args)

    check("mssql uses TOP", ms.sql.startswith("SELECT TOP 20 "))
    check("mssql has no LIMIT", "LIMIT" not in ms.sql.upper())
    check("pg ends with LIMIT 20", pg.sql.rstrip().endswith("LIMIT 20"))
    check("pg has no TOP", not pg.sql.upper().startswith("SELECT TOP"))

    none_limit = build_select(db_type="mssql", **args)
    check("no limit -> no TOP", "TOP" not in none_limit.sql.upper())
    expect_error(
        "zero limit rejected", lambda: build_select(db_type="mssql", limit=0, **args)
    )


# ---------------------------------------------------------------------------
# 4. Graph validation
# ---------------------------------------------------------------------------


def test_disconnected_graph():
    print("\n[4] Disconnected graph")
    expect_error(
        "third table unreachable",
        lambda: build_select(
            "postgresql",
            "Customers",
            ["Customers", "Orders", "Products"],
            [E_ORDERS_CUSTOMERS],
            COLUMNS,
        ),
        "not connected",
    )


def test_cycle():
    print("\n[5] Cycle detection")
    cyclic = JoinEdge("Customers", "segment", "Products", "name", INNER)
    expect_error(
        "A-B-C-A rejected",
        lambda: build_select(
            "postgresql",
            "Customers",
            ["Customers", "Orders", "Products"],
            [E_ORDERS_CUSTOMERS, E_ORDERS_PRODUCTS, cyclic],
            COLUMNS,
        ),
        "loop",
    )


def test_other_validation():
    print("\n[6] Remaining validation rules")
    cols2 = {k: COLUMNS[k] for k in ("Customers", "Orders")}

    expect_error(
        "base table not selected",
        lambda: build_select(
            "postgresql",
            "Products",
            ["Customers", "Orders"],
            [E_ORDERS_CUSTOMERS],
            cols2,
        ),
        "base table",
    )

    expect_error(
        "edge endpoint not selected",
        lambda: build_select(
            "postgresql",
            "Customers",
            ["Customers", "Orders"],
            [E_ORDERS_CUSTOMERS, E_ORDERS_PRODUCTS],
            cols2,
        ),
        "not one of the selected",
    )

    expect_error(
        "self-join rejected",
        lambda: build_select(
            "postgresql",
            "Customers",
            ["Customers", "Orders"],
            [JoinEdge("Orders", "id", "Orders", "id")],
            cols2,
        ),
        "itself",
    )

    expect_error(
        "duplicate edge rejected",
        lambda: build_select(
            "postgresql",
            "Customers",
            ["Customers", "Orders"],
            [E_ORDERS_CUSTOMERS, E_ORDERS_CUSTOMERS],
            cols2,
        ),
        "more than once",
    )

    expect_error(
        "duplicate table rejected",
        lambda: build_select(
            "postgresql",
            "Customers",
            ["Customers", "Customers"],
            [],
            {"Customers": ["id"]},
        ),
        "more than once",
    )

    expect_error(
        "max 3 tables enforced",
        lambda: build_select(
            "postgresql", "A", ["A", "B", "C", "D"], [], {"A": ["id"]}
        ),
        "maximum of 3",
    )

    expect_error(
        "bad dialect rejected",
        lambda: build_select(
            "oracle", "Customers", ["Customers"], [], {"Customers": ["id"]}
        ),
        "unsupported database type",
    )

    expect_error(
        "bad join type rejected",
        lambda: build_select(
            "postgresql",
            "Customers",
            ["Customers", "Orders"],
            [JoinEdge("Orders", "customer_id", "Customers", "id", "cross")],
            cols2,
        ),
        "unsupported join type",
    )

    expect_error(
        "control char in identifier rejected",
        lambda: build_select(
            "postgresql",
            "Cust\x00omers",
            ["Cust\x00omers"],
            [],
            {"Cust\x00omers": ["id"]},
        ),
        "control characters",
    )


# ---------------------------------------------------------------------------
# 7. Join ordering
# ---------------------------------------------------------------------------


def test_join_order_independence():
    print("\n[7] Join order and edge-direction independence")
    forward = build_select(
        "postgresql",
        "Customers",
        ["Customers", "Orders", "Products"],
        [E_ORDERS_CUSTOMERS, E_ORDERS_PRODUCTS],
        COLUMNS,
    )
    # Same graph, edges reversed and listed in the opposite order.
    reversed_edges = [
        JoinEdge("Products", "id", "Orders", "product_id", INNER),
        JoinEdge("Customers", "id", "Orders", "customer_id", INNER),
    ]
    backward = build_select(
        "postgresql",
        "Customers",
        ["Customers", "Orders", "Products"],
        reversed_edges,
        COLUMNS,
    )
    check(
        "edge direction does not change SQL",
        forward.sql == backward.sql,
        "\n" + forward.sql + "\n---\n" + backward.sql,
    )
    check("base table is t0", forward.table_aliases["Customers"] == "t0")
    check("2 edges planned", len(forward.join_order) == 2)
    check(
        "every ON references an existing alias",
        all(
            e.left_table in {"Customers", "Orders", "Products"}
            for e in forward.join_order
        ),
    )

    # Base table listed last must still be t0.
    last = build_select(
        "postgresql",
        "Products",
        ["Customers", "Orders", "Products"],
        [E_ORDERS_CUSTOMERS, E_ORDERS_PRODUCTS],
        COLUMNS,
    )
    check("base listed last still t0", last.table_aliases["Products"] == "t0")
    check("FROM names the base table", 'FROM "public"."Products" AS t0' in last.sql)


def test_left_join():
    print("\n[7b] LEFT JOIN emission")
    r = build_select(
        "postgresql",
        "Customers",
        ["Customers", "Orders"],
        [JoinEdge("Orders", "customer_id", "Customers", "id", LEFT)],
        {k: COLUMNS[k] for k in ("Customers", "Orders")},
    )
    print("        " + r.sql.replace("\n", "\n        "))
    check("LEFT JOIN emitted", "LEFT JOIN" in r.sql)
    check("new table is the joined one", '"public"."Orders" AS t1' in r.sql)


# ---------------------------------------------------------------------------
# 8. _validate_query compatibility
# ---------------------------------------------------------------------------


def test_validate_query_compatibility():
    print("\n[8] Compatibility with db_connector._validate_query")
    import re

    allowed_start = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)

    cases = []
    for dialect in ("mssql", "postgresql"):
        for limit in (None, 20, 50000):
            cases.append(
                build_select(
                    dialect,
                    "Orders",
                    ["Orders", "Customers", "Products"],
                    [E_ORDERS_CUSTOMERS, E_ORDERS_PRODUCTS],
                    COLUMNS,
                    limit=limit,
                ).sql
            )
        cases.append(
            build_count_select(
                dialect,
                "Orders",
                ["Orders", "Customers", "Products"],
                [E_ORDERS_CUSTOMERS, E_ORDERS_PRODUCTS],
                COLUMNS,
            )
        )

    check("no semicolons", all(";" not in s for s in cases))
    check("all start with SELECT", all(allowed_start.match(s) for s in cases))
    check(f"{len(cases)} statements checked", len(cases) == 8)


def test_count_select():
    print("\n[9] build_count_select")
    sql = build_count_select(
        "postgresql",
        "Customers",
        ["Customers", "Orders"],
        [E_ORDERS_CUSTOMERS],
        {k: COLUMNS[k] for k in ("Customers", "Orders")},
    )
    print("        " + sql.replace("\n", "\n        "))
    check("COUNT(*) present", "COUNT(*)" in sql)
    check("aliased row_count", '"row_count"' in sql)
    check("same join chain", "INNER JOIN" in sql and "ON t0." in sql)
    check("projects no columns", ' AS "segment"' not in sql)


def test_single_table():
    print("\n[10] Single table, no relationships")
    r = build_select(
        "mssql",
        "Customers",
        ["Customers"],
        [],
        {"Customers": COLUMNS["Customers"]},
        schema="dbo",
        limit=20,
    )
    print("        " + r.sql.replace("\n", "\n        "))
    check("no JOIN emitted", "JOIN" not in r.sql)
    check("aliases stay bare", "segment" in r.column_names)
    check("TOP applied", r.sql.startswith("SELECT TOP 20 "))


def test_edges_from_dicts():
    print("\n[11] edges_from_dicts")
    edges = edges_from_dicts(
        [
            {
                "left_table": "Orders",
                "left_column": "customer_id",
                "right_table": "Customers",
                "right_column": "id",
            },
        ]
    )
    check("defaults to inner", edges[0].join_type == INNER)
    check("returns JoinEdge", isinstance(edges[0], JoinEdge))
    expect_error(
        "missing field rejected",
        lambda: edges_from_dicts([{"left_table": "Orders"}]),
        "missing required fields",
    )
    check("empty list ok", edges_from_dicts([]) == [])


# ---------------------------------------------------------------------------

ALL = [
    test_duplicate_column_aliasing,
    test_three_way_aliasing,
    test_awkward_identifiers_mssql,
    test_awkward_identifiers_postgres,
    test_alias_edge_cases,
    test_limit_dialects,
    test_disconnected_graph,
    test_cycle,
    test_other_validation,
    test_join_order_independence,
    test_left_join,
    test_validate_query_compatibility,
    test_count_select,
    test_single_table,
    test_edges_from_dicts,
]

if __name__ == "__main__":
    for t in ALL:
        t()
    print("\n" + "=" * 62)
    if _failures:
        print(f"  {len(_failures)} FAILURE(S): " + ", ".join(_failures))
        raise SystemExit(1)
    print("  ALL CHECKS PASSED")
    print("=" * 62)
