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

# The compiler owns the operator vocabulary because it is the module that
# turns an operator into SQL. Importing it here keeps one source of truth;
# duplicating the list is how a validator and a compiler drift apart. The
# dependency is acyclic - dataset_compiler imports spec_models only.
from etl_copilot.dataset_compiler import (
    _OPERATOR_ALIASES as _FILTER_OPERATOR_ALIASES,
)
from etl_copilot.dataset_compiler import _OPERATORS as _FILTER_OPERATORS
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

    # The compiler uses entity_key[0] for the grain, every join-back and the
    # row-count check. A second key column was silently discarded, which made
    # the output a different grain from the one the spec declared.
    if len(spec.entity_key) > 1:
        issues.append(
            _err(
                "composite_entity_key_unsupported",
                "entity_key has %d columns (%s). Only a single-column entity "
                "key is supported." % (len(spec.entity_key), ", ".join(spec.entity_key)),
                "entity_key",
                "Pick the one column that identifies a row of the entity table.",
            )
        )

    if spec.limit is not None and spec.limit <= 0:
        issues.append(
            _err(
                "invalid_row_limit",
                "limit must be a positive integer.",
                "limit",
            )
        )

    if spec.limit is not None and not spec.order_by:
        issues.append(
            _err(
                "row_limit_requires_order",
                "A row limit requires order_by so the selected rows are deterministic.",
                "order_by",
                "Set order_by to the requested ranking column.",
            )
        )

    if spec.order_direction and str(spec.order_direction).strip().lower() not in ("asc", "desc"):
        issues.append(
            _err(
                "invalid_order_direction",
                "order_direction must be 'asc' or 'desc', not '%s'."
                % spec.order_direction,
                "order_direction",
            )
        )

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

    if spec.order_by:
        output_names = {str(key).lower() for key in spec.entity_key}
        output_names |= {
            str(a.name or a.source_column).lower() for a in spec.attributes
        }
        output_names |= {str(m.name).lower() for m in spec.measures}

        if str(spec.order_by).lower() not in output_names:
            issues.append(
                _err(
                    "unknown_order_column",
                    "order_by '%s' is not an output column. The ORDER BY runs "
                    "on the outer SELECT, where only output names exist."
                    % spec.order_by,
                    "order_by",
                    "Use one of: %s." % ", ".join(sorted(output_names)),
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

        if measure.aggregation is AggFunction.MEDIAN:
            issues.append(
                _err(
                    "unsupported_aggregation",
                    "MEDIAN is not supported by the compiler (measure '%s')."
                    % measure.name,
                    field,
                    "Use AVG, or compute the median outside the dataset.",
                )
            )

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
                _err(
                    "attribute_off_entity_grain",
                    "Attribute '%s.%s' is not on the entity table, so it is "
                    "not functionally dependent on the entity key."
                    % (attribute.source_table, attribute.source_column),
                    field,
                    "Make it a measure (MIN/MAX) if it can vary per entity.",
                )
            )

    return issues


def _validate_target(spec: DatasetSpec, catalog: Optional[Catalog]) -> List[ValidationIssue]:
    """The AutoML handoff rejects a target that is missing or non-numeric.

    routers.datamodel._create_project_from_frame raises HTTP 400 for both,
    after the dataset has been built, executed, reconciled and written. The
    checks are cheap and belong before that work, not after it.
    """

    issues: List[ValidationIssue] = []

    if not spec.target_column:
        return issues

    target = str(spec.target_column)

    outputs = {str(key): "key" for key in spec.entity_key}
    for attribute in spec.attributes:
        outputs[str(attribute.name or attribute.source_column)] = "attribute"
    for measure in spec.measures:
        outputs[str(measure.name)] = "measure"

    if target not in outputs:
        issues.append(
            _err(
                "unknown_target_column",
                "target_column '%s' is not one of the dataset's columns."
                % target,
                "target_column",
                "Use one of: %s." % ", ".join(sorted(outputs)),
            )
        )
        return issues

    # A measure is numeric by construction. An attribute or the entity key
    # carries the source column's type.
    if outputs[target] == "measure" or catalog is None:
        return issues

    source_table = spec.entity_table
    source_column = target

    for attribute in spec.attributes:
        if str(attribute.name or attribute.source_column) == target:
            source_table = attribute.source_table
            source_column = attribute.source_column
            break

    column = _column(catalog, source_table, source_column)

    if column is not None and not _is_numeric(column.type):
        issues.append(
            _warn(
                "non_numeric_target",
                "target_column '%s' is %s. AutoML currently accepts only a "
                "numeric target, so the handoff will refuse this dataset."
                % (target, column.type),
                "target_column",
                "Pick a numeric column or measure as the target.",
            )
        )

    return issues


def _validate_filters(spec: DatasetSpec, catalog: Optional[Catalog]) -> List[ValidationIssue]:
    """Filters were the one part of a spec nothing checked.

    An unknown filter column used to travel all the way into the generated
    SQL and fail at the database, and an unknown operator used to raise
    SpecCompileError at compile time. Neither reaches the repair round,
    because the repair round only ever sees ValidationIssue objects.
    """

    issues: List[ValidationIssue] = []

    def check(filters, owning_table, field):
        for index, spec_filter in enumerate(filters or []):
            here = "%s[%d]" % (field, index)
            operator = str(spec_filter.operator or "").strip().lower()

            if (operator not in _FILTER_OPERATORS
                    and operator not in _FILTER_OPERATOR_ALIASES):
                issues.append(
                    _err(
                        "unknown_filter_operator",
                        "Filter operator '%s' is not supported."
                        % spec_filter.operator,
                        here,
                        "Use one of: %s." % ", ".join(sorted(_FILTER_OPERATORS)),
                    )
                )

            canonical = _FILTER_OPERATOR_ALIASES.get(operator, operator)

            if (canonical not in ("is_null", "is_not_null")
                    and spec_filter.value is None):
                issues.append(
                    _err(
                        "missing_filter_value",
                        "Filter on '%s' uses %s but has no value."
                        % (spec_filter.column, canonical),
                        here,
                    )
                )

            if (isinstance(spec_filter.value, str)
                    and spec_filter.value.lstrip().upper().startswith(("SELECT ", "WITH "))):
                issues.append(
                    _err(
                        "sql_filter_value",
                        "Filter values must be scalar literals, not SQL statements.",
                        here,
                        "Use a row-level scalar filter or an aggregate HAVING condition.",
                    )
                )

            table = spec_filter.source_table or owning_table

            if catalog is None or not table:
                continue

            if _table(catalog, table) is None:
                issues.append(
                    _err(
                        "unknown_table",
                        "Filter sources unknown table '%s'." % table,
                        here,
                    )
                )
                continue

            if _column(catalog, table, spec_filter.column) is None:
                issues.append(
                    _err(
                        "unknown_column",
                        "Filter sources unknown column '%s.%s'."
                        % (table, spec_filter.column),
                        here,
                        "A hallucinated filter column reaches the database "
                        "if this is not checked.",
                    )
                )

    check(spec.filters, spec.entity_table, "filters")

    for index, measure in enumerate(spec.measures):
        check(measure.filters, measure.source_table, "measures[%d].filters" % index)

    return issues


def _validate_having(spec: DatasetSpec) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    measure_names = {measure.name for measure in spec.measures}

    for index, condition in enumerate(spec.having or []):
        field = "having[%d]" % index
        if condition.measure_name not in measure_names:
            issues.append(
                _err(
                    "unknown_having_measure",
                    "HAVING references unknown measure '%s'."
                    % condition.measure_name,
                    field,
                )
            )

        operator = str(condition.operator or "").strip().lower()
        if (operator not in _FILTER_OPERATORS
                and operator not in _FILTER_OPERATOR_ALIASES):
            issues.append(
                _err(
                    "unknown_having_operator",
                    "HAVING operator '%s' is not supported." % condition.operator,
                    field,
                )
            )
        elif _FILTER_OPERATOR_ALIASES.get(operator, operator) in (
            "is_null", "is_not_null"):
            issues.append(
                _err(
                    "invalid_having_operator",
                    "HAVING requires a comparison operator and value.",
                    field,
                )
            )

        if isinstance(condition.value, (dict, list)):
            issues.append(
                _err(
                    "invalid_having_value",
                    "HAVING value must be a scalar, not SQL or a structured value.",
                    field,
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
                # NOT an error: dataset_compiler routes exactly this case to
                # _multi_hop_single_level_cte, which aggregates once over the
                # joined path. That is correct because the CTE contains only
                # this path's tables, so no other measure can be inflated.
                # Verified numerically at 3 hops against a ground-truth AVG:
                # 10 of 10 entity values matched exactly. Grading it an error
                # blocked a dataset the compiler builds correctly.
                issues.append(
                    _warn(
                        "non_decomposable_multi_hop",
                        "%s on '%s' crosses %d hops, so it is aggregated once "
                        "over the joined path rather than in two levels."
                        % (measure.aggregation.value, measure.name, len(path.hops)),
                        field,
                        "Correct for this function; the value is computed over "
                        "every related row exactly once.",
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
    issues.extend(_validate_filters(spec, catalog))
    issues.extend(_validate_having(spec))
    issues.extend(_validate_target(spec, catalog))
    issues.extend(_validate_join_paths(spec, join_paths))
    issues.extend(_validate_grain(spec, join_paths))
    return issues


def errors_only(issues: List[ValidationIssue]) -> List[ValidationIssue]:
    return [issue for issue in issues if issue.is_error]
