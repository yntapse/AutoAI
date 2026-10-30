"""Measured join verification. Feature 4 of Phase 4.

This module adds nothing to the measurement itself - services/join_profiler.py
already builds a single-round-trip profile query, interprets the row and
produces a plain-language recommendation. What was missing was the wiring:
running that query against a registered source, deriving the figures the UI
asks for (match rate, orphan rate, fan-out risk, row explosion), and returning
them in the vocabulary models/join_verification.py stores.

The database call is injected, so every derivation below is testable without a
server and the module stays import-safe.

Security: the connection string is built per request by the caller from
credentials that are never persisted, and only aggregate counts come back - no
row values, no keys, no customer data. Nothing here is ever sent to a model.
"""

from typing import Any, Callable, Dict, Optional

from models.join_verification import (
    VERDICT_CAUTION,
    VERDICT_RISKY,
    VERDICT_SAFE,
    VERDICT_UNMEASURED,
)
from services.join_profiler import (
    MANY_TO_MANY,
    MANY_TO_ONE,
    ONE_TO_MANY,
    ONE_TO_ONE,
    SEVERITY_DANGER,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    JoinProfile,
    JoinProfileError,
    build_profile_query,
    profile_from_row,
    recommend,
)

# Fan-out risk from declared cardinality, before measurement corrects it.
_BASE_RISK = {
    ONE_TO_ONE: "none",
    MANY_TO_ONE: "low",
    ONE_TO_MANY: "medium",
    MANY_TO_MANY: "high",
}

# join_profiler uses snake_case cardinality internally; the plan engine, the
# canvas and the stored verification all use the compact form.
CARDINALITY_LABEL = {
    ONE_TO_ONE: "1:1",
    MANY_TO_ONE: "M:1",
    ONE_TO_MANY: "1:M",
    MANY_TO_MANY: "M:M",
}

_VERDICT_FOR_SEVERITY = {
    SEVERITY_INFO: VERDICT_SAFE,
    SEVERITY_WARNING: VERDICT_CAUTION,
    SEVERITY_DANGER: VERDICT_RISKY,
}


def fanout_risk(profile: JoinProfile) -> str:
    """Measured fan-out risk, not an inference from declared cardinality."""
    risk = _BASE_RISK.get(profile.cardinality, "medium")

    # Measurement overrides the declared label in both directions. A 1:M
    # relationship whose worst key has one partner cannot multiply anything,
    # whatever the schema asserts - which is the whole reason for measuring.
    if profile.max_rows_per_key <= 1:
        return "none" if risk in ("none", "low") else "low"
    if profile.max_rows_per_key > 50 and risk != "none":
        return "high"
    return risk


def row_explosion_ratio(profile: JoinProfile) -> float:
    """How many output rows each base row becomes, measured.

    1.0 means the join preserves the grain. 39.0 means every base row becomes
    39 - the fan-out that silently inflates every SUM taken afterwards.
    """
    if not profile.base_rows:
        return 0.0
    return round(profile.inner_result_rows / profile.base_rows, 4)


def orphan_rate(profile: JoinProfile) -> float:
    """Percentage of base rows with no partner on the other side."""
    if not profile.base_rows:
        return 0.0
    return round(100.0 * profile.base_unmatched_rows / profile.base_rows, 2)


def derive(
    profile: JoinProfile,
    left_table: str,
    right_table: str,
    row_limit: int,
) -> Dict[str, Any]:
    """Turn a measured JoinProfile into the stored verification payload.

    Keys match models/join_verification.JoinVerification columns exactly, so
    the router can pass this straight into the model.
    """
    suggestion = recommend(profile, left_table, right_table, row_limit)
    return {
        "method": "single_query",
        "measured": True,
        # derived
        "cardinality": CARDINALITY_LABEL.get(
            profile.cardinality, profile.cardinality
        ),
        "match_rate": round(profile.match_percentage, 2),
        "orphan_rate": orphan_rate(profile),
        # measured
        "base_rows": profile.base_rows,
        "related_rows": profile.related_rows,
        "base_null_keys": profile.base_null_keys,
        "inner_rows": profile.inner_result_rows,
        "left_rows": profile.left_result_rows,
        "max_rows_per_key": profile.max_rows_per_key,
        "row_multiplication": bool(profile.row_multiplication),
        "fanout_risk": fanout_risk(profile),
        # recommendation + evidence
        "recommended_join": suggestion.recommended,
        "verdict": _VERDICT_FOR_SEVERITY.get(suggestion.severity, VERDICT_CAUTION),
        "explanation": suggestion.headline,
        "reasons": list(suggestion.reasons),
        "error": None,
    }


def measure_relationship(
    db_type: str,
    schema: Optional[str],
    left_table: str,
    left_column: str,
    right_table: str,
    right_column: str,
    run_query: Callable[[str], Any],
    row_limit: int,
) -> Dict[str, Any]:
    """Measure one relationship end to end.

    Args:
        run_query: called with the profile SQL, must return something
            row-like - a pandas DataFrame, a list of mappings, or a mapping.
            Injected so this is testable with no database.
        row_limit: the dataset row cap, used by the recommendation rules.

    Returns the stored verification payload. On any failure the payload carries
    measured=False and verdict="unmeasured" - a failed measurement is never
    allowed to read as a pass.
    """
    try:
        sql = build_profile_query(
            db_type=db_type,
            schema=schema,
            base_table=left_table,
            base_column=left_column,
            related_table=right_table,
            related_column=right_column,
        )
        row = _first_row(run_query(sql))
        profile = profile_from_row(row)
    except JoinProfileError as exc:
        return _failure(str(exc))
    except Exception as exc:  # noqa: BLE001 - no driver error may pass as verified
        return _failure(f"Measurement failed: {exc}")

    return derive(profile, left_table, right_table, row_limit)


def _failure(message: str) -> Dict[str, Any]:
    return {
        "method": "single_query",
        "measured": False,
        "verdict": VERDICT_UNMEASURED,
        "error": message,
        "explanation": "Could not verify this relationship.",
        "reasons": [
            message,
            "It stays unverified until a measurement succeeds.",
        ],
        "recommended_join": None,
    }


def _first_row(result: Any) -> Dict[str, Any]:
    """Accept a DataFrame, a list of mappings, or a single mapping."""
    if result is None:
        raise JoinProfileError("The profile query returned no row.")

    # pandas DataFrame
    if hasattr(result, "empty") and hasattr(result, "to_dict"):
        if result.empty:
            raise JoinProfileError("The profile query returned no row.")
        return result.iloc[0].to_dict()

    if isinstance(result, dict):
        return result

    rows = list(result)
    if not rows:
        raise JoinProfileError("The profile query returned no row.")
    first = rows[0]
    return dict(first) if not isinstance(first, dict) else first
