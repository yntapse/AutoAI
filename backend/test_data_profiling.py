import unittest
from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.agent_decision import AgentDecision
from models.cleaning_rule import CleaningRule
from models.pipeline_definition import PipelineDefinition
from models.pipeline_run import PipelineRun
from models.pipeline_version import PipelineVersion
from models.project import Project
from models.source_profile_snapshot import SourceProfileSnapshot
from models.transformation_rule import TransformationRule
from routers.profiling import get_db, router
from services.data_profiler import profile_source_tables


class DataProfilingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE customers "
                    "(age INTEGER, city VARCHAR(40), active BOOLEAN)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO customers (age, city, active) VALUES "
                    "(10, 'Oslo', 1), (20, 'Oslo', 0), (NULL, NULL, 1)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE measurements (amount FLOAT, reference VARCHAR(40))"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO measurements (amount, reference) VALUES "
                    "(1, '12'), (1, '12'), (2, '3'), (3, 'abc'), "
                    "(100, '4'), (NULL, '4')"
                )
            )

        self.tables = [
            Project.__table__,
            PipelineDefinition.__table__,
            PipelineVersion.__table__,
            PipelineRun.__table__,
            CleaningRule.__table__,
            TransformationRule.__table__,
            AgentDecision.__table__,
            SourceProfileSnapshot.__table__,
        ]
        Base.metadata.create_all(bind=self.engine, tables=self.tables)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
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
        self.client.close()
        Base.metadata.drop_all(bind=self.engine, tables=self.tables)
        self.engine.dispose()

    def test_profiles_numeric_and_categorical_columns(self):
        with patch("services.data_profiler._make_engine", return_value=self.engine):
            report = profile_source_tables(
                "postgresql://test",
                "main",
                ["customers"],
            )

        self.assertEqual(report["table_count"], 1)
        table = report["tables"][0]
        self.assertEqual(table["row_count"], 3)
        self.assertEqual(table["column_count"], 3)
        by_name = {column["column_name"]: column for column in table["columns"]}
        self.assertEqual(by_name["age"]["null_count"], 1)
        self.assertAlmostEqual(by_name["age"]["null_percentage"], 100 / 3)
        self.assertEqual(by_name["age"]["distinct_count"], 2)
        self.assertEqual(by_name["age"]["min"], 10)
        self.assertEqual(by_name["age"]["max"], 20)
        self.assertEqual(by_name["age"]["mean"], 15)
        self.assertEqual(by_name["age"]["median"], 15)
        self.assertEqual(by_name["city"]["top_values"][0]["value"], "Oslo")
        self.assertEqual(by_name["city"]["top_values"][0]["count"], 2)
        self.assertEqual(
            by_name["city"]["value_inventory"],
            [{"value": "Oslo", "count": 2, "percentage": 100.0}],
        )
        self.assertTrue(by_name["city"]["inventory_complete"])

    def test_rejects_unselected_or_unknown_tables(self):
        with patch("services.data_profiler._make_engine", return_value=self.engine):
            with self.assertRaisesRegex(Exception, "not found in schema"):
                profile_source_tables(
                    "postgresql://test",
                    "main",
                    ["missing_table"],
                )

    def test_inventory_cap_marks_profile_incomplete(self):
        with self.engine.begin() as connection:
            connection.execute(
                text("INSERT INTO customers (age, city, active) VALUES (30, 'Rome', 1)")
            )

        with (
            patch("services.data_profiler._make_engine", return_value=self.engine),
            patch("services.data_profiler.VALUE_INVENTORY_LIMIT", 1),
        ):
            report = profile_source_tables(
                "postgresql://test",
                "main",
                ["customers"],
            )

        city = next(
            column
            for column in report["tables"][0]["columns"]
            if column["column_name"] == "city"
        )
        self.assertEqual(len(city["value_inventory"]), 1)
        self.assertFalse(city["inventory_complete"])

    def test_unsupported_column_inventory_is_incomplete_when_nonempty(self):
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE binary_values (payload BLOB)"))
            connection.execute(
                text("INSERT INTO binary_values (payload) VALUES (X'0102')")
            )

        with patch("services.data_profiler._make_engine", return_value=self.engine):
            report = profile_source_tables(
                "postgresql://test",
                "main",
                ["binary_values"],
            )

        column = report["tables"][0]["columns"][0]
        self.assertFalse(column["inventory_complete"])
        self.assertEqual(column["value_inventory"], [])

    def test_profile_includes_duplicates_iqr_type_issues_and_score(self):
        with patch("services.data_profiler._make_engine", return_value=self.engine):
            report = profile_source_tables(
                "postgresql://test",
                "main",
                ["measurements"],
            )

        table = report["tables"][0]
        self.assertEqual(table["duplicate_rows"]["count"], 1)
        self.assertTrue(table["duplicate_rows"]["complete"])
        self.assertEqual(table["duplicate_keys"]["columns"], [])
        amount = next(column for column in table["columns"] if column["column_name"] == "amount")
        self.assertEqual(amount["outliers"]["count"], 1)
        self.assertAlmostEqual(amount["outliers"]["percentage"], 20.0)
        self.assertEqual(amount["quartiles"]["q1"], 1.0)
        self.assertEqual(amount["quartiles"]["q3"], 3.0)
        reference = next(
            column for column in table["columns"] if column["column_name"] == "reference"
        )
        self.assertTrue(reference["type_analysis"]["mixed_type"])
        self.assertEqual(reference["type_analysis"]["observed_types"], ["integer", "string"])
        self.assertEqual(reference["type_analysis"]["type_mismatch_count"], 5)
        self.assertGreaterEqual(table["quality_score"], 0)
        self.assertLessEqual(table["quality_score"], 100)
        self.assertEqual(report["summary"]["overall_quality_score"], table["quality_score"])
        self.assertEqual(report["summary"]["columns_above_null_threshold"], 1)
        self.assertTrue(report["recommendations"])

    def test_analyze_persists_snapshot_linked_to_pipeline_run(self):
        run_id = uuid4()
        with self.session_factory() as db:
            db.add(
                PipelineRun(
                    id=run_id,
                    pipeline_version_id=uuid4(),
                    project_id=uuid4(),
                    status="running",
                    started_at=datetime.utcnow(),
                )
            )
            db.commit()

        profile = {
            "schema_name": "public",
            "table_count": 1,
            "tables": [
                {
                    "table_name": "customers",
                    "row_count": 3,
                    "column_count": 1,
                    "columns": [],
                }
            ],
        }
        request_body = {
            "datasource": {
                "db_type": "postgresql",
                "host": "localhost",
                "database": "source",
                "username": "reader",
                "password": "secret",
            },
            "selected_tables": ["customers"],
            "pipeline_run_id": str(run_id),
        }

        with patch(
            "routers.profiling.profile_source_tables",
            return_value=profile,
        ):
            response = self.client.post("/profiling/analyze", json=request_body)

        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["pipeline_run_id"], str(run_id))
        self.assertEqual(payload["tables"][0]["row_count"], 3)
        self.assertNotIn("secret", response.text)
        with self.session_factory() as db:
            snapshot = db.query(SourceProfileSnapshot).one()
            self.assertEqual(snapshot.pipeline_run_id, run_id)
            self.assertEqual(snapshot.profile_json, profile)

    def test_analyze_rejects_invalid_null_threshold(self):
        response = self.client.post(
            "/profiling/analyze",
            json={
                "datasource": {
                    "db_type": "postgresql",
                    "host": "localhost",
                    "database": "source",
                },
                "selected_tables": ["customers"],
                "null_threshold": 101,
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_analyze_returns_and_persists_full_profile_report(self):
        request_body = {
            "datasource": {
                "db_type": "postgresql",
                "host": "localhost",
                "database": "source",
                "username": "reader",
                "password": "secret",
            },
            "selected_tables": ["customers"],
        }
        with (
            patch(
                "routers.profiling._connection_string",
                return_value="postgresql://test",
            ),
            patch("routers.profiling._resolved_schema", return_value="main"),
            patch("services.data_profiler._make_engine", return_value=self.engine),
            patch.object(self.engine, "dispose"),
        ):
            response = self.client.post("/profiling/analyze", json=request_body)

        self.assertEqual(response.status_code, 201, response.text)
        report = response.json()
        self.assertEqual(report["summary"]["tables_profiled"], 1)
        self.assertIn("quality_score", report["tables"][0])
        self.assertIn("duplicate_rows", report["tables"][0])
        self.assertNotIn("value_inventory", report["tables"][0]["columns"][0])
        self.assertNotIn("secret", response.text)
        with self.session_factory() as db:
            snapshot = db.query(SourceProfileSnapshot).one()
            self.assertIn("summary", snapshot.profile_json)
            self.assertIn(
                "value_inventory",
                snapshot.profile_json["tables"][0]["columns"][0],
            )

    def test_analyze_without_run_saves_unlinked_snapshot(self):
        request_body = {
            "datasource": {
                "db_type": "postgresql",
                "host": "localhost",
                "database": "source",
                "username": "reader",
            },
            "selected_tables": ["customers"],
        }
        with patch(
            "routers.profiling.profile_source_tables",
            return_value={
                "schema_name": "public",
                "table_count": 1,
                "tables": [],
                "summary": {
                    "overall_quality_score": 91.0,
                    "tables_profiled": 1,
                    "total_rows": 3,
                    "columns_above_null_threshold": 0,
                    "issue_counts": {
                        "critical": 0,
                        "high": 0,
                        "warning": 0,
                        "info": 0,
                    },
                    "null_threshold_percentage": 5.0,
                },
                "issues": [],
                "recommendations": ["No immediate issues detected."],
            },
        ):
            response = self.client.post("/profiling/analyze", json=request_body)

        self.assertEqual(response.status_code, 201, response.text)
        self.assertIsNone(response.json()["pipeline_run_id"])
        self.assertEqual(
            response.json()["summary"]["overall_quality_score"], 91.0
        )
        self.assertEqual(
            response.json()["recommendations"],
            ["No immediate issues detected."],
        )
        with self.session_factory() as db:
            snapshot = db.query(SourceProfileSnapshot).one()
            self.assertIsNone(snapshot.pipeline_run_id)


if __name__ == "__main__":
    unittest.main()
