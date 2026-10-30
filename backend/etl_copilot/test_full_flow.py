"""Live end-to-end flow. Requires a real database AND an OpenAI key.

Run on the developer machine:

    set ETL_TEST_CONNECTION=mssql+pyodbc://...
    set OPENAI_API_KEY=...
    python -m etl_copilot.test_full_flow
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

CONNECTION = os.getenv("ETL_TEST_CONNECTION")
SCHEMA = os.getenv("ETL_TEST_SCHEMA", "dbo")

# The default request assumes a customer/order shape. A schema whose
# columns are named differently needs a request phrased in its own terms,
# so it is overridable without editing this file.
REQUEST = os.getenv(
    "ETL_TEST_REQUEST",
    "Show customer name, city, total sales and order count",
)

# One output row per entity, so a wide entity table can exceed the 50,000
# platform cap legitimately. Raise this if the run stops with "returned
# more than 50,000 rows".
MAX_ROWS = os.getenv("ETL_TEST_MAX_ROWS")

if not CONNECTION:
    print("SKIP: set ETL_TEST_CONNECTION to run the live end-to-end flow.")
    sys.exit(0)

if not os.getenv("OPENAI_API_KEY"):
    print("SKIP: set OPENAI_API_KEY to run the live end-to-end flow.")
    sys.exit(0)

from etl_copilot.build_service import BuildError, BuildService
from etl_copilot.copilot_service import CopilotError, ETLCopilot
from etl_copilot.reconciler import ReconciliationError
from services.db_connector import DataSourceError
    

def main():
    build_service = None
    if MAX_ROWS:
        build_service = BuildService(max_rows=int(MAX_ROWS))

    copilot = ETLCopilot(build_service=build_service)

    request = REQUEST

    print("=== live ETL Copilot flow ===")
    print("request: %s" % request)
    print("schema:  %s\n" % SCHEMA)

    try:
        result = copilot.create_dataset(
            user_request=request,
            connection_string=CONNECTION,
            schema=SCHEMA,
            dataset_name="acceptance",
        )
    except (CopilotError, BuildError, ReconciliationError,
            DataSourceError) as exc:  # noqa: B014
        # These are the four ways the pipeline refuses to produce a dataset.
        # All four are expected outcomes, not crashes, so the flow reports
        # them instead of raising a traceback that hides which stage stopped.
        print("BLOCKED (%s):\n%s" % (type(exc).__name__, exc))
        return 1

    print("spec chose entity table: %s" % result["spec"]["entity_table"])
    print("spec chose measures:     %s"
          % [(m["name"], m["source_table"], m["source_column"],
              m["aggregation"]) for m in result["spec"]["measures"]])
    print()
    print("SQL:\n%s\n" % result["sql"])
    print("rows:        %d" % result["row_count"])
    print("columns:     %s" % result["columns"])
    print("file_id:     %s" % result["file_id"])
    print("automl_ready: %s" % result["automl_ready"])

    print("\nreconciliation:")
    for check in result["reconciliation"]["checks"]:
        print("  [%s] %s: expected %s, got %s"
              % ("PASS" if check["passed"] else "FAIL", check["check"],
                 check["expected"], check["actual"]))

    print("\nexplanation:")
    for line in result["explanation"]:
        print("  %s" % line)

    if result["warnings"]:
        print("\nwarnings:")
        for warning in result["warnings"]:
            print("  %s: %s" % (warning["code"], warning["message"]))

    return 0 if result["reconciliation"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
