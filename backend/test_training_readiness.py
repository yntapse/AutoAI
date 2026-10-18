"""
Unit tests for assess_training_readiness() in services/training_readiness.py

Pure logic: no database, no HTTP server, no uploads directory. The function
used to live in main.py, which connects to PostgreSQL at import time; it is now
a service, so this test needs nothing running.

Run from backend/:
    python -m pytest test_training_readiness.py -v
    python test_training_readiness.py          # plain run, no pytest needed
"""

import sys

import pandas as pd

try:
    # The logic lives in services/training_readiness.py; main.py re-exports it.
    # Importing the service directly means this test no longer needs a database.
    from services.training_readiness import (
        DEFAULT_CV_FOLDS,
        MIN_TRAINING_ROWS,
        SMALL_DATASET_ROW_THRESHOLD,
        TOO_SMALL_MESSAGE,
        assess_training_readiness,
    )
except Exception as exc:  # pragma: no cover
    print("Could not import services/training_readiness.py. Original error:")
    print(f"  {type(exc).__name__}: {exc}")
    raise SystemExit(2)


_failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}   {detail}")
        _failures.append(label)


def make_df(n_rows: int, n_features: int = 3) -> pd.DataFrame:
    """A numeric regression-shaped frame: n_features predictors + 'target'."""
    data = {f"feature_{i}": list(range(n_rows)) for i in range(n_features)}
    data["target"] = [float(i) * 1.5 for i in range(n_rows)]
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# 1. Rejected band: fewer than MIN_TRAINING_ROWS
# ---------------------------------------------------------------------------


def test_rejected_band():
    print("\n[1] Rejected band (< 10 rows)")
    for n in (0, 1, 4, 5, 9):
        r = assess_training_readiness(make_df(n))
        check(f"{n:>3} rows -> not ready", r["ready"] is False, r)
        check(f"{n:>3} rows -> reason is the user-facing message",
              r["reason"] == TOO_SMALL_MESSAGE, r["reason"])
        check(f"{n:>3} rows -> no folds proposed", r["n_folds"] == 0, r["n_folds"])
        check(f"{n:>3} rows -> n_rows echoed", r["n_rows"] == n, r["n_rows"])


def test_rejection_message_is_user_facing():
    print("\n[1b] Rejection message quality")
    r = assess_training_readiness(make_df(4))
    msg = r["reason"]
    check("mentions the dataset is too small", "too small" in msg.lower(), msg)
    check("tells the user what to do", "add more rows" in msg.lower(), msg)
    check("leaks no sklearn internals",
          "n_splits" not in msg and "n_samples" not in msg, msg)
    check("leaks no Python type names",
          "Traceback" not in msg and "Error" not in msg, msg)


# ---------------------------------------------------------------------------
# 2. Warned band: 10 .. 29 rows
# ---------------------------------------------------------------------------


def test_warned_band():
    print("\n[2] Warned band (10-29 rows)")
    for n in (10, 12, 20, 29):
        r = assess_training_readiness(make_df(n))
        check(f"{n:>3} rows -> ready", r["ready"] is True, r)
        check(f"{n:>3} rows -> no rejection reason", r["reason"] is None, r["reason"])
        check(f"{n:>3} rows -> exactly one warning", len(r["warnings"]) == 1, r["warnings"])
        check(f"{n:>3} rows -> folds match the approved formula",
              r["n_folds"] == max(2, min(DEFAULT_CV_FOLDS, n // 2)), r["n_folds"])
        check(f"{n:>3} rows -> every fold gets >= 2 validation rows",
              n // r["n_folds"] >= 2, f"{n} // {r['n_folds']}")
        check(f"{n:>3} rows -> warning names the row count", str(n) in r["warnings"][0],
              r["warnings"][0])


# ---------------------------------------------------------------------------
# 3. Normal band: 30+ rows, behaviour must be untouched
# ---------------------------------------------------------------------------


def test_normal_band():
    print("\n[3] Normal band (30+ rows) - existing behaviour preserved")
    for n in (30, 100, 500, 5000):
        r = assess_training_readiness(make_df(n))
        check(f"{n:>4} rows -> ready", r["ready"] is True, r)
        check(f"{n:>4} rows -> exactly 5 folds (unchanged)",
              r["n_folds"] == DEFAULT_CV_FOLDS, r["n_folds"])
        check(f"{n:>4} rows -> no warnings", r["warnings"] == [], r["warnings"])
        check(f"{n:>4} rows -> no rejection reason", r["reason"] is None, r["reason"])


# ---------------------------------------------------------------------------
# 4. Boundaries - the rows named in the sprint brief
# ---------------------------------------------------------------------------


def test_exact_boundaries():
    print("\n[4] Exact boundaries from the brief: 4, 5, 9, 10, 12, 29, 30, 500")
    expected = {
        4:   (False, 0, 0),
        5:   (False, 0, 0),
        9:   (False, 0, 0),
        10:  (True,  5, 1),
        12:  (True,  5, 1),
        29:  (True,  5, 1),
        30:  (True,  5, 0),
        500: (True,  5, 0),
    }
    print(f"      {'rows':>5} | {'ready':>5} | {'folds':>5} | {'warns':>5}")
    for n, (ready, folds, warns) in expected.items():
        r = assess_training_readiness(make_df(n))
        actual = (r["ready"], r["n_folds"], len(r["warnings"]))
        print(f"      {n:>5} | {str(r['ready']):>5} | {r['n_folds']:>5} | "
              f"{len(r['warnings']):>5}")
        check(f"{n:>3} rows -> ({ready}, {folds} folds, {warns} warnings)",
              actual == (ready, folds, warns), actual)


def test_threshold_constants():
    print("\n[5] Thresholds are derived from the fold count, not hard-coded")
    check("MIN_TRAINING_ROWS == DEFAULT_CV_FOLDS * 2",
          MIN_TRAINING_ROWS == DEFAULT_CV_FOLDS * 2, MIN_TRAINING_ROWS)
    check("SMALL_DATASET_ROW_THRESHOLD == DEFAULT_CV_FOLDS * 6",
          SMALL_DATASET_ROW_THRESHOLD == DEFAULT_CV_FOLDS * 6,
          SMALL_DATASET_ROW_THRESHOLD)
    check("the floor is the last rejected row count",
          assess_training_readiness(make_df(MIN_TRAINING_ROWS - 1))["ready"] is False)
    check("the floor itself is accepted",
          assess_training_readiness(make_df(MIN_TRAINING_ROWS))["ready"] is True)
    check("one below the small threshold still warns",
          len(assess_training_readiness(
              make_df(SMALL_DATASET_ROW_THRESHOLD - 1))["warnings"]) == 1)
    check("the small threshold itself does not warn",
          assess_training_readiness(
              make_df(SMALL_DATASET_ROW_THRESHOLD))["warnings"] == [])


# ---------------------------------------------------------------------------
# 5. Shape and purity
# ---------------------------------------------------------------------------


def test_contract():
    print("\n[6] Return contract and purity")
    r = assess_training_readiness(make_df(50))
    for key in ("ready", "reason", "n_rows", "n_columns", "n_folds", "warnings"):
        check(f"key '{key}' present", key in r, sorted(r))
    check("ready is a bool", isinstance(r["ready"], bool), type(r["ready"]))
    check("n_folds is an int", isinstance(r["n_folds"], int), type(r["n_folds"]))
    check("warnings is a list", isinstance(r["warnings"], list), type(r["warnings"]))

    df = make_df(50)
    before = (df.shape, list(df.columns))
    assess_training_readiness(df)
    check("input DataFrame is not mutated", (df.shape, list(df.columns)) == before)

    wide = make_df(50, n_features=40)
    check("column count is reported", assess_training_readiness(wide)["n_columns"] == 41,
          assess_training_readiness(wide)["n_columns"])
    check("column count does not change the verdict",
          assess_training_readiness(wide)["n_folds"] == DEFAULT_CV_FOLDS)


def test_never_raises():
    print("\n[7] Never raises - it reports, the caller decides")
    for n in (0, 1, 2, 3, 4, 9, 10, 29, 30, 1000):
        try:
            assess_training_readiness(make_df(n))
            ok = True
        except Exception as exc:
            ok = False
            detail = f"{type(exc).__name__}: {exc}"
        check(f"{n:>4} rows -> no exception", ok, "" if ok else detail)


ALL = [
    test_rejected_band,
    test_rejection_message_is_user_facing,
    test_warned_band,
    test_normal_band,
    test_exact_boundaries,
    test_threshold_constants,
    test_contract,
    test_never_raises,
]

if __name__ == "__main__":
    for t in ALL:
        t()
    print("\n" + "=" * 62)
    if _failures:
        print(f"  {len(_failures)} FAILURE(S): " + ", ".join(_failures))
        sys.exit(1)
    print("  ALL CHECKS PASSED")
    print("=" * 62)
