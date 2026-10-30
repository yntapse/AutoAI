from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field, root_validator


class AggFunction(str, Enum):
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"
    COUNT = "COUNT"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    MEDIAN = "MEDIAN"
    STDDEV = "STDDEV"


FANOUT_SAFE = {
    AggFunction.MIN,
    AggFunction.MAX,
    AggFunction.COUNT_DISTINCT,
}

ZERO_WHEN_ABSENT = {
    AggFunction.SUM,
    AggFunction.COUNT,
    AggFunction.COUNT_DISTINCT,
}

DECOMPOSABLE = {
    AggFunction.SUM,
    AggFunction.MIN,
    AggFunction.MAX,
    AggFunction.COUNT,
}

SECOND_LEVEL = {
    AggFunction.SUM: AggFunction.SUM,
    AggFunction.MIN: AggFunction.MIN,
    AggFunction.MAX: AggFunction.MAX,
    AggFunction.COUNT: AggFunction.SUM,
}


class AttributeSpec(BaseModel):
    source_table: str
    source_column: str
    name: Optional[str] = None

    class Config:
        extra = "forbid"


class FilterSpec(BaseModel):
    source_table: Optional[str] = None
    column: str
    operator: str
    value: Any
    value_type: Optional[str] = None

    class Config:
        extra = "forbid"


class HavingSpec(BaseModel):
    measure_name: str
    operator: str
    value: Any

    class Config:
        extra = "forbid"


class MeasureSpec(BaseModel):
    name: str = Field(alias="measure_name")
    source_table: str
    source_column: Optional[str] = Field(default=None, alias="column")
    aggregation: AggFunction
    distinct_key: List[str] = []
    include_in_output: Optional[bool] = None
    zero_when_absent: Optional[bool] = None
    filters: List[FilterSpec] = []
    time_column: Optional[str] = None
    window: Optional[str] = None
    as_of: Optional[str] = None
    description: str = ""

    class Config:
        extra = "forbid"
        allow_population_by_field_name = True

    @root_validator
    def set_defaults(cls, values):
        aggregation = values.get("aggregation")

        if values.get("zero_when_absent") is None:
            values["zero_when_absent"] = aggregation in ZERO_WHEN_ABSENT

        return values

    @root_validator(pre=True)
    def accept_legacy_column_name(cls, values):
        if isinstance(values, dict):
            if "source_column" not in values and "column" in values:
                values["source_column"] = values["column"]
        return values


class JoinHop(BaseModel):
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    join_type: str = "left"

    cardinality: Optional[str] = None
    match_rate: Optional[float] = None
    row_multiplication: Optional[bool] = None

    class Config:
        extra = "forbid"


class JoinSpec(BaseModel):
    """
    Temporary compatibility model.

    The final architecture replaces JoinSpec with JoinPath / JoinHop.
    This remains only so the current discovery/test code continues to run
    while the join layer is migrated.
    """

    left_table: str
    left_column: str
    right_table: str
    right_column: str
    join_type: str = "left"
    verified: bool = False

    class Config:
        extra = "forbid"


class JoinPath(BaseModel):
    entity_table: str
    source_table: str
    hops: List[JoinHop] = []

    verified: bool = False
    discovered_via: str = "foreign_key"
    alternatives_considered: int = 1

    class Config:
        extra = "forbid"

    @property
    def requires_nested_aggregation(self) -> bool:
        return len(self.hops) > 1


class ColumnInfo(BaseModel):
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False

    class Config:
        extra = "forbid"


class TableInfo(BaseModel):
    name: str
    columns: List[ColumnInfo]

    class Config:
        extra = "forbid"


class Catalog(BaseModel):
    db_type: str
    schema_name: Optional[str] = None
    tables: List[TableInfo]
    relationships: List[JoinHop] = []

    class Config:
        extra = "forbid"


class DatasetSpec(BaseModel):
    spec_version: int = 1

    entity_table: str
    entity_key: List[str]

    attributes: List[AttributeSpec] = []
    measures: List[MeasureSpec] = []
    filters: List[FilterSpec] = []
    having: List[HavingSpec] = []

    # Temporary compatibility field.
    # Final architecture will use join_paths as the execution contract.
    joins: List[JoinSpec] = []

    join_paths: List[JoinPath] = []

    # Ordering. order_by names an OUTPUT column (the entity key, an attribute
    # alias or a measure name), not a source column - the ORDER BY runs on the
    # outer SELECT where only output names exist.
    order_by: Optional[str] = None
    order_direction: str = "desc"

    # Optional ranked row cap, used for explicit requests such as "top 10".
    limit: Optional[int] = None

    target_column: Optional[str] = None
    target_type: Optional[str] = None

    db_type: Optional[str] = None
    schema_name: Optional[str] = None
    source_kind: str = "database"

    class Config:
        extra = "forbid"

    @root_validator(pre=True)
    def normalize_legacy_shapes(cls, values):
        if not isinstance(values, dict):
            return values

        # Old code supplied entity_key as a string.
        entity_key = values.get("entity_key")
        if isinstance(entity_key, str):
            values["entity_key"] = [entity_key]

        # A model asked to order by something writes SQL: "TotalSales DESC".
        # order_by names an output COLUMN, so the direction is split off here
        # rather than rejected. Same input normalisation as the legacy shapes
        # above - it invents nothing the spec did not say.
        order_by = values.get("order_by")
        if isinstance(order_by, str):
            parts = order_by.strip().split()
            if len(parts) == 2 and parts[1].lower() in ("asc", "desc"):
                values["order_by"] = parts[0]
                if not values.get("order_direction"):
                    values["order_direction"] = parts[1].lower()

        # Old OpenAI output supplied attributes as strings.
        raw_attributes = values.get("attributes", [])
        entity_table = values.get("entity_table")

        if raw_attributes and all(isinstance(item, str) for item in raw_attributes):
            values["attributes"] = [
                {
                    "source_table": entity_table,
                    "source_column": item,
                    "name": item,
                }
                for item in raw_attributes
            ]

        return values


class ValidationIssue(BaseModel):
    """One problem found in a spec. Never raised - always returned in a list."""

    severity: str  # "error" | "warning"
    code: str
    message: str
    field: Optional[str] = None
    suggestion: Optional[str] = None

    class Config:
        extra = "forbid"

    @property
    def is_error(self) -> bool:
        return self.severity == "error"


class SubqueryPlan(BaseModel):
    """One CTE emitted by the compiler."""

    name: str
    sql: str
    source_table: str
    measure_names: List[str] = []
    level: int = 1

    class Config:
        extra = "forbid"


class ReconQuery(BaseModel):
    """A control query the reconciler runs. Emitted by the compiler only."""

    kind: str  # "measure_total" | "entity_count"
    measure_name: Optional[str] = None
    sql: str
    params: dict = {}

    class Config:
        extra = "forbid"


class MeasureLineage(BaseModel):
    measure_name: str
    source: str
    aggregation: str
    join_path: List[str] = []
    path_verified: bool = False
    cardinality_per_hop: List[str] = []
    zero_when_absent: bool = False
    nested_aggregation: bool = False
    explanation: str = ""

    class Config:
        extra = "forbid"


class CompiledPlan(BaseModel):
    sql: str
    params: dict = {}
    output_columns: List[str] = []
    declared_grain: List[str] = []
    tables_referenced: List[str] = []
    subqueries: List[SubqueryPlan] = []
    reconciliation: List[ReconQuery] = []
    preview_reconciliation: Optional[ReconQuery] = None
    lineage: List[MeasureLineage] = []
    spec_version: int = 1

    class Config:
        extra = "forbid"


# Compatibility aliases for the existing code.
Measure = MeasureSpec
Filter = FilterSpec
