"""Deterministic quality-rule validation and source-profile evaluation."""

import math
from numbers import Real
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

SUPPORTED_RULE_TYPES = {
    "null_percentage",
    "uniqueness",
    "distinct_percentage",
    "allowed_values",
    "numeric_range",
    "data_type",
    "row_count",
}
SUPPORTED_SEVERITIES = {"error", "warning"}
SUPPORTED_DATA_TYPES = {"date", "datetime", "integer", "numeric", "string", "boolean"}

_REQUIRED_CONFIG_KEYS = {
    "null_percentage": {"table_name", "column_name", "max_percentage"},
    "uniqueness": {"table_name", "column_name"},
    "distinct_percentage": {"table_name", "column_name", "min_percentage"},
    "allowed_values": {"table_name", "column_name", "allowed_values"},
    "numeric_range": {"table_name", "column_name", "minimum", "maximum"},
    "data_type": {"table_name", "column_name", "expected_type"},
    "row_count": set(),
}
_OPTIONAL_CONFIG_KEYS = {
    "row_count": {"table_name", "minimum", "maximum"},
}


class QualityRuleValidationError(ValueError):
    """A rule definition is invalid or unsupported."""


class ProfileTargetNotFound(LookupError):
    """A rule targets a table or column absent from a source profile."""


def _is_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def validate_rule_definition(
    rule_type: str,
    severity: str,
    rule_config: Mapping[str, Any],
) -> None:
    """Validate a supported rule and its exact configuration shape."""
    if rule_type not in SUPPORTED_RULE_TYPES:
        raise QualityRuleValidationError("Unsupported rule_type.")
    if severity not in SUPPORTED_SEVERITIES:
        raise QualityRuleValidationError("severity must be 'error' or 'warning'.")
    if not isinstance(rule_config, Mapping):
        raise QualityRuleValidationError("rule_config must be a JSON object.")

    required = _REQUIRED_CONFIG_KEYS[rule_type]
    permitted = required | _OPTIONAL_CONFIG_KEYS.get(rule_type, set())
    missing = required - set(rule_config)
    extra = set(rule_config) - permitted
    if missing:
        raise QualityRuleValidationError(
            "Missing rule_config field(s): %s." % ", ".join(sorted(missing))
        )
    if extra:
        raise QualityRuleValidationError(
            "Unsupported rule_config field(s): %s." % ", ".join(sorted(extra))
        )

    if rule_type != "row_count":
        for key in ("table_name", "column_name"):
            if not isinstance(rule_config.get(key), str) or not rule_config[key].strip():
                raise QualityRuleValidationError("%s must be a non-empty string." % key)

    if rule_type == "null_percentage":
        _validate_percentage(rule_config["max_percentage"], "max_percentage")
    elif rule_type == "distinct_percentage":
        _validate_percentage(rule_config["min_percentage"], "min_percentage")
    elif rule_type == "allowed_values":
        values = rule_config["allowed_values"]
        if not isinstance(values, list) or not values:
            raise QualityRuleValidationError("allowed_values must be a non-empty list.")
        if any(value is None for value in values):
            raise QualityRuleValidationError(
                "allowed_values must not include null; use a null_percentage rule."
            )
        if any(not _is_json_scalar(value) for value in values):
            raise QualityRuleValidationError(
                "allowed_values must contain only JSON scalar values."
            )
        if len({_typed_value_key(value) for value in values}) != len(values):
            raise QualityRuleValidationError("allowed_values must not contain duplicates.")
    elif rule_type == "numeric_range":
        minimum = rule_config["minimum"]
        maximum = rule_config["maximum"]
        if not _is_number(minimum) or not _is_number(maximum):
            raise QualityRuleValidationError("minimum and maximum must be finite numbers.")
        if minimum > maximum:
            raise QualityRuleValidationError("minimum must be less than or equal to maximum.")
    elif rule_type == "data_type":
        expected_type = rule_config["expected_type"]
        if not isinstance(expected_type, str) or expected_type.lower() not in SUPPORTED_DATA_TYPES:
            raise QualityRuleValidationError(
                "expected_type must be one of: %s."
                % ", ".join(sorted(SUPPORTED_DATA_TYPES))
            )
    elif rule_type == "row_count":
        if "table_name" in rule_config and (
            not isinstance(rule_config["table_name"], str)
            or not rule_config["table_name"].strip()
        ):
            raise QualityRuleValidationError("table_name must be a non-empty string.")
        if "minimum" not in rule_config and "maximum" not in rule_config:
            raise QualityRuleValidationError(
                "row_count requires minimum, maximum, or both."
            )
        for key in ("minimum", "maximum"):
            if key in rule_config and (
                isinstance(rule_config[key], bool)
                or not isinstance(rule_config[key], int)
                or rule_config[key] < 0
            ):
                raise QualityRuleValidationError("%s must be a non-negative integer." % key)
        if (
            "minimum" in rule_config
            and "maximum" in rule_config
            and rule_config["minimum"] > rule_config["maximum"]
        ):
            raise QualityRuleValidationError(
                "minimum must be less than or equal to maximum."
            )


def _validate_percentage(value: Any, field_name: str) -> None:
    if not _is_number(value) or not 0 <= value <= 100:
        raise QualityRuleValidationError(
            "%s must be a finite number from 0 to 100." % field_name
        )


def _is_json_scalar(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _typed_value_key(value: Any) -> Tuple[type, Any]:
    # bool and int compare equal in Python but are distinct JSON value types.
    return type(value), value


def _table_profiles(profile_json: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    tables = profile_json.get("tables")
    if not isinstance(tables, list):
        raise QualityRuleValidationError("Snapshot profile_json has no tables list.")
    result = {}
    for table in tables:
        if isinstance(table, dict) and isinstance(table.get("table_name"), str):
            result[table["table_name"]] = table
    return result


def _target(
    rule_type: str,
    config: Mapping[str, Any],
    tables: Mapping[str, Dict[str, Any]],
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    table_name = config.get("table_name")
    if rule_type == "row_count" and table_name is None:
        if not tables:
            raise ProfileTargetNotFound("The source profile contains no tables.")
        return {}, None
    table = tables.get(table_name)
    if table is None:
        raise ProfileTargetNotFound("Table '%s' is missing from the snapshot." % table_name)
    if rule_type == "row_count":
        return table, None
    columns = table.get("columns")
    if not isinstance(columns, list):
        raise QualityRuleValidationError(
            "Snapshot profile for table '%s' has no columns list." % table_name
        )
    column_name = config.get("column_name")
    for column in columns:
        if isinstance(column, dict) and column.get("column_name") == column_name:
            return table, column
    raise ProfileTargetNotFound(
        "Column '%s' is missing from table '%s' in the snapshot."
        % (column_name, table_name)
    )


def _normalized_data_type(source_type: Any) -> Optional[str]:
    value = str(source_type or "").strip().upper()
    if not value:
        return None
    if "BOOL" in value or value == "BIT":
        return "boolean"
    if "TIMESTAMP" in value or "DATETIME" in value or value == "TIME":
        return "datetime"
    if value == "DATE" or value.startswith("DATE("):
        return "date"
    if any(token in value for token in ("INT", "SERIAL")):
        return "integer"
    if any(token in value for token in ("NUMERIC", "DECIMAL", "FLOAT", "REAL", "DOUBLE", "MONEY")):
        return "numeric"
    if any(token in value for token in ("CHAR", "TEXT", "STRING", "CLOB")):
        return "string"
    return None


def _status(condition: bool, severity: str) -> str:
    return "pass" if condition else ("fail" if severity == "error" else "warning")


def _evaluate_one(
    rule: Any,
    tables: Mapping[str, Dict[str, Any]],
) -> Dict[str, Any]:
    rule_type = rule.rule_type
    config = rule.rule_config
    validate_rule_definition(rule_type, rule.severity, config)
    table, column = _target(rule_type, config, tables)
    table_name = config.get("table_name")
    column_name = config.get("column_name")
    evidence: Dict[str, Any] = {}

    if rule_type == "row_count":
        if table_name is None:
            row_counts = {
                name: item.get("row_count")
                for name, item in tables.items()
            }
            if any(not isinstance(count, int) or count < 0 for count in row_counts.values()):
                return _result(
                    rule,
                    "warning",
                    None,
                    _expected_row_count(config),
                    {"row_counts_by_table": row_counts, "reason": "Incomplete row-count evidence."},
                )
            actual = sum(row_counts.values())
            evidence = {
                "scope": "sum_of_selected_tables",
                "row_counts_by_table": row_counts,
            }
        else:
            actual = table.get("row_count")
            evidence = {"table_name": table_name, "row_count": actual}
        if not isinstance(actual, int) or actual < 0:
            return _result(
                rule, "warning", actual, _expected_row_count(config),
                {**evidence, "reason": "Row-count evidence is missing or invalid."},
            )
        passed = (
            ("minimum" not in config or actual >= config["minimum"])
            and ("maximum" not in config or actual <= config["maximum"])
        )
        return _result(
            rule, _status(passed, rule.severity), actual,
            _expected_row_count(config), evidence,
        )

    assert column is not None
    base_evidence = {
        "table_name": table_name,
        "column_name": column_name,
        "row_count": table.get("row_count"),
    }

    if rule_type == "null_percentage":
        actual = column.get("null_percentage")
        expected = "<= %s%%" % config["max_percentage"]
        if not _is_number(actual):
            return _result(rule, "warning", actual, expected,
                           {**base_evidence, "reason": "Null percentage evidence is unavailable."})
        evidence = {
            **base_evidence,
            "null_count": column.get("null_count"),
            "null_percentage": actual,
        }
        return _result(
            rule, _status(actual <= config["max_percentage"], rule.severity),
            actual, expected, evidence,
        )

    if rule_type == "uniqueness":
        row_count = table.get("row_count")
        null_count = column.get("null_count")
        distinct_count = column.get("distinct_count")
        if not all(isinstance(value, int) and value >= 0 for value in (row_count, null_count, distinct_count)):
            return _result(
                rule, "warning", None, "all non-null values are unique",
                {**base_evidence, "reason": "Uniqueness evidence is incomplete."},
            )
        non_null_count = row_count - null_count
        if non_null_count < 0:
            return _result(rule, "warning", None, "all non-null values are unique",
                           {**base_evidence, "reason": "Null count exceeds row count."})
        passed = distinct_count == non_null_count
        evidence = {
            **base_evidence,
            "null_count": null_count,
            "non_null_count": non_null_count,
            "distinct_count": distinct_count,
            "nulls_excluded": True,
        }
        return _result(
            rule, _status(passed, rule.severity),
            {"distinct_count": distinct_count, "non_null_count": non_null_count},
            "distinct_count == non_null_count", evidence,
        )

    if rule_type == "distinct_percentage":
        actual = column.get("distinct_percentage")
        non_null_count = table.get("row_count")
        if isinstance(non_null_count, int) and isinstance(column.get("null_count"), int):
            non_null_count -= column["null_count"]
        expected = ">= %s%%" % config["min_percentage"]
        if not _is_number(actual) or not isinstance(non_null_count, int) or non_null_count <= 0:
            return _result(
                rule, "warning", actual, expected,
                {**base_evidence, "distinct_count": column.get("distinct_count"),
                 "reason": "Distinct percentage has no valid non-null denominator or is unavailable."},
            )
        evidence = {
            **base_evidence,
            "distinct_count": column.get("distinct_count"),
            "non_null_count": non_null_count,
            "distinct_percentage": actual,
        }
        return _result(
            rule, _status(actual >= config["min_percentage"], rule.severity),
            actual, expected, evidence,
        )

    if rule_type == "allowed_values":
        inventory = column.get("value_inventory")
        complete = column.get("inventory_complete") is True
        allowed = config["allowed_values"]
        found_disallowed = []
        if isinstance(inventory, list):
            found_disallowed = [
                item.get("value")
                for item in inventory
                if isinstance(item, dict)
                and not _contains_typed(allowed, item.get("value"))
            ]
        evidence = {
            **base_evidence,
            "inventory_complete": complete,
            "inventory_count": len(inventory) if isinstance(inventory, list) else None,
            "disallowed_values_found": found_disallowed,
        }
        if not complete or not isinstance(inventory, list):
            evidence["reason"] = "A complete value inventory is unavailable."
            return _result(
                rule, "warning", found_disallowed,
                {"allowed_values": allowed}, evidence,
            )
        return _result(
            rule,
            _status(not found_disallowed, rule.severity),
            found_disallowed,
            {"allowed_values": allowed},
            evidence,
        )

    if rule_type == "numeric_range":
        minimum = column.get("min")
        maximum = column.get("max")
        expected = "[%s, %s]" % (config["minimum"], config["maximum"])
        if not _is_number(minimum) or not _is_number(maximum):
            return _result(
                rule, "warning", {"min": minimum, "max": maximum}, expected,
                {**base_evidence, "reason": "Numeric range evidence is unavailable."},
            )
        passed = minimum >= config["minimum"] and maximum <= config["maximum"]
        evidence = {**base_evidence, "min": minimum, "max": maximum}
        return _result(
            rule, _status(passed, rule.severity),
            {"min": minimum, "max": maximum}, expected, evidence,
        )

    observed_type = _normalized_data_type(column.get("data_type"))
    expected_type = config["expected_type"].lower()
    evidence = {
        **base_evidence,
        "source_data_type": column.get("data_type"),
        "normalized_data_type": observed_type,
    }
    if observed_type is None:
        evidence["reason"] = "Source data type is not recognized."
        return _result(rule, "warning", column.get("data_type"), expected_type, evidence)
    return _result(
        rule, _status(observed_type == expected_type, rule.severity),
        observed_type, expected_type, evidence,
    )


def _contains_typed(values: Iterable[Any], value: Any) -> bool:
    return any(type(candidate) is type(value) and candidate == value for candidate in values)


def _expected_row_count(config: Mapping[str, Any]) -> str:
    if "minimum" in config and "maximum" in config:
        return "%s <= rows <= %s" % (config["minimum"], config["maximum"])
    if "minimum" in config:
        return ">= %s rows" % config["minimum"]
    return "<= %s rows" % config["maximum"]


def _result(
    rule: Any,
    status: str,
    actual_value: Any,
    expected_value: Any,
    evidence: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "rule_id": str(rule.id),
        "rule_name": rule.rule_name,
        "rule_type": rule.rule_type,
        "status": status,
        "actual_value": actual_value,
        "expected_value": expected_value,
        "evidence": evidence,
    }


def evaluate_rules(rules: Iterable[Any], profile_json: Mapping[str, Any]) -> Dict[str, Any]:
    """Evaluate enabled rule objects against a source profile JSON payload."""
    if not isinstance(profile_json, Mapping):
        raise QualityRuleValidationError("Snapshot profile_json must be an object.")
    tables = _table_profiles(profile_json)
    results = [
        _evaluate_one(rule, tables)
        for rule in rules
        if rule.enabled
    ]
    summary = {
        status: sum(1 for result in results if result["status"] == status)
        for status in ("pass", "warning", "fail")
    }
    overall_status = (
        "fail" if summary["fail"]
        else "warning" if summary["warning"] or not results
        else "pass"
    )
    return {
        "overall_status": overall_status,
        "summary": summary,
        "results": results,
    }
