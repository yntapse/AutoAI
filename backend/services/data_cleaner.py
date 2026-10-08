"""Deterministic, in-memory cleaning operations for ETL Copilot."""

import math
from typing import Any, Dict, List, Mapping, Tuple

import pandas as pd

_CONFIG_KEYS = {
    "remove_null_rows": {"scope", "column_name"},
    "replace_null_values": {"column_name", "strategy", "custom_value"},
    "remove_duplicates": {"scope", "columns"},
    "convert_data_type": {"column_name", "target_type"},
    "handle_outliers": {"column_name", "strategy", "iqr_factor"},
}
_STEP_KEYS = {
    "id",
    "step_name",
    "name",
    "step_type",
    "rule_type",
    "configuration",
    "rule_config",
    "enabled",
    "step_order",
    "created_by",
    "created_at",
    "updated_at",
}


STEP_TYPES = {
    "remove_null_rows",
    "replace_null_values",
    "remove_duplicates",
    "convert_data_type",
    "handle_outliers",
}


class CleaningValidationError(ValueError):
    """A cleaning step is malformed or cannot safely run on this dataset."""


def validate_cleaning_steps(steps: Any) -> List[Dict[str, Any]]:
    if not isinstance(steps, list):
        raise CleaningValidationError("cleaning_steps must be a list.")
    normalized = []
    for index, raw_step in enumerate(steps):
        if not isinstance(raw_step, Mapping):
            raise CleaningValidationError("Step %s must be a JSON object." % (index + 1))
        step = dict(raw_step)
        extra = set(step) - _STEP_KEYS
        if extra:
            raise CleaningValidationError(
                "Step %s has unsupported field(s): %s."
                % (index + 1, ", ".join(sorted(map(str, extra))))
            )
        step_type = step.get("step_type", step.get("rule_type"))
        if not isinstance(step_type, str) or step_type not in STEP_TYPES:
            raise CleaningValidationError(
                "Step %s has an unsupported step_type." % (index + 1)
            )
        config = step.get("configuration", step.get("rule_config", {}))
        if not isinstance(config, Mapping):
            raise CleaningValidationError(
                "Step %s configuration must be a JSON object." % (index + 1)
            )
        _validate_step_config(step_type, config, index + 1)
        enabled = step.get("enabled", True)
        if not isinstance(enabled, bool):
            raise CleaningValidationError("Step %s enabled must be boolean." % (index + 1))
        name = step.get("step_name", step.get("name", step_type.replace("_", " ").title()))
        if not isinstance(name, str) or not name.strip():
            raise CleaningValidationError("Step %s step_name must not be blank." % (index + 1))
        normalized.append(
            {
                "step_name": name.strip(),
                "step_type": step_type,
                "configuration": dict(config),
                "enabled": enabled,
                "step_order": index + 1,
                "created_at": step.get("created_at"),
            }
        )
    return normalized


def _validate_step_config(
    step_type: str,
    config: Mapping[str, Any],
    number: int,
) -> None:
    extra = set(config) - _CONFIG_KEYS[step_type]
    if extra:
        raise CleaningValidationError(
            "Step %s has unsupported configuration field(s): %s."
            % (number, ", ".join(sorted(map(str, extra))))
        )
    if "created_by" in config or "created_at" in config or "step_order" in config:
        raise CleaningValidationError(
            "Step %s metadata belongs on the step object, not configuration." % number
        )
    if step_type == "remove_null_rows":
        scope = config.get("scope", "column")
        if not isinstance(scope, str) or scope not in {"column", "all_columns"}:
            raise CleaningValidationError(
                "Step %s scope must be 'column' or 'all_columns'." % number
            )
        if scope == "column" and (
            not isinstance(config.get("column_name"), str)
            or not config["column_name"].strip()
        ):
            raise CleaningValidationError(
                "Step %s requires a column_name when scope is 'column'." % number
            )
        if scope == "all_columns" and "column_name" in config:
            raise CleaningValidationError(
                "Step %s must not specify column_name with scope 'all_columns'."
                % number
            )
    elif step_type == "replace_null_values":
        _validate_column_name(config, number)
        strategy = config.get("strategy")
        if not isinstance(strategy, str) or strategy not in {
            "zero",
            "mean",
            "median",
            "mode",
            "custom",
        }:
            raise CleaningValidationError(
                "Step %s strategy must be zero, mean, median, mode, or custom."
                % number
            )
        if config.get("strategy") == "custom":
            value = config.get("custom_value")
            if not _json_scalar(value):
                raise CleaningValidationError(
                    "Step %s custom_value must be a JSON scalar." % number
                )
        elif "custom_value" in config:
            raise CleaningValidationError(
                "Step %s custom_value is only valid with custom strategy." % number
            )
    elif step_type == "remove_duplicates":
        scope = config.get("scope", "entire_row")
        if not isinstance(scope, str) or scope not in {"entire_row", "selected_columns"}:
            raise CleaningValidationError(
                "Step %s scope must be 'entire_row' or 'selected_columns'." % number
            )
        if scope == "selected_columns":
            columns = config.get("columns")
            if (
                not isinstance(columns, list)
                or not columns
                or any(not isinstance(column, str) or not column.strip() for column in columns)
                or len(columns) != len(set(columns))
            ):
                raise CleaningValidationError(
                    "Step %s selected_columns requires unique non-empty column names."
                    % number
                )
        elif "columns" in config:
            raise CleaningValidationError(
                "Step %s columns is only valid with scope 'selected_columns'." % number
            )
    elif step_type == "convert_data_type":
        _validate_column_name(config, number)
        target_type = config.get("target_type")
        if not isinstance(target_type, str) or target_type not in {
            "string",
            "integer",
            "float",
            "boolean",
            "date",
        }:
            raise CleaningValidationError(
                "Step %s target_type must be string, integer, float, boolean, or date."
                % number
            )
    elif step_type == "handle_outliers":
        _validate_column_name(config, number)
        strategy = config.get("strategy")
        if not isinstance(strategy, str) or strategy not in {"remove", "cap", "median"}:
            raise CleaningValidationError(
                "Step %s strategy must be remove, cap, or median." % number
            )
        factor = config.get("iqr_factor", 1.5)
        if (
            isinstance(factor, bool)
            or not isinstance(factor, (int, float))
            or not math.isfinite(factor)
            or factor <= 0
        ):
            raise CleaningValidationError(
                "Step %s iqr_factor must be a positive finite number." % number
            )


def _validate_column_name(config: Mapping[str, Any], number: int) -> None:
    column = config.get("column_name")
    if not isinstance(column, str) or not column.strip():
        raise CleaningValidationError(
            "Step %s requires a non-empty column_name." % number
        )


def _json_scalar(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return value is not None
    return isinstance(value, float) and pd.notna(value) and value not in {
        float("inf"),
        float("-inf"),
    }


def _required_column(frame: pd.DataFrame, config: Mapping[str, Any], step_number: int) -> str:
    column = config.get("column_name")
    if not isinstance(column, str) or not column.strip():
        raise CleaningValidationError(
            "Step %s requires a non-empty column_name." % step_number
        )
    if column not in frame.columns:
        raise CleaningValidationError(
            "Step %s column '%s' does not exist in the generated dataset."
            % (step_number, column)
        )
    return column


def _apply_step(frame: pd.DataFrame, step: Mapping[str, Any], number: int) -> pd.DataFrame:
    step_type = step["step_type"]
    config = step["configuration"]

    if step_type == "remove_null_rows":
        scope = config.get("scope", "column")
        if scope == "all_columns":
            return frame.dropna(axis=0, how="any")
        if scope != "column":
            raise CleaningValidationError(
                "Step %s scope must be 'column' or 'all_columns'." % number
            )
        column = _required_column(frame, config, number)
        return frame.loc[frame[column].notna()]

    if step_type == "replace_null_values":
        column = _required_column(frame, config, number)
        strategy = config.get("strategy")
        series = frame[column]
        non_null = series.dropna()
        if strategy == "zero":
            replacement = 0
        elif strategy == "mean":
            replacement = pd.to_numeric(non_null, errors="raise").mean()
        elif strategy == "median":
            replacement = pd.to_numeric(non_null, errors="raise").median()
        elif strategy == "mode":
            modes = non_null.mode(dropna=True)
            if modes.empty:
                raise CleaningValidationError(
                    "Step %s cannot calculate a mode for an empty column." % number
                )
            replacement = modes.iloc[0]
        elif strategy == "custom":
            if "custom_value" not in config:
                raise CleaningValidationError(
                    "Step %s custom strategy requires custom_value." % number
                )
            replacement = config["custom_value"]
        else:
            raise CleaningValidationError(
                "Step %s strategy must be zero, mean, median, mode, or custom."
                % number
            )
        if pd.isna(replacement):
            raise CleaningValidationError(
                "Step %s could not calculate a replacement value." % number
            )
        result = frame.copy()
        result[column] = result[column].fillna(replacement)
        return result

    if step_type == "remove_duplicates":
        scope = config.get("scope", "entire_row")
        if scope == "entire_row":
            subset = None
        elif scope == "selected_columns":
            subset = config.get("columns")
            if not isinstance(subset, list) or not subset:
                raise CleaningValidationError(
                    "Step %s selected_columns requires a non-empty columns list."
                    % number
                )
            missing = [column for column in subset if column not in frame.columns]
            if missing:
                raise CleaningValidationError(
                    "Step %s columns not found: %s." % (number, ", ".join(map(str, missing)))
                )
        else:
            raise CleaningValidationError(
                "Step %s scope must be 'entire_row' or 'selected_columns'." % number
            )
        return frame.drop_duplicates(subset=subset, keep="first")

    if step_type == "convert_data_type":
        column = _required_column(frame, config, number)
        target_type = config.get("target_type")
        result = frame.copy()
        try:
            if target_type == "string":
                result[column] = result[column].astype("string")
            elif target_type == "integer":
                converted = pd.to_numeric(result[column], errors="raise")
                if converted.dropna().mod(1).ne(0).any():
                    raise ValueError("values include a fractional component")
                result[column] = converted.astype("Int64")
            elif target_type == "float":
                result[column] = pd.to_numeric(result[column], errors="raise").astype(float)
            elif target_type == "boolean":
                mapping = {
                    True: True,
                    False: False,
                    1: True,
                    0: False,
                    "true": True,
                    "false": False,
                    "1": True,
                    "0": False,
                    "yes": True,
                    "no": False,
                }
                normalized = result[column].map(
                    lambda value: value.lower().strip()
                    if isinstance(value, str)
                    else value
                )
                invalid = normalized.notna() & ~normalized.isin(mapping)
                if invalid.any():
                    raise ValueError("values cannot be interpreted as boolean")
                result[column] = normalized.map(mapping).astype("boolean")
            elif target_type == "date":
                result[column] = pd.to_datetime(result[column], errors="raise").dt.date
            else:
                raise CleaningValidationError(
                    "Step %s target_type must be string, integer, float, boolean, or date."
                    % number
                )
        except (TypeError, ValueError, OverflowError) as exc:
            raise CleaningValidationError(
                "Step %s could not convert column '%s' to %s: %s"
                % (number, column, target_type, exc)
            ) from exc
        return result

    if step_type == "handle_outliers":
        column = _required_column(frame, config, number)
        strategy = config.get("strategy")
        factor = config.get("iqr_factor", 1.5)
        if (
            isinstance(factor, bool)
            or not isinstance(factor, (int, float))
            or not pd.notna(factor)
            or factor <= 0
        ):
            raise CleaningValidationError(
                "Step %s iqr_factor must be a positive number." % number
            )
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.notna().sum() != frame[column].notna().sum():
            raise CleaningValidationError(
                "Step %s column '%s' contains non-numeric values." % (number, column)
            )
        if numeric.notna().sum() == 0:
            raise CleaningValidationError(
                "Step %s has no numeric values to analyze." % number
            )
        q1 = numeric.quantile(0.25)
        q3 = numeric.quantile(0.75)
        iqr = q3 - q1
        lower, upper = q1 - factor * iqr, q3 + factor * iqr
        outlier_mask = numeric.notna() & ((numeric < lower) | (numeric > upper))
        if strategy == "remove":
            return frame.loc[~outlier_mask]
        result = frame.copy()
        if strategy == "cap":
            result.loc[numeric < lower, column] = lower
            result.loc[numeric > upper, column] = upper
        elif strategy == "median":
            result.loc[outlier_mask, column] = numeric.median()
        else:
            raise CleaningValidationError(
                "Step %s strategy must be remove, cap, or median." % number
            )
        return result

    raise CleaningValidationError("Step %s has an unsupported step_type." % number)


def apply_cleaning_steps(
    frame: pd.DataFrame,
    steps: Any,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Return a cleaned copy and a serializable preview/execution summary."""
    if not isinstance(frame, pd.DataFrame):
        raise CleaningValidationError("Cleaning input must be a DataFrame.")
    normalized = validate_cleaning_steps(steps)
    original_rows = len(frame)
    original_columns = list(frame.columns)
    result = frame.copy(deep=True)
    applied = []
    changed_columns = set()

    for index, step in enumerate(normalized, 1):
        if not step["enabled"]:
            applied.append(
                {
                    "step_number": index,
                    "step_name": step["step_name"],
                    "step_type": step["step_type"],
                    "status": "disabled",
                }
            )
            continue
        before = result
        try:
            result = _apply_step(result, step, index)
        except CleaningValidationError:
            raise
        except Exception as exc:
            raise CleaningValidationError(
                "Step %s (%s) failed: %s"
                % (index, step["step_name"], exc)
            ) from exc
        changed_columns.update(
            column
            for column in set(before.columns) & set(result.columns)
            if (
                before[column].dtype != result[column].dtype
                or not before.loc[before.index.intersection(result.index), column]
                .reset_index(drop=True)
                .equals(
                    result.loc[before.index.intersection(result.index), column]
                    .reset_index(drop=True)
                )
            )
        )
        applied.append(
            {
                "step_number": index,
                "step_name": step["step_name"],
                "step_type": step["step_type"],
                "status": "applied",
            }
        )

    return result, {
        "original_row_count": original_rows,
        "new_row_count": len(result),
        "rows_removed": original_rows - len(result),
        "columns_changed": [
            str(column) for column in original_columns if column in changed_columns
        ],
        "applied_steps": applied,
    }
