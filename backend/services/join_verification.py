"""Measured join verification for the AI Pipeline Builder (Phase 4, Feature 4).

Plan recommendations carry cardinality inferred from metadata. This module
replaces inference with measurement:

  * same source  -> services.join_profiler's single profile query (one round
                    trip, exact counts, already used by the ETL Copilot)
  * cross source -> key-frequency extraction from each side, intersected in
                    process. Key values stay inside this function: they are
                    never stored, never logged and never sent to a model.

Both paths produce the SAME JoinProfile, so the same deterministic
`join_profiler.recommend()` rules and the same assessment run on both.

A relationship is only "verified" when `measured` is True. A failed
measurement returns measured=False with the error - it is never guessed.
"""

from dataclasses import asdict
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from services.db_connector import DataSourceError, MAX_ROWS, extract_to_dataframe
from services.join_profiler import (
    DEFAULT_SCHEMA,
    MANY_TO_MANY,
    MANY_TO_ONE,
    ONE_TO_MANY,
    ONE_TO_ONE,
    JoinProfile,
    JoinProfileError,
    _quote,
    _quote_table,
    build_profile_query,
    profile_from_row,
    recommend,
)

Runner = Callable[[str, str], pd.DataFrame]

CARDINALITY_LABELS = {
    ONE_TO_ONE: "1:1",
    ONE_TO_MANY: "1:M",
    MANY_TO_ONE: "M:1",
    MANY_TO_MANY: "M:M",
}

SAFE_MATCH_RATE = 95.0
RISKY_MATCH_RATE = 80.0


def _default_runner(connection_string: str, sql: str) -> pd.DataFrame:
    return extract_to_dataframe(connection_string, sql)


def _norm_key(value: Any) -> Optional[str]:
    """Normalise a key so 5, 5.0, '5' and ' 5 ' compare equal across engines."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    text = str(value).strip()
    try:
        number = float(text)
        if number.is_integer() and text.lstrip("-").replace(".", "", 1).isdigit():
            return str(int(number))
    except ValueError:
        pass
    return text.casefold()


def _key_frequencies(side: Dict[str, Any], runner: Runner) -> Dict[str, Any]:
    db_type = side["db_type"]
    schema = side.get("schema") or DEFAULT_SCHEMA.get(db_type)
    table = _quote_table(db_type, schema, side["table"])
    column = _quote(db_type, side["column"], "column name")
    key_alias = _quote(db_type, "key_value", "column alias")
    count_alias = _quote(db_type, "key_count", "column alias")

    totals_sql = (
        "SELECT COUNT(*) AS %s, SUM(CASE WHEN t.%s IS NULL THEN 1 ELSE 0 END) AS %s "
        "FROM %s AS t"
        % (
            _quote(db_type, "total_rows", "column alias"),
            column,
            _quote(db_type, "null_keys", "column alias"),
            table,
        )
    )
    keys_sql = (
        "SELECT t.%s AS %s, COUNT(*) AS %s FROM %s AS t WHERE t.%s IS NOT NULL "
        "GROUP BY t.%s" % (column, key_alias, count_alias, table, column, column)
    )
    totals = runner(side["connection_string"], totals_sql)
    totals_row = {str(k).lower(): v for k, v in totals.iloc[0].to_dict().items()}
    frequencies: Dict[str, int] = {}
    try:
        keys = runner(side["connection_string"], keys_sql)
    except DataSourceError as exc:
        # db_connector raises on an EMPTY result too - a table with no keys.
        if "no rows" in str(exc).lower():
            keys = pd.DataFrame(columns=["key_value", "key_count"])
        else:
            raise
    keys.columns = [str(c).lower() for c in keys.columns]
    for raw, count in zip(keys["key_value"], keys["key_count"]):
        key = _norm_key(raw)
        if key is None:
            continue
        frequencies[key] = frequencies.get(key, 0) + int(count)
    return {
        "total_rows": int(totals_row.get("total_rows") or 0),
        "null_keys": int(totals_row.get("null_keys") or 0),
        "frequencies": frequencies,
    }


def profile_cross_source(
    left: Dict[str, Any], right: Dict[str, Any], runner: Runner = _default_runner
) -> JoinProfile:
    """Measure a join whose two sides live in different databases."""
    left_stats = _key_frequencies(left, runner)
    right_stats = _key_frequencies(right, runner)
    lf, rf = left_stats["frequencies"], right_stats["frequencies"]
    shared = set(lf) & set(rf)
    row = {
        "base_rows": left_stats["total_rows"],
        "related_rows": right_stats["total_rows"],
        "base_null_keys": left_stats["null_keys"],
        "related_null_keys": right_stats["null_keys"],
        "base_distinct_keys": len(lf),
        "related_distinct_keys": len(rf),
        "base_matched_rows": sum(lf[k] for k in shared),
        "related_matched_rows": sum(rf[k] for k in shared),
        "inner_join_rows": sum(lf[k] * rf[k] for k in shared),
        "max_rows_per_key": max(rf.values()) if rf else 0,
    }
    # Drop the key sets before returning: values never leave this function.
    del lf, rf, shared
    return profile_from_row(row)


def profile_same_source(
    side_left: Dict[str, Any], side_right: Dict[str, Any], runner: Runner = _default_runner
) -> JoinProfile:
    sql = build_profile_query(
        db_type=side_left["db_type"],
        schema=side_left.get("schema"),
        base_table=side_left["table"],
        base_column=side_left["column"],
        related_table=side_right["table"],
        related_column=side_right["column"],
    )
    frame = runner(side_left["connection_string"], sql)
    return profile_from_row(frame.iloc[0].to_dict())


def _fanout(profile: JoinProfile) -> Dict[str, Any]:
    matched = max(profile.base_matched_rows, 1)
    multiplier = round(profile.inner_result_rows / matched, 2) if profile.inner_result_rows else 0.0
    if profile.cardinality == MANY_TO_MANY and profile.row_multiplication:
        risk = "high"
    elif not profile.row_multiplication:
        risk = "low"
    elif multiplier <= 3:
        risk = "medium"
    else:
        risk = "high"
    return {"multiplier": multiplier, "risk": risk}


def assess(profile: JoinProfile, left_table: str, right_table: str) -> Dict[str, Any]:
    """Deterministic verdict + plain-language explanation from measured numbers."""
    advice = recommend(profile, left_table, right_table, MAX_ROWS)
    fan = _fanout(profile)
    orphan_rows = profile.base_unmatched_rows
    orphan_rate = round(100.0 - profile.match_percentage, 2) if profile.base_rows else 0.0
    cardinality = CARDINALITY_LABELS.get(profile.cardinality, profile.cardinality)

    sentences: List[str] = []
    if profile.base_matched_rows == 0:
        verdict = "blocked"
        sentences.append(
            f"No {left_table} record has a partner in {right_table}. These columns "
            f"are almost certainly not related - do not use this join."
        )
    elif profile.row_multiplication:
        verdict = "risky"
        sentences.append(
            f"This join is risky: {right_table} has up to {profile.max_rows_per_key:,} "
            f"rows per key, so joining turns {profile.base_matched_rows:,} matched "
            f"{left_table} rows into {profile.inner_result_rows:,} rows "
            f"({fan['multiplier']}x). Any SUM taken afterwards would be inflated. "
            f"Aggregate {right_table} to the {left_table} grain before joining."
        )
    elif profile.match_percentage >= SAFE_MATCH_RATE:
        verdict = "safe"
        sentences.append(
            f"This join is safe: each {left_table} row matches at most one "
            f"{right_table} row, so the row count stays at {profile.base_rows:,}, and "
            f"{profile.match_percentage}% of rows find a partner."
        )
    elif profile.match_percentage >= RISKY_MATCH_RATE:
        verdict = "review"
        sentences.append(
            f"The join does not multiply rows, but only {profile.match_percentage}% of "
            f"{left_table} rows match. Check whether the {orphan_rows:,} unmatched "
            f"rows are expected."
        )
    else:
        verdict = "risky"
        sentences.append(
            f"This join is risky: only {profile.match_percentage}% of {left_table} "
            f"rows match {right_table}. The key may be the wrong column or the data "
            f"may be incomplete."
        )

    if orphan_rows > 0 and profile.base_matched_rows > 0:
        sentences.append(
            f"An INNER JOIN would remove {orphan_rate}% of {left_table} records "
            f"({orphan_rows:,} rows); a LEFT JOIN keeps them with empty "
            f"{right_table} columns."
        )
    if profile.base_null_keys:
        sentences.append(
            f"{profile.base_null_keys:,} {left_table} rows have an empty key and can "
            f"never match."
        )

    recommended_join = advice.recommended
    expected_rows = (
        profile.left_result_rows if recommended_join == "left" else profile.inner_result_rows
    )
    return {
        "measured": True,
        "cardinality": cardinality,
        "match_rate": profile.match_percentage,
        "orphan_rate": orphan_rate,
        "fanout_risk": fan["risk"],
        "fanout_multiplier": fan["multiplier"],
        "row_multiplication": profile.row_multiplication,
        "current_rows": profile.base_rows,
        "expected_output_rows": expected_rows,
        "recommended_join": recommended_join,
        "verdict": verdict,
        "explanation": " ".join(sentences),
        "reasons": list(advice.reasons),
        "headline": advice.headline,
        "profile": asdict(profile),
    }


def verify_join(
    left: Dict[str, Any],
    right: Dict[str, Any],
    runner: Runner = _default_runner,
) -> Dict[str, Any]:
    """Measure one candidate join. Never raises for data problems."""
    cross_source = (
        left.get("connection_string") != right.get("connection_string")
        or left.get("db_type") != right.get("db_type")
    )
    method = "key_frequency" if cross_source else "single_query"
    try:
        profile = (
            profile_cross_source(left, right, runner)
            if cross_source
            else profile_same_source(left, right, runner)
        )
    except (DataSourceError, JoinProfileError, KeyError, IndexError, ValueError) as exc:
        return {
            "measured": False,
            "method": method,
            "verdict": "unmeasured",
            "error": str(exc)[:500],
            "explanation": (
                "The join could not be measured, so it is NOT verified. "
                "Nothing was assumed about its safety."
            ),
        }
    result = assess(profile, left["table"], right["table"])
    result["method"] = method
    return result
