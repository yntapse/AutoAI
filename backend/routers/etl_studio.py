"""Visual ETL Pipeline Builder - validate, show SQL, preview and measure joins.

This router EXECUTES NOTHING THAT CHANGES DATA. Every statement it sends to a
customer database is one read-only SELECT built by services/pipeline_compiler.py
and run through db_connector.run_preview_query (SELECT-only check, read-only
transaction on PostgreSQL, timeouts, row cap). Table and column names come from
the Metadata Catalog, never from free text; values travel as bind parameters.

Moving, connecting or deleting a node on the canvas calls nothing here. The
browser asks for a validation or a preview only when the person presses a button.

Credentials follow the Catalog rules exactly: a password in the body, Windows
authentication, or the HttpOnly credential session (with the CSRF header). A
pipeline definition never contains a credential.
"""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from models.catalog_column import CatalogColumn
from models.catalog_table import CatalogTable
from models.data_source import DataSource
from routers.catalog import (
    SUPPORTED_DB_TYPES,
    _apply_session_credentials,
    _connection_mismatches,
    _scrub,
    current_session_owner,
    get_db,
    get_session_store,
)
from routers.etl_copilot import EtlCopilotConnection, _connection_string
from services import pipeline_compiler as pc
from services.credential_sessions import CredentialSessionStore
from services.db_connector import DataSourceError, run_preview_query
from services.table_preview import json_cell

logger = logging.getLogger(__name__)

# The database-session cookie is limited to the path "/catalog" (see
# routers/catalog.py). A browser only sends it to URLs under that path, so these
# endpoints live under it too. Moving them elsewhere would silently break previews.
router = APIRouter(prefix="/catalog/etl-studio", tags=["etl-studio"])


# --------------------------------------------------------------------------
# Request / response models
# --------------------------------------------------------------------------

class GraphBody(BaseModel):
    """A pipeline as the canvas sees it. Nodes carry configuration only."""

    source_id: UUID
    nodes: List[Dict[str, Any]] = Field(default_factory=list)
    edges: List[Dict[str, Any]] = Field(default_factory=list)


class SqlRequest(GraphBody):
    node_id: str = Field(..., max_length=40)


class PreviewRequest(EtlCopilotConnection):
    """Connection for this call only, plus the pipeline. Never persisted."""

    source_id: UUID
    nodes: List[Dict[str, Any]] = Field(default_factory=list)
    edges: List[Dict[str, Any]] = Field(default_factory=list)
    node_id: str = Field(..., max_length=40)
    limit: int = Field(25, ge=1, le=pc.PREVIEW_MAX_ROWS, description="Rows to show")

    @validator("db_type")
    @classmethod
    def validate_db_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in SUPPORTED_DB_TYPES:
            raise ValueError("db_type must be 'mssql' or 'postgresql'.")
        return normalized


class DiagnosticsRequest(EtlCopilotConnection):
    source_id: UUID
    nodes: List[Dict[str, Any]] = Field(default_factory=list)
    edges: List[Dict[str, Any]] = Field(default_factory=list)
    node_id: str = Field(..., max_length=40)

    @validator("db_type")
    @classmethod
    def validate_db_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in SUPPORTED_DB_TYPES:
            raise ValueError("db_type must be 'mssql' or 'postgresql'.")
        return normalized


class ColumnOut(BaseModel):
    name: str
    data_type: str
    category: str


class IssueOut(BaseModel):
    message: str
    kind: str
    field: Optional[str] = None


class NodeOut(BaseModel):
    node_id: str
    node_type: str
    status: str
    columns: Optional[List[ColumnOut]] = None
    issues: List[IssueOut]
    notes: List[str]
    inputs: Dict[str, str]
    # The registered data sources this step reads from, and why it cannot be
    # previewed on the connected database (None = it can).
    source_ids: List[str] = Field(default_factory=list)
    preview_blocked_reason: Optional[str] = None


class ValidateResponse(BaseModel):
    db_type: str
    order: List[str]
    nodes: List[NodeOut]
    # Per node: every node that sits after it. The canvas uses this to show
    # "needs recalculation" when something upstream changes.
    downstream: Dict[str, List[str]]


class SqlResponse(BaseModel):
    node_id: str
    sql: str
    parameters: Dict[str, Any]
    execution_path: str


class PreviewResponse(BaseModel):
    node_id: str
    columns: List[ColumnOut]
    rows: List[List[Any]]
    row_limit: int
    has_more: bool
    execution_path: str


class DiagnosticsResponse(BaseModel):
    node_id: str
    summary: Dict[str, Any]
    key_pairs: List[Dict[str, str]]
    execution_path: str


EXECUTION_PATH = (
    "Pushed down to the source database as one read-only SELECT "
    "(CTE chain, row-capped in the database)."
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _require_source(db: Session, source_id: UUID) -> DataSource:
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return source


def _load_sources(db: Session, source: DataSource, nodes: List[Dict[str, Any]]) -> Dict[str, pc.SourceTable]:
    """The catalog tables named by the source nodes, and only those that belong
    to this source. A table id from anywhere else is simply unknown."""
    wanted: List[UUID] = []
    # The connected source, plus any other registered source a step names. A step
    # can only NAME a source; the table is still looked up in the Catalog and its
    # real owner is recorded, so the browser cannot choose a table's source.
    allowed: set = {source.id}
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "source":
            continue
        cfg = node.get("config") or {}
        raw = cfg.get("table_id")
        if not isinstance(raw, str):
            continue
        try:
            wanted.append(UUID(raw))
        except ValueError:
            continue
        declared = cfg.get("source_id")
        if isinstance(declared, str):
            try:
                allowed.add(UUID(declared))
            except ValueError:
                pass
    if not wanted:
        return {}

    tables = (
        db.query(CatalogTable)
        .filter(CatalogTable.data_source_id.in_(allowed), CatalogTable.id.in_(set(wanted)))
        .all()
    )
    out: Dict[str, pc.SourceTable] = {}
    for table in tables:
        columns = (
            db.query(CatalogColumn)
            .filter(CatalogColumn.catalog_table_id == table.id)
            .order_by(CatalogColumn.ordinal_position.asc())
            .all()
        )
        out[str(table.id)] = pc.SourceTable(
            table_id=str(table.id),
            schema=table.schema_name,
            table=table.table_name,
            columns=[pc.make_column(c.column_name, c.data_type) for c in columns],
            row_count=table.row_count,
            row_count_is_estimate=bool(getattr(table, "row_count_is_estimate", True)),
            source_id=str(table.data_source_id),
        )
    return out


def _source_labels(db: Session, source: DataSource, nodes: List[Dict[str, Any]]) -> Dict[str, str]:
    """A readable name for the connected source and every source the pipeline names."""
    ids: set = {source.id}
    for node in nodes:
        if isinstance(node, dict) and node.get("type") == "source":
            declared = (node.get("config") or {}).get("source_id")
            if isinstance(declared, str):
                try:
                    ids.add(UUID(declared))
                except ValueError:
                    pass
    rows = db.query(DataSource).filter(DataSource.id.in_(ids)).all()
    return {str(r.id): f"{r.source_name} ({r.db_type})" for r in rows}


def _compile(db: Session, source: DataSource, body: Any) -> pc.Compiled:
    sources = _load_sources(db, source, body.nodes)
    try:
        return pc.compile_pipeline(
            source.db_type, body.nodes, body.edges, sources,
            connected_source_id=str(source.id),
            source_labels=_source_labels(db, source, body.nodes),
        )
    except pc.PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _node_out(res: pc.NodeResult, compiled: Optional[pc.Compiled] = None) -> NodeOut:
    return NodeOut(
        node_id=res.node_id,
        node_type=res.node_type,
        status=res.status,
        columns=(
            [ColumnOut(name=c.name, data_type=c.data_type, category=c.category) for c in res.columns]
            if res.columns is not None
            else None
        ),
        issues=[IssueOut(message=i.message, kind=i.kind, field=i.field) for i in res.issues],
        notes=list(res.notes),
        inputs=dict(res.inputs),
        source_ids=list(res.source_ids),
        preview_blocked_reason=pc.execution_problem(compiled, res.node_id) if compiled is not None else None,
    )


def _downstream(compiled: pc.Compiled) -> Dict[str, List[str]]:
    children: Dict[str, List[str]] = {nid: [] for nid in compiled.order}
    for nid, res in compiled.results.items():
        for parent in res.inputs.values():
            if parent in children and nid not in children[parent]:
                children[parent].append(nid)
    result: Dict[str, List[str]] = {}
    for nid in compiled.order:
        seen: List[str] = []
        stack = list(children[nid])
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.append(cur)
            stack.extend(children.get(cur, []))
        result[nid] = [n for n in compiled.order if n in seen]
    return result


def _prepare_connection(
    db: Session,
    body: Any,
    http_request: Request,
    store: CredentialSessionStore,
    owner: Optional[str],
):
    source = _require_source(db, body.source_id)
    mismatches = _connection_mismatches(body, source)
    if mismatches:
        raise HTTPException(
            status_code=400,
            detail=(
                "These connection details do not match the registered source "
                f"({', '.join(mismatches)})."
            ),
        )
    _apply_session_credentials(http_request, body, source, store, owner)
    return source


def _typed_columns(columns: Optional[List[pc.Column]]) -> List[ColumnOut]:
    return [ColumnOut(name=c.name, data_type=c.data_type, category=c.category) for c in (columns or [])]


def _check_node_runnable(compiled: pc.Compiled, node_id: str) -> pc.NodeResult:
    res = compiled.results.get(node_id)
    if res is None:
        raise HTTPException(status_code=404, detail="That node is not in the pipeline.")
    return res


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

@router.post("/validate", response_model=ValidateResponse)
def validate_pipeline(request: GraphBody, db: Session = Depends(get_db)):
    """Check every node and connection against the catalog. Touches only the
    application's own catalog tables - the customer database is not contacted."""
    source = _require_source(db, request.source_id)
    compiled = _compile(db, source, request)
    return ValidateResponse(
        db_type=compiled.db_type,
        order=list(compiled.order),
        nodes=[_node_out(compiled.results[n], compiled) for n in compiled.order],
        downstream=_downstream(compiled),
    )


@router.post("/sql", response_model=SqlResponse)
def pipeline_sql(request: SqlRequest, db: Session = Depends(get_db)):
    """The exact SELECT a preview of this node would run. Not executed."""
    source = _require_source(db, request.source_id)
    compiled = _compile(db, source, request)
    _check_node_runnable(compiled, request.node_id)
    try:
        sql, params = pc.build_preview_sql(compiled, request.node_id, 25)
    except pc.PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return SqlResponse(
        node_id=request.node_id,
        sql=pc.display_sql(sql),
        parameters=params,
        execution_path=EXECUTION_PATH,
    )


@router.post("/preview", response_model=PreviewResponse)
def preview_node(
    request: PreviewRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    store: CredentialSessionStore = Depends(get_session_store),
    owner: Optional[str] = Depends(current_session_owner),
):
    """Real rows from one node, at most `limit` of them (the cap is applied by
    the database, so a large table is never pulled into memory)."""
    source = _prepare_connection(db, request, http_request, store, owner)
    compiled = _compile(db, source, request)
    res = _check_node_runnable(compiled, request.node_id)
    try:
        sql, params = pc.build_preview_sql(compiled, request.node_id, request.limit)
    except pc.PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        names, data = run_preview_query(
            _connection_string(request), sql, params, max_rows=request.limit + 1
        )
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=_scrub(str(exc), request.password))

    has_more = len(data) > request.limit
    rows = [[json_cell(v) for v in row] for row in data[: request.limit]]
    return PreviewResponse(
        node_id=request.node_id,
        columns=_typed_columns(res.columns),
        rows=rows,
        row_limit=request.limit,
        has_more=has_more,
        execution_path=EXECUTION_PATH,
    )


@router.post("/join-diagnostics", response_model=DiagnosticsResponse)
def join_diagnostics(
    request: DiagnosticsRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    store: CredentialSessionStore = Depends(get_session_store),
    owner: Optional[str] = Depends(current_session_owner),
):
    """Measure a join on the real data: matched and unmatched rows, null keys
    and whether it repeats rows. One aggregate SELECT; no rows are returned."""
    source = _prepare_connection(db, request, http_request, store, owner)
    compiled = _compile(db, source, request)
    res = _check_node_runnable(compiled, request.node_id)
    if res.node_type != "join":
        raise HTTPException(status_code=400, detail="Diagnostics are only available for a join node.")
    node = next((n for n in request.nodes if n.get("id") == request.node_id), None) or {}
    try:
        sql, params, pairs = pc.build_join_diagnostics_sql(compiled, request.node_id, node)
    except pc.PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        names, data = run_preview_query(_connection_string(request), sql, params, max_rows=2)
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=_scrub(str(exc), request.password))
    if not data:
        raise HTTPException(status_code=400, detail="The database returned no measurements.")

    row = dict(zip(names, data[0]))
    join_type = str((node.get("config") or {}).get("join_type") or "INNER")
    return DiagnosticsResponse(
        node_id=request.node_id,
        summary=pc.summarise_join(row, join_type),
        key_pairs=[{"left": lc, "right": rc} for lc, rc in pairs],
        execution_path=EXECUTION_PATH,
    )
