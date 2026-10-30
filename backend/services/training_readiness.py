"""Training readiness: is there enough data here to EVALUATE a model honestly?

Extracted verbatim from main.py so it can be imported without booting the
application. main.py re-exports it, so every existing caller - including
services/etl_agent.py and test_training_readiness.py - keeps working
unchanged, and there is still exactly ONE implementation.

PURE: reads a DataFrame's shape and returns a verdict. Never raises, never
touches the database, never writes a file. The caller decides what the verdict
means, which is what lets the HTTP layer return 400 while the agent's internal
call raises instead.
"""

from typing import Any, Dict

import pandas as pd

# ---------------------------------------------------------------------------
# Training readiness
#
# validate_dataset_limits() above answers "is this dataset too BIG to accept?"
# and guards project creation. This answers a different question - "is there
# enough data here to EVALUATE a model honestly?" - and guards training only.
#
# They are deliberately separate. A 4-row table is a perfectly valid project:
# the user may create it, inspect it, and enrich it through the Transform
# module. It is only training that cannot produce a meaningful score.
#
# WHY THESE THRESHOLDS
#
#   The trainer evaluates with DEFAULT_CV_FOLDS-fold cross-validation, so each
#   fold validates on roughly n_rows / n_folds samples.
#
#   MIN_TRAINING_ROWS = folds * 2 - the smallest dataset where every fold has
#   at least TWO validation rows. Below this, a fold validates on one sample:
#   r2_score() on a single sample is mathematically undefined and returns nan,
#   so the leaderboard would be full of nan rather than useful numbers. A clear
#   refusal beats a meaningless score.
#
#   SMALL_DATASET_ROW_THRESHOLD = folds * 6 - at least six validation rows per
#   fold. Above this the existing behaviour is left completely alone.
#
# Both are derived from DEFAULT_CV_FOLDS, so changing the fold count moves the
# thresholds with it and the three stay consistent.
#
# This function is PURE: it reads a DataFrame's shape and returns a verdict.
# It never raises, never touches the database, never writes a file. The caller
# decides what to do with the verdict - which is what lets the HTTP layer
# return 400 while the agent's internal call raises instead.
# ---------------------------------------------------------------------------

DEFAULT_CV_FOLDS = 5
MIN_TRAINING_ROWS = DEFAULT_CV_FOLDS * 2          # 10
SMALL_DATASET_ROW_THRESHOLD = DEFAULT_CV_FOLDS * 6  # 30

TOO_SMALL_MESSAGE = (
    "Dataset too small for reliable model training. "
    "Add more rows or modify your table selection."
)


def assess_training_readiness(df: pd.DataFrame) -> Dict[str, Any]:
    """Decide whether a dataset can be trained on, and with how many CV folds.

    Returns:
        {
            "ready":      bool,           # False -> do not train
            "reason":     Optional[str],  # user-facing text when not ready
            "n_rows":     int,
            "n_columns":  int,
            "n_folds":    int,            # 0 when not ready
            "warnings":   List[str],      # shown alongside the results
        }

    Never raises. Never mutates the input frame.
    """
    n_rows = int(len(df))
    n_columns = int(len(df.columns))

    # --- Band 1: too small to evaluate at all ---
    if n_rows < MIN_TRAINING_ROWS:
        return {
            "ready": False,
            "reason": TOO_SMALL_MESSAGE,
            "n_rows": n_rows,
            "n_columns": n_columns,
            "n_folds": 0,
            "warnings": [],
        }

    # --- Band 2: trainable, but the scores are rough ---
    if n_rows < SMALL_DATASET_ROW_THRESHOLD:
        # Never more folds than leave 2 validation rows each, never fewer
        # than 2. With DEFAULT_CV_FOLDS = 5 and a floor of 10 rows this
        # always resolves to 5; the bounds matter if the fold count changes.
        n_folds = max(2, min(DEFAULT_CV_FOLDS, n_rows // 2))
        rows_per_fold = n_rows // n_folds

        return {
            "ready": True,
            "reason": None,
            "n_rows": n_rows,
            "n_columns": n_columns,
            "n_folds": n_folds,
            "warnings": [
                f"Small dataset: {n_rows} rows. Cross-validation used "
                f"{n_folds} folds of about {rows_per_fold} rows each. "
                f"Treat these scores as indicative, not reliable."
            ],
        }

    # --- Band 3: normal. Existing behaviour, untouched. ---
    return {
        "ready": True,
        "reason": None,
        "n_rows": n_rows,
        "n_columns": n_columns,
        "n_folds": DEFAULT_CV_FOLDS,
        "warnings": [],
    }
