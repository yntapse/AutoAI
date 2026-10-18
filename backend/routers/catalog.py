"""Metadata Catalog - source registration, scanning and exploration.

Sprint 1 of the Data Intelligence OS. Follows routers/profiling.py exactly:
same get_db shape, same Pydantic v1 validators, same error mapping.

Credentials are NOT stored. A registered source holds connection metadata only;
every scan supplies the password in the request body, which is the same posture
every other router in this codebase already uses.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import SessionLocal
from models.catalog_column import CatalogColumn
from models.catalog_relationship import CatalogRelationship
from models.catalog_scan import CatalogScan
from models.catalog_table import CatalogTable
from models.data_source import DataSource
from routers.etl_copilot import EtlCopilotConnection, _connection_string
from services.credential_sessions import (
    ActiveSession,
    CredentialSessionStore,
    SessionLimitError,
    get_credential_store,
    multi_worker_configured,
)
from services.db_connector import (
    DEFAULT_PORT,
    DataSourceError,
    DataSourceTableNotFound,
    build_connection_string,
    default_schema,
    test_connection,
)
from services.metadata_scanner import scan_source
from services.table_preview import PreviewCursorError, get_table_page

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["metadata-catalog"])

SUPPORTED_DB_TYPES = {"mssql", "postgresql"}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --------------------------------------------------------------------------
# Request / response models
# --------------------------------------------------------------------------


class SourceRegisterRequest(BaseModel):
    source_name: str = Field(..., min_length=1, max_length=120)
    db_type: str = Field(..., description="'mssql' or 'postgresql'")
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    username: Optional[str] = None
    schema_name: Optional[str] = None
    trusted_connection: bool = False
    odbc_driver: Optional[str] = None
    description: Optional[str] = Field(None, max_length=500)

    @validator("db_type")
    @classmethod
    def validate_db_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in SUPPORTED_DB_TYPES:
            raise ValueError("db_type must be 'mssql' or 'postgresql'.")
        return normalized

    @validator("source_name")
    @classmethod
    def validate_source_name(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("source_name must not be blank.")
        return cleaned


class SourceResponse(BaseModel):
    id: UUID
    source_name: str
    db_type: str
    host: Optional[str]
    port: Optional[int]
    database: Optional[str]
    username: Optional[str]
    schema_name: Optional[str]
    trusted_connection: bool
    description: Optional[str]
    status: str
    last_scanned_at: Optional[datetime]
    created_at: datetime

    class Config:
        orm_mode = True


class ScanRequest(EtlCopilotConnection):
    """Connection credentials for this scan only. Never persisted."""

    include_row_counts: bool = True
    include_sample_values: bool = False
    max_tables: Optional[int] = Field(None, ge=1, le=500)

    @validator("db_type")
    @classmethod
    def validate_db_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in SUPPORTED_DB_TYPES:
            raise ValueError("db_type must be 'mssql' or 'postgresql'.")
        return normalized


class TablePreviewRequest(EtlCopilotConnection):
    """Connection credentials for this preview only. Never persisted."""

    limit: int = Field(20, ge=1, le=100, description="Rows per page")
    cursor: Optional[str] = Field(
        None,
        max_length=4096,
        description="next_cursor from the previous page; omit for the first page",
    )

    @validator("db_type")
    @classmethod
    def validate_db_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in SUPPORTED_DB_TYPES:
            raise ValueError("db_type must be 'mssql' or 'postgresql'.")
        return normalized


class TablePreviewResponse(BaseModel):
    """One page of a table. Declares every field returned, so a credential can
    never be added by accident."""

    table_name: str
    schema_name: str
    column_names: List[str]
    rows: List[Dict[str, Any]]
    row_count: int
    page_size: int
    has_more: bool
    next_cursor: Optional[str] = None
    pagination_mode: str
    stable_order: bool
    start_row: Optional[int] = None
    message: Optional[str] = None


class ScanResponse(BaseModel):
    scan_id: UUID
    data_source_id: UUID
    schema_name: str
    status: str
    table_count: int
    column_count: int
    relationship_count: int
    duration_ms: Optional[int]
    created_at: datetime


class ColumnResponse(BaseModel):
    column_name: str
    data_type: str
    ordinal_position: int
    is_nullable: bool
    is_primary_key: bool
    is_foreign_key: bool
    normalized_name: Optional[str]
    sample_values: Optional[List[Any]]
    business_description: Optional[str] = None

    class Config:
        orm_mode = True


class TableResponse(BaseModel):
    id: UUID
    table_name: str
    schema_name: str
    column_count: int
    row_count: Optional[int]
    # True when row_count came from statistics rather than COUNT(*).
    row_count_is_estimate: bool = True
    has_primary_key: bool
    business_description: Optional[str] = None

    class Config:
        orm_mode = True


class TableDetailResponse(TableResponse):
    columns: List[ColumnResponse] = Field(default_factory=list)


class RelationshipResponse(BaseModel):
    id: UUID
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    constraint_name: Optional[str]
    origin: str
    confidence: float
    approved: Optional[bool]

    class Config:
        orm_mode = True


class CatalogOverviewResponse(BaseModel):
    data_source_id: UUID
    source_name: str
    scan_id: Optional[UUID]
    scanned_at: Optional[datetime]
    schema_name: Optional[str]
    table_count: int
    column_count: int
    relationship_count: int
    tables: List[TableResponse] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _require_source(db: Session, source_id: UUID) -> DataSource:
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if source is None:
        raise HTTPException(status_code=404, detail="Data source not found.")
    return source


def _latest_scan(db: Session, source_id: UUID) -> Optional[CatalogScan]:
    return (
        db.query(CatalogScan)
        .filter(
            CatalogScan.data_source_id == source_id,
            CatalogScan.status == "completed",
        )
        .order_by(CatalogScan.created_at.desc())
        .first()
    )


# --------------------------------------------------------------------------
# Source registration
# --------------------------------------------------------------------------


@router.post("/sources", response_model=SourceResponse, status_code=201)
def register_source(request: SourceRegisterRequest, db: Session = Depends(get_db)):
    """Register a data source. Stores connection metadata, never a password."""
    existing = (
        db.query(DataSource)
        .filter(DataSource.source_name == request.source_name)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A source named '{request.source_name}' already exists.",
        )

    source = DataSource(
        source_name=request.source_name,
        db_type=request.db_type,
        host=request.host,
        port=request.port,
        database=request.database,
        username=request.username,
        schema_name=request.schema_name or default_schema(request.db_type),
        trusted_connection=request.trusted_connection,
        odbc_driver=request.odbc_driver,
        description=request.description,
        status="registered",
    )

    try:
        db.add(source)
        db.commit()
        db.refresh(source)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to register data source")
        raise HTTPException(status_code=500, detail="Failed to register source.") from exc

    return source


@router.get("/sources", response_model=List[SourceResponse])
def list_sources(db: Session = Depends(get_db)):
    return (
        db.query(DataSource)
        .order_by(DataSource.created_at.desc())
        .all()
    )


@router.get("/sources/{source_id}", response_model=SourceResponse)
def get_source(source_id: UUID, db: Session = Depends(get_db)):
    return _require_source(db, source_id)


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(source_id: UUID, db: Session = Depends(get_db)):
    source = _require_source(db, source_id)
    try:
        db.delete(source)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to delete data source")
        raise HTTPException(status_code=500, detail="Failed to delete source.") from exc
    return None


@router.post("/sources/{source_id}/test", status_code=200)
def test_source_connection(
    source_id: UUID,
    request: ScanRequest,
    db: Session = Depends(get_db),
):
    """Verify credentials reach the source. Nothing is persisted."""
    _require_source(db, source_id)
    connection_string = _connection_string(request)
    try:
        return test_connection(connection_string)
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --------------------------------------------------------------------------
# Temporary database sessions
#
# The password is typed once to CONNECT. The server verifies it and keeps it in
# memory for a limited time (services/credential_sessions.py); the browser gets
# an unguessable token in an HttpOnly cookie. Scans and previews then need no
# password. Nothing here persists, logs or returns a password or token.
# --------------------------------------------------------------------------

CSRF_HEADER = "X-Requested-By"
CSRF_VALUE = "catalog-ui"
SESSION_REQUIRED_MESSAGE = (
    "Your database session has expired or was not started. "
    "Reconnect to continue."
)


class SessionConnectRequest(BaseModel):
    """The password is used for this call to open the session and is not kept
    anywhere except the server's temporary session memory."""

    password: Optional[str] = Field(None, max_length=1024)
    odbc_driver: Optional[str] = Field(None, max_length=200)


class SessionStatusResponse(BaseModel):
    source_id: UUID
    connected: bool
    expires_in_seconds: Optional[int] = None
    idle_timeout_seconds: int
    max_lifetime_seconds: int


def current_session_owner(http_request: Request) -> Optional[str]:
    """Who a new session belongs to.

    The application has no user accounts yet (the login and register pages are
    not connected to a backend), so there is no user to bind to and this returns
    None. The session is then protected by its unguessable token alone, held in
    an HttpOnly cookie of the browser that connected.

    WHEN LOGIN IS ADDED: return the authenticated user's id here. Sessions are
    then bound to that user, and another user presenting the same token is
    refused (this is already enforced and tested with a stand-in owner).
    """
    return None


def get_session_store() -> CredentialSessionStore:
    return get_credential_store()


def _cookie_name(source_id: UUID) -> str:
    return f"catalog_db_session_{source_id.hex[:16]}"


def _require_csrf_header(http_request: Request) -> None:
    """Cookie-carried requests must send a custom header. A browser only lets
    pages from allowed origins add one (it forces a CORS pre-flight), so a
    page on another site cannot forge these calls."""
    if http_request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(
            status_code=403, detail="Missing or invalid request header."
        )


def _session_status(
    source_id: UUID, session: Optional[ActiveSession], store: CredentialSessionStore
) -> SessionStatusResponse:
    return SessionStatusResponse(
        source_id=source_id,
        connected=session is not None,
        expires_in_seconds=session.expires_in_seconds if session else None,
        idle_timeout_seconds=store.settings.idle_seconds,
        max_lifetime_seconds=store.settings.max_seconds,
    )


def _apply_session_credentials(
    http_request: Request,
    request: EtlCopilotConnection,
    source: DataSource,
    store: CredentialSessionStore,
    owner: Optional[str],
) -> None:
    """Give `request` the password of the caller's active session.

    A password in the request body (the original behaviour) or Windows
    authentication skips this entirely. Otherwise the session is required, and
    its host, port, database and login REPLACE what the request said, so the
    stored password can only ever be sent to the server the source was
    registered with.
    """
    if request.password is not None or request.trusted_connection:
        return

    _require_csrf_header(http_request)

    mismatches = _connection_mismatches(request, source)
    if mismatches:
        raise HTTPException(
            status_code=400,
            detail=(
                "These connection details do not match the registered source "
                f"({', '.join(mismatches)})."
            ),
        )

    token = http_request.cookies.get(_cookie_name(source.id))
    session = store.lookup(token, source.id, owner)
    if session is None:
        raise HTTPException(status_code=401, detail=SESSION_REQUIRED_MESSAGE)

    request.host = session.host
    request.port = session.port
    request.database = session.database
    request.username = session.username
    request.password = session.password
    if not request.odbc_driver:
        request.odbc_driver = session.odbc_driver


@router.post("/sources/{source_id}/session", response_model=SessionStatusResponse)
def connect_source_session(
    source_id: UUID,
    body: SessionConnectRequest,
    http_request: Request,
    response: Response,
    db: Session = Depends(get_db),
    store: CredentialSessionStore = Depends(get_session_store),
    owner: Optional[str] = Depends(current_session_owner),
):
    """Verify the password against the registered source and open a temporary
    session. The connection details come from the registered source, not from
    the request."""
    _require_csrf_header(http_request)
    source = _require_source(db, source_id)

    if multi_worker_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Temporary database sessions need a single backend worker. "
                "This server is configured with several."
            ),
        )

    if not source.host or not source.database:
        raise HTTPException(
            status_code=400,
            detail="This source has no host or database recorded. Register it again with both.",
        )
    trusted = bool(source.trusted_connection)
    if not trusted and not source.username:
        raise HTTPException(
            status_code=400,
            detail="This source has no username recorded. Register it again with one.",
        )
    if not trusted and body.password is None:
        raise HTTPException(status_code=400, detail="The database password is required.")

    odbc_driver = body.odbc_driver or source.odbc_driver
    try:
        connection_string = build_connection_string(
            db_type=source.db_type,
            host=source.host,
            port=source.port,
            database=source.database,
            username=source.username,
            password=body.password,
            trusted_connection=trusted,
            odbc_driver=odbc_driver,
        )
        test_connection(connection_string)
    except DataSourceError as exc:
        # The driver's message can name the host and login; the user gets a
        # fixed sentence and the log gets only the error's type.
        logger.warning(
            "Database session could not be opened for source %s (%s)",
            source_id, type(exc).__name__,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not connect to the database with these credentials. "
                "Check the password and that the server is reachable."
            ),
        )

    name = _cookie_name(source.id)
    store.delete(http_request.cookies.get(name), source.id, owner)  # replace, don't pile up
    try:
        token, session = store.create(
            owner=owner, source_id=source.id, host=source.host, port=source.port,
            database=source.database, username=source.username,
            trusted_connection=trusted, odbc_driver=odbc_driver,
            password=body.password or "",
        )
    except SessionLimitError:
        raise HTTPException(status_code=503, detail="Too many active database sessions. Try again later.")

    samesite = store.settings.cookie_samesite
    secure = store.settings.cookie_secure
    if secure is None:
        secure = http_request.url.scheme == "https"
    response.set_cookie(
        key=name, value=token, httponly=True, samesite=samesite,
        secure=secure or samesite == "none", path="/catalog",
    )
    return _session_status(source.id, session, store)


@router.get("/sources/{source_id}/session", response_model=SessionStatusResponse)
def source_session_status(
    source_id: UUID,
    http_request: Request,
    db: Session = Depends(get_db),
    store: CredentialSessionStore = Depends(get_session_store),
    owner: Optional[str] = Depends(current_session_owner),
):
    """Is there a live session? Checking never extends it."""
    source = _require_source(db, source_id)
    token = http_request.cookies.get(_cookie_name(source.id))
    session = store.lookup(token, source.id, owner, touch=False)
    return _session_status(source.id, session, store)


@router.delete("/sources/{source_id}/session", response_model=SessionStatusResponse)
def disconnect_source_session(
    source_id: UUID,
    http_request: Request,
    response: Response,
    db: Session = Depends(get_db),
    store: CredentialSessionStore = Depends(get_session_store),
    owner: Optional[str] = Depends(current_session_owner),
):
    """End the session now. The stored password is dropped immediately."""
    _require_csrf_header(http_request)
    source = _require_source(db, source_id)
    name = _cookie_name(source.id)
    store.delete(http_request.cookies.get(name), source.id, owner)
    response.delete_cookie(key=name, path="/catalog")
    return _session_status(source.id, None, store)


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------


@router.post("/sources/{source_id}/scan", response_model=ScanResponse, status_code=201)
def scan_data_source(
    source_id: UUID,
    request: ScanRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    store: CredentialSessionStore = Depends(get_session_store),
    owner: Optional[str] = Depends(current_session_owner),
):
    """Scan a source and persist its metadata as a new catalog scan.

    With no password in the body, the caller's temporary database session
    supplies it (401 if there is none).
    """
    source = _require_source(db, source_id)
    _apply_session_credentials(http_request, request, source, store, owner)
    connection_string = _connection_string(request)
    resolved_schema = (
        request.schema_name or source.schema_name or default_schema(request.db_type)
    )

    scan = CatalogScan(
        data_source_id=source.id,
        schema_name=resolved_schema,
        status="running",
        included_row_counts=1 if request.include_row_counts else 0,
        included_sample_values=1 if request.include_sample_values else 0,
    )
    db.add(scan)
    db.commit()
    db.refresh(scan)

    try:
        catalog = scan_source(
            connection_string=connection_string,
            db_type=request.db_type,
            schema=resolved_schema,
            include_row_counts=request.include_row_counts,
            include_sample_values=request.include_sample_values,
            max_tables=request.max_tables,
        )
    except DataSourceError as exc:
        scan.status = "failed"
        scan.error = str(exc)
        scan.completed_at = datetime.utcnow()
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - a scan must never leak a traceback
        scan.status = "failed"
        scan.error = f"{type(exc).__name__}: {exc}"
        scan.completed_at = datetime.utcnow()
        db.commit()
        logger.exception("Metadata scan failed")
        raise HTTPException(status_code=500, detail="Metadata scan failed.") from exc

    try:
        for table in catalog["tables"]:
            table_row = CatalogTable(
                scan_id=scan.id,
                data_source_id=source.id,
                schema_name=table["schema_name"],
                table_name=table["table_name"],
                column_count=table["column_count"],
                row_count=table["row_count"],
                row_count_is_estimate=table["row_count_is_estimate"],
                has_primary_key=table["has_primary_key"],
            )
            db.add(table_row)
            db.flush()

            for column in table["columns"]:
                db.add(
                    CatalogColumn(
                        catalog_table_id=table_row.id,
                        column_name=column["column_name"],
                        data_type=column["data_type"],
                        ordinal_position=column["ordinal_position"],
                        is_nullable=column["is_nullable"],
                        is_primary_key=column["is_primary_key"],
                        is_foreign_key=column["is_foreign_key"],
                        normalized_name=column["normalized_name"],
                        sample_values=column["sample_values"],
                    )
                )

        for edge in catalog["relationships"]:
            db.add(
                CatalogRelationship(
                    scan_id=scan.id,
                    data_source_id=source.id,
                    left_table=edge["left_table"],
                    left_column=edge["left_column"],
                    right_table=edge["right_table"],
                    right_column=edge["right_column"],
                    constraint_name=edge.get("constraint_name"),
                    origin=edge["origin"],
                    confidence=edge["confidence"],
                )
            )

        scan.status = "completed"
        scan.table_count = catalog["table_count"]
        scan.column_count = catalog["column_count"]
        scan.relationship_count = catalog["relationship_count"]
        scan.duration_ms = catalog["duration_ms"]
        scan.completed_at = datetime.utcnow()

        source.status = "scanned"
        source.last_scanned_at = scan.completed_at

        db.commit()
        db.refresh(scan)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to persist catalog scan")
        raise HTTPException(status_code=500, detail="Failed to save scan.") from exc

    return ScanResponse(
        scan_id=scan.id,
        data_source_id=source.id,
        schema_name=scan.schema_name,
        status=scan.status,
        table_count=scan.table_count,
        column_count=scan.column_count,
        relationship_count=scan.relationship_count,
        duration_ms=scan.duration_ms,
        created_at=scan.created_at,
    )


@router.get("/sources/{source_id}/scans", response_model=List[ScanResponse])
def list_scans(source_id: UUID, db: Session = Depends(get_db)):
    _require_source(db, source_id)
    scans = (
        db.query(CatalogScan)
        .filter(CatalogScan.data_source_id == source_id)
        .order_by(CatalogScan.created_at.desc())
        .all()
    )
    return [
        ScanResponse(
            scan_id=scan.id,
            data_source_id=scan.data_source_id,
            schema_name=scan.schema_name,
            status=scan.status,
            table_count=scan.table_count,
            column_count=scan.column_count,
            relationship_count=scan.relationship_count,
            duration_ms=scan.duration_ms,
            created_at=scan.created_at,
        )
        for scan in scans
    ]


# --------------------------------------------------------------------------
# Source Explorer
# --------------------------------------------------------------------------


@router.get("/sources/{source_id}/overview", response_model=CatalogOverviewResponse)
def source_overview(source_id: UUID, db: Session = Depends(get_db)):
    """Everything the Source Explorer needs for its landing view."""
    source = _require_source(db, source_id)
    scan = _latest_scan(db, source_id)

    if scan is None:
        return CatalogOverviewResponse(
            data_source_id=source.id,
            source_name=source.source_name,
            scan_id=None,
            scanned_at=None,
            schema_name=source.schema_name,
            table_count=0,
            column_count=0,
            relationship_count=0,
            tables=[],
        )

    tables = (
        db.query(CatalogTable)
        .filter(CatalogTable.scan_id == scan.id)
        .order_by(CatalogTable.table_name.asc())
        .all()
    )

    return CatalogOverviewResponse(
        data_source_id=source.id,
        source_name=source.source_name,
        scan_id=scan.id,
        scanned_at=scan.completed_at,
        schema_name=scan.schema_name,
        table_count=scan.table_count,
        column_count=scan.column_count,
        relationship_count=scan.relationship_count,
        tables=[TableResponse.from_orm(table) for table in tables],
    )


@router.get("/tables/{table_id}", response_model=TableDetailResponse)
def table_detail(table_id: UUID, db: Session = Depends(get_db)):
    table = db.query(CatalogTable).filter(CatalogTable.id == table_id).first()
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found in the catalog.")

    columns = (
        db.query(CatalogColumn)
        .filter(CatalogColumn.catalog_table_id == table.id)
        .order_by(CatalogColumn.ordinal_position.asc())
        .all()
    )

    detail = TableDetailResponse.from_orm(table)
    detail.columns = [ColumnResponse.from_orm(column) for column in columns]
    return detail


@router.get(
    "/sources/{source_id}/relationships",
    response_model=List[RelationshipResponse],
)
def source_relationships(source_id: UUID, db: Session = Depends(get_db)):
    _require_source(db, source_id)
    scan = _latest_scan(db, source_id)
    if scan is None:
        return []
    return (
        db.query(CatalogRelationship)
        .filter(CatalogRelationship.scan_id == scan.id)
        .order_by(CatalogRelationship.left_table.asc())
        .all()
    )


@router.get("/search", response_model=List[Dict[str, Any]])
def search_catalog(
    q: str = Query(..., min_length=2, description="Table or column name fragment"),
    source_id: Optional[UUID] = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Search the catalog by table or column name."""
    pattern = f"%{q.strip().lower()}%"

    table_query = db.query(CatalogTable).filter(
        CatalogTable.table_name.ilike(pattern)
    )
    column_query = (
        db.query(CatalogColumn, CatalogTable)
        .join(CatalogTable, CatalogColumn.catalog_table_id == CatalogTable.id)
        .filter(CatalogColumn.column_name.ilike(pattern))
    )
    if source_id is not None:
        table_query = table_query.filter(CatalogTable.data_source_id == source_id)
        column_query = column_query.filter(CatalogTable.data_source_id == source_id)

    results: List[Dict[str, Any]] = []
    for table in table_query.limit(limit).all():
        results.append(
            {
                "match_type": "table",
                "table_id": str(table.id),
                "table_name": table.table_name,
                "schema_name": table.schema_name,
                "column_name": None,
                "data_type": None,
            }
        )
    for column, table in column_query.limit(limit).all():
        results.append(
            {
                "match_type": "column",
                "table_id": str(table.id),
                "table_name": table.table_name,
                "schema_name": table.schema_name,
                "column_name": column.column_name,
                "data_type": column.data_type,
            }
        )
    return results[:limit]


def _connection_mismatches(request: EtlCopilotConnection, source: DataSource) -> List[str]:
    """Names of the request fields that disagree with the registered source.

    Only what the source actually recorded is compared (host, database and
    username are optional at registration). The port is compared as the
    effective port, so "no port" equals the database's default port. With
    Windows authentication the username is not compared; the password never
    is. Host matching is case-insensitive but literal: "localhost" and
    "127.0.0.1" are different values.
    """
    db_type = (source.db_type or "").strip().lower()
    is_mssql = db_type == "mssql"

    def loose(value: Optional[str]) -> str:
        return (value or "").strip().lower()

    def exact(value: Optional[str]) -> str:
        return (value or "").strip()

    # SQL Server names and logins are case-insensitive by default; PostgreSQL's
    # database names and roles are case-sensitive.
    same_name = loose if is_mssql else exact

    mismatches: List[str] = []
    if request.db_type != db_type:
        mismatches.append("db_type")
    if source.host and loose(request.host) != loose(source.host):
        mismatches.append("host")
    if (request.port or DEFAULT_PORT.get(request.db_type)) != (
        source.port or DEFAULT_PORT.get(db_type)
    ):
        mismatches.append("port")
    if source.database and same_name(request.database) != same_name(source.database):
        mismatches.append("database")
    if bool(request.trusted_connection) != bool(source.trusted_connection):
        mismatches.append("trusted_connection")
    elif (
        not source.trusted_connection
        and source.username
        and same_name(request.username) != same_name(source.username)
    ):
        mismatches.append("username")
    return mismatches


def _scrub(message: str, password: Optional[str]) -> str:
    return message.replace(password, "****") if password else message


@router.post("/tables/{table_id}/preview", response_model=TablePreviewResponse)
def preview_table_data(
    table_id: UUID,
    request: TablePreviewRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    store: CredentialSessionStore = Depends(get_session_store),
    owner: Optional[str] = Depends(current_session_owner),
):
    """One page of real rows from a catalogued table.

    The schema, table and column names come from the catalog, never from the
    request. The supplied connection must match the table's registered source
    before the database is contacted. Credentials are used for this call only.
    """
    table = db.query(CatalogTable).filter(CatalogTable.id == table_id).first()
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found in the catalog.")

    source = _require_source(db, table.data_source_id)

    mismatches = _connection_mismatches(request, source)
    if mismatches:
        raise HTTPException(
            status_code=400,
            detail=(
                "These connection details do not match the registered source "
                f"({', '.join(mismatches)})."
            ),
        )

    _apply_session_credentials(http_request, request, source, store, owner)

    columns = (
        db.query(CatalogColumn)
        .filter(CatalogColumn.catalog_table_id == table.id)
        .order_by(CatalogColumn.ordinal_position.asc())
        .all()
    )
    column_info = [
        {
            "column_name": column.column_name,
            "data_type": column.data_type,
            "is_primary_key": bool(column.is_primary_key),
        }
        for column in columns
    ]

    connection_string = _connection_string(request)

    try:
        return get_table_page(
            connection_string=connection_string,
            db_type=source.db_type,
            schema=table.schema_name,
            table=table.table_name,
            columns=column_info,
            limit=request.limit,
            cursor=request.cursor,
        )
    except PreviewCursorError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except DataSourceTableNotFound as exc:
        raise HTTPException(status_code=404, detail=_scrub(str(exc), request.password))
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=_scrub(str(exc), request.password))
