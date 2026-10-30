"""Temporary database credential sessions.

Same approach as test_table_preview.py: the catalog is SQLite in memory, the
"customer database" is a temporary SQLite file for the preview SQL, and the
connection test / metadata scan are replaced by recorders, so no network and no
real database server is involved. A live PostgreSQL and SQL Server are still
needed for a final check (see the report).
"""

import logging
import os
import pickle
import unittest
from unittest.mock import patch
from urllib.parse import quote_plus
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base

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
from routers.catalog import (
    SESSION_REQUIRED_MESSAGE,
    current_session_owner,
    get_db,
    get_session_store,
    router,
)
from services.credential_sessions import (
    CredentialSessionStore,
    SessionLimitError,
    SessionSettings,
    _Secret,
    multi_worker_configured,
)
from services.db_connector import DataSourceError
from test_table_preview import _engine_at, _make_db

CSRF = {"X-Requested-By": "catalog-ui"}
PASSWORD = "Pa55-w0rd-SECRET!"


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# ---------------------------------------------------------------------------
# The store on its own
# ---------------------------------------------------------------------------


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.settings = SessionSettings(idle_seconds=600, max_seconds=3600, max_sessions=3)
        self.store = CredentialSessionStore(self.settings, self.clock)
        self.source = uuid4()

    def _create(self, owner=None, source=None, password=PASSWORD):
        return self.store.create(
            owner, source or self.source, "h", 5432, "d", "u", False, None, password
        )

    def test_create_and_lookup(self):
        token, info = self._create()
        self.assertGreaterEqual(len(token), 40)  # 256 random bits
        session = self.store.lookup(token, self.source, None)
        self.assertEqual(session.password, PASSWORD)
        self.assertEqual((session.host, session.port, session.database, session.username), ("h", 5432, "d", "u"))

    def test_tokens_are_unique_and_unguessable(self):
        tokens = {self._create()[0] for _ in range(3)}
        self.assertEqual(len(tokens), 3)

    def test_idle_timeout_ends_the_session_and_use_renews_it(self):
        token, _ = self._create()
        self.clock.advance(500)
        self.assertIsNotNone(self.store.lookup(token, self.source, None))  # renews
        self.clock.advance(500)
        self.assertIsNotNone(self.store.lookup(token, self.source, None))  # 500s idle: alive
        self.clock.advance(601)
        self.assertIsNone(self.store.lookup(token, self.source, None))  # idle too long
        self.assertEqual(self.store.count(), 0)

    def test_absolute_lifetime_is_never_extended_by_use(self):
        token, _ = self._create()
        for _ in range(5):
            self.clock.advance(500)
            self.assertIsNotNone(self.store.lookup(token, self.source, None))
        self.clock.advance(500)  # 3000s so far; 3600 is the cap
        self.assertIsNotNone(self.store.lookup(token, self.source, None))
        self.clock.advance(600)
        self.assertIsNone(self.store.lookup(token, self.source, None))

    def test_status_checks_do_not_extend_a_session(self):
        token, _ = self._create()
        self.clock.advance(590)
        self.assertIsNotNone(self.store.lookup(token, self.source, None, touch=False))
        self.clock.advance(20)
        self.assertIsNone(self.store.lookup(token, self.source, None, touch=False))

    def test_wrong_source_or_owner_finds_nothing_and_does_not_delete(self):
        token, _ = self._create(owner="alice")
        self.assertIsNone(self.store.lookup(token, uuid4(), "alice"))
        self.assertIsNone(self.store.lookup(token, self.source, "bob"))
        self.assertIsNone(self.store.lookup(token, self.source, None))
        self.assertFalse(self.store.delete(token, self.source, "bob"))
        self.assertIsNotNone(self.store.lookup(token, self.source, "alice"))

    def test_delete_is_immediate(self):
        token, _ = self._create()
        self.assertTrue(self.store.delete(token, self.source, None))
        self.assertIsNone(self.store.lookup(token, self.source, None))
        self.assertFalse(self.store.delete(token, self.source, None))

    def test_junk_tokens_are_refused(self):
        for junk in (None, "", "x", 123, "a" * 10_000):
            self.assertIsNone(self.store.lookup(junk, self.source, None))

    def test_session_cap(self):
        for _ in range(3):
            self._create()
        with self.assertRaises(SessionLimitError):
            self._create()
        self.clock.advance(601)  # the old ones expire, room again
        self._create()

    def test_nothing_secret_is_visible_in_the_store_or_reprs(self):
        token, info = self._create()
        session = self.store.lookup(token, self.source, None)
        for text in (repr(self.store._records), str(self.store._records), repr(session),
                     repr(info), str(_Secret(PASSWORD)), repr(list(self.store._records.values()))):
            self.assertNotIn(PASSWORD, text)
            self.assertNotIn(token, text)
        self.assertNotIn(token, list(self.store._records))  # keyed by the token's hash
        with self.assertRaises(TypeError):
            pickle.dumps(_Secret(PASSWORD))
        with self.assertRaises(AttributeError):
            _Secret(PASSWORD)._value = "x"

    def test_settings_come_from_the_environment_and_are_clamped(self):
        env = {"CATALOG_SESSION_IDLE_SECONDS": "900", "CATALOG_SESSION_MAX_SECONDS": "7200",
               "CATALOG_SESSION_COOKIE_SAMESITE": "STRICT", "CATALOG_SESSION_COOKIE_SECURE": "true",
               "CATALOG_SESSION_MAX_COUNT": "5"}
        with patch.dict(os.environ, env):
            s = SessionSettings.from_env()
        self.assertEqual((s.idle_seconds, s.max_seconds, s.max_sessions), (900, 7200, 5))
        self.assertEqual((s.cookie_samesite, s.cookie_secure), ("strict", True))
        with patch.dict(os.environ, {"CATALOG_SESSION_IDLE_SECONDS": "5", "CATALOG_SESSION_MAX_SECONDS": "999999",
                                     "CATALOG_SESSION_COOKIE_SAMESITE": "bogus"}):
            s = SessionSettings.from_env()
        self.assertEqual((s.idle_seconds, s.max_seconds, s.cookie_samesite), (60, 86400, "lax"))
        with patch.dict(os.environ, {"CATALOG_SESSION_IDLE_SECONDS": "x"}):
            self.assertEqual(SessionSettings.from_env().idle_seconds, 1800)
        with patch.dict(os.environ, {"CATALOG_SESSION_IDLE_SECONDS": "9000", "CATALOG_SESSION_MAX_SECONDS": "600"}):
            self.assertEqual(SessionSettings.from_env().idle_seconds, 600)  # never longer than the cap

    def test_multi_worker_detection(self):
        with patch.dict(os.environ, {"WEB_CONCURRENCY": "4"}):
            self.assertTrue(multi_worker_configured())
        with patch.dict(os.environ, {"WEB_CONCURRENCY": "1"}):
            self.assertFalse(multi_worker_configured())
        with patch.dict(os.environ, {"WEB_CONCURRENCY": "junk"}):
            self.assertFalse(multi_worker_configured())


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


FAKE_CATALOG = {
    "tables": [{
        "table_name": "customers", "schema_name": "main", "column_count": 1, "row_count": 3,
        "row_count_is_estimate": False, "has_primary_key": True,
        "columns": [{"column_name": "customer_id", "data_type": "INTEGER", "ordinal_position": 0,
                     "is_nullable": False, "is_primary_key": True, "is_foreign_key": False,
                     "normalized_name": "customer id", "sample_values": None}],
    }],
    "relationships": [], "table_count": 1, "column_count": 1, "relationship_count": 0, "duration_ms": 5,
}


class _SessionFixture(unittest.TestCase):
    """Catalog + customer-database fixtures shared by the API test classes."""

    def setUp(self):
        self.catalog = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.tables = [DataSource.__table__, CatalogScan.__table__, CatalogTable.__table__,
                       CatalogColumn.__table__, CatalogRelationship.__table__]
        Base.metadata.create_all(bind=self.catalog, tables=self.tables)
        self.sessions = sessionmaker(bind=self.catalog, autoflush=False, autocommit=False)

        self.clock = FakeClock()
        self.store = CredentialSessionStore(SessionSettings(idle_seconds=600, max_seconds=3600), self.clock)
        self.owner = None

        app = FastAPI()
        app.include_router(router)

        def override_get_db():
            db = self.sessions()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_session_store] = lambda: self.store
        app.dependency_overrides[current_session_owner] = lambda: self.owner
        self.app = app
        self.client = TestClient(app)

        self.db_path = _make_db(
            ["CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, full_name TEXT)"],
            [("INSERT INTO customers VALUES (?, ?)", [(i, f"Customer {i}") for i in range(1, 46)])],
        )
        self.source_id = self._add_source()
        self.table_id = self._add_table(self.source_id)

        self.logs = _Capture()
        logging.getLogger().addHandler(self.logs)
        self._old_level = logging.getLogger().level
        logging.getLogger().setLevel(logging.DEBUG)

        self.test_conn = patch("routers.catalog.test_connection", return_value={"ok": True})
        self.test_conn_mock = self.test_conn.start()

    def tearDown(self):
        self.test_conn.stop()
        logging.getLogger().removeHandler(self.logs)
        logging.getLogger().setLevel(self._old_level)
        Base.metadata.drop_all(bind=self.catalog, tables=self.tables)
        self.catalog.dispose()
        os.unlink(self.db_path)

    # -- fixtures --
    def _add_source(self, **over):
        values = dict(source_name=f"src-{uuid4().hex[:6]}", db_type="postgresql", host="localhost", port=5432,
                      database="meridian", username="svc_ro", schema_name="main", trusted_connection=False,
                      status="scanned")
        values.update(over)
        db = self.sessions()
        source = DataSource(**values)
        db.add(source)
        db.commit()
        sid = source.id
        db.close()
        return sid

    def _add_table(self, source_id, name="customers", schema="main"):
        db = self.sessions()
        scan = CatalogScan(data_source_id=source_id, schema_name=schema, status="completed")
        db.add(scan)
        db.flush()
        table = CatalogTable(scan_id=scan.id, data_source_id=source_id, schema_name=schema, table_name=name,
                             column_count=2, row_count=None, row_count_is_estimate=False, has_primary_key=True)
        db.add(table)
        db.flush()
        db.add(CatalogColumn(catalog_table_id=table.id, column_name="customer_id", data_type="INTEGER",
                             ordinal_position=0, is_nullable=False, is_primary_key=True, is_foreign_key=False))
        db.add(CatalogColumn(catalog_table_id=table.id, column_name="full_name", data_type="TEXT",
                             ordinal_position=1, is_nullable=True, is_primary_key=False, is_foreign_key=False))
        db.commit()
        tid = table.id
        db.close()
        return tid

    def _conn_fields(self, **over):
        body = {"db_type": "postgresql", "host": "localhost", "port": 5432, "database": "meridian",
                "username": "svc_ro"}
        body.update(over)
        return body

    def _connect(self, source_id=None, password=PASSWORD, client=None, **extra):
        return (client or self.client).post(
            f"/catalog/sources/{source_id or self.source_id}/session",
            json={"password": password, **extra}, headers=CSRF)

    def _preview(self, table_id=None, client=None, headers=CSRF, **over):
        return (client or self.client).post(
            f"/catalog/tables/{table_id or self.table_id}/preview",
            json=self._conn_fields(**over), headers=headers)

    def _scan(self, source_id=None, client=None, headers=CSRF, **over):
        return (client or self.client).post(
            f"/catalog/sources/{source_id or self.source_id}/scan",
            json=self._conn_fields(**over), headers=headers)

    def _status(self, source_id=None, client=None):
        return (client or self.client).get(f"/catalog/sources/{source_id or self.source_id}/session")

    def _token(self, response):
        cookie = response.headers["set-cookie"]
        return cookie.split("=", 1)[1].split(";", 1)[0]


class SessionApiTests(_SessionFixture):
    # -- 1. connect once, scan without the password --
    def test_connect_then_scan_without_the_password(self):
        connected = self._connect()
        self.assertEqual(connected.status_code, 200, connected.text)
        self.assertTrue(connected.json()["connected"])
        with patch("routers.catalog.scan_source", return_value=FAKE_CATALOG) as scanner:
            scan = self._scan()
        self.assertEqual(scan.status_code, 201, scan.text)
        # The scan really ran with the session's password...
        self.assertIn(quote_plus(PASSWORD), scanner.call_args.kwargs["connection_string"])
        # ...which the browser never sent again.
        self.assertNotIn(PASSWORD, scan.text)

    def test_connect_verifies_the_password_with_the_database(self):
        self._connect()
        self.test_conn_mock.assert_called_once()
        self.assertIn("postgresql+psycopg2://svc_ro:", self.test_conn_mock.call_args.args[0])

    # -- 2. previews and page navigation on the same session --
    def test_same_session_previews_and_navigates_pages(self):
        self._connect()
        with _engine_at(self.db_path):
            seen, cursor, pages = [], None, 0
            while True:
                body = self._conn_fields(limit=20)
                if cursor:
                    body["cursor"] = cursor
                page = self.client.post(f"/catalog/tables/{self.table_id}/preview", json=body, headers=CSRF)
                self.assertEqual(page.status_code, 200, page.text)
                data = page.json()
                pages += 1
                seen += [r["customer_id"] for r in data["rows"]]
                cursor = data["next_cursor"]
                if not data["has_more"]:
                    break
        self.assertEqual((pages, seen), (3, list(range(1, 46))))

    # -- 3. another user / another browser --
    def test_another_user_cannot_reuse_the_session(self):
        self.owner = "alice"
        self.assertEqual(self._connect().status_code, 200)
        token = self.client.cookies.get(f"catalog_db_session_{self.source_id.hex[:16]}")
        self.assertTrue(token)
        self.owner = "bob"
        with _engine_at(self.db_path):
            stolen = self._preview()  # bob's browser even holds the cookie
        self.assertEqual(stolen.status_code, 401)
        self.assertEqual(stolen.json()["detail"], SESSION_REQUIRED_MESSAGE)
        self.assertFalse(self._status().json()["connected"])
        self.client.delete(f"/catalog/sources/{self.source_id}/session", headers=CSRF)  # bob "disconnects"
        self.assertEqual(self.store.count(), 1)  # Alice's session was not touched by Bob
        self.owner = "alice"
        alice = TestClient(self.app)  # Alice's own browser still holds her cookie
        alice.cookies.set(f"catalog_db_session_{self.source_id.hex[:16]}", token, path="/catalog")
        with _engine_at(self.db_path):
            self.assertEqual(self._preview(client=alice).status_code, 200)

    def test_a_browser_without_the_cookie_is_refused(self):
        self._connect()
        other_browser = TestClient(self.app)
        with _engine_at(self.db_path):
            self.assertEqual(self._preview(client=other_browser).status_code, 401)
        self.assertFalse(self._status(client=other_browser).json()["connected"])

    # -- 4. a session cannot reach another source --
    def test_session_cannot_be_used_for_a_different_source(self):
        self._connect()
        token = self.client.cookies.get(f"catalog_db_session_{self.source_id.hex[:16]}")
        other = self._add_source(source_name="other", host="elsewhere", database="other_db")
        other_table = self._add_table(other)
        thief = TestClient(self.app)
        thief.cookies.set(f"catalog_db_session_{other.hex[:16]}", token, path="/catalog")  # right token, wrong source
        with _engine_at(self.db_path), patch("routers.catalog.scan_source", return_value=FAKE_CATALOG) as scanner:
            body = self._conn_fields(host="elsewhere", database="other_db")
            self.assertEqual(thief.post(f"/catalog/tables/{other_table}/preview", json=body, headers=CSRF).status_code, 401)
            self.assertEqual(thief.post(f"/catalog/sources/{other}/scan", json=body, headers=CSRF).status_code, 401)
        scanner.assert_not_called()
        self.assertFalse(self._status(other, client=thief).json()["connected"])

    def test_the_password_is_never_sent_to_a_host_the_source_was_not_registered_with(self):
        self._connect()
        with patch("routers.catalog.scan_source", return_value=FAKE_CATALOG) as scanner:
            for change in ({"host": "attacker.example"}, {"port": 6543}, {"database": "other"}, {"username": "root"}):
                with self.subTest(change):
                    response = self._scan(**change)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("do not match", response.json()["detail"])
        scanner.assert_not_called()

    # -- 5. expiry --
    def test_idle_expiry_is_rejected_with_a_reconnect_message(self):
        self._connect()
        self.clock.advance(601)
        with _engine_at(self.db_path):
            expired = self._preview()
        self.assertEqual(expired.status_code, 401)
        self.assertIn("Reconnect", expired.json()["detail"])
        self.assertFalse(self._status().json()["connected"])
        with patch("routers.catalog.scan_source", return_value=FAKE_CATALOG):
            self.assertEqual(self._scan().status_code, 401)
        self.assertEqual(self._connect().status_code, 200)  # and reconnecting works
        with _engine_at(self.db_path):
            self.assertEqual(self._preview().status_code, 200)

    def test_using_the_session_keeps_it_alive_but_not_forever(self):
        self._connect()
        with _engine_at(self.db_path):
            for _ in range(5):
                self.clock.advance(500)
                self.assertEqual(self._preview().status_code, 200)
            self.clock.advance(700)  # 3200s in, but 700s idle: gone
            self.assertEqual(self._preview().status_code, 401)

    def test_status_reports_the_time_left(self):
        self._connect()
        self.clock.advance(100)
        status = self._status().json()
        self.assertTrue(status["connected"])
        self.assertEqual(status["expires_in_seconds"], 500)
        self.assertEqual((status["idle_timeout_seconds"], status["max_lifetime_seconds"]), (600, 3600))

    # -- 6. disconnect --
    def test_disconnect_invalidates_the_session_immediately(self):
        connected = self._connect()
        token = self._token(connected)
        gone = self.client.delete(f"/catalog/sources/{self.source_id}/session", headers=CSRF)
        self.assertEqual(gone.status_code, 200)
        self.assertFalse(gone.json()["connected"])
        self.assertEqual(self.store.count(), 0)
        replay = TestClient(self.app)  # even someone who kept the token
        replay.cookies.set(f"catalog_db_session_{self.source_id.hex[:16]}", token, path="/catalog")
        with _engine_at(self.db_path):
            self.assertEqual(self._preview(client=replay).status_code, 401)
            self.assertEqual(self._preview().status_code, 401)
        self.assertEqual(self.client.delete(f"/catalog/sources/{self.source_id}/session", headers=CSRF).status_code, 200)  # idempotent

    def test_reconnecting_replaces_the_old_session(self):
        first = self._token(self._connect())
        second = self._token(self._connect())
        self.assertNotEqual(first, second)
        self.assertEqual(self.store.count(), 1)

    # -- 7. nothing secret leaks --
    def test_no_password_or_token_in_responses_or_logs(self):
        outputs = []
        connected = self._connect()
        token = self._token(connected)
        outputs += [connected.text]
        with _engine_at(self.db_path), patch("routers.catalog.scan_source", return_value=FAKE_CATALOG):
            outputs += [self._preview().text, self._scan().text, self._status().text]
            self.test_conn_mock.side_effect = DataSourceError(
                f"Could not connect to postgresql://svc_ro:{PASSWORD}@localhost:5432/meridian - boom")
            outputs += [self._connect(password=PASSWORD).text]
            self.test_conn_mock.side_effect = None
            self.clock.advance(601)
            expired = self._preview()
            outputs += [expired.text, self._preview(host="evil").text]
            outputs += [self.client.delete(f"/catalog/sources/{self.source_id}/session", headers=CSRF).text]
        headers_text = "".join(f"{k}:{v}" for r in (connected, expired) for k, v in r.headers.items() if k.lower() != "set-cookie")
        for text in outputs + [headers_text, "\n".join(self.logs.lines)]:
            self.assertNotIn(PASSWORD, text)
            self.assertNotIn(token, text)
        # The token travels only in Set-Cookie
        self.assertIn(token, connected.headers["set-cookie"])

    def test_cookie_is_httponly_scoped_and_not_persistent(self):
        cookie = self._connect().headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertIn("path=/catalog", cookie)
        self.assertNotIn("max-age", cookie)
        self.assertNotIn("expires", cookie)  # dies with the browser
        self.assertNotIn("secure", cookie)  # plain http in this test
        # Host-only: no Domain attribute, so the cookie belongs to exactly the
        # host the API was reached at (localhost or 127.0.0.1, never both).
        self.assertNotIn("domain=", cookie)

    def test_cookie_is_secure_over_https_or_when_configured(self):
        https = TestClient(self.app, base_url="https://testserver")
        self.assertIn("secure", self._connect(client=https).headers["set-cookie"].lower())
        self.store.settings = SessionSettings(cookie_samesite="none")
        self.assertIn("secure", self._connect().headers["set-cookie"].lower())  # SameSite=None requires it

    def test_nothing_is_written_to_the_database_models(self):
        self._connect()
        columns = {c.name for c in DataSource.__table__.columns}
        self.assertFalse({c for c in columns if "pass" in c or "secret" in c or "token" in c})
        db = self.sessions()
        try:
            for row in db.query(DataSource).all():
                self.assertNotIn(PASSWORD, repr(row.__dict__))
        finally:
            db.close()

    # -- 8. bad database credentials --
    def test_invalid_database_credentials_fail_safely(self):
        self.test_conn_mock.side_effect = DataSourceError(
            f"Could not connect to postgresql://svc_ro:{PASSWORD}@localhost:5432/meridian - password authentication failed")
        response = self._connect(password="wrong-" + PASSWORD)
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertIn("Could not connect", detail)
        for fragment in (PASSWORD, "svc_ro", "localhost", "meridian", "5432"):
            self.assertNotIn(fragment, detail)
        self.assertEqual(self.store.count(), 0)  # no session was opened
        self.assertNotIn("set-cookie", response.headers)

    # -- 9. SQL Server and PostgreSQL stay compatible --
    def test_sql_server_login_session(self):
        source = self._add_source(db_type="mssql", host="SQLBOX", port=None, database="Meridian",
                                  username="svc", schema_name="dbo")
        response = self._connect(source, odbc_driver="ODBC Driver 17 for SQL Server")
        self.assertEqual(response.status_code, 200, response.text)
        conn = self.test_conn_mock.call_args.args[0]
        self.assertTrue(conn.startswith("mssql+pyodbc://svc:"))
        self.assertIn("SQLBOX:1433/Meridian", conn)
        self.assertIn("ODBC+Driver+17+for+SQL+Server", conn)
        table = self._add_table(source, schema="dbo")
        body = {"db_type": "mssql", "host": "sqlbox", "database": "meridian", "username": "SVC"}
        with patch("routers.catalog.get_table_page", return_value={
                "table_name": "customers", "schema_name": "dbo", "column_names": [], "rows": [], "row_count": 0,
                "page_size": 20, "has_more": False, "next_cursor": None, "pagination_mode": "keyset",
                "stable_order": True, "start_row": None, "message": None}) as page:
            ok = self.client.post(f"/catalog/tables/{table}/preview", json=body, headers=CSRF)
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertIn(quote_plus(PASSWORD), page.call_args.kwargs["connection_string"])
        self.assertEqual(page.call_args.kwargs["schema"], "dbo")

    def test_windows_authentication_needs_no_password_and_no_session(self):
        source = self._add_source(db_type="mssql", host="SQLBOX", port=None, database="Meridian",
                                  username=None, trusted_connection=True, schema_name="dbo")
        ok = self.client.post(f"/catalog/sources/{source}/session", json={}, headers=CSRF)
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertIn("Trusted_Connection=yes", self.test_conn_mock.call_args.args[0])
        self.assertNotIn("@", self.test_conn_mock.call_args.args[0])
        table = self._add_table(source, schema="dbo")
        body = {"db_type": "mssql", "host": "sqlbox", "database": "meridian", "trusted_connection": True}
        with patch("routers.catalog.get_table_page", return_value={
                "table_name": "customers", "schema_name": "dbo", "column_names": [], "rows": [], "row_count": 0,
                "page_size": 20, "has_more": False, "next_cursor": None, "pagination_mode": "keyset",
                "stable_order": True, "start_row": None, "message": None}) as page:
            plain = TestClient(self.app).post(f"/catalog/tables/{table}/preview", json=body)  # no cookie, no header
        self.assertEqual(plain.status_code, 200, plain.text)
        self.assertIn("Trusted_Connection=yes", page.call_args.kwargs["connection_string"])

    def test_the_original_password_in_the_body_flow_still_works(self):
        with _engine_at(self.db_path):
            body = self._conn_fields(password=PASSWORD)
            plain = TestClient(self.app).post(f"/catalog/tables/{self.table_id}/preview", json=body)
            self.assertEqual(plain.status_code, 200, plain.text)
            empty = TestClient(self.app).post(f"/catalog/tables/{self.table_id}/preview",
                                              json=self._conn_fields(password=""))
            self.assertEqual(empty.status_code, 200)  # an explicit empty password is allowed
        with patch("routers.catalog.scan_source", return_value=FAKE_CATALOG):
            scan = TestClient(self.app).post(f"/catalog/sources/{self.source_id}/scan",
                                             json=self._conn_fields(password=PASSWORD))
        self.assertEqual(scan.status_code, 201)

    def test_without_a_session_or_password_the_user_is_asked_to_connect(self):
        bare = TestClient(self.app)
        with _engine_at(self.db_path):
            response = self._preview(client=bare)
        self.assertEqual(response.status_code, 401)
        self.assertIn("Reconnect", response.json()["detail"])

    # -- request guards --
    def test_state_changing_calls_need_the_custom_header(self):
        self._connect()
        bare = {}
        self.assertEqual(self.client.post(f"/catalog/sources/{self.source_id}/session",
                                          json={"password": PASSWORD}, headers=bare).status_code, 403)
        self.assertEqual(self.client.delete(f"/catalog/sources/{self.source_id}/session", headers=bare).status_code, 403)
        with _engine_at(self.db_path), patch("routers.catalog.scan_source", return_value=FAKE_CATALOG) as scanner:
            self.assertEqual(self._preview(headers=bare).status_code, 403)
            self.assertEqual(self._scan(headers=bare).status_code, 403)
        scanner.assert_not_called()
        self.assertTrue(self._status().json()["connected"])  # the forged calls changed nothing

    def test_missing_password_is_a_clear_error(self):
        response = self.client.post(f"/catalog/sources/{self.source_id}/session", json={}, headers=CSRF)
        self.assertEqual(response.status_code, 400)

    def test_unknown_source_is_404(self):
        self.assertEqual(self._connect(uuid4()).status_code, 404)
        self.assertEqual(self._status(uuid4()).status_code, 404)

    def test_source_without_host_cannot_open_a_session(self):
        bare = self._add_source(host=None, database=None)
        self.assertEqual(self._connect(bare).status_code, 400)

    def test_several_workers_are_refused_not_silently_broken(self):
        with patch.dict(os.environ, {"WEB_CONCURRENCY": "4"}):
            response = self._connect()
        self.assertEqual(response.status_code, 503)
        self.assertIn("single backend worker", response.json()["detail"])

    def test_session_limit_is_a_clear_error(self):
        self.store.settings = SessionSettings(idle_seconds=600, max_seconds=3600, max_sessions=1)
        other = self._add_source(source_name="two")
        self.assertEqual(self._connect().status_code, 200)
        self.assertEqual(self._connect(other).status_code, 503)

    def test_existing_scan_error_handling_is_unchanged_with_a_session(self):
        self._connect()
        with patch("routers.catalog.scan_source", side_effect=DataSourceError("nope")):
            response = self._scan()
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(PASSWORD, response.text)


# ---------------------------------------------------------------------------
# The full-screen viewer's request pattern: batches of 100 along one session
# ---------------------------------------------------------------------------


class ViewerBatchTests(_SessionFixture):
    """Reuses the session fixtures. Stand-ins for the sample tables
    MLTest_LargeCustomers (100 rows), MLTest_LargeOrders (1,000) and
    MLTest_LargeOrderItems (3,000), created in SQLite with the same names and
    sizes. This proves the API contract the viewer relies on; it is NOT a test
    against the real PostgreSQL / SQL Server tables."""

    SIZES = {"MLTest_LargeCustomers": 100, "MLTest_LargeOrders": 1000, "MLTest_LargeOrderItems": 3000}

    def setUp(self):
        super().setUp()
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        for name, n in self.SIZES.items():
            conn.execute(f'CREATE TABLE "{name}" (customer_id INTEGER PRIMARY KEY, full_name TEXT, amount NUMERIC)')
            conn.executemany(f'INSERT INTO "{name}" VALUES (?, ?, ?)',
                             [(i, None if i % 7 == 0 else f"row {i}", i * 1.5) for i in range(1, n + 1)])
        conn.execute('CREATE TABLE "MLTest_Empty" (customer_id INTEGER PRIMARY KEY, full_name TEXT)')
        conn.commit()
        conn.close()
        self.big = {name: self._add_table(self.source_id, name=name) for name in self.SIZES}
        self.big["MLTest_Empty"] = self._add_table(self.source_id, name="MLTest_Empty")
        self._connect()

    def _browse(self, table_id):
        """Follow next_cursor in batches of 100, as the viewer does."""
        requests, rows, cursor, expected_start = 0, [], None, 1
        while True:
            body = self._conn_fields(limit=100)
            if cursor:
                body["cursor"] = cursor
            response = self.client.post(f"/catalog/tables/{table_id}/preview", json=body, headers=CSRF)
            self.assertEqual(response.status_code, 200, response.text)
            page = response.json()
            requests += 1
            self.assertLessEqual(page["row_count"], 100)
            if page["rows"]:
                # the viewer's continuity guard: each batch starts where the last ended
                self.assertEqual(page["start_row"], expected_start)
                expected_start += page["row_count"]
            rows += page["rows"]
            cursor = page["next_cursor"]
            if not page["has_more"]:
                self.assertIsNone(cursor)
                return requests, rows
            self.assertIsNotNone(cursor)
            self.assertLess(requests, 1000, "pagination did not end")

    def test_every_sample_size_can_be_browsed_to_the_end(self):
        with _engine_at(self.db_path):
            for name, n in self.SIZES.items():
                with self.subTest(table=name, rows=n):
                    requests, rows = self._browse(self.big[name])
                    ids = [r["customer_id"] for r in rows]
                    self.assertEqual(ids, list(range(1, n + 1)))  # no gaps, no repeats, in order
                    self.assertEqual(requests, n // 100)  # batches of 100, never one big fetch
                    self.assertIsNone(rows[6]["full_name"])  # NULL arrives as JSON null
                    self.assertEqual(rows[0]["full_name"], "row 1")

    def test_empty_table_ends_immediately(self):
        with _engine_at(self.db_path):
            requests, rows = self._browse(self.big["MLTest_Empty"])
        self.assertEqual((requests, rows), (1, []))

    def test_a_mid_browse_expiry_is_resumable_after_reconnecting(self):
        with _engine_at(self.db_path):
            first = self.client.post(f"/catalog/tables/{self.big['MLTest_LargeOrders']}/preview",
                                     json=self._conn_fields(limit=100), headers=CSRF).json()
            self.clock.advance(601)  # the session lapses while the user is reading
            body = self._conn_fields(limit=100, cursor=first["next_cursor"])
            gone = self.client.post(f"/catalog/tables/{self.big['MLTest_LargeOrders']}/preview", json=body, headers=CSRF)
            self.assertEqual(gone.status_code, 401)
            self.assertEqual(self._connect().status_code, 200)  # reconnect...
            resumed = self.client.post(f"/catalog/tables/{self.big['MLTest_LargeOrders']}/preview", json=body, headers=CSRF)
        self.assertEqual(resumed.status_code, 200)  # ...and the same cursor carries on
        self.assertEqual(resumed.json()["rows"][0]["customer_id"], 101)
        self.assertEqual(resumed.json()["start_row"], 101)


if __name__ == "__main__":
    unittest.main()
