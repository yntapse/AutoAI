"""The single spec validator. Returns issues; never raises on user input.

Replaces the old schema_validator.py (dict-based) and the original
spec_validator.py (raise-on-first-error). Six validation families:

  structural  - the spec is internally coherent
  catalog     - every table and column actually exists
  type        - the aggregation suits the column type
  attribute   - attributes exist and sit at entity grain
  join path   - a path exists, is unambiguous and is verified
  grain       - fan-out is handled; distinct keys are sane

Pure module: no database connection, no pandas, no LLM.
"""

from typing import List, Optional

from etl_copilot.spec_models import (
    DECOMPOSABLE,
    FANOUT_SAFE,
    AggFunction,
    Catalog,
    DatasetSpec,
    JoinPath,
    ValidationIssue,
)

_NUMERIC_HINTS = (
    "INT",
    "DECIMAL",
    "NUMERIC",
    "FLOAT",
    "REAL",
    "DOUBLE",
    "MONEY",
    "BIGINT",
    "SMALLINT",
    "TINYINT",
)

_NUMERIC_ONLY_AGGREGATIONS = {
    AggFunction.SUM,
    AggFunction.AVG,
    AggFunction.MEDIAN,
    AggFunction.STDDEV,
}


def _is_numeric(column_type: str) -> bool:
    upper = (column_type or "").upper()
    return any(hint in upper for hint in _NUMERIC_HINTS)


def _table(catalog: Optional[Catalog], name: str):
    if catalog is None:
        return None
    for table in catalog.tables:
        if table.name == name:
            return table
    return None


def _column(catalog: Optional[Catalog], table_name: str, column_name: str):
    table = _table(catalog, table_name)
    if table is None:
        return None
    for column in table.columns:
        if column.name == column_name:
            return column
    return None


def _err(code, message, field=None, suggestion=None) -> ValidationIssue:
    return ValidationIssue(
        severity="error",
        code=code,
        message=message,
        field=field,
        suggestion=suggestion,
    )


def _warn(code, message, field=None, suggestion=None) -> ValidationIssue:
    return ValidationIssue(
        severity="warning",
        code=code,
        message=message,
        field=field,
        suggestion=suggestion,
    )


def _validate_structural(spec: DatasetSpec) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []

    if not spec.entity_table:
        issues.append(_err("missing_entity_table", "An entity table is required.", "entity_table"))

    if not spec.entity_key:
        issues.append(_err("missing_entity_key", "An entity key is required.", "entity_key"))

    if not spec.measures and not spec.attributes:
        issues.append(
            _err(
                "empty_dataset",
                "The dataset must contain at least one attribute or measure.",
                "measures",
            )
        )

    seen = set()
    entity_key_names = {str(key).lower() for key in spec.entity_key}

    for index, measure in enumerate(spec.measures):
        field = "measures[%d]" % index

        if measure.name.lower() in seen:
            issues.append(
                _err("duplicate_measure_name", "Duplicate measure name '%s'." % measure.name, field)
            )
        seen.add(measure.name.lower())

        if measure.name.lower() in entity_key_names:
            issues.append(
                _err(
                    "measure_name_collides_with_key",
                    "Measure '%s' collides with the entity key." % measure.name,
                    field,
                    "Rename the measure.",
                )
            )

        if not measure.source_table:
            issues.append(_err("missing_source_table", "Measure '%s' has no source_table." % measure.name, field))

        if measure.source_column is None and measure.aggregation is not AggFunction.COUNT:
            issues.append(
                _err(
                    "missing_source_column",
                    "Measure '%s' needs a source_column for %s."
                    % (measure.name, measure.aggregation.value),
                    field,
                    "Only COUNT may omit a column (COUNT(*)).",
                )
            )

    for index, attribute in enumerate(spec.attributes):
        if attribute.name and attribute.name.lower() in seen:
            issues.append(
                _err(
                    "attribute_name_collides",
                    "Attribute '%s' collides with a measure name." % attribute.name,
                    "attributes[%d]" % index,
                )
            )

    return issues


def _validate_catalog_and_types(spec: DatasetSpec, catalog: Optional[Catalog]) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []

    if catalog is None:
        return issues

    if spec.entity_table and _table(catalog, spec.entity_table) is None:
        issues.append(
            _err(
                "unknown_table",
                "Entity table '%s' is not in the catalog." % spec.entity_table,
                "entity_table",
            )
        )

    for key in spec.entity_key:
        if spec.entity_table and _column(catalog, spec.entity_table, key) is None:
            issues.append(
                _err(
                    "unknown_column",
                    "Entity key '%s' does not exist in '%s'." % (key, spec.entity_table),
                    "entity_key",
                )
            )

    for index, measure in enumerate(spec.measures):
        field = "measures[%d]" % index

        if _table(catalog, measure.source_table) is None:
            issues.append(
                _err(
                    "unknown_table",
                    "Measure '%s' sources unknown table '%s'." % (measure.name, measure.source_table),
                    field,
                )
            )
            continue

        if measure.source_column is not None:
            column = _column(catalog, measure.source_table, measure.source_column)

            if column is None:
                issues.append(
                    _err(
                        "unknown_column",
                        "Measure '%s' sources unknown column '%s.%s'."
                        % (measure.name, measure.source_table, measure.source_column),
                        field,
                        "Use a column that exists in the catalog.",
                    )
                )
            elif measure.aggregation in _NUMERIC_ONLY_AGGREGATIONS and not _is_numeric(column.type):
                issues.append(
                    _err(
                        "aggregation_type_mismatch",
                        "%s cannot be applied to '%s.%s' (type %s)."
                        % (
                            measure.aggregation.value,
                            measure.source_table,
                            measure.source_column,
                            column.type,
                        ),
                        field,
                        "Use COUNT or COUNT_DISTINCT for a non-numeric column.",
                    )
                )

        for key in measure.distinct_key:
            if _column(catalog, measure.source_table, key) is None:
                issues.append(
                    _err(
                        "unknown_column",
                        "distinct_key '%s' does not exist in '%s'." % (key, measure.source_table),
                        field,
                    )
                )

    # ---- attributes (the hole the old validator had) ----
    for index, attribute in enumerate(spec.attributes):
        field = "attributes[%d]" % index

        if _table(catalog, attribute.source_table) is None:
            issues.append(
                _err(
                    "unknown_table",
                    "Attribute sources unknown table '%s'." % attribute.source_table,
                    field,
                )
            )
            continue

        if _column(catalog, attribute.source_table, attribute.source_column) is None:
            issues.append(
                _err(
                    "unknown_column",
                    "Attribute sources unknown column '%s.%s'."
                    % (attribute.source_table, attribute.source_column),
                    field,
                    "A hallucinated column name reaches SQL if this is not checked.",
                )
            )

        if spec.entity_table and attribute.source_table != spec.entity_table:
            issues.append(
                _warn(
                    "attribute_off_entity_grain",
                    "Attribute '%s.%s' is not on the entity table; it may not be "
                    "functionally dependent on the entity key."
                    % (attribute.source_table, attribute.source_column),
                    field,
                    "Make it a measure (MIN/MAX) if it can vary per entity.",
                )
            )

    return issues


def _validate_join_paths(spec: DatasetSpec, paths: Optional[List[JoinPath]]) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []

    if paths is None:
        return issues

    by_source = {path.source_table: path for path in paths}

    needed = {measure.source_table for measure in spec.measures}
    needed |= {attribute.source_table for attribute in spec.attributes}

    for source_table in sorted(needed):
        if source_table == spec.entity_table:
            continue

        path = by_source.get(source_table)

        if path is None:
            issues.append(
                _err(
                    "missing_join_path",
                    "No join path was resolved for source table '%s'." % source_table,
                    "join_paths",
                    "Resolve the path before compiling; a missing path must never "
                    "silently drop the measure.",
                )
            )
            continue

        if not path.verified:
            issues.append(
                _err(
                    "unverified_join_path",
                    "The join path to '%s' has not been verified." % source_table,
                    "join_paths",
                    "Only join_verifier may mark a path verified.",
                )
            )

        for hop in path.hops:
            if hop.match_rate is not None and hop.match_rate < 5.0:
                issues.append(
                    _warn(
                        "sparse_join",
                        "%s -> %s matches only %.1f%% of rows."
                        % (hop.left_table, hop.right_table, hop.match_rate),
                        "join_paths",
                        "The dataset will be mostly null for this measure.",
                    )
                )

    return issues


def _validate_grain(spec: DatasetSpec, paths: Optional[List[JoinPath]]) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []

    entity_key_names = {str(key).lower() for key in spec.entity_key}
    by_source = {path.source_table: path for path in (paths or [])}

    for index, measure in enumerate(spec.measures):
        field = "measures[%d]" % index

        # Rule harvested from etl_agent._grain_guard: de-duplicating on the
        # output grain is data loss, not de-fanning.
        for key in measure.distinct_key:
            if str(key).lower() in entity_key_names:
                issues.append(
                    _err(
                        "distinct_key_is_entity_key",
                        "Measure '%s' uses the entity key '%s' as distinct_key. "
                        "That deletes real rows rather than de-fanning."
                        % (measure.name, key),
                        field,
                        "Use the source table's own primary key instead.",
                    )
                )

        path = by_source.get(measure.source_table)

        if path is None or not path.hops:
            continue

        fans_out = any(hop.row_multiplication for hop in path.hops)

        if not fans_out:
            continue

        if measure.aggregation in FANOUT_SAFE:
            continue

        if path.requires_nested_aggregation:
            if measure.aggregation not in DECOMPOSABLE:
                issues.append(
                    _err(
                        "non_decomposable_multi_hop",
                        "%s on '%s' crosses %d hops and cannot be decomposed into "
                        "two aggregation levels."
                        % (measure.aggregation.value, measure.name, len(path.hops)),
                        field,
                        "The compiler must aggregate it once over the joined path.",
                    )
                )
            continue

        if not measure.distinct_key:
            issues.append(
                _warn(
                    "fanout_without_distinct_key",
                    "Measure '%s' is sourced across a fan-out join with no "
                    "distinct_key." % measure.name,
                    field,
                    "Set distinct_key to the source table's primary key, or use "
                    "COUNT_DISTINCT.",
                )
            )

    return issues


def validate_spec(
    spec: DatasetSpec,
    catalog: Optional[Catalog] = None,
    join_paths: Optional[List[JoinPath]] = None,
) -> List[ValidationIssue]:
    """Return every issue found. Callers decide what to do with them."""

    issues: List[ValidationIssue] = []
    issues.extend(_validate_structural(spec))
    issues.extend(_validate_catalog_and_types(spec, catalog))
    issues.extend(_validate_join_paths(spec, join_paths))
    issues.extend(_validate_grain(spec, join_paths))
    return issues


def errors_only(issues: List[ValidationIssue]) -> List[ValidationIssue]:
    return [issue for issue in issues if issue.is_error]
