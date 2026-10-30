"""Offline end-to-end pipeline test: stub LLM, no database.

Proves the Copilot wiring (spec -> paths -> verify -> validate -> compile)
without an API key or a live connection. The LLM is replaced by a stub that
returns the JSON a real call would return; the verifier is replaced by one
that reports measured statistics. Execution is covered by test_acceptance.
"""

import sys

from etl_copilot.copilot_service import ETLCopilot, catalog_for_llm
from etl_copilot.spec_models import Catalog, ColumnInfo, JoinHop, TableInfo

_failures = []


def check(label, condition, detail=""):
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", label,
                           (" - " + detail) if detail else ""))
    if not condition:
        _failures.append(label)


class StubGenerator(object):
    """Returns the shape a real OpenAI call returns. Records what it was sent."""

    def __init__(self):
        self.last_payload = None

    def generate_spec(self, user_request, schema):
        self.last_payload = schema
        return {
            "entity_table": "Customers",
            "entity_key": "CustomerID",
            "attributes": ["CustomerName", "City"],
            "measures": [
                {"measure_name": "total_sales", "source_table": "Orders",
                 "source_column": "OrderAmount", "aggregation": "SUM"},
                {"measure_name": "order_count", "source_table": "Orders",
                 "source_column": "OrderID", "aggregation": "COUNT_DISTINCT"},
            ],
            "filters": [],
        }

    def fix_spec(self, **kwargs):
        raise AssertionError("fix_spec should not be needed for a valid spec")


class StubVerifier(object):
    def verify_all(self, connection_string, db_type, schema, paths):
        verified = []
        for path in paths:
            hops = [h.copy(update={"cardinality": "one_to_many",
                                   "match_rate": 100.0,
                                   "row_multiplication": True})
                    for h in path.hops]
            verified.append(path.copy(update={"hops": hops, "verified": True}))
        return verified, []


def catalog():
    return Catalog(
        db_type="mssql",
        schema_name="",
        tables=[
            TableInfo(name="Customers", columns=[
                ColumnInfo(name="CustomerID", type="INTEGER", primary_key=True),
                ColumnInfo(name="CustomerName", type="VARCHAR(100)"),
                ColumnInfo(name="City", type="VARCHAR(50)"),
            ]),
            TableInfo(name="Orders", columns=[
                ColumnInfo(name="OrderID", type="INTEGER", primary_key=True),
                ColumnInfo(name="CustomerID", type="INTEGER"),
                ColumnInfo(name="OrderAmount", type="DECIMAL(10,2)"),
            ]),
        ],
        relationships=[
            JoinHop(left_table="Customers", left_column="CustomerID",
                    right_table="Orders", right_column="CustomerID"),
        ],
    )


def main():
    print("=== ETL Copilot offline pipeline test ===\n")

    cat = catalog()
    generator = StubGenerator()
    copilot = ETLCopilot(generator=generator)
    copilot.verifier = StubVerifier()

    print("[1] LLM payload is sanitised metadata only")
    payload = catalog_for_llm(cat)
    flat = repr(payload)
    check("tables present", "Customers" in flat and "Orders" in flat)
    check("primary keys present", "primary_keys" in payload)
    check("no row values leak", "Customer 1" not in flat and "City 3" not in flat)
    check("payload keys are exactly the 3 expected",
          sorted(payload.keys()) == ["primary_keys", "relationships", "tables"],
          str(sorted(payload.keys())))

    print("\n[2] spec generation and parsing")
    spec = copilot.generate_spec("customer sales", cat)
    check("entity_key normalised to a list", spec.entity_key == ["CustomerID"])
    check("string attributes normalised", len(spec.attributes) == 2)
    check("attributes carry source_table",
          all(a.source_table == "Customers" for a in spec.attributes))
    check("db_type inherited from catalog", spec.db_type == "mssql")

    print("\n[3] path resolution + verification")
    issues = copilot.resolve_and_verify("stub://connection", cat, spec)
    check("no blocking issues", not [i for i in issues if i.is_error],
          "; ".join(i.message for i in issues if i.is_error))
    check("paths verified", all(p.verified for p in spec.join_paths))

    print("\n[4] compile")
    plan = copilot.compile_spec(spec)
    check("SQL emitted", bool(plan.sql))
    check("one CTE for Orders", len(plan.subqueries) == 1,
          str([s.name for s in plan.subqueries]))
    check("COALESCE on SUM", "COALESCE" in plan.sql)
    check("COUNT(DISTINCT) emitted", "COUNT(DISTINCT" in plan.sql)
    check("lineage for both measures", len(plan.lineage) == 2)
    check("reconciliation queries emitted", len(plan.reconciliation) >= 2)

    print("\n--- SQL ---")
    print(plan.sql)

    print("\n" + "=" * 62)
    if _failures:
        print("  %d FAILURE(S): %s" % (len(_failures), ", ".join(_failures)))
        return 1
    print("  OFFLINE PIPELINE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
