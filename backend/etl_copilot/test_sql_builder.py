from services.sql_builder import (
    build_aggregate_select,
    _assert_extract_safe,
)

sql, params = build_aggregate_select(
    db_type="mssql",
    table="MLTest_LargeOrders",
    group_by=["CustomerID"],
    aggregates=[
        ("total_sales", "SUM", "OrderAmount"),
        ("order_count", "COUNT", "OrderID"),
    ],
    filters=[
        ("OrderAmount", ">", 1000),
    ],
    schema="dbo",
)

assert "GROUP BY" in sql
assert "SUM" in sql
assert "COUNT" in sql
assert "WHERE" in sql
assert ":p0" in sql
assert params == {"p0": 1000}

_assert_extract_safe("WITH test AS (SELECT 1) SELECT * FROM test")

print("SQL builder aggregate test successful")
