"""
Multi-table dataset builder - Sprint 1.

Three stateless endpoints that let a user combine up to three tables from one
database into a single dataset, then hand that dataset to the EXISTING project
and AutoML pipeline through the usual file_id seam.

    /datamodel/columns   what columns exist in the selected tables
    /datamodel/preview   validate joins, compile SQL, count rows, 20-row sample
    /datamodel/extract   run it, write the CSV, create Project + DatasetProfile

WHY THIS FILE IMPORTS NOTHING FROM main AT MODULE LEVEL
-------------------------------------------------------
main.py imports this router, so a module-level `from main import ...` here is a
circular import. Two consequences, both deliberate:

  * The connection model below is declared locally instead of subclassing
    main.DataSourceConnection. It carries the identical eight fields and calls
    services.db_connector.build_connection_string() directly - which is where
    main._connection_string_from()'s real logic lives anyway; that function is
    only a thin HTTPException wrapper around it.

  * The four helpers that genuinely live in main (UPLOAD_DIR,
    validate_dataset_limits, _build_dataset_profile, _summarize_dataset_profile)
    are imported INSIDE the request handler. At request time main is fully
    loaded, so this resolves to the same objects the CSV and datasource paths
    use - real reuse, not a second copy.

STATELESS: connection details travel in each request body and are never stored,
exactly like the existing /datasource/* endpoints.

NO SCHEMA CHANGES: the only rows written are Project and DatasetProfile, using
the existing models.
"""


import uuid
from typing import Any, Dict, List, Optional


import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from dataclasses import asdict
from database import SessionLocal
from models.dataset_profile import DatasetProfile
from models.project import Project
from services.db_connector import (
    MAX_ROWS,
    PREVIEW_ROWS,
    DataSourceError,
    build_connection_string,
    default_schema,
    extract_to_dataframe,
    list_columns,
    preview_to_dataframe,
)
from services.sql_builder import (
    MAX_TABLES,
    SUPPORTED_JOIN_TYPES,
    SqlBuildError,
    build_count_select,
    build_select,
    edges_from_dicts,
)

from services.join_profiler import (
    JoinProfileError,
    build_profile_query,
    profile_from_row,
    recommend,
)

router = APIRouter(prefix="/datamodel", tags=["datamodel"])


# ---------------------------------------------------------------------------
# Local copy of get_db. main.get_db cannot be imported at module level (see
# the module docstring) and FastAPI resolves Depends() at import time.
# Behaviour is identical: a session per request, always closed.
# ---------------------------------------------------------------------------


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class RelationshipSpec(BaseModel):
    """One user-defined join. Sprint 1 has no detection: every relationship
    here was entered or approved by hand."""

    left_table: str
    left_column: str
    right_table: str
    right_column: str
    join_type: str = Field(default="inner", description="'inner' or 'left'")

    @validator("join_type")
    @classmethod
    def _check_join_type(cls, value: str) -> str:
        normalised = (value or "inner").strip().lower()
        if normalised not in SUPPORTED_JOIN_TYPES:
            raise ValueError(
                f"join_type must be one of {', '.join(SUPPORTED_JOIN_TYPES)}."
            )
        return normalised


class DataModelConnection(BaseModel):
    """Connection details. Sent with every call and never stored on disk.

    Mirrors main.DataSourceConnection field for field; declared here to keep
    this module free of any import from main.
    """

    db_type: str = Field(default="mssql", description="'mssql' or 'postgresql'")
    host: str = Field(..., description="Server hostname or IP, e.g. localhost")
    port: Optional[int] = Field(default=None, description="Defaults to 1433 / 5432")
    database: str = Field(..., description="Database name")
    username: Optional[str] = Field(default=None)
    password: Optional[str] = Field(default=None)
    trusted_connection: bool = Field(
        default=False, description="SQL Server Windows Authentication"
    )
    odbc_driver: Optional[str] = Field(
        default=None, description="Override the ODBC driver name if needed"
    )
    schema_name: Optional[str] = Field(
        default=None,
        description="Schema for every selected table. Defaults to dbo / public.",
    )


class DataModelColumnsRequest(DataModelConnection):
    tables: List[str] = Field(..., min_items=1, max_items=MAX_TABLES)


class DataModelPreviewRequest(DataModelConnection):
    tables: List[str] = Field(..., min_items=1, max_items=MAX_TABLES)
    base_table: str = Field(
        ..., description="The table whose rows become the dataset's rows."
    )
    relationships: List[RelationshipSpec] = Field(default_factory=list)
    selected_columns: Optional[Dict[str, List[str]]] = Field(
        default=None,
        description="Optional per-table column subset. Omit to select every column.",
    )


class DataModelExtractRequest(DataModelPreviewRequest):
    project_name: str = Field(..., description="Name for the new project")
    target_column: str = Field(
        ...,
        description=(
            "The OUTPUT column alias to predict, as returned by "
            "/datamodel/preview.column_names - not the source column name."
        ),
    )


class RelationshipProfileRequest(DataModelConnection):
    base_table: str
    base_column: str
    related_table: str
    related_column: str


# ---------------------------------------------------------------------------
# Shared internals
# ---------------------------------------------------------------------------


def _connection_string(request: DataModelConnection) -> str:
    """Build a connection URL, or raise a 400.

    Same behaviour as main._connection_string_from(); calls the same
    db_connector function underneath.
    """
    try:
        return build_connection_string(
            db_type=request.db_type,
            host=request.host,
            port=request.port,
            database=request.database,
            username=request.username,
            password=request.password,
            trusted_connection=request.trusted_connection,
            odbc_driver=request.odbc_driver,
        )
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _resolved_schema(request: DataModelConnection) -> str:
    if request.schema_name:
        return request.schema_name
    try:
        return default_schema(request.db_type)
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _rows_as_json(df: pd.DataFrame, limit: int = PREVIEW_ROWS) -> list:
    """Sample rows as plain JSON.

    Mirrors main._rows_as_json(): stringify everything so UUID, Decimal and
    timestamp values cannot break FastAPI serialization.
    """
    preview_df = df.head(limit).copy()
    preview_df = preview_df.astype(str)
    return preview_df.to_dict(orient="records")


def _columns_for_build(
    connection_string: str,
    tables: List[str],
    schema: str,
    selected_columns: Optional[Dict[str, List[str]]],
) -> Dict[str, List[str]]:
    """Introspect the selected tables and reduce to {table: [column names]}.

    When selected_columns is supplied, each table's list is filtered to it -
    preserving the database's own column order and rejecting unknown names, so
    a stale UI selection fails loudly rather than silently dropping columns.
    """
    try:
        metadata = list_columns(connection_string, tables, schema=schema)
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    columns_by_table: Dict[str, List[str]] = {}

    for table in tables:
        available = [column["name"] for column in metadata.get(table, [])]

        if selected_columns is None or table not in selected_columns:
            columns_by_table[table] = available
            continue

        wanted = {str(name).lower() for name in selected_columns[table]}
        unknown = wanted - {name.lower() for name in available}
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Column(s) {', '.join(sorted(unknown))} do not exist in "
                    f"'{table}'."
                ),
            )

        filtered = [name for name in available if name.lower() in wanted]
        if not filtered:
            raise HTTPException(
                status_code=400,
                detail=f"No columns selected for table '{table}'.",
            )
        columns_by_table[table] = filtered

    return columns_by_table


def _build_or_400(**kwargs):
    """Call sql_builder.build_select, turning SqlBuildError into a 400.

    SqlBuildError messages are written to be shown to an end user, so they are
    passed through unchanged.
    """
    try:
        return build_select(**kwargs)
    except SqlBuildError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _estimate_row_count(
    connection_string: str,
    request: DataModelPreviewRequest,
    edges,
    columns_by_table: Dict[str, List[str]],
    schema: str,
) -> Optional[int]:
    """COUNT(*) over the join, run BEFORE any rows are fetched.

    This is what stops a fan-out join from previewing perfectly on 20 rows and
    then failing at extract time against db_connector.MAX_ROWS. A count that
    cannot be produced is not fatal - the caller reports it as unknown.
    """
    try:
        count_sql = build_count_select(
            db_type=request.db_type,
            base_table=request.base_table,
            tables=request.tables,
            relationships=edges,
            columns_by_table=columns_by_table,
            schema=schema,
        )
    except SqlBuildError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        count_df = extract_to_dataframe(connection_string, count_sql)
    except DataSourceError:
        return None

    if count_df.empty:
        return None

    try:
        return int(count_df.iloc[0, 0])
    except (TypeError, ValueError):
        return None


def _join_warnings(request: DataModelPreviewRequest) -> List[str]:
    """Plain-language notes shown beside the preview."""
    warnings: List[str] = []

    for relationship in request.relationships:
        if relationship.join_type == "left":
            warnings.append(
                f"{relationship.right_table} is attached with a LEFT JOIN - "
                f"unmatched rows will contain empty values."
            )

    if len(request.tables) >= MAX_TABLES:
        warnings.append(f"{len(request.tables)} tables is the Sprint 1 maximum.")

    return warnings


def _create_project_from_frame(
    db: Session,
    df: pd.DataFrame,
    project_name: str,
    target_column: str,
    source_label: str,
) -> Dict[str, Any]:
    """Write the CSV and create Project + DatasetProfile.

    This is a DELIBERATE COPY of the tail of main.datasource_extract(), not a
    shared helper. Refactoring that function into a common helper would edit
    the only database-to-project path that works in production today; roughly
    forty duplicated lines is the reversible choice.

    Two intentional differences from the original:
      * DatasetProfile is COMMITTED here. main.datasource_extract() adds it
        without committing, so the row is discarded when the session closes
        and is only rebuilt later by _ensure_project_dataset_profile().
      * The CSV is unlinked on any failure after it is written.

    The main-owned helpers are imported here rather than at module level to
    avoid a circular import - see the module docstring.
    """
    from main import (  # noqa: PLC0415  (deliberate: see module docstring)
        UPLOAD_DIR,
        _build_dataset_profile,
        _summarize_dataset_profile,
        validate_dataset_limits,
    )

    # Same row/column caps the CSV upload path enforces.
    validate_dataset_limits(df)

    if target_column not in df.columns:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Target column '{target_column}' is not in the generated "
                f"dataset. Available columns: {', '.join(map(str, df.columns))}"
            ),
        )

    # Same rule the CSV and datasource paths enforce today (regression only).
    if not pd.api.types.is_numeric_dtype(df[target_column]):
        raise HTTPException(
            status_code=400,
            detail="Currently only regression (numeric target) is supported.",
        )

    unique_filename = f"{uuid.uuid4()}.csv"
    file_path = UPLOAD_DIR / unique_filename
    try:
        df.to_csv(file_path, index=False)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not save the generated dataset: {exc}"
        )

    try:
        numeric_features = df.drop(columns=[target_column]).select_dtypes(
            include=["number"]
        )
        categorical_features = df.drop(columns=[target_column]).select_dtypes(
            exclude=["number"]
        )

        dataset_profile = _build_dataset_profile(df, target_column)
        dataset_profile_summary = _summarize_dataset_profile(dataset_profile)

        new_project = Project(
            project_name=project_name,
            file_id=unique_filename,
            target_column=target_column,
            num_rows=len(df),
            num_features=df.shape[1] - 1,
            num_numeric_features=numeric_features.shape[1],
            num_categorical_features=categorical_features.shape[1],
            missing_value_count=int(df.isnull().sum().sum()),
            target_variance=float(df[target_column].var()),
        )
        db.add(new_project)
        db.commit()
        db.refresh(new_project)

        db.add(
            DatasetProfile(
                project_id=new_project.id,
                profile_json=dataset_profile,
            )
        )
        db.commit()
    except HTTPException:
        file_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        db.rollback()
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Database insert failed: {exc}")

    return {
        "project_id": str(new_project.id),
        "project_name": project_name,
        "file_id": unique_filename,
        "target_column": target_column,
        "rows": len(df),
        "columns": len(df.columns),
        "source": source_label,
        "dataset_profile_summary": dataset_profile_summary,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/columns")
async def datamodel_columns(request: DataModelColumnsRequest):
    """Return the columns of the selected tables, so the join editor can
    populate its dropdowns.

    Reads metadata only - no table data is touched.
    """
    connection_string = _connection_string(request)
    schema = _resolved_schema(request)

    try:
        metadata = list_columns(connection_string, request.tables, schema=schema)
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "schema": schema,
        "tables": [
            {"table": table, "columns": metadata.get(table, [])}
            for table in request.tables
        ],
    }


@router.post("/preview")
async def datamodel_preview(request: DataModelPreviewRequest):
    """Validate the joins, compile one SELECT, estimate the row count and
    return a 20-row sample.

    The first six response fields are deliberately identical to
    /datasource/preview so the existing target-column picker consumes a joined
    dataset with no change.
    """
    connection_string = _connection_string(request)
    schema = _resolved_schema(request)

    columns_by_table = _columns_for_build(
        connection_string, request.tables, schema, request.selected_columns
    )

    try:
        edges = edges_from_dicts([r.dict() for r in request.relationships])
    except SqlBuildError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Validates the graph (max tables, connectivity, cycles, column existence)
    # and compiles the statement. Any problem surfaces as a 400 here, before
    # the source database is asked to run anything expensive.
    built = _build_or_400(
        db_type=request.db_type,
        base_table=request.base_table,
        tables=request.tables,
        relationships=edges,
        columns_by_table=columns_by_table,
        schema=schema,
        limit=PREVIEW_ROWS,
    )

    estimated_row_count = _estimate_row_count(
        connection_string, request, edges, columns_by_table, schema
    )

    try:
        df, has_more = preview_to_dataframe(
            connection_string, built.sql, limit=PREVIEW_ROWS
        )
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    numeric_columns = df.select_dtypes(include=["number"]).columns.tolist()

    warnings = _join_warnings(request)
    exceeds_limit = bool(
        estimated_row_count is not None and estimated_row_count > MAX_ROWS
    )
    if exceeds_limit:
        warnings.insert(
            0,
            f"This join produces about {estimated_row_count:,} rows, over the "
            f"{MAX_ROWS:,}-row limit. Narrow the selection before extracting.",
        )

    return {
        # --- identical shape to /datasource/preview ---
        "preview_rows": len(df),
        "has_more": has_more,
        "columns": len(df.columns),
        "column_names": [str(c) for c in df.columns],
        "numeric_columns": [str(c) for c in numeric_columns],
        "sample_rows": _rows_as_json(df, limit=len(df)),
        # --- additive, read only by the multi-table panel ---
        "generated_sql": built.sql,
        "estimated_row_count": estimated_row_count,
        "row_limit": MAX_ROWS,
        "exceeds_limit": exceeds_limit,
        "alias_map": built.alias_map,
        "table_aliases": built.table_aliases,
        "base_table": request.base_table,
        "schema": schema,
        "warnings": warnings,
    }


@router.post("/analyze-relationship")
async def datamodel_analyze_relationship(
    request: RelationshipProfileRequest,
):
    """Profile one candidate relationship against the real source data."""

    connection_string = _connection_string(request)
    schema = _resolved_schema(request)

    # Verify both tables/columns exist before running the profile query.
    _columns_for_build(
        connection_string,
        [request.base_table, request.related_table],
        schema,
        None,
    )

    try:
        sql = build_profile_query(
            db_type=request.db_type,
            schema=schema,
            base_table=request.base_table,
            base_column=request.base_column,
            related_table=request.related_table,
            related_column=request.related_column,
        )
    except JoinProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        df = extract_to_dataframe(connection_string, sql)
    except DataSourceError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"While profiling the relationship: {exc}",
        )

    profile = profile_from_row(df.iloc[0].to_dict())

    recommendation = recommend(
        profile,
        request.base_table,
        request.related_table,
        MAX_ROWS,
    )

    return {
        "base_table": request.base_table,
        "related_table": request.related_table,
        "profile": asdict(profile),
        "recommendation": asdict(recommendation),
    }


@router.post("/extract")
async def datamodel_extract(
    request: DataModelExtractRequest,
    db: Session = Depends(get_db),
):
    """Run the compiled join, save the result as a CSV in uploads/, and create
    a Project - exactly as if the user had uploaded that CSV by hand.

    Returns a file_id. Every existing feature (training, agent, transform,
    results, downloads) then works with no further change.
    """
    connection_string = _connection_string(request)
    schema = _resolved_schema(request)

    columns_by_table = _columns_for_build(
        connection_string, request.tables, schema, request.selected_columns
    )

    try:
        edges = edges_from_dicts([r.dict() for r in request.relationships])
    except SqlBuildError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    built = _build_or_400(
        db_type=request.db_type,
        base_table=request.base_table,
        tables=request.tables,
        relationships=edges,
        columns_by_table=columns_by_table,
        schema=schema,
        limit=None,
    )

    # extract_to_dataframe applies _validate_query, the streaming cursor, the
    # 50,000-row cap and the 200-column cap - unchanged.
    try:
        df = extract_to_dataframe(connection_string, built.sql)
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    table_count = len(request.tables)
    source_label = (
        f"{request.db_type}://{request.host}/{request.database} "
        f"({table_count} table{'s' if table_count != 1 else ''} joined)"
    )

    payload = _create_project_from_frame(
        db=db,
        df=df,
        project_name=request.project_name,
        target_column=request.target_column,
        source_label=source_label,
    )
    payload["generated_sql"] = built.sql
    payload["tables"] = list(request.tables)

    return JSONResponse(status_code=201, content=payload)
