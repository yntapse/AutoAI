"""Sprint 1 - Metadata Catalog API tests.

Mirrors test_quality_api.py: SQLite in-memory, dependency override, no network
and no real database connection. The scanner itself is tested against a fake
that returns the exact shape db_connector returns.
"""

import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base

# SQLAlchemy configures every registered mapper at once. routers.catalog imports
# routers.etl_copilot, which registers PipelineRun, whose relationships name
# AgentDecision and the rest - so they must all be imported or mapper
# configuration fails. test_quality_api.py does the same for the same reason.
from models.agent_decision import AgentDecision  # noqa: F401
from models.agent_run import AgentRun  # noqa: F401
from models.cleaning_rule import CleaningRule  # noqa: F401
from models.dataset_profile import DatasetProfile  # noqa: F401
from models.pipeline_definition import PipelineDefinition  # noqa: F401
from models.pipeline_run import PipelineRun  # noqa: F401
from models.pipeline_version import PipelineVersion  # noqa: F401
from models.project import Project  # noqa: F401
from models.quality_rule import QualityRule  # noqa: F401
from models.source_profile_snapshot import SourceProfileSnapshot  # noqa: F401
from models.transformation_rule import TransformationRule  # noqa: F401

from models.catalog_column import CatalogColumn
from models.catalog_relationship import CatalogRelationship
from models.catalog_scan import CatalogScan
from models.catalog_table import CatalogTable
from models.data_source import DataSource
from routers.catalog import get_db, router
from services.metadata_scanner import catalog_for_llm, normalize_column_name


FAKE_TABLES = ["customer_master", "loan_master"]

FAKE_COLUMNS = {
    "customer_master": [
        {"name": "customer_id", "type": "INTEGER", "nullable": False, "primary_key": True},
        {"name": "full_name", "type": "VARCHAR(200)", "nullable": True, "primary_key": False},
    ],
    "loan_master": [
        {"name": "loan_id", "type": "INTEGER", "nullable": False, "primary_key": True},
        {"name": "customer_id", "type": "INTEGER", "nullable": True, "primary_key": False},
        {"name": "sanctioned_amount", "type": "NUMERIC(18, 2)", "nullable": True, "primary_key": False},
    ],
}

FAKE_FKS = [
    {
        "constraint_name": "fk_loan_customer",
        "left_table": "loan_master",
        "left_column": "customer_id",
        "right_table": "customer_master",
        "right_column": "customer_id",
    }
]


class CatalogApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.tables = [
            DataSource.__table__,
            CatalogScan.__table__,
            CatalogTable.__table__,
            CatalogColumn.__table__,
            CatalogRelationship.__table__,
        ]
        Base.metadata.create_all(bind=self.engine, tables=self.tables)
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, autocommit=False
        )

        app = FastAPI()
        app.include_router(router)

        def override_get_db():
            db = self.session_factory()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        Base.metadata.drop_all(bind=self.engine, tables=self.tables)
        self.engine.dispose()

    # ---------------- source registration ----------------

    def _register(self, name="meridian_dw"):
        return self.client.post(
            "/catalog/sources",
            json={
                "source_name": name,
                "db_type": "postgresql",
                "host": "localhost",
                "port": 5432,
                "database": "meridian",
                "username": "svc_ro",
                "schema_name": "public",
            },
        )

    def test_register_source_returns_201_and_never_stores_a_password(self):
        response = self._register()
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["source_name"], "meridian_dw")
        self.assertEqual(body["status"], "registered")
        self.assertNotIn("password", body)

        db = self.session_factory()
        try:
            row = db.query(DataSource).one()
            self.assertFalse(hasattr(row, "password"))
            self.assertIsNone(getattr(row, "password", None))
        finally:
            db.close()

    def test_duplicate_source_name_returns_409(self):
        self.assertEqual(self._register().status_code, 201)
        self.assertEqual(self._register().status_code, 409)

    def test_invalid_db_type_returns_422(self):
        response = self.client.post(
            "/catalog/sources",
            json={"source_name": "oracle_box", "db_type": "oracle", "host": "h", "database": "d"},
        )
        self.assertEqual(response.status_code, 422)

    def test_list_and_get_source(self):
        source_id = self._register().json()["id"]
        listed = self.client.get("/catalog/sources")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)

        fetched = self.client.get(f"/catalog/sources/{source_id}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["id"], source_id)

    def test_unknown_source_returns_404(self):
        response = self.client.get(f"/catalog/sources/{uuid4()}")
        self.assertEqual(response.status_code, 404)

    # ---------------- scanning ----------------

    def _scan(self, source_id, **overrides):
        payload = {
            "db_type": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "meridian",
            "username": "svc_ro",
            "password": "secret",
            "schema_name": "public",
            "include_row_counts": False,
            "include_sample_values": False,
        }
        payload.update(overrides)
        return self.client.post(f"/catalog/sources/{source_id}/scan", json=payload)

    def test_scan_persists_tables_columns_and_relationships(self):
        source_id = self._register().json()["id"]

        with patch("services.metadata_scanner.list_tables", return_value=FAKE_TABLES), \
             patch("services.metadata_scanner.list_columns", return_value=FAKE_COLUMNS), \
             patch("services.metadata_scanner.list_foreign_keys", return_value=FAKE_FKS):
            response = self._scan(source_id)

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["table_count"], 2)
        self.assertEqual(body["column_count"], 5)
        self.assertEqual(body["relationship_count"], 1)

        db = self.session_factory()
        try:
            self.assertEqual(db.query(CatalogTable).count(), 2)
            self.assertEqual(db.query(CatalogColumn).count(), 5)
            self.assertEqual(db.query(CatalogRelationship).count(), 1)

            fk_column = (
                db.query(CatalogColumn)
                .filter(CatalogColumn.column_name == "customer_id",
                        CatalogColumn.is_foreign_key.is_(True))
                .all()
            )
            self.assertEqual(len(fk_column), 1)

            edge = db.query(CatalogRelationship).one()
            self.assertEqual(edge.origin, "declared")
            self.assertIsNone(edge.approved)
        finally:
            db.close()

    def test_scan_marks_source_scanned(self):
        source_id = self._register().json()["id"]
        with patch("services.metadata_scanner.list_tables", return_value=FAKE_TABLES), \
             patch("services.metadata_scanner.list_columns", return_value=FAKE_COLUMNS), \
             patch("services.metadata_scanner.list_foreign_keys", return_value=FAKE_FKS):
            self._scan(source_id)

        fetched = self.client.get(f"/catalog/sources/{source_id}").json()
        self.assertEqual(fetched["status"], "scanned")
        self.assertIsNotNone(fetched["last_scanned_at"])

    def test_scan_failure_records_the_error_and_returns_400(self):
        from services.db_connector import DataSourceError

        source_id = self._register().json()["id"]
        with patch("services.metadata_scanner.list_tables",
                   side_effect=DataSourceError("login failed")):
            response = self._scan(source_id)

        self.assertEqual(response.status_code, 400)
        db = self.session_factory()
        try:
            scan = db.query(CatalogScan).one()
            self.assertEqual(scan.status, "failed")
            self.assertIn("login failed", scan.error)
        finally:
            db.close()

    def test_sample_values_are_off_by_default(self):
        source_id = self._register().json()["id"]
        with patch("services.metadata_scanner.list_tables", return_value=FAKE_TABLES), \
             patch("services.metadata_scanner.list_columns", return_value=FAKE_COLUMNS), \
             patch("services.metadata_scanner.list_foreign_keys", return_value=FAKE_FKS), \
             patch("services.metadata_scanner._sample_values") as sampler:
            self._scan(source_id)

        sampler.assert_not_called()
        db = self.session_factory()
        try:
            self.assertTrue(
                all(c.sample_values is None for c in db.query(CatalogColumn).all())
            )
        finally:
            db.close()

    # ---------------- explorer ----------------

    def test_overview_before_any_scan_is_empty_not_an_error(self):
        source_id = self._register().json()["id"]
        response = self.client.get(f"/catalog/sources/{source_id}/overview")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["table_count"], 0)
        self.assertEqual(response.json()["tables"], [])

    def test_overview_and_table_detail_after_scan(self):
        source_id = self._register().json()["id"]
        with patch("services.metadata_scanner.list_tables", return_value=FAKE_TABLES), \
             patch("services.metadata_scanner.list_columns", return_value=FAKE_COLUMNS), \
             patch("services.metadata_scanner.list_foreign_keys", return_value=FAKE_FKS):
            self._scan(source_id)

        overview = self.client.get(f"/catalog/sources/{source_id}/overview").json()
        self.assertEqual(overview["table_count"], 2)
        self.assertEqual(len(overview["tables"]), 2)

        table_id = overview["tables"][0]["id"]
        detail = self.client.get(f"/catalog/tables/{table_id}").json()
        self.assertIn("columns", detail)
        self.assertTrue(len(detail["columns"]) >= 2)
        self.assertEqual(detail["columns"][0]["ordinal_position"], 0)

    def test_table_responses_expose_estimate_flag_and_descriptions(self):
        """The details panel needs to tell an exact count from an estimate."""
        source_id = self._register().json()["id"]
        with patch("services.metadata_scanner.list_tables", return_value=FAKE_TABLES), \
             patch("services.metadata_scanner.list_columns", return_value=FAKE_COLUMNS), \
             patch("services.metadata_scanner.list_foreign_keys", return_value=FAKE_FKS):
            self._scan(source_id)

        overview = self.client.get(f"/catalog/sources/{source_id}/overview").json()
        for table in overview["tables"]:
            self.assertIsInstance(table["row_count_is_estimate"], bool)
            self.assertIn("business_description", table)
        detail = self.client.get(f"/catalog/tables/{overview['tables'][0]['id']}").json()
        for column in detail["columns"]:
            self.assertIn("business_description", column)
            self.assertIsNone(column["business_description"])  # the scanner does not fill it

    def test_search_finds_tables_and_columns(self):
        source_id = self._register().json()["id"]
        with patch("services.metadata_scanner.list_tables", return_value=FAKE_TABLES), \
             patch("services.metadata_scanner.list_columns", return_value=FAKE_COLUMNS), \
             patch("services.metadata_scanner.list_foreign_keys", return_value=FAKE_FKS):
            self._scan(source_id)

        results = self.client.get("/catalog/search", params={"q": "customer"}).json()
        kinds = {row["match_type"] for row in results}
        self.assertIn("table", kinds)
        self.assertIn("column", kinds)

    def test_relationships_endpoint(self):
        source_id = self._register().json()["id"]
        with patch("services.metadata_scanner.list_tables", return_value=FAKE_TABLES), \
             patch("services.metadata_scanner.list_columns", return_value=FAKE_COLUMNS), \
             patch("services.metadata_scanner.list_foreign_keys", return_value=FAKE_FKS):
            self._scan(source_id)

        edges = self.client.get(f"/catalog/sources/{source_id}/relationships").json()
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["left_table"], "loan_master")


class MetadataScannerUnitTests(unittest.TestCase):
    def test_normalize_column_name_folds_common_spellings(self):
        self.assertEqual(normalize_column_name("CustomerID"), "customerid")
        self.assertEqual(normalize_column_name("Customer_ID"), "customerid")
        self.assertEqual(normalize_column_name("cust_id"), "customerid")
        self.assertEqual(normalize_column_name("CUST ID"), "customerid")

    def test_catalog_for_llm_strips_sample_values(self):
        catalog = {
            "schema_name": "public",
            "db_type": "postgresql",
            "table_count": 1,
            "tables": [
                {
                    "table_name": "customer_master",
                    "column_count": 1,
                    "row_count": 10,
                    "columns": [
                        {
                            "column_name": "full_name",
                            "data_type": "VARCHAR",
                            "is_nullable": True,
                            "is_primary_key": False,
                            "is_foreign_key": False,
                            "sample_values": ["Priya Sharma", "Rahul Mehta"],
                        }
                    ],
                }
            ],
            "relationships": [],
        }
        sanitized = catalog_for_llm(catalog)
        payload = str(sanitized)
        self.assertNotIn("Priya Sharma", payload)
        self.assertNotIn("sample_values", payload)
        self.assertIn("full_name", payload)


if __name__ == "__main__":
    unittest.main()
