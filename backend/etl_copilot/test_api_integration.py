"""Offline test of the ETL Copilot HTTP layer.

No database server, no OpenAI key, no credentials. The real router, the real
ETLCopilot, the real compiler and the real reconciliation gate run against a
SQLite fixture; only two things are substituted:

  * the LLM              - a stub returning the JSON a real call returns
  * _create_project_from_frame - AutoAI's registration helper, which needs
                           PostgreSQL and main.UPLOAD_DIR. It is replaced by a
                           recorder so the test can assert WHAT the router
                           hands it, which is the whole point of the
                           integration.

Run:  python -m etl_copilot.test_api_integration
"""

import copy
import os
import sqlite3
import sys
import tempfile

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import services.db_connector as dbc
import services.join_profiler as jp
import services.sql_builder as sb

_failures = []


def check(label, condition, detail=""):
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", label,
                           (" - " + detail) if detail else ""))
    if not condition:
        _failures.append(label)


# ---------------------------------------------------------------------------
# Fixture: SQLite standing in for the customer's database.
#
# The dialect is reported as postgresql so the compiler emits the quoting it
# emits in production; SQLite accepts double-quoted identifiers. Schema is
# None because SQLite has no schema layer.
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(tempfile.mkdtemp(), "etl_api_fixture.db")


def build_fixture():
    con = sqlite3.connect(DB_PATH)
    con.executescript(
        """
        CREATE TABLE Customers (
            CustomerID INTEGER PRIMARY KEY,
            CustomerName TEXT,
            City TEXT
        );
        CREATE TABLE Orders (
            OrderID INTEGER PRIMARY KEY,
            CustomerID INTEGER,
            OrderAmount REAL,
            FOREIGN KEY (CustomerID) REFERENCES Customers(CustomerID)
        );
        CREATE TABLE OrderItems (
            ItemID INTEGER PRIMARY KEY,
            OrderID INTEGER,
            Quantity INTEGER,
            FOREIGN KEY (OrderID) REFERENCES Orders(OrderID)
        );
        """
    )
    order_id = 0
    for customer_id in range(1, 21):
        con.execute(
            "INSERT INTO Customers VALUES (?,?,?)",
            (customer_id, "Cust%d" % customer_id,
             "Pune" if customer_id % 2 else "Mumbai"),
        )
        for _ in range(3):
            order_id += 1
            con.execute("INSERT INTO Orders VALUES (?,?,?)",
                        (order_id, customer_id, 100.0 + order_id))
            for k in range(2):
                con.execute("INSERT INTO OrderItems VALUES (?,?,?)",
                            (order_id * 100 + k, order_id, 5))
    con.commit()
    totals = con.execute(
        "SELECT SUM(OrderAmount), SUM(Quantity) FROM Orders, OrderItems "
        "WHERE OrderItems.OrderID = Orders.OrderID"
    ).fetchone()
    sales = con.execute("SELECT SUM(OrderAmount) FROM Orders").fetchone()[0]
    con.close()
    return sales


SOURCE_SALES = build_fixture()

dbc.DEFAULT_SCHEMA[dbc.POSTGRESQL] = None
jp.DEFAULT_SCHEMA[jp.POSTGRESQL] = None
sb.DEFAULT_SCHEMA[sb.POSTGRESQL] = None
dbc._dialect_of = lambda cs: dbc.POSTGRESQL
dbc._make_engine = lambda cs, **kw: create_engine("sqlite:///" + DB_PATH)

import routers.etl_copilot as api  # noqa: E402  (must follow the patching above)
from etl_copilot.copilot_service import ETLCopilot  # noqa: E402


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

GOOD_SPEC = {
    "entity_table": "Customers",
    "entity_key": "CustomerID",
    "attributes": ["CustomerName", "City"],
    "measures": [
        {"measure_name": "TotalSales", "source_table": "Orders",
         "source_column": "OrderAmount", "aggregation": "SUM"},
        {"measure_name": "AverageOrderValue", "source_table": "Orders",
         "source_column": "OrderAmount", "aggregation": "AVG"},
        {"measure_name": "OrderCount", "source_table": "Orders",
         "source_column": "OrderID", "aggregation": "COUNT"},
    ],
    "filters": [],
}

BAD_TABLE_SPEC = {
    "entity_table": "Klanten",
    "entity_key": "CustomerID",
    "attributes": [],
    "measures": [
        {"measure_name": "TotalSales", "source_table": "Bestellingen",
         "source_column": "OrderAmount", "aggregation": "SUM"},
    ],
    "filters": [],
}

EMPTY_SPEC = copy.deepcopy(GOOD_SPEC)
EMPTY_SPEC["filters"] = [{"column": "City", "operator": "eq", "value": "Atlantis"}]

HAVING_HELPER_SPEC = {
    "entity_table": "Customers",
    "entity_key": "CustomerID",
    "attributes": ["CustomerName", "City"],
    "measures": [
        {"measure_name": "TotalSales", "source_table": "Orders",
         "source_column": "OrderAmount", "aggregation": "SUM"},
        {"measure_name": "AverageOrderValue", "source_table": "Orders",
         "source_column": "OrderAmount", "aggregation": "AVG"},
        {"measure_name": "OrderCount", "source_table": "Orders",
         "source_column": "OrderID", "aggregation": "COUNT"},
    ],
    "having": [{"measure_name": "OrderCount", "operator": "gt", "value": 5}],
    "filters": [],
}

TOP_N_SPEC = {
    "entity_table": "Customers",
    "entity_key": "CustomerID",
    "attributes": ["CustomerName", "City"],
    "measures": [
        {"measure_name": "TotalSales", "source_table": "Orders",
         "source_column": "OrderAmount", "aggregation": "SUM",
         "include_in_output": False},
    ],
    "filters": [],
    "order_by": "TotalSales",
    "order_direction": "desc",
    "limit": 10,
}

MUMBAI_SPEC = copy.deepcopy(GOOD_SPEC)
MUMBAI_SPEC["filters"] = [
    {"column": "City", "operator": "eq", "value": "Mumbai"}
]


class StubLLM(object):
    def __init__(self, spec):
        self.spec = spec

    def generate_spec(self, user_request, schema):
        return copy.deepcopy(self.spec)

    def fix_spec(self, **kwargs):
        return copy.deepcopy(self.spec)


def use_spec(spec):
    """Point the router's shared copilot at a stub returning `spec`."""
    api._copilot = ETLCopilot(generator=StubLLM(spec))


REGISTERED = []


def fake_register(db, df, project_name, target_column, source_label):
    """Stand-in for routers.datamodel._create_project_from_frame."""
    REGISTERED.append(
        {
            "rows": len(df),
            "columns": list(df.columns),
            "project_name": project_name,
            "target_column": target_column,
            "source_label": source_label,
            "frame": df,
        }
    )
    return {
        "project_id": "11111111-2222-3333-4444-555555555555",
        "project_name": project_name,
        "file_id": "fake-file-id.csv",
        "target_column": target_column,
        "rows": len(df),
        "columns": len(df.columns),
        "dataset_profile_summary": "stub profile",
    }


api._create_project_from_frame = fake_register
class _FakeSession(object):
    """Stands in for a SQLAlchemy Session; the recorder never touches it."""

    def close(self):
        pass


api.SessionLocal = _FakeSession

app = FastAPI()
app.include_router(api.router)
app.dependency_overrides[api.get_db] = lambda: None
client = TestClient(app)

CONN = {
    "db_type": "postgresql",
    "host": "localhost",
    "database": "fixture",
    "username": "u",
    "password": "p",
}


def body(**extra):
    payload = dict(CONN)
    payload.update(extra)
    return payload


def reconciliation_check(data, name):
    reconciliation = data.get("reconciliation") or {}
    return next(
        (check for check in reconciliation.get("checks", [])
         if check.get("check") == name),
        None,
    )


# ---------------------------------------------------------------------------

print("ETL Copilot API integration (offline)")
print("fixture: 20 customers, 60 orders, SUM(OrderAmount)=%s\n" % SOURCE_SALES)

print("1. Swagger / OpenAPI visibility")
spec = app.openapi()
paths = sorted(p for p in spec["paths"] if "etl-copilot" in p)
for wanted in ["/etl-copilot/schema", "/etl-copilot/preview",
               "/etl-copilot/build", "/etl-copilot/status/{job_id}"]:
    check("%s in OpenAPI" % wanted, wanted in paths)
check("request models documented",
      "EtlCopilotBuildRequest" in spec.get("components", {}).get("schemas", {}))

print("\n2. POST /etl-copilot/schema")
use_spec(GOOD_SPEC)
response = client.post("/etl-copilot/schema", json=body())
check("200", response.status_code == 200, str(response.status_code))
data = response.json()
check("three tables found", data["table_count"] == 3, str(data["table_count"]))
check("two relationships found", data["relationship_count"] == 2,
      str(data["relationship_count"]))
check("usable flag true", data["usable"] is True)
check("no FK notice when FKs exist", data["notice"] is None)
check("no data values leaked",
      set(data["tables"]) == {"Customers", "Orders", "OrderItems"})

print("\n3. POST /etl-copilot/preview  (no side effects)")
before = set(os.listdir("etl_copilot")) if os.path.isdir("etl_copilot") else set()
response = client.post("/etl-copilot/preview",
                       json=body(user_request="customers with total sales"))
check("200", response.status_code == 200, str(response.status_code))
data = response.json()
check("not blocking", data["blocking"] is False)
check("sql returned", bool(data["sql"]) and "SELECT" in data["sql"])
check("explanation returned", bool(data["explanation"]))
check("entity table reported", data["entity_table"] == "Customers")
check("columns match the compiled output plan",
      data["columns"] == ["CustomerID", "CustomerName", "City",
                  "TotalSales", "AverageOrderValue", "OrderCount"],
    str(data["columns"]))
check("preview registered nothing", REGISTERED == [])

print("\n3a. HAVING helper stays out of preview metadata")
use_spec(HAVING_HELPER_SPEC)
helper_connection = sqlite3.connect(DB_PATH)
helper_connection.executemany(
    "INSERT INTO Orders VALUES (?,?,?)",
    [(61, 1, 1000.0), (62, 1, 1100.0), (63, 1, 1200.0)],
)
helper_connection.commit()
helper_connection.close()
response = client.post(
    "/etl-copilot/preview",
    json=body(user_request="customer sales with more than 5 orders"),
)
check("200", response.status_code == 200, str(response.status_code))
data = response.json()
expected_sales_columns = [
    "CustomerID", "CustomerName", "City", "TotalSales", "AverageOrderValue"
]
check("preview columns exclude HAVING helper",
    data["columns"] == expected_sales_columns, str(data["columns"]))
check("lineage output_columns exclude HAVING helper",
    data["lineage"]["output_columns"] == expected_sales_columns,
    str(data["lineage"]["output_columns"]))
check("lineage measure audit excludes HAVING helper",
    all(measure["name"] != "OrderCount" for measure in data["lineage"]["measures"]))
check("HAVING helper remains in spec for internal execution",
    any(measure["name"] == "OrderCount"
        for measure in data["spec"]["measures"]))
sales_check = reconciliation_check(data, "source_grain_total:TotalSales")
check("more than 5 orders preview reconciliation passes",
    data["reconciliation"]["passed"] is True and
    sales_check is not None and sales_check["passed"] is True,
    str(sales_check))
check("more than 5 orders source and dataset totals match",
    sales_check is not None and
    float(sales_check["expected"]) == float(sales_check["actual"]) == 3606.0,
    str(sales_check))
REGISTERED.clear()
response = client.post(
    "/etl-copilot/build",
    json=body(user_request="customer sales with more than 5 orders",
          project_name="helper only", target_column="TotalSales"),
)
check("helper-only build succeeds", response.status_code == 201,
    str(response.status_code))
check("Dataset Ready schema excludes the helper",
    bool(REGISTERED) and REGISTERED[0]["columns"] == expected_sales_columns,
    str(REGISTERED[0]["columns"] if REGISTERED else []))
helper_connection = sqlite3.connect(DB_PATH)
helper_connection.execute("DELETE FROM Orders WHERE OrderID BETWEEN 61 AND 63")
helper_connection.commit()
helper_connection.close()

print("\n3aa. Empty HAVING result reconciles to zero")
helper_connection = sqlite3.connect(DB_PATH)
ten_order_rows = [
    (1000 + (customer_id - 1) * 7 + offset, customer_id, 10.0)
    for customer_id in range(1, 21)
    for offset in range(7)
]
helper_connection.executemany(
    "INSERT INTO Orders VALUES (?,?,?)", ten_order_rows
)
helper_connection.commit()
order_counts = helper_connection.execute(
    "SELECT MIN(order_count), MAX(order_count) FROM ("
    "SELECT CustomerID, COUNT(*) AS order_count FROM Orders "
    "GROUP BY CustomerID)"
).fetchone()
helper_connection.close()
check("empty-result fixture has exactly 10 orders per customer",
    order_counts == (10, 10), str(order_counts))
over_ten_spec = copy.deepcopy(HAVING_HELPER_SPEC)
over_ten_spec["having"][0]["value"] = 10
use_spec(over_ten_spec)
response = client.post(
    "/etl-copilot/preview",
    json=body(user_request="customers with more than 10 orders"),
)
check("more than 10 orders preview returns 200", response.status_code == 200,
    str(response.status_code))
data = response.json()
sales_check = reconciliation_check(data, "source_grain_total:TotalSales")
check("more than 10 orders returns zero preview rows",
    data["preview_rows"] == [], str(data["preview_rows"]))
check("more than 10 orders preview reconciliation passes",
    data["reconciliation"]["available"] is True and
    data["reconciliation"]["passed"] is True,
    str(data["reconciliation"]))
check("more than 10 orders source and dataset totals are numeric zero",
    sales_check is not None and sales_check["expected"] == 0 and
    sales_check["actual"] == 0 and sales_check["passed"] is True,
    str(sales_check))
helper_connection = sqlite3.connect(DB_PATH)
helper_connection.execute("DELETE FROM Orders WHERE OrderID BETWEEN 1000 AND 1139")
helper_connection.commit()
helper_connection.close()

print("\n3b. Top-N is ordered and limited")
use_spec(TOP_N_SPEC)
REGISTERED.clear()
response = client.post(
    "/etl-copilot/preview",
    json=body(user_request="top 10 customers by total sales"),
)
check("Top-N preview returns 200", response.status_code == 200,
    str(response.status_code))
data = response.json()
check("Top-N preview returns exactly 10 rows",
    len(data["preview_rows"]) == 10 and data["has_more"] is False,
    "%d rows; has_more=%s" % (len(data["preview_rows"]), data["has_more"]))
check("Top-N preview reconciliation still passes",
    data["reconciliation"]["passed"] is True,
    str(data["reconciliation"]))
REGISTERED.clear()
response = client.post(
    "/etl-copilot/build",
    json=body(user_request="top 10 customers by total sales",
          project_name="top ten customers", target_column="TotalSales"),
)
check("201", response.status_code == 201, str(response.status_code))
data = response.json()
check("Top-N SQL has LIMIT 10 and no HAVING threshold",
    "LIMIT 10" in data["sql"] and "HAVING" not in data["sql"] and
    "WHERE" not in data["sql"], data["sql"])
check("Top-N output retains TotalSales",
    bool(REGISTERED) and REGISTERED[0]["columns"] == [
        "CustomerID", "CustomerName", "City", "TotalSales"
    ], str(REGISTERED[0]["columns"] if REGISTERED else []))
check("Top-N build returns exactly 10 rows",
    bool(REGISTERED) and REGISTERED[0]["rows"] == 10,
    str(REGISTERED[0]["rows"] if REGISTERED else 0))
check("Top-N rows sorted by TotalSales descending",
    bool(REGISTERED) and
    REGISTERED[0]["frame"]["TotalSales"].is_monotonic_decreasing)

print("\n3c. Existing Mumbai filter remains effective")
use_spec(MUMBAI_SPEC)
response = client.post(
    "/etl-copilot/preview",
    json=body(user_request="customers in Mumbai with total sales"),
)
check("200", response.status_code == 200, str(response.status_code))
data = response.json()
check("Mumbai filter returns only Mumbai customers",
    bool(data["preview_rows"]) and
    all(row["City"] == "Mumbai" for row in data["preview_rows"]))
sales_check = reconciliation_check(data, "source_grain_total:TotalSales")
check("Mumbai filtered total reconciliation passes",
    data["reconciliation"]["passed"] is True and
    sales_check is not None and sales_check["passed"] is True,
    str(sales_check))
check("Mumbai source and dataset totals match",
    sales_check is not None and
    float(sales_check["expected"]) == float(sales_check["actual"]),
    str(sales_check))

print("\n4. POST /etl-copilot/build  (registers through AutoAI)")
use_spec(GOOD_SPEC)
REGISTERED.clear()
response = client.post(
    "/etl-copilot/build",
    json=body(user_request="customers with total sales",
              project_name="copilot demo", target_column="TotalSales"),
)
check("201", response.status_code == 201, str(response.status_code))
data = response.json()
check("file_id returned", data.get("file_id") == "fake-file-id.csv")
check("target_column returned", data.get("target_column") == "TotalSales")
check("sql returned", bool(data.get("sql")))
check("explanation returned", bool(data.get("explanation")))
check("reconciliation reported", data["reconciliation"]["passed"] is True)
check("registered exactly once", len(REGISTERED) == 1, str(len(REGISTERED)))
registered = REGISTERED[0]
check("20 rows handed to AutoAI", registered["rows"] == 20, str(registered["rows"]))
check("columns handed to AutoAI",
      registered["columns"] == ["CustomerID", "CustomerName", "City",
                     "TotalSales", "AverageOrderValue", "OrderCount"],
      str(registered["columns"]))
check("totals survive registration",
      abs(float(registered["frame"]["TotalSales"].sum()) - float(SOURCE_SALES)) < 0.01,
      "%s vs %s" % (registered["frame"]["TotalSales"].sum(), SOURCE_SALES))
check("average and count remain available",
    registered["frame"].loc[registered["frame"]["CustomerID"] == 1,
                    "AverageOrderValue"].iloc[0] ==
    registered["frame"].loc[registered["frame"]["CustomerID"] == 1,
                    "TotalSales"].iloc[0] / 3 and
    registered["frame"].loc[registered["frame"]["CustomerID"] == 1,
                    "OrderCount"].iloc[0] == 3)
check("source label names the copilot", "ETL Copilot" in registered["source_label"])

print("\n5. Copilot wrote no CSV of its own (one file, one owner)")
generated = "generated_datasets"
copilot_files = sorted(os.listdir(generated)) if os.path.isdir(generated) else []
check("no copilot demo csv in generated_datasets",
      not any(f.startswith("copilot demo") for f in copilot_files),
      str(copilot_files[:4]))

print("\n6. Target column defaulting")
REGISTERED.clear()
response = client.post(
    "/etl-copilot/build",
    json=body(user_request="customers with total sales", project_name="no target"),
)
check("201 without an explicit target", response.status_code == 201,
      str(response.status_code))
check("falls back to the last column",
      REGISTERED and REGISTERED[0]["target_column"] == "OrderCount",
      REGISTERED[0]["target_column"] if REGISTERED else "none")

print("\n7. Bad target column is refused before any work")
response = client.post(
    "/etl-copilot/build",
    json=body(user_request="customers with total sales",
              project_name="bad target", target_column="Nonexistent"),
)
check("400", response.status_code == 400, str(response.status_code))
check("names the available columns", "Available columns" in str(response.json()))

print("\n8. Hallucinated tables -> 400 with the issue list")
REGISTERED.clear()
use_spec(BAD_TABLE_SPEC)
response = client.post(
    "/etl-copilot/build",
    json=body(user_request="nonsense", project_name="bad"),
)
check("400", response.status_code == 400, str(response.status_code))
detail = response.json()["detail"]
check("issues itemised", isinstance(detail, dict) and len(detail["issues"]) > 0,
      str(detail)[:80])
check("unknown_table reported",
      any(i["code"] == "unknown_table" for i in detail["issues"]))
check("nothing registered", REGISTERED == [])

print("\n9. Empty result -> 400, not a crash")
use_spec(EMPTY_SPEC)
response = client.post(
    "/etl-copilot/build",
    json=body(user_request="atlantis customers", project_name="empty"),
)
check("400", response.status_code == 400, str(response.status_code))
check("explains emptiness", "empty" in str(response.json()).lower())
check("nothing registered", REGISTERED == [])

print("\n10. Background build + status")
use_spec(GOOD_SPEC)
REGISTERED.clear()
response = client.post(
    "/etl-copilot/build",
    json=body(user_request="customers with total sales",
              project_name="bg", background=True),
)
check("202", response.status_code == 202, str(response.status_code))
job_id = response.json().get("job_id")
check("job_id returned", bool(job_id))
status = client.get("/etl-copilot/status/%s" % job_id)
check("status 200", status.status_code == 200, str(status.status_code))
job = status.json()
check("job completed", job["status"] == "completed", str(job["status"]))
check("job carries the payload",
      job["result"] and job["result"]["file_id"] == "fake-file-id.csv")
check("background build registered once", len(REGISTERED) == 1, str(len(REGISTERED)))

print("\n11. Unknown job -> 404")
response = client.get("/etl-copilot/status/does-not-exist")
check("404", response.status_code == 404, str(response.status_code))

print("\n12. Health")
response = client.get("/etl-copilot/health")
check("200", response.status_code == 200)
check("reports the service", response.json()["service"] == "etl_copilot")

print("\n" + "=" * 62)
if _failures:
    print("  %d FAILED: %s" % (len(_failures), ", ".join(_failures)))
    sys.exit(1)
print("  ALL API INTEGRATION CHECKS PASSED")
