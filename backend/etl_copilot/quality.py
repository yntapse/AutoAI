"""Data quality scoring shared without importing the application module."""

import pandas as pd


def compute_quality_score(df: pd.DataFrame) -> int:
    """Compute the existing 0-100 dataset quality score."""
    if df.shape[0] == 0:
        return 0
    total_cells = df.shape[0] * df.shape[1]
    nulls = int(df.isnull().sum().sum())
    dups = int(df.duplicated().sum())

    completeness_ratio = 1 - (nulls / max(total_cells, 1))
    completeness_score = int(completeness_ratio * 50)

    dup_ratio = dups / max(df.shape[0], 1)
    dup_score = int((1 - min(dup_ratio * 5, 1)) * 15)

    type_penalty = 0
    for col in df.select_dtypes(include=["object"]).columns:
        numeric_count = pd.to_numeric(df[col], errors="coerce").notna().sum()
        if numeric_count > df.shape[0] * 0.5:
            type_penalty += 5
    type_score = max(0, 20 - type_penalty)

    constant_penalty = 0
    for col in df.columns:
        if df[col].nunique() <= 1:
            constant_penalty += 5
    utility_score = max(0, 15 - constant_penalty)

    return min(100, max(0, completeness_score + dup_score + type_score + utility_score))