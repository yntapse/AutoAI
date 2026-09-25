"""
Step 1 checkpoint test for AutoAI Builder's database connector.

Run this from inside the `backend` folder:

    cd backend
    python test_db_connector.py

It asks for your local database details, connects, lists your tables,
and pulls a few rows. Nothing is saved to disk and nothing in your app
is touched.
"""

from getpass import getpass

from services.db_connector import (
    DataSourceError,
    build_connection_string,
    extract_to_dataframe,
    list_tables,
    test_connection,
)


def ask(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{label}{suffix}: ").strip()
    return answer or default


def main() -> None:
    print("\n--- AutoAI database connector test ---\n")
    print("Press Enter to accept the value shown in brackets.\n")

    host = ask("Host", "localhost")
    port = ask("Port", "5432")
    database = ask("Database name")
    username = ask("Username", "postgres")
    password = getpass("Password (typing is hidden): ")

    if not database:
        print("\nA database name is required. Stopping.")
        return

    connection_string = build_connection_string(
        host=host,
        port=int(port),
        database=database,
        username=username,
        password=password,
    )

    # --- Check 1: can we connect at all? ---
    print("\n[1/3] Connecting...")
    try:
        info = test_connection(connection_string)
    except DataSourceError as exc:
        print(f"  FAILED: {exc}")
        return
    print(f"  OK - {info['server_version'][:60]}")

    # --- Check 2: what tables are there? ---
    print("\n[2/3] Listing tables in schema 'public'...")
    try:
        tables = list_tables(connection_string)
    except DataSourceError as exc:
        print(f"  FAILED: {exc}")
        return

    if not tables:
        print("  No tables found in this database.")
        return

    for name in tables[:25]:
        print(f"  - {name}")
    if len(tables) > 25:
        print(f"  ... and {len(tables) - 25} more")

    # --- Check 3: pull real rows ---
    table = ask("\n[3/3] Table to preview", tables[0])
    query = f'SELECT * FROM "{table}" LIMIT 5'
    print(f"  Running: {query}")

    try:
        df = extract_to_dataframe(connection_string, query)
    except DataSourceError as exc:
        print(f"  FAILED: {exc}")
        return

    print(f"\n  OK - {len(df)} rows, {len(df.columns)} columns")
    print(f"  Columns: {', '.join(map(str, df.columns))}\n")
    print(df.head().to_string(index=False))

    # --- Check 4: confirm dangerous queries are blocked ---
    print("\n[bonus] Checking that a DELETE is refused...")
    try:
        extract_to_dataframe(connection_string, f'DELETE FROM "{table}"')
        print("  PROBLEM: the DELETE was not blocked. Stop and tell Claude.")
    except DataSourceError as exc:
        print(f"  OK - blocked as expected: {exc}")

    print("\nStep 1 passed.\n")


if __name__ == "__main__":
    main()
