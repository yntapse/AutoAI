"""Live schema read: proves Catalog construction and FK discovery.

Run one of these from backend/ :

    set ETL_TEST_CONNECTION=mssql+pyodbc://automl:PASS@localhost:53199/AutoMLDB?driver=ODBC+Driver+17+for+SQL+Server
    python -m etl_copilot.test_schema_reader

or let it build the connection string for you:

    set ETL_TEST_PASSWORD=yourpassword
    python -m etl_copilot.test_schema_reader

With neither set it prompts for the password interactively.
"""

import os
import sys

from etl_copilot.schema_reader import SchemaReader
from services.db_connector import DataSourceError, build_connection_string

HOST = os.getenv("ETL_TEST_HOST", "localhost")
PORT = int(os.getenv("ETL_TEST_PORT", "53199"))
DATABASE = os.getenv("ETL_TEST_DATABASE", "AutoMLDB")
USERNAME = os.getenv("ETL_TEST_USER", "automl")
SCHEMA = os.getenv("ETL_TEST_SCHEMA", "dbo")

_failures = []


def check(label, condition, detail=""):
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", label,
                           (" - " + detail) if detail else ""))
    if not condition:
        _failures.append(label)


def connection_string():
    explicit = os.getenv("ETL_TEST_CONNECTION")
    if explicit:
        print("using ETL_TEST_CONNECTION")
        return explicit

    password = os.getenv("ETL_TEST_PASSWORD")
    if not password:
        if not sys.stdin.isatty():
            print("SKIP: set ETL_TEST_CONNECTION or ETL_TEST_PASSWORD "
                  "(no terminal available to prompt).")
            sys.exit(0)
        password = input("Enter SQL Server password for %s: " % USERNAME).strip()

    print("building connection string for %s@%s:%d/%s"
          % (USERNAME, HOST, PORT, DATABASE))

    return build_connection_string(
        db_type="mssql",
        host=HOST,
        database=DATABASE,
        port=PORT,
        username=USERNAME,
        password=password,
        trust_server_certificate=True,
    )


def main():
    print("=== live schema read ===\n")

    try:
        catalog = SchemaReader().get_schema(
            connection_string=connection_string(),
            schema=SCHEMA,
        )
    except DataSourceError as exc:
        print("\nDATABASE ERROR: %s" % exc)
        return 1

    print("\n[1] Catalog")
    check("catalog built", catalog is not None)
    check("db_type is mssql", catalog.db_type == "mssql", catalog.db_type)
    check("schema recorded", catalog.schema_name == SCHEMA, str(catalog.schema_name))
    check("tables discovered", len(catalog.tables) > 0, "%d tables" % len(catalog.tables))

    print("\n[2] Tables and columns")
    for tbl in catalog.tables:
        keys = [c.name for c in tbl.columns if c.primary_key]
        print("  %s  (%d columns, PK=%s)"
              % (tbl.name, len(tbl.columns), ", ".join(keys) or "none"))
        for col in tbl.columns:
            print("      %-22s %-16s %s%s"
                  % (col.name, col.type,
                     "NULL" if col.nullable else "NOT NULL",
                     "  PK" if col.primary_key else ""))

    print("\n[3] Foreign-key relationships (the blocker)")
    check("relationships discovered", len(catalog.relationships) > 0,
          "%d found" % len(catalog.relationships))

    for rel in catalog.relationships:
        print("  %s.%s -> %s.%s"
              % (rel.left_table, rel.left_column, rel.right_table, rel.right_column))

    print("\n[4] Relationship contract")
    check("relationships are JoinHop (no 'verified' field)",
          all(not hasattr(r, "verified") for r in catalog.relationships))
    check("measurement slots start empty",
          all(r.cardinality is None and r.match_rate is None
              and r.row_multiplication is None for r in catalog.relationships),
          "only join_verifier may fill these")

    print("\n[5] Join-path resolution over the real catalog")
    from etl_copilot.join_graph import build_fk_graph

    graph = build_fk_graph(catalog)
    print("  graph nodes: %d" % len(graph))
    for node in sorted(graph):
        print("    %-28s -> %s"
              % (node, ", ".join(sorted({e[0] for e in graph[node]}))))

    check("graph is non-empty", len(graph) > 0,
          "an empty graph means no declared foreign keys")

    print("\n" + "=" * 62)
    if _failures:
        print("  %d FAILURE(S): %s" % (len(_failures), ", ".join(_failures)))
        return 1
    print("  LIVE SCHEMA READ PASSED")
    print("  %d tables, %d relationships"
          % (len(catalog.tables), len(catalog.relationships)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
