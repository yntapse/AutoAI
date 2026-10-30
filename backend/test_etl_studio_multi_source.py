"""Visual ETL Pipeline Builder - several data sources in one pipeline (Step D1).

What is promised:
  * every Source step keeps the identity of the data source its table belongs to;
  * tables of other sources can be CHECKED, but never previewed, shown as SQL or
    measured while another source is the connected one;
  * a join across sources is refused with an explanation and generates no SQL;
  * a pipeline saved before this change (no source_id on the steps) behaves as before.

Same simulation as test_etl_studio_api.py: an in-memory SQLite for the application
database and a SQLite file standing in for the customer database. No live SQL Server
or PostgreSQL is used here.
"""

import unittest
from unittest.mock import patch
from uuid import uuid4

import test_etl_studio_api as _base  # module import, so its tests are not collected twice
from models.catalog_column import CatalogColumn
from models.catalog_scan import CatalogScan
from models.catalog_table import CatalogTable
from models.data_source import DataSource

VALIDATE = "/catalog/etl-studio/validate"
SQL = "/catalog/etl-studio/sql"
PREVIEW = "/catalog/etl-studio/preview"
DIAG = "/catalog/etl-studio/join-diagnostics"


class MultiSourceTests(unittest.TestCase):
    # Reuse the base fixture (application database, customer database stand-in, client)
    # without re-running the base tests.
    setUp = _base.EtlStudioApiTests.setUp
    tearDown = _base.EtlStudioApiTests.tearDown
    _seed_catalog = _base.EtlStudioApiTests._seed_catalog
    graph = _base.EtlStudioApiTests.graph
    preview_body = _base.EtlStudioApiTests.preview_body

    def _second_source(self, name="warehouse", db_type="mssql", host="localhost", database="meridian", port=1433, schema="main"):
        """A second registered source with its own Catalog table (default: same host and database)."""
        db = self.session_factory()
        try:
            source = DataSource(source_name=name, db_type=db_type, host=host, port=port, database=database,
                                username="svc_ro", status="registered")
            db.add(source)
            db.flush()
            scan = CatalogScan(data_source_id=source.id, schema_name=schema, status="completed")
            db.add(scan)
            db.flush()
            table = CatalogTable(scan_id=scan.id, data_source_id=source.id, schema_name=schema, table_name="accounts",
                                 column_count=2, row_count=3, row_count_is_estimate=False)
            db.add(table)
            db.flush()
            for pos, (cname, ctype) in enumerate([("customer_id", "INTEGER"), ("balance", "REAL")]):
                db.add(CatalogColumn(catalog_table_id=table.id, column_name=cname, data_type=ctype, ordinal_position=pos))
            db.commit()
            return str(source.id), str(table.id)
        finally:
            db.close()

    @staticmethod
    def src(node_id, table_id, source_id=None):
        cfg = {"table_id": table_id}
        if source_id is not None:
            cfg["source_id"] = source_id
        return {"id": node_id, "type": "source", "config": cfg}

    def body(self, nodes, edges=(), source_id=None):
        return {"source_id": source_id or self.source_id, "nodes": nodes, "edges": list(edges)}

    def validate(self, body):
        response = self.client.post(VALIDATE, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return {n["node_id"]: n for n in response.json()["nodes"]}

    def cross_graph(self, other_id, other_table):
        nodes = [
            self.src("c", self.table_ids["customers"], self.source_id),
            self.src("a", other_table, other_id),
            {"id": "j", "type": "join", "config": {"join_type": "LEFT", "conditions": [{"left_column": "customer_id", "right_column": "customer_id"}]}},
            {"id": "out", "type": "output", "config": {}},
        ]
        edges = [{"source": "c", "target": "j", "port": "left"}, {"source": "a", "target": "j", "port": "right"}, {"source": "j", "target": "out"}]
        return self.body(nodes, edges)

    # ------------------------------------------------ old pipelines still work
    def test_a_pipeline_without_source_ids_behaves_exactly_as_before(self):
        res = self.validate(self.graph())
        self.assertEqual(res["c"]["status"], "ready")
        self.assertEqual(res["c"]["source_ids"], [self.source_id])
        self.assertIsNone(res["c"]["preview_blocked_reason"])

    def test_a_legacy_pipeline_can_still_be_previewed(self):
        g = self.graph()
        response = self.client.post(PREVIEW, json=self.preview_body(g, "c"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreater(len(response.json()["rows"]), 0)

    def test_explicit_source_id_of_the_connected_source_is_equivalent(self):
        nodes = [self.src("c", self.table_ids["customers"], self.source_id)]
        res = self.validate(self.body(nodes))
        self.assertEqual(res["c"]["status"], "ready")
        self.assertIsNone(res["c"]["preview_blocked_reason"])

    # ------------------------------------------------------- identity is checked
    def test_a_step_cannot_claim_a_different_source_for_a_table(self):
        other_id, other_table = self._second_source()
        # The table is in `warehouse`, but the step says it is in the connected source.
        res = self.validate(self.body([self.src("x", other_table, self.source_id)]))
        self.assertEqual(res["x"]["status"], "invalid")
        self.assertTrue(any(i["field"] in ("source_id", "table_id") for i in res["x"]["issues"]))
        # And the other way round: a connected-source table claimed to be the warehouse's.
        res = self.validate(self.body([self.src("y", self.table_ids["customers"], other_id)]))
        self.assertEqual(res["y"]["status"], "invalid")

    def test_an_unregistered_source_id_is_not_trusted(self):
        res = self.validate(self.body([self.src("x", self.table_ids["customers"], str(uuid4()))]))
        self.assertEqual(res["x"]["status"], "invalid")

    def test_a_garbage_source_id_is_ignored_safely(self):
        res = self.validate(self.body([self.src("x", self.table_ids["customers"], "not-a-uuid")]))
        self.assertEqual(res["x"]["status"], "invalid")

    def test_each_table_reports_the_source_it_really_belongs_to(self):
        other_id, other_table = self._second_source()
        res = self.validate(self.body([self.src("c", self.table_ids["customers"], self.source_id), self.src("a", other_table, other_id)]))
        self.assertEqual(res["c"]["source_ids"], [self.source_id])
        self.assertEqual(res["a"]["source_ids"], [other_id])

    # --------------------------------------- tables of another source: check only
    def test_a_table_of_another_source_can_be_checked_but_is_marked_not_previewable(self):
        other_id, other_table = self._second_source(name="warehouse")
        res = self.validate(self.body([self.src("a", other_table, other_id)]))
        self.assertEqual(res["a"]["status"], "ready")
        self.assertEqual([c["name"] for c in res["a"]["columns"]], ["customer_id", "balance"])
        reason = res["a"]["preview_blocked_reason"]
        self.assertIn("warehouse (mssql)", reason)
        self.assertIn("meridian (postgresql)", reason)
        self.assertIn("main data source", reason)

    def test_sql_preview_and_measurement_refuse_another_sources_tables_without_touching_a_database(self):
        other_id, other_table = self._second_source()
        g = self.body([self.src("a", other_table, other_id)])
        with patch("routers.etl_studio.run_preview_query", side_effect=AssertionError("the database must not be contacted")) as run:
            for path, extra in ((SQL, {"node_id": "a"}), (PREVIEW, self.preview_body(g, "a"))):
                body = {**g, **extra} if path == SQL else extra
                response = self.client.post(path, json=body)
                self.assertEqual(response.status_code, 400, (path, response.text))
                self.assertIn("main data source", response.json()["detail"])
            run.assert_not_called()

    def test_opening_the_other_source_as_main_makes_its_tables_previewable_and_blocks_the_first_source(self):
        other_id, other_table = self._second_source()
        nodes = [self.src("a", other_table, other_id), self.src("c", self.table_ids["customers"], self.source_id)]
        res = self.validate(self.body(nodes, source_id=other_id))
        self.assertIsNone(res["a"]["preview_blocked_reason"])
        self.assertIn("meridian (postgresql)", res["c"]["preview_blocked_reason"])
        res = self.validate(self.body(nodes, source_id=self.source_id))
        self.assertIsNone(res["c"]["preview_blocked_reason"])
        self.assertIn("warehouse (mssql)", res["a"]["preview_blocked_reason"])

    # --------------------------------------------------- cross-source joins
    def test_a_cross_source_join_is_invalid_and_explains_why_and_what_to_do(self):
        other_id, other_table = self._second_source()
        res = self.validate(self.cross_graph(other_id, other_table))
        self.assertEqual(res["c"]["status"], "ready")
        self.assertEqual(res["a"]["status"], "ready")
        self.assertEqual(res["j"]["status"], "invalid")
        issue = res["j"]["issues"][0]
        self.assertEqual(issue["field"], "cross_source")
        for text in ("meridian (postgresql)", "warehouse (mssql)", "one database connection", "nothing is copied", "same server or database",
                     "use tables from one source", "view or a copy"):
            self.assertIn(text, issue["message"])
        self.assertIsNone(res["j"]["columns"])
        self.assertEqual(res["out"]["status"], "blocked")

    def test_the_same_server_and_database_registered_twice_is_still_two_sources(self):
        other_id, other_table = self._second_source(name="meridian copy", db_type="postgresql", host="localhost", database="meridian", port=5432)
        res = self.validate(self.cross_graph(other_id, other_table))
        self.assertEqual(res["j"]["status"], "invalid")
        self.assertEqual(res["j"]["issues"][0]["field"], "cross_source")

    def test_no_sql_is_built_or_run_for_a_cross_source_join_or_anything_after_it(self):
        other_id, other_table = self._second_source()
        g = self.cross_graph(other_id, other_table)
        with patch("routers.etl_studio.run_preview_query", side_effect=AssertionError("the database must not be contacted")) as run:
            for node in ("j", "out"):
                self.assertEqual(self.client.post(SQL, json={**g, "node_id": node}).status_code, 400)
                self.assertEqual(self.client.post(PREVIEW, json=self.preview_body(g, node)).status_code, 400)
                self.assertEqual(self.client.post(DIAG, json={**_base.CONNECTION, **g, "node_id": node}).status_code, 400)
            run.assert_not_called()

    def test_a_cross_source_join_does_not_spoil_the_single_source_branch_next_to_it(self):
        other_id, other_table = self._second_source()
        g = self.cross_graph(other_id, other_table)
        response = self.client.post(PREVIEW, json=self.preview_body(g, "c"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreater(len(response.json()["rows"]), 0)

    def test_a_same_source_join_is_unaffected_by_other_sources_on_the_canvas(self):
        other_id, other_table = self._second_source()
        g = _base.EtlStudioApiTests.join_graph(self)
        g["nodes"].append(self.src("a", other_table, other_id))
        res = self.validate(g)
        self.assertEqual(res["j"]["status"], "ready")
        self.assertEqual(res["j"]["source_ids"], [self.source_id])
        response = self.client.post(PREVIEW, json=self.preview_body(g, "j"))
        self.assertEqual(response.status_code, 200, response.text)

    # ------------------------------------------------------------ credentials
    def test_credentials_must_match_the_connected_source_whatever_the_steps_name(self):
        other_id, other_table = self._second_source(name="warehouse", db_type="postgresql", database="warehouse", port=5433)
        g = self.body([self.src("c", self.table_ids["customers"], self.source_id)])
        # Credentials of the OTHER source sent for this source: refused before anything runs.
        wrong = {**g, "node_id": "c", "limit": 5, **_base.CONNECTION, "database": "warehouse", "port": 5433}
        with patch("routers.etl_studio.run_preview_query", side_effect=AssertionError("the database must not be contacted")):
            response = self.client.post(PREVIEW, json=wrong)
        self.assertEqual(response.status_code, 400)
        self.assertIn("do not match", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
