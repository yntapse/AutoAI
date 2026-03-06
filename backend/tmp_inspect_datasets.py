"""Inspect first 8 CSV datasets to pick two diverse ones for Phase E verification."""
import pandas as pd
from pathlib import Path

csvs = sorted(Path("uploads").glob("*.csv"))
for c in csvs[:8]:
    df = pd.read_csv(c)
    ncols = df.select_dtypes(include=["number"]).columns
    print(f"{c.name}: rows={len(df)}, cols={len(df.columns)}, numeric={len(ncols)}, target_cand={ncols[-1] if len(ncols)>0 else 'NONE'}")
