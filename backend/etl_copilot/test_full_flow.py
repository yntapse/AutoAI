"""Live end-to-end flow. Requires a real database AND an OpenAI key.

Run on the developer machine:

    set ETL_TEST_CONNECTION=mssql+pyodbc://...
    set OPENAI_API_KEY=...
    python -m etl_copilot.test_full_flow
"""

import os
import sys

CONNECTION = os.getenv("ETL_TEST_CONNECTION")
SCHEMA = os.getenv("ETL_TEST_SCHEMA", "dbo")

if not CONNECTION:
    print("SKIP: set ETL_TEST_CONNECTION to run the live end-to-end flow.")
    sys.exit(0)

if not os.getenv("OPENAI_API_KEY"):
    print("SKIP: set OPENAI_API_KEY to run the live end-to-end flow.")
    sys.exit(0)

from etl_copilot.copilot_service import CopilotError, ETLCopilot


def main():
    copilot = ETLCopilot()

    request = "Show customer name, city, total sales and order count"

    print("=== live ETL Copilot flow ===")
    print("request: %s\n" % request)

    try:
        result = copilot.create_dataset(
            user_request=request,
            connection_string=CONNECTION,
            schema=SCHEMA,
            dataset_name="acceptance",
        )
    except CopilotError as exc:
        print("BLOCKED:\n%s" % exc)
        return 1

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
