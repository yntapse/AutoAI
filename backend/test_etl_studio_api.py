"""Visual ETL Pipeline Builder - API tests.

The application's own database is an in-memory SQLite. The "customer database"
is a temporary SQLite file reached by pointing db_connector's engine factory at
it, so the SELECTs the router builds are really executed. No network, no live
SQL Server, no live PostgreSQL.
"""

import os
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base

# All mappers must be importable before SQLAlchemy configures them (see
# test_catalog_api.py for the reason).
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
from routers.etl_studio import get_db, router
from services import db_connector
from test_pipeline_compiler import CAN_RUN_OUTER, build_db

PASSWORD = "s3cret-pw"
CONNECTION = {
    "db_type": "postgresql",
    "host": "localhost",
    "port": 5432,
    "database": "meridian",
    "username": "svc_ro",
    "password": PASSWORD,
    "schema_name": "main",
}


class EtlStudioApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.tables = [
            DataSource.__table__, CatalogScan.__table__, CatalogTable.__table__,
            CatalogColumn.__table__, CatalogRelationship.__table__,
        ]
        Base.metadata.create_all(bind=self.engine, tables=self.tables)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        build_db(self.path)
        patcher = patch.object(db_connector, "_make_engine", lambda cs: create_engine(f"sqlite:///{self.path}"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

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
        self.source_id, self.table_ids = self._seed_catalog()

    def tearDown(self):
        Base.metadata.drop_all(bind=self.engine, tables=self.tables)
        self.engine.dispose()

    # ---------------------------------------------------------------- helpers
    def _seed_catalog(self, db_type="postgresql", name="meridian"):
        db = self.session_factory()
        try:
            source = DataSource(
                source_name=name, db_type=db_type, host="localhost", port=5432,
                database="meridian", username="svc_ro", status="registered",
            )
            db.add(source)
            db.flush()
            scan = CatalogScan(data_source_id=source.id, schema_name="main", status="completed")
            db.add(scan)
            db.flush()
            ids = {}
            layout = {
                "customers": [("customer_id", "INTEGER"), ("full_name", "TEXT"), ("region", "TEXT"),
                              ("age", "INTEGER"), ("score", "REAL"), ("joined", "DATE")],
                "orders": [("order_id", "INTEGER"), ("customer_id", "INTEGER"), ("amount", "REAL"), ("status", "TEXT")],
            }
            for name, cols in layout.items():
                table = CatalogTable(
                    scan_id=scan.id, data_source_id=source.id, schema_name="main", table_name=name,
                    column_count=len(cols), row_count=6, row_count_is_estimate=False,
                )
                db.add(table)
                db.flush()
                for pos, (cname, ctype) in enumerate(cols):
                    db.add(CatalogColumn(catalog_table_id=table.id, column_name=cname, data_type=ctype, ordinal_position=pos))
                ids[name] = str(table.id)
            db.commit()
            return str(source.id), ids
        finally:
            db.close()

    def graph(self, *extra_nodes, extra_edges=()):
        nodes = [
            {"id": "c", "type": "source", "config": {"table_id": self.table_ids["customers"]}},
            {"id": "o", "type": "source", "config": {"table_id": self.table_ids["orders"]}},
            *extra_nodes,
        ]
        return {"source_id": self.source_id, "nodes": nodes, "edges": list(extra_edges)}

    def join_graph(self, join_type="INNER"):
        join = {"id": "j", "type": "join", "config": {
            "join_type": join_type, "conditions": [{"left_column": "customer_id", "right_column": "customer_id"}]}}
        g = self.graph(join, {"id": "out", "type": "output", "config": {}},
                       extra_edges=[
                           {"source": "c", "target": "j", "port": "left"},
                           {"source": "o", "target": "j", "port": "right"},
                           {"source": "j", "target": "out"},
                       ])
        return g

    def preview_body(self, graph, node_id, limit=25, **over):
        return {**CONNECTION, **graph, "node_id": node_id, "limit": limit, **over}

    # ----------------------------------------------------------------- cookie
    def test_every_route_sits_under_the_session_cookie_path(self):
        # The credential-session cookie is set with Path=/catalog. A browser sends
        # it only to URLs under that path, so a route elsewhere would get a 401 in
        # the real app even though these unit tests (which ignore cookie paths) pass.
        import inspect
        from routers import catalog as catalog_module
        self.assertIn('path="/catalog"', inspect.getsource(catalog_module.connect_source_session))
        paths = [r.path for r in router.routes]
        self.assertTrue(paths)
        for path in paths:
            self.assertTrue(path.startswith("/catalog/"), path)

    # --------------------------------------------------------------- validate
    def test_validate_reports_every_node_with_columns_from_the_catalog(self):
        response = self.client.post("/catalog/etl-studio/validate", json=self.join_graph())
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        by_id = {n["node_id"]: n for n in body["nodes"]}
        self.assertEqual({n for n in by_id}, {"c", "o", "j", "out"})
        self.assertEqual(by_id["c"]["status"], "ready")
        self.assertEqual(by_id["j"]["status"], "ready")
        self.assertEqual(by_id["out"]["status"], "ready")
        self.assertIn("customer_id_right", [c["name"] for c in by_id["out"]["columns"]])
        self.assertEqual(body["downstream"]["c"], ["j", "out"])
        self.assertEqual(body["downstream"]["out"], [])

    def test_validate_does_not_need_or_use_credentials(self):
        with patch.object(db_connector, "_make_engine", side_effect=AssertionError("database contacted")):
            response = self.client.post("/catalog/etl-studio/validate", json=self.join_graph())
        self.assertEqual(response.status_code, 200)

    def test_missing_column_and_unconfigured_nodes_are_reported_not_raised(self):
        g = self.graph(
            {"id": "f", "type": "filter", "config": {"conditions": [{"column": "nope", "operator": "eq", "value": 1}]}},
            {"id": "out", "type": "output", "config": {}},
            extra_edges=[{"source": "c", "target": "f"}, {"source": "f", "target": "out"}],
        )
        body = self.client.post("/catalog/etl-studio/validate", json=g).json()
        by_id = {n["node_id"]: n for n in body["nodes"]}
        self.assertEqual(by_id["f"]["status"], "invalid")
        self.assertEqual(by_id["out"]["status"], "blocked")
        self.assertTrue(any("nope" in i["message"] for i in by_id["f"]["issues"]))
        self.assertEqual(by_id["o"]["status"], "ready")  # the unconnected orders source is fine

    def test_a_table_from_another_source_is_not_trusted(self):
        other_id, other_tables = self._seed_catalog(name="other_source")
        g = {"source_id": self.source_id, "edges": [],
             "nodes": [{"id": "x", "type": "source", "config": {"table_id": other_tables["customers"]}}]}
        body = self.client.post("/catalog/etl-studio/validate", json=g).json()
        self.assertEqual(body["nodes"][0]["status"], "invalid")

    def test_garbage_table_ids_and_unknown_sources(self):
        g = {"source_id": self.source_id, "edges": [], "nodes": [{"id": "x", "type": "source", "config": {"table_id": "not-a-uuid"}}]}
        self.assertEqual(self.client.post("/catalog/etl-studio/validate", json=g).json()["nodes"][0]["status"], "invalid")
        g["source_id"] = str(uuid4())
        self.assertEqual(self.client.post("/catalog/etl-studio/validate", json=g).status_code, 404)

    def test_request_level_problems_are_400(self):
        dup = {"source_id": self.source_id, "edges": [],
               "nodes": [{"id": "a", "type": "source", "config": {}}, {"id": "a", "type": "source", "config": {}}]}
        self.assertEqual(self.client.post("/catalog/etl-studio/validate", json=dup).status_code, 400)
        loop = self.graph({"id": "f", "type": "filter", "config": {}}, {"id": "g", "type": "filter", "config": {}},
                          extra_edges=[{"source": "f", "target": "g"}, {"source": "g", "target": "f"}])
        self.assertEqual(self.client.post("/catalog/etl-studio/validate", json=loop).status_code, 400)

    # -------------------------------------------------------------------- sql
    def test_sql_endpoint_shows_the_statement_and_parameters_without_running_it(self):
        g = self.graph({"id": "f", "type": "filter", "config": {"conditions": [{"column": "region", "operator": "eq", "value": "West"}]}},
                       extra_edges=[{"source": "c", "target": "f"}])
        with patch.object(db_connector, "_make_engine", side_effect=AssertionError("database contacted")):
            response = self.client.post("/catalog/etl-studio/sql", json={**g, "node_id": "f"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("SELECT", body["sql"])
        self.assertNotIn("West", body["sql"])          # the value is a parameter
        self.assertIn("West", body["parameters"].values())
        self.assertIn("read-only", body["execution_path"])

    def test_sql_for_a_broken_node_is_refused(self):
        g = self.graph({"id": "f", "type": "filter", "config": {}}, extra_edges=[{"source": "c", "target": "f"}])
        self.assertEqual(self.client.post("/catalog/etl-studio/sql", json={**g, "node_id": "f"}).status_code, 400)
        self.assertEqual(self.client.post("/catalog/etl-studio/sql", json={**g, "node_id": "zzz"}).status_code, 404)

    # ---------------------------------------------------------------- preview
    def test_preview_returns_real_rows(self):
        response = self.client.post("/catalog/etl-studio/preview", json=self.preview_body(self.join_graph(), "j"))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body["rows"]), 4)               # inner join of the seeded data
        self.assertFalse(body["has_more"])
        names = [c["name"] for c in body["columns"]]
        self.assertEqual(len(names), len(body["rows"][0]))
        self.assertNotIn(PASSWORD, response.text)

    def test_preview_limit_is_enforced_by_the_database(self):
        g = self.graph(extra_edges=[])
        response = self.client.post("/catalog/etl-studio/preview", json=self.preview_body(g, "c", limit=2))
        body = response.json()
        self.assertEqual(len(body["rows"]), 2)
        self.assertTrue(body["has_more"])
        self.assertEqual(body["row_limit"], 2)

    def test_preview_limit_range_is_validated(self):
        g = self.graph()
        for bad in (0, 101, -5):
            self.assertEqual(self.client.post("/catalog/etl-studio/preview", json=self.preview_body(g, "c", limit=bad)).status_code, 422)

    def test_preview_of_a_blocked_node_is_a_clear_error_and_runs_nothing(self):
        g = self.graph({"id": "f", "type": "filter", "config": {}}, extra_edges=[{"source": "c", "target": "f"}])
        with patch.object(db_connector, "_make_engine", side_effect=AssertionError("database contacted")):
            response = self.client.post("/catalog/etl-studio/preview", json=self.preview_body(g, "f"))
        self.assertEqual(response.status_code, 400)

    def test_transform_chain_executes(self):
        g = self.graph(
            {"id": "f", "type": "filter", "config": {"conditions": [{"column": "region", "operator": "eq", "value": "West"}]}},
            {"id": "d", "type": "drop_columns", "config": {"columns": ["score", "joined", "customer_id"]}},
            {"id": "r", "type": "rename_columns", "config": {"mappings": [{"from": "full_name", "to": "name"}]}},
            {"id": "u", "type": "remove_duplicates", "config": {}},
            extra_edges=[{"source": "c", "target": "f"}, {"source": "f", "target": "d"},
                         {"source": "d", "target": "r"}, {"source": "r", "target": "u"}],
        )
        body = self.client.post("/catalog/etl-studio/preview", json=self.preview_body(g, "u")).json()
        self.assertEqual([c["name"] for c in body["columns"]], ["name", "region", "age"])
        self.assertEqual(len(body["rows"]), 2)   # West: Asha(30) twice collapses, Chitra(41)

    def test_injection_attempt_in_a_value_is_just_text(self):
        evil = "x'; DROP TABLE customers; --"
        g = self.graph({"id": "f", "type": "filter", "config": {"conditions": [{"column": "region", "operator": "eq", "value": evil}]}},
                       extra_edges=[{"source": "c", "target": "f"}])
        body = self.client.post("/catalog/etl-studio/preview", json=self.preview_body(g, "f")).json()
        self.assertEqual(body["rows"], [])
        again = self.client.post("/catalog/etl-studio/preview", json=self.preview_body(self.graph(), "c")).json()
        self.assertEqual(len(again["rows"]), 6)    # customers still exists

    def test_connection_must_match_the_registered_source(self):
        body = self.preview_body(self.graph(), "c", host="evil.example.com")
        response = self.client.post("/catalog/etl-studio/preview", json=body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("host", response.json()["detail"])

    def test_session_required_when_no_password_is_sent(self):
        body = self.preview_body(self.graph(), "c")
        del body["password"]
        response = self.client.post("/catalog/etl-studio/preview", json=body, headers={"X-Requested-By": "catalog-ui"})
        self.assertEqual(response.status_code, 401)
        no_header = self.client.post("/catalog/etl-studio/preview", json=body)
        self.assertEqual(no_header.status_code, 403)

    def test_database_errors_are_returned_without_the_password(self):
        from services.db_connector import DataSourceError
        with patch("routers.etl_studio.run_preview_query", side_effect=DataSourceError(f"login failed for {PASSWORD}")):
            response = self.client.post("/catalog/etl-studio/preview", json=self.preview_body(self.graph(), "c"))
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(PASSWORD, response.text)
        self.assertIn("****", response.text)

    # ------------------------------------------------------------ diagnostics
    def test_join_diagnostics_measure_the_real_data(self):
        body = {**CONNECTION, **self.join_graph("LEFT"), "node_id": "j"}
        response = self.client.post("/catalog/etl-studio/join-diagnostics", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        s = response.json()["summary"]
        self.assertEqual((s["left_rows"], s["right_rows"]), (6, 6))
        self.assertEqual(s["matched_pairs"], 4)
        self.assertEqual(s["output_rows"], 8)
        self.assertTrue(s["multiplies_left_rows"])
        self.assertEqual(response.json()["key_pairs"], [{"left": "customer_id", "right": "customer_id"}])

    def test_diagnostics_only_for_a_join(self):
        body = {**CONNECTION, **self.join_graph(), "node_id": "c"}
        self.assertEqual(self.client.post("/catalog/etl-studio/join-diagnostics", json=body).status_code, 400)

    def test_full_join_preview_when_sqlite_supports_it(self):
        if not CAN_RUN_OUTER:
            self.skipTest("this SQLite build is older than 3.39 and cannot run FULL joins")
        body = self.client.post("/catalog/etl-studio/preview", json=self.preview_body(self.join_graph("FULL"), "j", limit=50)).json()
        self.assertEqual(len(body["rows"]), 10)


if __name__ == "__main__":
    unittest.main()
