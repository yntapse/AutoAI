"""Paged table preview - POST /catalog/tables/{id}/preview.

No network and no real database server is needed:
  * The catalog lives in SQLite in memory, like test_catalog_api.py.
  * The "customer database" is a temporary SQLite file. db_connector's engine
    factory is pointed at it, so the SQL this feature generates for PostgreSQL
    is actually EXECUTED and its rows checked.
  * SQL Server SQL cannot run on SQLite, so for SQL Server the tests check the
    generated text and the connection-identity rules only. A live SQL Server
    and a live PostgreSQL are still needed for a final check.
"""

import json
import os
import py_compile
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from datetime import date, datetime, time
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base

# Same reason as test_catalog_api.py: routers.catalog imports routers.etl_copilot,
# so every mapped model must be imported before mappers are configured.
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
from services import db_connector, metadata_scanner, table_preview
from services.db_connector import (
    DataSourceConnectionError,
    DataSourceError,
    DataSourceTableNotFound,
    build_page_query,
    extract_to_dataframe,
    preview_query,
    preview_to_dataframe,
    run_preview_query,
)
from services.table_preview import (
    PreviewCursorError,
    choose_ordering,
    decode_cursor,
    encode_cursor,
    get_table_page,
    json_cell,
)

BACKEND = os.path.dirname(os.path.abspath(__file__))
PG_CONN = "postgresql+psycopg2://svc_ro:pw@localhost:5432/meridian"


def col(name, data_type="INTEGER", pk=False):
    return {"column_name": name, "data_type": data_type, "is_primary_key": pk}


# ---------------------------------------------------------------------------
# 1. Syntax
# ---------------------------------------------------------------------------


class SyntaxTests(unittest.TestCase):
    def test_changed_files_compile(self):
        for rel in (
            "services/db_connector.py",
            "services/metadata_scanner.py",
            "services/table_preview.py",
            "routers/catalog.py",
        ):
            with self.subTest(file=rel):
                py_compile.compile(os.path.join(BACKEND, rel), doraise=True)


# ---------------------------------------------------------------------------
# 2. Query generation (text only)
# ---------------------------------------------------------------------------


class QueryGenerationTests(unittest.TestCase):
    def test_postgresql_first_page_without_schema(self):
        sql, params = build_page_query("postgresql", "orders", page_size=20, order_by=["id"])
        self.assertEqual(sql, 'SELECT * FROM "orders" ORDER BY "id" LIMIT 21')
        self.assertEqual(params, {})

    def test_postgresql_with_schema_and_offset(self):
        sql, _ = build_page_query(
            "postgresql", "orders", schema="sales", page_size=20, order_by=["id"], offset=40
        )
        self.assertEqual(sql, 'SELECT * FROM "sales"."orders" ORDER BY "id" LIMIT 21 OFFSET 40')

    def test_postgresql_unordered_has_no_order_by(self):
        sql, _ = build_page_query("postgresql", "orders", schema="sales")
        self.assertEqual(sql, 'SELECT * FROM "sales"."orders" LIMIT 21')

    def test_postgresql_keyset_single_key(self):
        sql, params = build_page_query(
            "postgresql", "orders", schema="sales", page_size=10, order_by=["id"], after=[7]
        )
        self.assertEqual(
            sql, 'SELECT * FROM "sales"."orders" WHERE ("id" > :k0) ORDER BY "id" LIMIT 11'
        )
        self.assertEqual(params, {"k0": 7})

    def test_keyset_composite_key(self):
        sql, params = build_page_query(
            "postgresql", "lines", order_by=["a", "b"], after=[1, 9]
        )
        self.assertEqual(
            sql,
            'SELECT * FROM "lines" WHERE ("a" > :k0) OR ("a" = :k0 AND "b" > :k1) '
            'ORDER BY "a", "b" LIMIT 21',
        )
        self.assertEqual(params, {"k0": 1, "k1": 9})

    def test_sqlserver_without_schema_unordered_uses_top(self):
        sql, _ = build_page_query("mssql", "orders")
        self.assertEqual(sql, "SELECT TOP 21 * FROM [orders]")

    def test_sqlserver_with_schema_ordered_uses_offset_fetch(self):
        sql, _ = build_page_query(
            "mssql", "orders", schema="dbo", page_size=20, order_by=["id"], offset=20
        )
        self.assertEqual(
            sql,
            "SELECT * FROM [dbo].[orders] ORDER BY [id] OFFSET 20 ROWS FETCH NEXT 21 ROWS ONLY",
        )

    def test_sqlserver_keyset_with_schema(self):
        sql, params = build_page_query(
            "mssql", "orders", schema="dbo", order_by=["a", "b"], after=["x", 3]
        )
        self.assertEqual(
            sql,
            "SELECT * FROM [dbo].[orders] WHERE ([a] > :k0) OR ([a] = :k0 AND [b] > :k1) "
            "ORDER BY [a], [b] OFFSET 0 ROWS FETCH NEXT 21 ROWS ONLY",
        )
        self.assertEqual(params, {"k0": "x", "k1": 3})

    def test_sqlserver_offset_without_key_orders_by_select_null(self):
        sql, _ = build_page_query("mssql", "orders", schema="dbo", offset=20)
        self.assertEqual(
            sql,
            "SELECT * FROM [dbo].[orders] ORDER BY (SELECT NULL) "
            "OFFSET 20 ROWS FETCH NEXT 21 ROWS ONLY",
        )

    def test_identifiers_are_quoted_and_escaped(self):
        sql, _ = build_page_query("mssql", "we]ird", schema="s]x", order_by=["c]d"])
        self.assertIn("[s]]x].[we]]ird]", sql)
        self.assertIn("ORDER BY [c]]d]", sql)
        sql, _ = build_page_query("postgresql", 'we"ird', order_by=['c"d'])
        self.assertIn('"we""ird"', sql)
        self.assertIn('ORDER BY "c""d"', sql)

    def test_colon_in_a_name_is_not_a_bind_parameter(self):
        sql, _ = build_page_query("postgresql", "t", order_by=["a:b"])
        self.assertIn('"a\\:b"', sql)

    def test_hostile_table_name_stays_one_quoted_identifier(self):
        sql, _ = build_page_query("postgresql", 'x"; DROP TABLE y; --')
        self.assertIn('"x""; DROP TABLE y; --"', sql)
        # ...and the read-only guard still refuses anything with a semicolon.
        with patch.object(db_connector, "_make_engine", side_effect=AssertionError("must not connect")):
            with self.assertRaises(DataSourceError):
                run_preview_query(PG_CONN, sql)

    def test_bad_parameters_are_rejected(self):
        for kwargs in (
            {"page_size": 0},
            {"page_size": 101},
            {"page_size": "20"},
            {"page_size": True},
            {"offset": -1},
            {"offset": 1.5},
            {"order_by": ["a", "a"]},
            {"order_by": [""]},
            {"after": [1]},  # keyset without a sort key
            {"order_by": ["a", "b"], "after": [1]},  # wrong length
            {"order_by": ["a"], "after": [None]},
            {"order_by": ["a"], "after": [1], "offset": 5},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(DataSourceError):
                    build_page_query("postgresql", "t", **kwargs)
        with self.assertRaises(DataSourceError):
            build_page_query("oracle", "t")
        with self.assertRaises(DataSourceError):
            build_page_query("postgresql", "")

    def test_values_are_never_pasted_into_sql(self):
        sql, params = build_page_query(
            "postgresql", "t", order_by=["id"], after=["1' OR '1'='1"]
        )
        self.assertNotIn("OR '1'='1", sql)
        self.assertEqual(params["k0"], "1' OR '1'='1")


# ---------------------------------------------------------------------------
# 3. Existing callers keep working
# ---------------------------------------------------------------------------


class ExistingCallerTests(unittest.TestCase):
    def test_preview_query_output_is_unchanged(self):
        self.assertEqual(preview_query("postgresql", "t"), 'SELECT * FROM "t" LIMIT 5')
        self.assertEqual(preview_query("mssql", "t", 3), "SELECT TOP 3 * FROM [t]")
        self.assertEqual(
            preview_query("postgresql", "t", 7, "sales"), 'SELECT * FROM "sales"."t" LIMIT 7'
        )
        self.assertEqual(
            preview_query("mssql", "t", limit=7, schema="dbo"), "SELECT TOP 7 * FROM [dbo].[t]"
        )

    def test_preview_to_dataframe_and_extract_still_work(self):
        path = _make_db(["CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"],
                        [("INSERT INTO t VALUES (?, ?)", [(i, f"v{i}") for i in range(1, 8)])])
        try:
            with _engine_at(path):
                frame, has_more = preview_to_dataframe(PG_CONN, 'SELECT * FROM "t" ORDER BY "id"', limit=5)
                self.assertEqual(len(frame), 5)
                self.assertTrue(has_more)
                frame, has_more = preview_to_dataframe(PG_CONN, 'SELECT * FROM "t" ORDER BY "id"', limit=7)
                self.assertEqual(len(frame), 7)
                self.assertFalse(has_more)
                self.assertEqual(len(extract_to_dataframe(PG_CONN, 'SELECT * FROM "t"')), 7)
                with self.assertRaises(DataSourceError):  # unchanged: empty -> error
                    preview_to_dataframe(PG_CONN, 'SELECT * FROM "t" WHERE "id" > 99')
                with self.assertRaises(DataSourceError):  # unchanged: SELECT only
                    preview_to_dataframe(PG_CONN, 'DELETE FROM "t"')
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# 4. Schema-aware metadata sampling
# ---------------------------------------------------------------------------


class SamplingTests(unittest.TestCase):
    def test_sample_values_query_is_schema_qualified(self):
        import pandas as pd

        seen = []

        def fake_extract(connection_string, query, max_rows=0, params=None):
            seen.append(query)
            return pd.DataFrame({"id": [1, 2]})

        with patch.object(metadata_scanner, "extract_to_dataframe", fake_extract):
            out = metadata_scanner._sample_values(
                PG_CONN, "postgresql", "orders", [{"name": "id"}], schema="sales"
            )
            self.assertIn('"sales"."orders"', seen[-1])
            self.assertEqual(out, {"id": ["1", "2"]})
            metadata_scanner._sample_values(PG_CONN, "mssql", "orders", [{"name": "id"}], schema="hr")
            self.assertIn("[hr].[orders]", seen[-1])
            metadata_scanner._sample_values(PG_CONN, "postgresql", "orders", [{"name": "id"}])
            self.assertIn('FROM "orders"', seen[-1])  # schema stays optional

    def test_scan_source_passes_resolved_schema_to_sampling(self):
        with patch.object(metadata_scanner, "list_tables", return_value=["orders"]), \
             patch.object(metadata_scanner, "list_columns", return_value={"orders": [
                 {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True}]}), \
             patch.object(metadata_scanner, "list_foreign_keys", return_value=[]), \
             patch.object(metadata_scanner, "_sample_values", return_value={}) as sampler:
            metadata_scanner.scan_source(
                PG_CONN, "postgresql", schema="sales",
                include_row_counts=False, include_sample_values=True,
            )
        self.assertEqual(sampler.call_args.kwargs["schema"], "sales")
        # default schema when none is given
        with patch.object(metadata_scanner, "list_tables", return_value=["orders"]), \
             patch.object(metadata_scanner, "list_columns", return_value={"orders": [
                 {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True}]}), \
             patch.object(metadata_scanner, "list_foreign_keys", return_value=[]), \
             patch.object(metadata_scanner, "_sample_values", return_value={}) as sampler:
            metadata_scanner.scan_source(
                PG_CONN, "mssql", include_row_counts=False, include_sample_values=True
            )
        self.assertEqual(sampler.call_args.kwargs["schema"], "dbo")


# ---------------------------------------------------------------------------
# Helpers for a throw-away "customer database"
# ---------------------------------------------------------------------------


def _make_db(ddl, inserts=()):
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    conn = sqlite3.connect(path)
    for statement in ddl:
        conn.execute(statement)
    for statement, rows in inserts:
        conn.executemany(statement, rows)
    conn.commit()
    conn.close()
    return path


def _engine_at(path, calls=None):
    def factory(_connection_string):
        if calls is not None:
            calls.append(1)
        return create_engine(f"sqlite:///{path}")

    return patch.object(db_connector, "_make_engine", factory)


def walk(columns, limit, path, schema="main", table="t", db_type="postgresql"):
    """Follow next_cursor until the table is exhausted. Returns the pages."""
    pages, cursor = [], None
    with _engine_at(path):
        for _ in range(500):
            page = get_table_page(PG_CONN, db_type, schema, table, columns, limit, cursor)
            pages.append(page)
            cursor = page["next_cursor"]
            if not page["has_more"]:
                return pages
    raise AssertionError("pagination never ended")


# ---------------------------------------------------------------------------
# 5. Pagination behaviour against executed SQL
# ---------------------------------------------------------------------------


class PaginationTests(unittest.TestCase):
    def setUp(self):
        self._paths = []

    def tearDown(self):
        for path in self._paths:
            os.unlink(path)

    def _table(self, n):
        path = _make_db(
            ["CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)"],
            [("INSERT INTO t VALUES (?, ?)", [(i, f"n{i}") for i in range(1, n + 1)])],
        )
        self._paths.append(path)
        return path

    COLS = [col("id", pk=True), col("name", "TEXT")]

    def test_pages_do_not_repeat_the_first_rows(self):
        pages = walk(self.COLS, 20, self._table(45))
        self.assertEqual([p["row_count"] for p in pages], [20, 20, 5])
        ids = [row["id"] for page in pages for row in page["rows"]]
        self.assertEqual(ids, list(range(1, 46)))  # every row once, in order
        self.assertEqual(pages[1]["rows"][0]["id"], 21)
        self.assertEqual(pages[2]["rows"][0]["id"], 41)
        self.assertEqual([p["start_row"] for p in pages], [1, 21, 41])
        self.assertTrue(all(p["pagination_mode"] == "keyset" for p in pages))
        self.assertTrue(all(p["stable_order"] for p in pages))

    def test_has_more_is_correct_at_page_boundaries(self):
        cases = {
            0: [False],
            1: [False],
            19: [False],
            20: [False],  # exactly one full page: nothing more
            21: [True, False],
            39: [True, False],
            40: [True, False],  # exactly two full pages
            41: [True, True, False],
        }
        for n, expected in cases.items():
            with self.subTest(rows=n):
                pages = walk(self.COLS, 20, self._table(n))
                self.assertEqual([p["has_more"] for p in pages], expected)
                self.assertEqual(sum(p["row_count"] for p in pages), n)
                for page in pages:  # a token exists exactly when more rows exist
                    self.assertEqual(page["next_cursor"] is not None, page["has_more"])

    def test_page_size_only_limits_one_page(self):
        pages = walk(self.COLS, 100, self._table(250))
        self.assertEqual([p["row_count"] for p in pages], [100, 100, 50])
        self.assertEqual(sum(p["row_count"] for p in pages), 250)

    def test_empty_table(self):
        path = self._table(0)
        with _engine_at(path):
            page = get_table_page(PG_CONN, "postgresql", "main", "t", self.COLS, 20)
        self.assertEqual(page["rows"], [])
        self.assertEqual(page["column_names"], ["id", "name"])  # columns still known
        self.assertFalse(page["has_more"])
        self.assertIsNone(page["next_cursor"])
        self.assertEqual(page["message"], "This table is empty.")

    def test_composite_key_has_no_gaps_or_repeats(self):
        path = _make_db(
            ["CREATE TABLE t (a INTEGER, b INTEGER, v TEXT, PRIMARY KEY (a, b))"],
            [("INSERT INTO t VALUES (?, ?, 'x')", [(a, b) for a in (1, 2, 3) for b in range(1, 16)])],
        )
        self._paths.append(path)
        cols = [col("a", pk=True), col("b", pk=True), col("v", "TEXT")]
        pages = walk(cols, 7, path)
        keys = [(r["a"], r["b"]) for p in pages for r in p["rows"]]
        self.assertEqual(keys, [(a, b) for a in (1, 2, 3) for b in range(1, 16)])

    def test_text_key(self):
        path = _make_db(
            ["CREATE TABLE t (code TEXT PRIMARY KEY, v INTEGER)"],
            [("INSERT INTO t VALUES (?, 1)", [(f"C{i:04d}",) for i in range(1, 51)])],
        )
        self._paths.append(path)
        pages = walk([col("code", "TEXT", pk=True), col("v")], 20, path)
        codes = [r["code"] for p in pages for r in p["rows"]]
        self.assertEqual(codes, [f"C{i:04d}" for i in range(1, 51)])

    def test_table_without_primary_key_uses_offset_and_covers_every_row(self):
        rows = [("a", 1)] * 3 + [("b", 2)] * 30 + [("c", 3)] * 12  # many identical rows
        path = _make_db(
            ["CREATE TABLE t (kind TEXT, amount INTEGER)"],
            [("INSERT INTO t VALUES (?, ?)", rows)],
        )
        self._paths.append(path)
        pages = walk([col("kind", "TEXT"), col("amount")], 20, path)
        got = [(r["kind"], r["amount"]) for p in pages for r in p["rows"]]
        self.assertEqual(sorted(got), sorted(rows))
        self.assertEqual(len(got), 45)
        self.assertTrue(all(p["pagination_mode"] == "offset" for p in pages))
        self.assertTrue(all(p["stable_order"] for p in pages))  # all columns sortable
        self.assertEqual([p["start_row"] for p in pages], [1, 21, 41])

    def test_unsortable_columns_are_reported_as_unstable(self):
        order, basis, stable = choose_ordering("mssql", [col("a"), col("b", "TEXT"), col("c", "XML")])
        self.assertEqual((order, basis, stable), (["a"], "all_columns", False))
        order, basis, stable = choose_ordering("postgresql", [col("a"), col("b", "TEXT"), col("c", "JSON")])
        self.assertEqual((order, basis, stable), (["a", "b"], "all_columns", False))
        self.assertEqual(choose_ordering("mssql", [col("a", "TEXT")]), ([], "none", False))
        self.assertEqual(
            choose_ordering("mssql", [col("a"), col("b", pk=True)]), (["b"], "primary_key", True)
        )

    def test_unencodable_key_falls_back_to_position_paging(self):
        calls = []

        def fake_run(connection_string, query, params=None, max_rows=21):
            calls.append((query, params))
            return ["id"], [(object(),) for _ in range(max_rows)]

        with patch.object(table_preview, "run_preview_query", fake_run):
            first = get_table_page(PG_CONN, "postgresql", "main", "t", [col("id", pk=True)], 20)
            self.assertTrue(first["has_more"])
            second = get_table_page(
                PG_CONN, "postgresql", "main", "t", [col("id", pk=True)], 20, first["next_cursor"]
            )
        self.assertIn("OFFSET 20", calls[1][0])  # same ORDER BY, now by position
        self.assertEqual(second["start_row"], 21)

    def test_decimal_and_date_keys_survive_the_cursor(self):
        fp = "abc"
        for key in ([Decimal("12.50")], [date(2026, 1, 2)], [datetime(2026, 1, 2, 3, 4, 5, 678)],
                    [time(1, 2, 3)], [UUID(int=5)], [b"\x00\xff"], [2**70], [1.5], ["é"], [True],
                    [1, "x", Decimal("0.1")]):
            with self.subTest(key=key):
                state = decode_cursor(encode_cursor(fp, "keyset", 40, key), fp)
                self.assertEqual(state["key"], key)
                self.assertEqual(state["consumed"], 40)

    def test_bad_tokens_are_rejected(self):
        fp = "abc"
        good = encode_cursor(fp, "keyset", 20, [5])
        with self.assertRaises(PreviewCursorError):
            decode_cursor(good, "other")  # issued for another table
        for bad in ("", "!!!", "e30", "bm90LWpzb24", encode_cursor(fp, "keyset", 20, None) if False else "e30"):
            with self.subTest(token=bad):
                with self.assertRaises(PreviewCursorError):
                    decode_cursor(bad, fp)

    def test_token_for_one_table_is_refused_by_another(self):
        path = self._table(45)
        with _engine_at(path):
            page = get_table_page(PG_CONN, "postgresql", "main", "t", self.COLS, 20)
            with self.assertRaises(PreviewCursorError):
                get_table_page(PG_CONN, "postgresql", "main", "other", self.COLS, 20, page["next_cursor"])

    def test_limit_is_validated(self):
        for bad in (0, -1, 101, "20", None, True):
            with self.subTest(limit=bad):
                with self.assertRaises(DataSourceError):
                    get_table_page(PG_CONN, "postgresql", "main", "t", self.COLS, bad)


# ---------------------------------------------------------------------------
# 6. Values and errors
# ---------------------------------------------------------------------------


class ValueAndErrorTests(unittest.TestCase):
    def test_null_is_json_null(self):
        path = _make_db(
            ["CREATE TABLE t (id INTEGER PRIMARY KEY, email TEXT, amount REAL)"],
            [("INSERT INTO t VALUES (?, ?, ?)", [(1, None, None), (2, "a@b.c", 1.5)])],
        )
        try:
            with _engine_at(path):
                page = get_table_page(
                    PG_CONN, "postgresql", "main", "t",
                    [col("id", pk=True), col("email", "TEXT"), col("amount", "REAL")], 20,
                )
        finally:
            os.unlink(path)
        self.assertIsNone(page["rows"][0]["email"])
        self.assertIsNone(page["rows"][0]["amount"])
        self.assertEqual(page["rows"][1]["email"], "a@b.c")
        encoded = json.dumps(page, allow_nan=False)
        self.assertIn('"email": null', encoded)
        self.assertNotIn('"None"', encoded)
        self.assertNotIn('"nan"', encoded.lower())

    def test_json_cell_conversions(self):
        self.assertIsNone(json_cell(None))
        self.assertIs(json_cell(True), True)
        self.assertEqual(json_cell(5), 5)
        self.assertEqual(json_cell(2**60), str(2**60))  # a browser would round this
        self.assertEqual(json_cell(1.5), 1.5)
        self.assertEqual(json_cell(float("nan")), "nan")
        self.assertEqual(json_cell(float("inf")), "inf")
        self.assertEqual(json_cell(Decimal("12.3400")), "12.3400")
        self.assertEqual(json_cell(datetime(2026, 1, 2, 3, 4)), "2026-01-02T03:04:00")
        self.assertEqual(json_cell(date(2026, 1, 2)), "2026-01-02")
        self.assertEqual(json_cell(UUID(int=1)), str(UUID(int=1)))
        self.assertEqual(json_cell(b"\x01\x02"), "0x0102")
        self.assertTrue(json_cell(b"\x00" * 100).endswith("(100 bytes)"))
        self.assertEqual(len(json_cell("x" * 5000)), table_preview.MAX_CELL_CHARS + 1)
        json.dumps([json_cell(v) for v in (Decimal("1"), float("nan"), b"z", date.today())], allow_nan=False)

    def test_connection_failure_is_a_clear_error(self):
        with patch.object(db_connector, "_make_engine", lambda cs: create_engine("sqlite:////no/such/dir/x.db")):
            with self.assertRaises(DataSourceConnectionError) as ctx:
                run_preview_query(PG_CONN, 'SELECT * FROM "t"')
        self.assertIn("Could not connect", str(ctx.exception))
        self.assertNotIn("[SQL:", str(ctx.exception))

    def test_failed_query_is_a_clear_error_without_the_sql_echo(self):
        path = _make_db(["CREATE TABLE t (id INTEGER)"])
        try:
            with _engine_at(path):
                with self.assertRaises(DataSourceError) as ctx:
                    run_preview_query(PG_CONN, 'SELECT * FROM "ghost" WHERE "id" > :k0', {"k0": "SECRET-VALUE"})
        finally:
            os.unlink(path)
        self.assertIn("Query failed", str(ctx.exception))
        self.assertNotIn("SECRET-VALUE", str(ctx.exception))

    def test_missing_table_is_recognised_for_both_databases(self):
        class PgMissing(Exception):
            pgcode = "42P01"

        class Wrapped(Exception):
            def __init__(self, orig):
                self.orig = orig

        self.assertTrue(db_connector._is_missing_table(Wrapped(PgMissing("x"))))
        self.assertTrue(db_connector._is_missing_table(Wrapped(Exception(
            "('42S02', \"[42S02] [Microsoft][ODBC Driver 18 for SQL Server]"
            "[SQL Server]Invalid object name 'dbo.ghost'. (208)\")"))))
        self.assertTrue(db_connector._is_missing_table(Exception('relation "s.t" does not exist')))
        self.assertFalse(db_connector._is_missing_table(Exception('column "x" does not exist')))
        self.assertFalse(db_connector._is_missing_table(Exception("Invalid column name 'x'")))

    def test_passwords_in_a_url_are_masked(self):
        message = db_connector._driver_message(Exception("bad url postgresql://svc:TopSecret@h:5432/d"))
        self.assertNotIn("TopSecret", message)


# ---------------------------------------------------------------------------
# 7. The API: connection identity, credentials, end to end
# ---------------------------------------------------------------------------


class PreviewApiTests(unittest.TestCase):
    def setUp(self):
        self.catalog = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.tables = [
            DataSource.__table__, CatalogScan.__table__, CatalogTable.__table__,
            CatalogColumn.__table__, CatalogRelationship.__table__,
        ]
        Base.metadata.create_all(bind=self.catalog, tables=self.tables)
        self.sessions = sessionmaker(bind=self.catalog, autoflush=False, autocommit=False)

        app = FastAPI()
        app.include_router(router)

        def override_get_db():
            db = self.sessions()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

        self.db_path = _make_db(
            [
                "CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, full_name TEXT, email TEXT)",
                "CREATE TABLE empty_t (id INTEGER PRIMARY KEY)",
            ],
            [(
                "INSERT INTO customers VALUES (?, ?, ?)",
                [(i, f"Customer {i}", None if i % 5 == 0 else f"c{i}@example.com") for i in range(1, 46)],
            )],
        )
        self.password = "pa55-Secret-w0rd"
        self.source_id = self._add_source()
        self.customers = self._add_table(self.source_id, "customers", [
            ("customer_id", "INTEGER", True), ("full_name", "TEXT", False), ("email", "TEXT", False)])
        self.empty = self._add_table(self.source_id, "empty_t", [("id", "INTEGER", True)])

    def tearDown(self):
        Base.metadata.drop_all(bind=self.catalog, tables=self.tables)
        self.catalog.dispose()
        os.unlink(self.db_path)

    # -- fixtures --
    def _add_source(self, **over):
        values = dict(
            source_name=f"src-{uuid4().hex[:6]}", db_type="postgresql", host="localhost",
            port=5432, database="meridian", username="svc_ro", schema_name="main",
            trusted_connection=False, status="scanned",
        )
        values.update(over)
        db = self.sessions()
        source = DataSource(**values)
        db.add(source)
        db.commit()
        source_id = source.id
        db.close()
        return source_id

    def _add_table(self, source_id, name, columns, schema="main"):
        db = self.sessions()
        scan = CatalogScan(data_source_id=source_id, schema_name=schema, status="completed")
        db.add(scan)
        db.flush()
        table = CatalogTable(
            scan_id=scan.id, data_source_id=source_id, schema_name=schema, table_name=name,
            column_count=len(columns), row_count=None, row_count_is_estimate=False,
            has_primary_key=any(c[2] for c in columns),
        )
        db.add(table)
        db.flush()
        for position, (cname, ctype, pk) in enumerate(columns):
            db.add(CatalogColumn(
                catalog_table_id=table.id, column_name=cname, data_type=ctype,
                ordinal_position=position, is_nullable=not pk, is_primary_key=pk,
                is_foreign_key=False,
            ))
        db.commit()
        table_id = table.id
        db.close()
        return table_id

    def _body(self, **over):
        body = {
            "db_type": "postgresql", "host": "localhost", "port": 5432, "database": "meridian",
            "username": "svc_ro", "password": self.password,
        }
        body.update(over)
        return body

    def _preview(self, table_id, **over):
        return self.client.post(f"/catalog/tables/{table_id}/preview", json=self._body(**over))

    # -- behaviour --
    def test_browse_the_whole_table_through_the_api(self):
        with _engine_at(self.db_path):
            seen, cursor, pages = [], None, 0
            while True:
                response = self._preview(self.customers, cursor=cursor)
                self.assertEqual(response.status_code, 200, response.text)
                page = response.json()
                pages += 1
                seen += [row["customer_id"] for row in page["rows"]]
                self.assertEqual(page["page_size"], 20)
                self.assertEqual(page["table_name"], "customers")
                self.assertEqual(page["schema_name"], "main")
                self.assertEqual(page["column_names"], ["customer_id", "full_name", "email"])
                cursor = page["next_cursor"]
                if not page["has_more"]:
                    break
        self.assertEqual(pages, 3)
        self.assertEqual(seen, list(range(1, 46)))

    def test_null_is_json_null_in_the_http_response(self):
        with _engine_at(self.db_path):
            response = self._preview(self.customers, limit=10)
        row5 = response.json()["rows"][4]
        self.assertEqual(row5["customer_id"], 5)
        self.assertIsNone(row5["email"])
        self.assertIn('"email":null', response.text.replace(" ", ""))
        self.assertNotIn('"None"', response.text)

    def test_empty_table(self):
        with _engine_at(self.db_path):
            response = self._preview(self.empty)
        self.assertEqual(response.status_code, 200)
        page = response.json()
        self.assertEqual((page["rows"], page["row_count"], page["has_more"]), ([], 0, False))
        self.assertEqual(page["column_names"], ["id"])
        self.assertEqual(page["message"], "This table is empty.")

    def test_page_size_is_validated(self):
        for bad in (0, 101, -5):
            self.assertEqual(self._preview(self.customers, limit=bad).status_code, 422)

    def test_unknown_table_is_404(self):
        self.assertEqual(self._preview(uuid4()).status_code, 404)

    def test_bad_or_foreign_cursor_is_400(self):
        with _engine_at(self.db_path):
            first = self._preview(self.customers).json()
            self.assertEqual(self._preview(self.customers, cursor="garbage").status_code, 400)
            other = self._preview(self.empty, cursor=first["next_cursor"])
            self.assertEqual(other.status_code, 400)
            self.assertNotIn(self.password, other.text)

    def test_table_missing_from_the_database_is_a_clear_error(self):
        ghost = self._add_table(self.source_id, "ghost", [("id", "INTEGER", True)])
        with _engine_at(self.db_path):
            response = self._preview(ghost)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Query failed", response.json()["detail"])
        self.assertNotIn(self.password, response.text)

    def test_unreachable_database_is_a_clear_error(self):
        with patch.object(db_connector, "_make_engine", lambda cs: create_engine("sqlite:////no/dir/x.db")):
            response = self._preview(self.customers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Could not connect", response.json()["detail"])
        self.assertNotIn(self.password, response.text)

    def test_missing_table_error_maps_to_404(self):
        with patch("routers.catalog.get_table_page", side_effect=DataSourceTableNotFound("gone")):
            self.assertEqual(self._preview(self.customers).status_code, 404)

    # -- connection identity --
    def test_mismatched_connections_are_rejected_before_any_database_contact(self):
        engines = []
        cases = {
            "host": {"host": "other-server"},
            "port": {"port": 5433},
            "database": {"database": "other_db"},
            "username": {"username": "someone_else"},
            "db_type": {"db_type": "mssql"},
            "trusted_connection": {"trusted_connection": True},
            "database case (PostgreSQL is case-sensitive)": {"database": "MERIDIAN"},
        }
        with _engine_at(self.db_path, engines):
            for label, change in cases.items():
                with self.subTest(label):
                    response = self._preview(self.customers, **change)
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertIn("do not match", response.json()["detail"])
                    self.assertNotIn(self.password, response.text)
        self.assertEqual(engines, [], "no database connection may be opened for a mismatch")

    def test_valid_variations_are_accepted(self):
        with _engine_at(self.db_path):
            for label, change in {
                "host in other case": {"host": "LOCALHOST"},
                "host with spaces": {"host": " localhost "},
                "port omitted means the default 5432": {"port": None},
                "extra odbc_driver ignored": {"odbc_driver": "whatever"},
            }.items():
                with self.subTest(label):
                    self.assertEqual(self._preview(self.customers, **change).status_code, 200)

    def test_source_without_optional_fields_does_not_reject_a_valid_connection(self):
        source = self._add_source(host=None, database=None, username=None, port=None)
        table = self._add_table(source, "customers", [("customer_id", "INTEGER", True), ("full_name", "TEXT", False), ("email", "TEXT", False)])
        with _engine_at(self.db_path):
            self.assertEqual(self._preview(table, username="anyone").status_code, 200)
        # the port that was not recorded means the default, so a different one is a mismatch
        self.assertEqual(self._preview(table, port=6000).status_code, 400)

    def test_sql_server_windows_authentication(self):
        source = self._add_source(
            db_type="mssql", host="SQLBOX", port=None, database="Meridian", username=None,
            trusted_connection=True, schema_name="dbo",
        )
        table = self._add_table(source, "Customers", [("Id", "INT", True)], schema="dbo")
        windows = {"db_type": "mssql", "host": "sqlbox", "port": None, "database": "meridian",
                   "username": None, "password": None, "trusted_connection": True}
        fake = {"table_name": "Customers", "schema_name": "dbo", "column_names": ["Id"], "rows": [],
                "row_count": 0, "page_size": 20, "has_more": False, "next_cursor": None,
                "pagination_mode": "keyset", "stable_order": True, "start_row": None, "message": None}
        with patch("routers.catalog.get_table_page", return_value=fake) as page:
            response = self.client.post(f"/catalog/tables/{table}/preview", json=windows)
            self.assertEqual(response.status_code, 200, response.text)
            conn = page.call_args.kwargs["connection_string"]
            self.assertIn("Trusted_Connection=yes", conn)
            self.assertNotIn("@", conn)
            # a username and password for a Windows-auth source is not the same connection
            sql_login = dict(windows, trusted_connection=False, username="sa", password="x")
            self.assertEqual(
                self.client.post(f"/catalog/tables/{table}/preview", json=sql_login).status_code, 400)

    def test_sql_server_login_names_are_case_insensitive(self):
        source = self._add_source(
            db_type="mssql", host="sqlbox", port=1433, database="Meridian", username="SvcRead",
            schema_name="dbo")
        table = self._add_table(source, "Customers", [("Id", "INT", True)], schema="dbo")
        body = {"db_type": "mssql", "host": "SQLBOX", "database": "MERIDIAN", "username": "svcread",
                "password": "x"}
        fake = {"table_name": "Customers", "schema_name": "dbo", "column_names": [], "rows": [],
                "row_count": 0, "page_size": 20, "has_more": False, "next_cursor": None,
                "pagination_mode": "keyset", "stable_order": True, "start_row": None, "message": None}
        with patch("routers.catalog.get_table_page", return_value=fake):
            self.assertEqual(self.client.post(f"/catalog/tables/{table}/preview", json=body).status_code, 200)
            body["username"] = "other"
            self.assertEqual(self.client.post(f"/catalog/tables/{table}/preview", json=body).status_code, 400)

    def test_catalog_names_are_used_not_request_names(self):
        fake = {"table_name": "customers", "schema_name": "main", "column_names": [], "rows": [],
                "row_count": 0, "page_size": 20, "has_more": False, "next_cursor": None,
                "pagination_mode": "keyset", "stable_order": True, "start_row": None, "message": None}
        with patch("routers.catalog.get_table_page", return_value=fake) as page:
            response = self._preview(self.customers, schema_name="evil", table_name="pg_shadow")
            self.assertEqual(response.status_code, 200)
        self.assertEqual(page.call_args.kwargs["schema"], "main")
        self.assertEqual(page.call_args.kwargs["table"], "customers")
        self.assertEqual(page.call_args.kwargs["db_type"], "postgresql")
        self.assertEqual(
            [c["column_name"] for c in page.call_args.kwargs["columns"]],
            ["customer_id", "full_name", "email"])
        self.assertTrue(page.call_args.kwargs["columns"][0]["is_primary_key"])

    # -- credentials --
    def test_no_credentials_in_any_response(self):
        with _engine_at(self.db_path):
            ok = self._preview(self.customers)
            self.assertEqual(
                sorted(ok.json()),
                sorted(["table_name", "schema_name", "column_names", "rows", "row_count", "page_size",
                        "has_more", "next_cursor", "pagination_mode", "stable_order", "start_row", "message"]))
            responses = [ok, self._preview(self.customers, host="elsewhere"), self._preview(self.customers, cursor="x")]
        responses.append(self._preview(self.customers, limit=0))
        with patch("routers.catalog.get_table_page", side_effect=DataSourceError(f"boom {self.password} boom")):
            failure = self._preview(self.customers)
        self.assertEqual(failure.status_code, 400)
        responses.append(failure)
        for response in responses:
            self.assertNotIn(self.password, response.text)
            self.assertNotIn("password", response.text.lower().replace("password authentication", ""))
        # the token carries a position only
        token = ok.json()["next_cursor"]
        self.assertNotIn(self.password, token)

    def test_the_catalog_still_holds_no_password(self):
        with _engine_at(self.db_path):
            self._preview(self.customers)
        columns = {c.name for c in DataSource.__table__.columns}
        self.assertFalse({c for c in columns if "pass" in c or "secret" in c})


if __name__ == "__main__":
    unittest.main()
