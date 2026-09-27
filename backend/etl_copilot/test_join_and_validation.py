"""Unit tests for join_graph and spec_validator. No database, no LLM."""

import sys

from etl_copilot.join_graph import (
    MAX_HOPS,
    build_fk_graph,
    find_all_shortest_paths,
    resolve_join_path,
)
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
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", label,
                           (" - " + detail) if detail else ""))
    if not condition:
        _failures.append(label)


def table(name, columns):
    return TableInfo(
        name=name,
        columns=[ColumnInfo(name=c, type=t, primary_key=pk)
                 for c, t, pk in columns],
    )


def chain_catalog():
    return Catalog(
        db_type="mssql",
        tables=[
            table("Customers", [("CustomerID", "INTEGER", True), ("City", "VARCHAR(50)", False)]),
            table("Orders", [("OrderID", "INTEGER", True), ("CustomerID", "INTEGER", False),
                             ("OrderAmount", "DECIMAL(10,2)", False)]),
            table("OrderItems", [("OrderItemID", "INTEGER", True), ("OrderID", "INTEGER", False),
                                 ("Quantity", "INTEGER", False)]),
        ],
        relationships=[
            JoinHop(left_table="Customers", left_column="CustomerID",
                    right_table="Orders", right_column="CustomerID"),
            JoinHop(left_table="Orders", left_column="OrderID",
                    right_table="OrderItems", right_column="OrderID"),
        ],
    )


def test_graph_is_undirected():
    print("\n[1] build_fk_graph")
    graph = build_fk_graph(chain_catalog())
    check("Customers -> Orders edge", any(e[0] == "Orders" for e in graph["Customers"]))
    check("Orders -> Customers edge", any(e[0] == "Customers" for e in graph["Orders"]))
    check("Orders -> OrderItems edge", any(e[0] == "OrderItems" for e in graph["Orders"]))


def test_multi_hop():
    print("\n[2] multi-hop path")
    catalog = chain_catalog()
    paths = find_all_shortest_paths(catalog, "Customers", "OrderItems")
    check("exactly one path", len(paths) == 1, str(len(paths)))
    check("two hops", len(paths[0]) == 2)
    check("hop order Customers->Orders->OrderItems",
          paths[0][0].right_table == "Orders" and paths[0][1].right_table == "OrderItems")


def test_ambiguity_detected():
    print("\n[3] ambiguity (the old visited-set bug)")
    catalog = Catalog(
        db_type="mssql",
        tables=[table("Customers", [("CustomerID", "INTEGER", True)]),
                table("Orders", [("OrderID", "INTEGER", True), ("CustomerID", "INTEGER", False)]),
                table("Invoices", [("InvoiceID", "INTEGER", True), ("CustomerID", "INTEGER", False)]),
                table("Payments", [("PaymentID", "INTEGER", True), ("OrderID", "INTEGER", False),
                                   ("InvoiceID", "INTEGER", False), ("Amount", "DECIMAL(10,2)", False)])],
        relationships=[
            JoinHop(left_table="Customers", left_column="CustomerID",
                    right_table="Orders", right_column="CustomerID"),
            JoinHop(left_table="Customers", left_column="CustomerID",
                    right_table="Invoices", right_column="CustomerID"),
            JoinHop(left_table="Orders", left_column="OrderID",
                    right_table="Payments", right_column="OrderID"),
            JoinHop(left_table="Invoices", left_column="InvoiceID",
                    right_table="Payments", right_column="InvoiceID"),
        ],
    )
    paths = find_all_shortest_paths(catalog, "Customers", "Payments")
    check("two equal-length routes found", len(paths) == 2, str(len(paths)))

    path, issues = resolve_join_path(catalog, "Customers", "Payments")
    check("resolve refuses to pick", path is None)
    check("issue code is ambiguous_join_path",
          any(i.code == "ambiguous_join_path" for i in issues))


def test_no_path():
    print("\n[4] no path")
    catalog = Catalog(
        db_type="mssql",
        tables=[table("Customers", [("CustomerID", "INTEGER", True)]),
                table("Products", [("ProductID", "INTEGER", True)])],
        relationships=[],
    )
    path, issues = resolve_join_path(catalog, "Customers", "Products")
    check("no path returned", path is None)
    check("issue code is no_join_path", any(i.code == "no_join_path" for i in issues))
    check("never raises", True)


def test_cycle_terminates():
    print("\n[5] cycle does not hang")
    catalog = Catalog(
        db_type="mssql",
        tables=[table("A", [("id", "INTEGER", True)]),
                table("B", [("id", "INTEGER", True)]),
                table("C", [("id", "INTEGER", True)])],
        relationships=[
            JoinHop(left_table="A", left_column="id", right_table="B", right_column="id"),
            JoinHop(left_table="B", left_column="id", right_table="C", right_column="id"),
            JoinHop(left_table="C", left_column="id", right_table="A", right_column="id"),
        ],
    )
    paths = find_all_shortest_paths(catalog, "A", "C")
    check("terminates and finds direct hop", len(paths) >= 1 and len(paths[0]) == 1)


def test_verified_never_set_by_graph():
    print("\n[6] graph never sets verified")
    path, issues = resolve_join_path(chain_catalog(), "Customers", "OrderItems")
    check("path found", path is not None)
    check("verified is False", path is not None and path.verified is False)
    check("discovered_via is foreign_key", path.discovered_via == "foreign_key")


def base_spec():
    return DatasetSpec(
        entity_table="Customers",
        entity_key=["CustomerID"],
        attributes=[AttributeSpec(source_table="Customers", source_column="City", name="City")],
        measures=[MeasureSpec(measure_name="total_sales", source_table="Orders",
                              source_column="OrderAmount", aggregation="SUM")],
    )


def test_validator_catches_hallucinations():
    print("\n[7] validator")
    catalog = chain_catalog()

    spec = base_spec()
    spec.attributes.append(
        AttributeSpec(source_table="Customers", source_column="CustomerSegment", name="Seg")
    )
    issues = validate_spec(spec, catalog, [])
    check("hallucinated ATTRIBUTE rejected",
          any(i.code == "unknown_column" for i in errors_only(issues)))

    spec = base_spec()
    spec.measures.append(
        MeasureSpec(measure_name="bad", source_table="Orders",
                    source_column="NotAColumn", aggregation="SUM")
    )
    issues = validate_spec(spec, catalog, [])
    check("hallucinated MEASURE column rejected",
          any(i.code == "unknown_column" for i in errors_only(issues)))

    spec = base_spec()
    spec.measures = [MeasureSpec(measure_name="bad_sum", source_table="Customers",
                                 source_column="City", aggregation="SUM")]
    issues = validate_spec(spec, catalog, [])
    check("SUM on VARCHAR rejected",
          any(i.code == "aggregation_type_mismatch" for i in errors_only(issues)))

    spec = base_spec()
    path, _ = resolve_join_path(catalog, "Customers", "Orders")
    issues = validate_spec(spec, catalog, [path])
    check("unverified path rejected",
          any(i.code == "unverified_join_path" for i in errors_only(issues)))

    spec = base_spec()
    spec.measures[0].distinct_key = ["CustomerID"]
    issues = validate_spec(spec, catalog, [])
    check("distinct_key == entity_key rejected",
          any(i.code == "distinct_key_is_entity_key" for i in errors_only(issues)))

    spec = base_spec()
    issues = validate_spec(spec, catalog, [])
    check("missing path for Orders is an error",
          any(i.code == "missing_join_path" for i in errors_only(issues)))

    spec = base_spec()
    verified = path.copy(update={
        "hops": [h.copy(update={"cardinality": "one_to_many", "match_rate": 100.0,
                                "row_multiplication": True}) for h in path.hops],
        "verified": True,
    })
    issues = validate_spec(spec, catalog, [verified])
    check("clean spec has no errors", not errors_only(issues),
          "; ".join(i.message for i in errors_only(issues)))


def test_catalog_relationship_contract():
    """Regression: Catalog.relationships must be JoinHop, never JoinSpec.

    schema_reader once built JoinSpec here. JoinSpec carries `verified`, and
    JoinHop forbids extra fields, so pydantic coercion failed with
    "relationships -> 0 -> verified  extra fields not permitted".

    It was invisible until real foreign keys existed, because an empty
    relationship list has nothing to coerce.
    """
    print("\n[8] Catalog relationship contract (regression)")

    from etl_copilot.schema_reader import SchemaReader
    from etl_copilot.spec_models import JoinSpec
    from pydantic import ValidationError

    hop = JoinHop(left_table="Orders", left_column="CustomerID",
                  right_table="Customers", right_column="CustomerID")
    catalog = Catalog(db_type="mssql", tables=[], relationships=[hop])
    check("JoinHop is accepted", len(catalog.relationships) == 1)
    check("no 'verified' on a catalog edge",
          not hasattr(catalog.relationships[0], "verified"))
    check("measurement slots start empty",
          catalog.relationships[0].cardinality is None
          and catalog.relationships[0].row_multiplication is None)

    legacy = JoinSpec(left_table="Orders", left_column="CustomerID",
                      right_table="Customers", right_column="CustomerID")
    rejected = False
    try:
        Catalog(db_type="mssql", tables=[], relationships=[legacy])
    except ValidationError as exc:
        rejected = "verified" in str(exc)
    check("JoinSpec is rejected (the original error)", rejected)

    # And the source of the original bug: schema_reader must not build JoinSpec.
    import inspect
    source = inspect.getsource(SchemaReader.get_schema)
    check("schema_reader builds JoinHop", "JoinHop(" in source)
    check("schema_reader does not build JoinSpec", "JoinSpec(" not in source)


ALL = [
    test_graph_is_undirected,
    test_multi_hop,
    test_ambiguity_detected,
    test_no_path,
    test_cycle_terminates,
    test_verified_never_set_by_graph,
    test_validator_catches_hallucinations,
    test_catalog_relationship_contract,
]


def main():
    print("=== join_graph + spec_validator unit tests (MAX_HOPS=%d) ===" % MAX_HOPS)
    for test in ALL:
        test()
    print("\n" + "=" * 62)
    if _failures:
        print("  %d FAILURE(S): %s" % (len(_failures), ", ".join(_failures)))
        return 1
    print("  ALL UNIT TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
