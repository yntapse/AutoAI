"""
MCP (Model Context Protocol) Server for Data Transformation Tools.

Exposes structured, validated tools for dataset manipulation.
The LLM calls these tools via function-calling instead of generating raw code.
Each tool is safe, idempotent, and operates on a pandas DataFrame.
"""

import json
import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional, Callable


# ─── Tool Registry ────────────────────────────────────────────────────────────

TRANSFORM_TOOLS: Dict[str, Dict[str, Any]] = {}


def register_tool(name: str, description: str, parameters: Dict[str, Any]):
    """Decorator to register a transform tool with its schema."""
    def decorator(func: Callable):
        TRANSFORM_TOOLS[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "function": func,
        }
        return func
    return decorator


# ─── Tool Definitions ─────────────────────────────────────────────────────────

@register_tool(
    name="fill_nulls",
    description="Fill null/missing values in one or more columns using a specified strategy (mean, median, mode, constant, forward_fill, backward_fill).",
    parameters={
        "type": "object",
        "properties": {
            "columns": {"type": "array", "items": {"type": "string"}, "description": "Column names to fill nulls in. Use ['*'] for all columns."},
            "strategy": {"type": "string", "enum": ["mean", "median", "mode", "constant", "forward_fill", "backward_fill"], "description": "Fill strategy"},
            "fill_value": {"type": "string", "description": "Value to use when strategy is 'constant'", "default": "0"},
        },
        "required": ["columns", "strategy"],
    },
)
def fill_nulls(df: pd.DataFrame, columns: List[str], strategy: str, fill_value: str = "0") -> pd.DataFrame:
    target_cols = list(df.columns) if columns == ["*"] else [c for c in columns if c in df.columns]
    for col in target_cols:
        if df[col].isnull().sum() == 0:
            continue
        if strategy == "mean":
            if df[col].dtype in ["float64", "int64", "float32", "int32"]:
                df[col] = df[col].fillna(df[col].mean())
            else:
                df[col] = df[col].fillna(df[col].mode().iloc[0] if not df[col].mode().empty else "")
        elif strategy == "median":
            if df[col].dtype in ["float64", "int64", "float32", "int32"]:
                df[col] = df[col].fillna(df[col].median())
            else:
                df[col] = df[col].fillna(df[col].mode().iloc[0] if not df[col].mode().empty else "")
        elif strategy == "mode":
            mode_val = df[col].mode()
            df[col] = df[col].fillna(mode_val.iloc[0] if not mode_val.empty else "")
        elif strategy == "constant":
            df[col] = df[col].fillna(fill_value)
        elif strategy == "forward_fill":
            df[col] = df[col].ffill()
        elif strategy == "backward_fill":
            df[col] = df[col].bfill()
    return df


@register_tool(
    name="drop_columns",
    description="Remove one or more columns from the dataset.",
    parameters={
        "type": "object",
        "properties": {
            "columns": {"type": "array", "items": {"type": "string"}, "description": "Column names to drop"},
        },
        "required": ["columns"],
    },
)
def drop_columns(df: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
    existing = [c for c in columns if c in df.columns]
    return df.drop(columns=existing)


@register_tool(
    name="drop_rows_with_nulls",
    description="Remove rows that have null values in specified columns (or any column).",
    parameters={
        "type": "object",
        "properties": {
            "columns": {"type": "array", "items": {"type": "string"}, "description": "Columns to check for nulls. Use ['*'] for any column."},
            "how": {"type": "string", "enum": ["any", "all"], "description": "'any' drops rows with any null, 'all' drops only fully null rows", "default": "any"},
        },
        "required": ["columns"],
    },
)
def drop_rows_with_nulls(df: pd.DataFrame, columns: List[str], how: str = "any") -> pd.DataFrame:
    if columns == ["*"]:
        return df.dropna(how=how)
    existing = [c for c in columns if c in df.columns]
    if existing:
        return df.dropna(subset=existing, how=how)
    return df


@register_tool(
    name="drop_duplicates",
    description="Remove duplicate rows from the dataset.",
    parameters={
        "type": "object",
        "properties": {
            "columns": {"type": "array", "items": {"type": "string"}, "description": "Columns to consider for duplicates. Use ['*'] for all columns."},
            "keep": {"type": "string", "enum": ["first", "last", "none"], "description": "Which duplicate to keep", "default": "first"},
        },
        "required": [],
    },
)
def drop_duplicates(df: pd.DataFrame, columns: Optional[List[str]] = None, keep: str = "first") -> pd.DataFrame:
    subset = None if not columns or columns == ["*"] else [c for c in columns if c in df.columns]
    keep_val = False if keep == "none" else keep
    return df.drop_duplicates(subset=subset, keep=keep_val)


@register_tool(
    name="rename_columns",
    description="Rename one or more columns.",
    parameters={
        "type": "object",
        "properties": {
            "mapping": {"type": "object", "description": "Old name → new name mapping, e.g. {'old_col': 'new_col'}"},
        },
        "required": ["mapping"],
    },
)
def rename_columns(df: pd.DataFrame, mapping: Dict[str, str]) -> pd.DataFrame:
    valid_mapping = {k: v for k, v in mapping.items() if k in df.columns}
    return df.rename(columns=valid_mapping)


@register_tool(
    name="convert_dtype",
    description="Convert column(s) to a different data type (numeric, string, datetime, category).",
    parameters={
        "type": "object",
        "properties": {
            "columns": {"type": "array", "items": {"type": "string"}, "description": "Columns to convert"},
            "target_type": {"type": "string", "enum": ["numeric", "string", "datetime", "category", "int", "float"], "description": "Target data type"},
        },
        "required": ["columns", "target_type"],
    },
)
def convert_dtype(df: pd.DataFrame, columns: List[str], target_type: str) -> pd.DataFrame:
    for col in columns:
        if col not in df.columns:
            continue
        if target_type == "numeric":
            df[col] = pd.to_numeric(df[col], errors="coerce")
        elif target_type == "float":
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
        elif target_type == "int":
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        elif target_type == "string":
            df[col] = df[col].astype(str)
        elif target_type == "datetime":
            df[col] = pd.to_datetime(df[col], errors="coerce")
        elif target_type == "category":
            df[col] = df[col].astype("category")
    return df


@register_tool(
    name="normalize_columns",
    description="Normalize (scale) numeric columns using min-max scaling or z-score standardization.",
    parameters={
        "type": "object",
        "properties": {
            "columns": {"type": "array", "items": {"type": "string"}, "description": "Numeric columns to normalize. Use ['*'] for all numeric columns."},
            "method": {"type": "string", "enum": ["minmax", "zscore"], "description": "Normalization method", "default": "minmax"},
        },
        "required": ["columns"],
    },
)
def normalize_columns(df: pd.DataFrame, columns: List[str], method: str = "minmax") -> pd.DataFrame:
    if columns == ["*"]:
        target_cols = df.select_dtypes(include=["number"]).columns.tolist()
    else:
        target_cols = [c for c in columns if c in df.columns and df[c].dtype in ["float64", "int64", "float32", "int32"]]

    for col in target_cols:
        if method == "minmax":
            col_min = df[col].min()
            col_max = df[col].max()
            if col_max - col_min != 0:
                df[col] = (df[col] - col_min) / (col_max - col_min)
        elif method == "zscore":
            col_mean = df[col].mean()
            col_std = df[col].std()
            if col_std != 0:
                df[col] = (df[col] - col_mean) / col_std
    return df


@register_tool(
    name="encode_categorical",
    description="Encode categorical columns using one-hot encoding or label encoding.",
    parameters={
        "type": "object",
        "properties": {
            "columns": {"type": "array", "items": {"type": "string"}, "description": "Categorical columns to encode. Use ['*'] for all object/category columns."},
            "method": {"type": "string", "enum": ["onehot", "label"], "description": "Encoding method", "default": "label"},
        },
        "required": ["columns"],
    },
)
def encode_categorical(df: pd.DataFrame, columns: List[str], method: str = "label") -> pd.DataFrame:
    if columns == ["*"]:
        target_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
    else:
        target_cols = [c for c in columns if c in df.columns]

    if method == "onehot":
        df = pd.get_dummies(df, columns=target_cols, drop_first=True)
    elif method == "label":
        for col in target_cols:
            if col in df.columns:
                df[col] = df[col].astype("category").cat.codes
    return df


@register_tool(
    name="replace_values",
    description="Replace specific values in a column with another value.",
    parameters={
        "type": "object",
        "properties": {
            "column": {"type": "string", "description": "Column name"},
            "old_values": {"type": "array", "items": {"type": "string"}, "description": "Values to replace (as strings)"},
            "new_value": {"type": "string", "description": "Replacement value"},
        },
        "required": ["column", "old_values", "new_value"],
    },
)
def replace_values(df: pd.DataFrame, column: str, old_values: List[str], new_value: str) -> pd.DataFrame:
    if column not in df.columns:
        return df
    replace_map = {}
    for v in old_values:
        replace_map[v] = new_value if new_value != "null" else None
    df[column] = df[column].replace(replace_map)
    return df


@register_tool(
    name="remove_outliers",
    description="Remove rows where numeric column values are outliers (using IQR or z-score method).",
    parameters={
        "type": "object",
        "properties": {
            "columns": {"type": "array", "items": {"type": "string"}, "description": "Numeric columns to check for outliers"},
            "method": {"type": "string", "enum": ["iqr", "zscore"], "description": "Outlier detection method", "default": "iqr"},
            "threshold": {"type": "number", "description": "IQR multiplier (default 1.5) or z-score threshold (default 3)", "default": 1.5},
        },
        "required": ["columns"],
    },
)
def remove_outliers(df: pd.DataFrame, columns: List[str], method: str = "iqr", threshold: float = 1.5) -> pd.DataFrame:
    target_cols = [c for c in columns if c in df.columns and df[c].dtype in ["float64", "int64", "float32", "int32"]]
    mask = pd.Series([True] * len(df), index=df.index)

    for col in target_cols:
        if method == "iqr":
            q1 = df[col].quantile(0.25)
            q3 = df[col].quantile(0.75)
            iqr = q3 - q1
            lower = q1 - threshold * iqr
            upper = q3 + threshold * iqr
            mask = mask & (df[col] >= lower) & (df[col] <= upper)
        elif method == "zscore":
            col_mean = df[col].mean()
            col_std = df[col].std()
            if col_std > 0:
                z_scores = abs((df[col] - col_mean) / col_std)
                mask = mask & (z_scores <= threshold)
    return df[mask].reset_index(drop=True)


@register_tool(
    name="filter_rows",
    description="Filter rows based on a condition on a column.",
    parameters={
        "type": "object",
        "properties": {
            "column": {"type": "string", "description": "Column to filter on"},
            "operator": {"type": "string", "enum": ["==", "!=", ">", "<", ">=", "<=", "contains", "not_contains"], "description": "Comparison operator"},
            "value": {"type": "string", "description": "Value to compare against"},
        },
        "required": ["column", "operator", "value"],
    },
)
def filter_rows(df: pd.DataFrame, column: str, operator: str, value: str) -> pd.DataFrame:
    if column not in df.columns:
        return df
    col_series = df[column]

    # Try to convert value to numeric for comparison
    try:
        num_value = float(value)
        is_numeric = True
    except (ValueError, TypeError):
        num_value = None
        is_numeric = False

    if operator == "==" :
        mask = col_series == (num_value if is_numeric and col_series.dtype in ["float64", "int64"] else value)
    elif operator == "!=":
        mask = col_series != (num_value if is_numeric and col_series.dtype in ["float64", "int64"] else value)
    elif operator == ">" and is_numeric:
        mask = pd.to_numeric(col_series, errors="coerce") > num_value
    elif operator == "<" and is_numeric:
        mask = pd.to_numeric(col_series, errors="coerce") < num_value
    elif operator == ">=" and is_numeric:
        mask = pd.to_numeric(col_series, errors="coerce") >= num_value
    elif operator == "<=" and is_numeric:
        mask = pd.to_numeric(col_series, errors="coerce") <= num_value
    elif operator == "contains":
        mask = col_series.astype(str).str.contains(value, case=False, na=False)
    elif operator == "not_contains":
        mask = ~col_series.astype(str).str.contains(value, case=False, na=False)
    else:
        return df

    return df[mask].reset_index(drop=True)


@register_tool(
    name="lowercase_columns",
    description="Convert text values in columns to lowercase.",
    parameters={
        "type": "object",
        "properties": {
            "columns": {"type": "array", "items": {"type": "string"}, "description": "Columns to lowercase. Use ['*'] for all text columns."},
        },
        "required": ["columns"],
    },
)
def lowercase_columns(df: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
    if columns == ["*"]:
        target_cols = df.select_dtypes(include=["object"]).columns.tolist()
    else:
        target_cols = [c for c in columns if c in df.columns and df[c].dtype == "object"]
    for col in target_cols:
        df[col] = df[col].str.lower()
    return df


@register_tool(
    name="strip_whitespace",
    description="Remove leading/trailing whitespace from text columns.",
    parameters={
        "type": "object",
        "properties": {
            "columns": {"type": "array", "items": {"type": "string"}, "description": "Columns to strip. Use ['*'] for all text columns."},
        },
        "required": ["columns"],
    },
)
def strip_whitespace(df: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
    if columns == ["*"]:
        target_cols = df.select_dtypes(include=["object"]).columns.tolist()
    else:
        target_cols = [c for c in columns if c in df.columns and df[c].dtype == "object"]
    for col in target_cols:
        df[col] = df[col].str.strip()
    return df


# ─── Tool Execution Engine ────────────────────────────────────────────────────

def get_tools_schema() -> List[Dict[str, Any]]:
    """Return OpenAI-compatible function/tool schemas for all registered tools."""
    tools = []
    for name, tool_def in TRANSFORM_TOOLS.items():
        tools.append({
            "type": "function",
            "function": {
                "name": tool_def["name"],
                "description": tool_def["description"],
                "parameters": tool_def["parameters"],
            },
        })
    return tools


def execute_tool(tool_name: str, df: pd.DataFrame, arguments: Dict[str, Any]) -> pd.DataFrame:
    """Execute a registered tool by name with given arguments on a DataFrame."""
    tool_def = TRANSFORM_TOOLS.get(tool_name)
    if not tool_def:
        raise ValueError(f"Unknown tool: {tool_name}")

    func = tool_def["function"]
    return func(df.copy(), **arguments)


def get_tool_names() -> List[str]:
    """Return all registered tool names."""
    return list(TRANSFORM_TOOLS.keys())
