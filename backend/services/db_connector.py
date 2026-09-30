"""
Database connector for AutoAI Builder.

Connects to a PostgreSQL or Microsoft SQL Server database, runs ONE read-only
SELECT query, and returns the result as a pandas DataFrame.

The DataFrame it returns is the same shape as pd.read_csv() produces, so the
rest of the platform (training, transform, results) works unchanged.

MVP scope:
  - PostgreSQL and Microsoft SQL Server
  - Credentials are passed in per call and never stored on disk
  - SELECT statements only
  - Hard caps on rows, columns and query time

SECURITY NOTE for SQL Server:
  PostgreSQL supports a genuine read-only transaction, which this module uses.
  SQL Server has no equivalent switch. On SQL Server the protection is the
  SELECT-only check below PLUS the permissions of the login you connect with.
  Always connect using a login that only has the `db_datareader` role.
"""

import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import create_engine, event, inspect as sa_inspect, text
from sqlalchemy.engine import Engine

# ---------------------------------------------------------------------------
# Limits. These mirror the existing CSV limits in backend/main.py.
# ---------------------------------------------------------------------------

MAX_ROWS = 50_000  # same cap as validate_dataset_limits() in main.py
PREVIEW_ROWS = 20
MAX_COLUMNS = 200  # same cap as validate_dataset_limits() in main.py
CONNECT_TIMEOUT_SECONDS = 10  # give up if the database does not answer
QUERY_TIMEOUT_SECONDS = 30  # kill any query that runs longer than this

POSTGRESQL = "postgresql"
MSSQL = "mssql"
SUPPORTED_DB_TYPES = (POSTGRESQL, MSSQL)

# The ODBC driver Microsoft ships today. If the machine has an older one
# installed, pass a different name as `odbc_driver`.
DEFAULT_ODBC_DRIVER = "ODBC Driver 18 for SQL Server"

DEFAULT_SCHEMA = {POSTGRESQL: "public", MSSQL: "dbo"}
DEFAULT_PORT = {POSTGRESQL: 5432, MSSQL: 1433}


class DataSourceError(Exception):
    """Raised when a connection or query is rejected. The message is safe to
    show to a user - it never contains the password."""


def _coerce_decimal_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert SQL Server Decimal columns into true numeric pandas columns.

    SQL Server returns DECIMAL/MONEY values as decimal.Decimal objects.
    Pandas stores them as dtype=object, which later causes AutoML
    numeric-target validation to fail.
    """
    for column in df.columns:
        if df[column].dtype != object:
            continue

        values = df[column].dropna()

        if len(values) and all(isinstance(v, Decimal) for v in values):
            df[column] = pd.to_numeric(df[column], errors="coerce")

    return df


def _check_db_type(db_type: str) -> str:
    normalised = (db_type or "").strip().lower()
    if normalised not in SUPPORTED_DB_TYPES:
        raise DataSourceError(
            f"Unsupported database type '{db_type}'. "
            f"Supported: {', '.join(SUPPORTED_DB_TYPES)}."
        )
    return normalised


# ---------------------------------------------------------------------------
# Building the connection string
# ---------------------------------------------------------------------------


def build_connection_string(
    db_type: str,
    host: str,
    database: str,
    port: Optional[int] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    trusted_connection: bool = False,
    odbc_driver: Optional[str] = None,
    encrypt: bool = True,
    trust_server_certificate: bool = True,
) -> str:
    """Assemble a connection URL.

    For SQL Server with Windows Authentication, set trusted_connection=True and
    leave username and password empty.

    trust_server_certificate defaults to True because ODBC Driver 18 encrypts by
    default, and a local SQL Server almost always uses a self-signed certificate.
    Set it to False against a server with a proper certificate.
    """
    db_type = _check_db_type(db_type)

    if not host or not database:
        raise DataSourceError("Host and database name are both required.")

    resolved_port = int(port) if port else DEFAULT_PORT[db_type]

    if db_type == POSTGRESQL:
        if not username:
            raise DataSourceError("Username is required for PostgreSQL.")
        safe_user = quote_plus(str(username))
        safe_password = quote_plus(str(password or ""))
        return (
            f"postgresql+psycopg2://{safe_user}:{safe_password}"
            f"@{host}:{resolved_port}/{database}"
        )

    # --- SQL Server ---
    driver = quote_plus(odbc_driver or DEFAULT_ODBC_DRIVER)
    options = [
        f"driver={driver}",
        f"Encrypt={'yes' if encrypt else 'no'}",
        f"TrustServerCertificate={'yes' if trust_server_certificate else 'no'}",
    ]

    if trusted_connection:
        options.append("Trusted_Connection=yes")
        credentials = ""
    else:
        if not username:
            raise DataSourceError(
                "Username is required unless you use Windows Authentication."
            )
        credentials = f"{quote_plus(str(username))}:{quote_plus(str(password or ''))}@"

    return (
        f"mssql+pyodbc://{credentials}{host}:{resolved_port}/{database}"
        f"?{'&'.join(options)}"
    )


def _mask(connection_string: str) -> str:
    """Hide the password so it never reaches a log file or an error message."""
    return re.sub(r"://([^:/@]+):([^@]*)@", r"://\1:****@", connection_string)


def _make_engine(connection_string: str) -> Engine:
    """Create a short-lived engine with connect and query timeouts applied."""
    is_mssql = connection_string.startswith("mssql")

    if is_mssql:
        # pyodbc: the connect() timeout keyword is the LOGIN timeout.
        connect_args: Dict[str, Any] = {"timeout": CONNECT_TIMEOUT_SECONDS}
    else:
        # psycopg2 understands these directly.
        connect_args = {
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
        }

    engine = create_engine(
        connection_string,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    if is_mssql:
        # The QUERY timeout is a property on the live pyodbc connection, so it
        # has to be set once the connection is actually open.
        @event.listens_for(engine, "connect")
        def _set_query_timeout(dbapi_connection, _record):  # noqa: ANN001
            try:
                dbapi_connection.timeout = QUERY_TIMEOUT_SECONDS
            except Exception:
                pass  # older drivers may not expose it; the row cap still applies

    return engine


# ---------------------------------------------------------------------------
# Query safety
# ---------------------------------------------------------------------------

_ALLOWED_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


def _validate_query(query: str) -> str:
    """Allow exactly one SELECT (or WITH ... SELECT) statement. Reject
    everything else before it ever reaches the database."""
    if not query or not query.strip():
        raise DataSourceError("The query is empty.")

    cleaned = query.strip().rstrip(";").strip()

    if ";" in cleaned:
        raise DataSourceError(
            "Only a single statement is allowed. Please remove the semicolon."
        )

    if not _ALLOWED_START.match(cleaned):
        raise DataSourceError(
            "Only SELECT queries are allowed. "
            "INSERT, UPDATE, DELETE, DROP and similar statements are blocked."
        )

    return cleaned


# ---------------------------------------------------------------------------
# Small dialect helpers, used by the API layer and the test script
# ---------------------------------------------------------------------------


def default_schema(db_type: str) -> str:
    return DEFAULT_SCHEMA[_check_db_type(db_type)]


def quote_identifier(db_type: str, name: str) -> str:
    """Wrap a table name so that spaces and reserved words are safe."""
    db_type = _check_db_type(db_type)
    if db_type == MSSQL:
        return "[" + str(name).replace("]", "]]") + "]"
    return '"' + str(name).replace('"', '""') + '"'


def preview_query(db_type: str, table: str, limit: int = 5) -> str:
    """SQL Server uses TOP; PostgreSQL uses LIMIT."""
    db_type = _check_db_type(db_type)
    safe_table = quote_identifier(db_type, table)
    if db_type == MSSQL:
        return f"SELECT TOP {int(limit)} * FROM {safe_table}"
    return f"SELECT * FROM {safe_table} LIMIT {int(limit)}"


def _version_query(db_type: str) -> str:
    return "SELECT @@VERSION" if db_type == MSSQL else "SELECT version()"


def _dialect_of(connection_string: str) -> str:
    return MSSQL if connection_string.startswith("mssql") else POSTGRESQL


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def test_connection(connection_string: str) -> Dict[str, Any]:
    """Check that the database is reachable and the credentials work.

    Returns {"ok": True, "server_version": "..."} on success.
    Raises DataSourceError with a readable message on failure.
    """
    engine = None
    try:
        engine = _make_engine(connection_string)
        with engine.connect() as conn:
            version = conn.execute(
                text(_version_query(_dialect_of(connection_string)))
            ).scalar()
        return {"ok": True, "server_version": str(version)}
    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError(
            f"Could not connect to {_mask(connection_string)} - {exc}"
        ) from exc
    finally:
        if engine is not None:
            engine.dispose()


def list_tables(connection_string: str, schema: Optional[str] = None) -> List[str]:
    """List the table names in a schema, so a user can pick instead of typing."""
    engine = None
    resolved_schema = schema or DEFAULT_SCHEMA[_dialect_of(connection_string)]
    try:
        engine = _make_engine(connection_string)
        with engine.connect() as conn:
            inspector = sa_inspect(conn)
            names = list(inspector.get_table_names(schema=resolved_schema))
            names += list(inspector.get_view_names(schema=resolved_schema))
        return sorted(set(names))
    except Exception as exc:
        raise DataSourceError(f"Could not list tables - {exc}") from exc
    finally:
        if engine is not None:
            engine.dispose()


def extract_to_dataframe(
    connection_string: str,
    query: str,
    max_rows: int = MAX_ROWS,
    params: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """Run one SELECT and return the result as a pandas DataFrame.

    Raises DataSourceError if the query is not a SELECT, returns no rows or
    columns, or returns more than max_rows rows.
    """
    safe_query = _validate_query(query)
    engine = None

    try:
        engine = _make_engine(connection_string) 
        # stream_results opens a server-side cursor
        connection = engine.connect().execution_options(
            stream_results=True,
            max_row_buffer=max_rows + 1,
        )

        # PostgreSQL can enforce read-only at the transaction level. SQL Server
        # cannot, so there the SELECT-only check plus a db_datareader login are
        # what protect the source database.

        if _dialect_of(connection_string) == POSTGRESQL:
            connection = connection.execution_options(postgresql_readonly=True)

        with connection as conn:
            # Bound parameters, never string interpolation. The ETL Copilot
            # compiler emits :name placeholders for every filter value; a
            # caller that passes none behaves exactly as before.
            result = conn.execute(text(safe_query), params or {})
            columns = list(result.keys())
            # Fetch one row more than the cap, so we can tell "exactly at the
            # limit" apart from "over the limit".
            rows = result.fetchmany(max_rows + 1)

    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError(f"Query failed - {exc}") from exc
    finally:
        if engine is not None:
            engine.dispose()

    if not columns:
        raise DataSourceError("The query returned no columns.")

    if len(rows) > max_rows:
        raise DataSourceError(
            f"The query returned more than {max_rows:,} rows. "
            f"Please add a WHERE clause to narrow it down."
        )

    df = pd.DataFrame(rows, columns=columns)

    if df.empty:
        raise DataSourceError("The query returned no rows.")

    if len(df.columns) > MAX_COLUMNS:
        raise DataSourceError(
            f"The query returned {len(df.columns)} columns. "
            f"Maximum {MAX_COLUMNS} allowed."
        )

    return _coerce_decimal_columns(df)


def preview_to_dataframe(
    connection_string: str,
    query: str,
    limit: int = PREVIEW_ROWS,
    params=None,
) -> Tuple[pd.DataFrame, bool]:

    safe_query = _validate_query(query)
    engine = None

    try:
        engine = _make_engine(connection_string)

        connection = engine.connect().execution_options(
            stream_results=True,
            max_row_buffer=limit + 1,
        )

        if _dialect_of(connection_string) == POSTGRESQL:
            connection = connection.execution_options(postgresql_readonly=True)

        with connection as conn:
            result = conn.execute(text(safe_query), params or {})
            columns = list(result.keys())
            rows = result.fetchmany(limit + 1)

    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError(f"Query failed - {exc}") from exc
    finally:
        if engine is not None:
            engine.dispose()

    if not columns:
        raise DataSourceError("The query returned no columns.")

    has_more = len(rows) > limit
    df = pd.DataFrame(rows[:limit], columns=columns)

    if df.empty:
        raise DataSourceError("The query returned no rows.")

    df = _coerce_decimal_columns(df)
    return df, has_more


# ---------------------------------------------------------------------------
# Schema introspection - used by the multi-table dataset builder.
#
# These two functions answer "what columns exist" and "what relationships did
# the database itself declare". They read metadata only; they never read table
# data. Both take a list of tables so a single connection serves one request.
# ---------------------------------------------------------------------------


def list_columns(
    connection_string: str,
    tables: List[str],
    schema: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Return the columns of each requested table.

    Shape:
        {
            "Customers": [
                {"name": "id", "type": "INTEGER",
                 "nullable": False, "primary_key": True},
                ...
            ],
            "Orders": [...]
        }

    The `type` value is stringified here because SQLAlchemy type objects are
    not JSON-serializable and would fail at the FastAPI response boundary.

    Raises DataSourceError if a table does not exist or is not readable.
    """
    if not tables:
        raise DataSourceError("At least one table is required.")

    resolved_schema = schema or DEFAULT_SCHEMA[_dialect_of(connection_string)]
    engine = None
    result: Dict[str, List[Dict[str, Any]]] = {}

    try:
        engine = _make_engine(connection_string)
        with engine.connect() as conn:
            inspector = sa_inspect(conn)

            for table in tables:
                try:
                    raw_columns = inspector.get_columns(table, schema=resolved_schema)
                except Exception as exc:
                    raise DataSourceError(
                        f"Could not read the columns of '{table}' - {exc}"
                    ) from exc

                if not raw_columns:
                    raise DataSourceError(
                        f"Table '{table}' was not found in schema "
                        f"'{resolved_schema}', or it has no columns."
                    )

                # Primary key columns, so the UI can mark join candidates.
                try:
                    pk_constraint = inspector.get_pk_constraint(
                        table, schema=resolved_schema
                    )
                    pk_columns = set(pk_constraint.get("constrained_columns") or [])
                except Exception:
                    # Views and some permission setups have no PK information.
                    # That is not an error - the column list is still usable.
                    pk_columns = set()

                result[table] = [
                    {
                        "name": str(col["name"]),
                        "type": str(col.get("type")),
                        "nullable": bool(col.get("nullable", True)),
                        "primary_key": str(col["name"]) in pk_columns,
                    }
                    for col in raw_columns
                ]

        return result

    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError(f"Could not read table columns - {exc}") from exc
    finally:
        if engine is not None:
            engine.dispose()


def list_foreign_keys(
    connection_string: str,
    tables: List[str],
    schema: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return the foreign keys declared between the requested tables.

    Shape:
        [
            {"constraint_name": "FK_Orders_Customers",
             "left_table": "Orders",    "left_column": "customer_id",
             "right_table": "Customers","right_column": "id"},
            ...
        ]

    Only edges where BOTH sides are inside `tables` are returned - a foreign
    key pointing at a table the user did not select is not a usable join.

    Sprint 1 scope: composite (multi-column) foreign keys and cross-schema
    foreign keys are skipped. A composite key cannot be expressed as a single
    column pair, and emitting one column of it would silently produce a wrong
    join. Those are a later-sprint concern.

    This function is not called by any Sprint 1 endpoint. It exists now
    because it shares a code path and a test run with list_columns().
    """
    if not tables:
        raise DataSourceError("At least one table is required.")

    resolved_schema = schema or DEFAULT_SCHEMA[_dialect_of(connection_string)]
    selected = set(tables)
    engine = None
    edges: List[Dict[str, Any]] = []

    try:
        engine = _make_engine(connection_string)
        with engine.connect() as conn:
            inspector = sa_inspect(conn)

            for table in tables:
                try:
                    raw_fks = inspector.get_foreign_keys(table, schema=resolved_schema)
                except Exception:
                    # A table without FK metadata (a view, or a restricted
                    # login) contributes no edges. Not fatal.
                    continue

                for fk in raw_fks or []:
                    constrained = fk.get("constrained_columns") or []
                    referred = fk.get("referred_columns") or []
                    referred_table = fk.get("referred_table")
                    referred_schema = fk.get("referred_schema")

                    # Skip composite keys - see the docstring.
                    if len(constrained) != 1 or len(referred) != 1:
                        continue

                    # Skip cross-schema references in Sprint 1.
                    if referred_schema and referred_schema != resolved_schema:
                        continue

                    # Only keep edges between tables the user actually selected.
                    if referred_table not in selected:
                        continue

                    edges.append(
                        {
                            "constraint_name": str(fk.get("name") or ""),
                            "left_table": str(table),
                            "left_column": str(constrained[0]),
                            "right_table": str(referred_table),
                            "right_column": str(referred[0]),
                        }
                    )

        # Two tables can declare mirrored constraints; keep one edge per pair.
        deduped: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
        for edge in edges:
            key = (
                edge["left_table"],
                edge["left_column"],
                edge["right_table"],
                edge["right_column"],
            )
            deduped.setdefault(key, edge)

        return list(deduped.values())

    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError(f"Could not read foreign keys - {exc}") from exc
    finally:
        if engine is not None:
            engine.dispose()
