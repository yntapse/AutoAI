"""Run the whole ETL Copilot suite. Exit non-zero on any failure.

    python -m etl_copilot.run_tests

Live tests (real database, real OpenAI) skip unless their environment
variables are set:

    ETL_TEST_CONNECTION   live schema read and end-to-end flow
    OPENAI_API_KEY        live spec generation
"""

import subprocess
import sys

MODULES = [
    "etl_copilot.test_spec",
    "etl_copilot.test_join_graph",
    "etl_copilot.test_join_and_validation",
    "etl_copilot.test_copilot",
    "etl_copilot.test_acceptance",
    "etl_copilot.test_sql_builder",
    "etl_copilot.test_api_integration",
    "etl_copilot.test_schema_reader",
    "etl_copilot.test_openai_connection",
    "etl_copilot.test_openai_spec_generator",
    "etl_copilot.test_fix_spec",
    "etl_copilot.test_full_flow",
    "services.test_sql_builder",
]


def main():
    failed = []
    skipped = []

    for module in MODULES:
        process = subprocess.run(
            [sys.executable, "-m", module],
            capture_output=True,
            text=True,
        )
        output = (process.stdout or "") + (process.stderr or "")

        if process.returncode != 0:
            status = "FAIL"
            failed.append(module)
        elif output.lstrip().startswith("SKIP") or "\nSKIP" in output:
            status = "SKIP"
            skipped.append(module)
        else:
            status = "PASS"

        print("%-6s %s" % (status, module))

        if status == "FAIL":
            for line in output.strip().splitlines()[-12:]:
                print("       %s" % line)

    print("\n" + "=" * 62)
    print("  %d passed, %d skipped, %d failed"
          % (len(MODULES) - len(failed) - len(skipped), len(skipped), len(failed)))

    if skipped:
        print("  skipped (need credentials): %s" % ", ".join(skipped))

    if failed:
        print("  FAILED: %s" % ", ".join(failed))
        return 1

    print("  SUITE GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
