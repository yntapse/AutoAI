"""The six data-quality dimensions. Feature 6 of Phase 4.

The platform already has a rule engine (services/quality_rules.py) with seven
supported rule types. What it did not have was the dimension vocabulary a data
steward actually works in: completeness, uniqueness, validity, consistency,
accuracy, freshness.

This module is the mapping layer. It does not introduce a second rule engine -
every rule it recommends is one the existing engine can already execute, and
every rule type is asserted against SUPPORTED_RULE_TYPES before it is returned.

PURE. No database, no network, no LLM.
"""

from typing import Any, Dict, List, Sequence

from services.quality_rules import SUPPORTED_RULE_TYPES

DIMENSIONS = (
    "completeness",
    "uniqueness",
    "validity",
    "consistency",
    "accuracy",
    "freshness",
)

DIMENSION_DESCRIPTIONS = {
    "completeness": "Are the values that should be there actually there?",
    "uniqueness": "Is each real-world thing represented exactly once?",
    "validity": "Do values conform to the type, range or allowed set?",
    "consistency": "Do related values agree with each other across tables?",
    "accuracy": "Do values fall within a range that is possible in reality?",
    "freshness": "Is the data recent enough to be worth acting on?",
}

# Which dimension a rule belongs to. A rule type can serve more than one
# dimension depending on its configuration, so the config is consulted too.
_RULE_TO_DIMENSION = {
    "null_percentage": "completeness",
    "row_count": "completeness",
    "uniqueness": "uniqueness",
    "distinct_percentage": "consistency",
    "allowed_values": "validity",
    "data_type": "validity",
    "numeric_range": "accuracy",
}

_DATE_HINTS = ("date", "dt", "time", "timestamp", "created", "updated", "loaded")
_AMOUNT_HINTS = ("amount", "amt", "balance", "value", "price", "salary", "income")


def dimension_for_rule(rule_type: str, rule_config: Dict[str, Any] = None) -> str:
    """Which quality dimension a stored rule reports against."""
    config = rule_config or {}
    column = str(config.get("column_name") or "").lower()

    # A date column checked for completeness is really a freshness check:
    # a load-date that is null means the pipeline did not land today's data.
    if rule_type == "null_percentage" and any(
        hint in column for hint in _DATE_HINTS
    ):
        return "freshness"
    return _RULE_TO_DIMENSION.get(rule_type, "validity")


def recommend_dimension_rules(
    columns_by_table: Dict[str, List[Dict[str, Any]]],
    selected_tables: Sequence[str],
) -> List[Dict[str, Any]]:
    """Recommend rules organised by dimension, with a reason for each.

    Complements services/plan_builder.recommend_quality_rules, which covers
    keys and foreign keys. This adds the dimensions that key checks miss:
    validity on typed columns, accuracy on measures, freshness on date columns.
    Duplicates are harmless - the plan router de-duplicates by ref_key.
    """
    results: List[Dict[str, Any]] = []

    for table in selected_tables:
        for column in columns_by_table.get(table, []):
            name = column["column_name"]
            lowered = name.lower()
            data_type = str(column.get("data_type") or "").lower()

            # --- validity: the column must keep the type it was scanned with
            if column.get("is_primary_key") or column.get("is_foreign_key"):
                results.append(
                    _rule(
                        dimension="validity",
                        rule_type="data_type",
                        severity="error",
                        table=table,
                        column=name,
                        config={"expected_type": data_type or "integer"},
                        reason=(
                            f"{table}.{name} is a key. If its type changes at "
                            f"source, every join against it silently stops "
                            f"matching instead of failing."
                        ),
                        confidence=0.9,
                        risk="high",
                    )
                )

            # --- accuracy: a measure that goes negative is usually a bug
            if any(hint in lowered for hint in _AMOUNT_HINTS) and (
                "num" in data_type
                or "dec" in data_type
                or "float" in data_type
                or "money" in data_type
                or "int" in data_type
            ):
                results.append(
                    _rule(
                        dimension="accuracy",
                        rule_type="numeric_range",
                        severity="warning",
                        table=table,
                        column=name,
                        config={"min_value": 0},
                        reason=(
                            f"{table}.{name} is a monetary measure. A negative "
                            f"value is possible but rare, so it is worth a "
                            f"warning rather than a silent total."
                        ),
                        confidence=0.66,
                        risk="medium",
                    )
                )

            # --- freshness: a date column that stops arriving
            if any(hint in lowered for hint in _DATE_HINTS):
                results.append(
                    _rule(
                        dimension="freshness",
                        rule_type="null_percentage",
                        severity="warning",
                        table=table,
                        column=name,
                        config={"max_percentage": 2.0},
                        reason=(
                            f"{table}.{name} carries the time dimension. A rise "
                            f"in nulls here is the first sign that an upstream "
                            f"feed stopped landing."
                        ),
                        confidence=0.7,
                        risk="medium",
                    )
                )

        # --- completeness at table level: the load must not come back empty
        results.append(
            _rule(
                dimension="completeness",
                rule_type="row_count",
                severity="error",
                table=table,
                column=None,
                config={"min_rows": 1},
                reason=(
                    f"Fail the build if {table} returns no rows. An empty "
                    f"source is the failure mode that looks most like success."
                ),
                confidence=0.95,
                risk="high",
            )
        )

    for item in results:
        assert item["rule_type"] in SUPPORTED_RULE_TYPES, item["rule_type"]
        assert item["dimension"] in DIMENSIONS, item["dimension"]
    return results


def _rule(
    dimension: str,
    rule_type: str,
    severity: str,
    table: str,
    column: str,
    config: Dict[str, Any],
    reason: str,
    confidence: float,
    risk: str,
) -> Dict[str, Any]:
    rule_config = {"table_name": table}
    if column:
        rule_config["column_name"] = column
    rule_config.update(config)
    return {
        "dimension": dimension,
        "rule_type": rule_type,
        "severity": severity,
        "rule_config": rule_config,
        "reason": reason,
        "confidence": confidence,
        "risk_level": risk,
    }


def score_dimensions(rules: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Coverage per dimension: how many rules guard it, and how many block.

    This is coverage, not a data-quality score. A real score needs execution
    results, which only exist after a run.
    """
    summary: Dict[str, Dict[str, Any]] = {
        dimension: {
            "dimension": dimension,
            "description": DIMENSION_DESCRIPTIONS[dimension],
            "rules": 0,
            "blocking_rules": 0,
            "covered": False,
        }
        for dimension in DIMENSIONS
    }

    for rule in rules:
        dimension = rule.get("dimension") or dimension_for_rule(
            rule.get("rule_type", ""), rule.get("rule_config")
        )
        bucket = summary.setdefault(
            dimension,
            {
                "dimension": dimension,
                "description": DIMENSION_DESCRIPTIONS.get(dimension, ""),
                "rules": 0,
                "blocking_rules": 0,
                "covered": False,
            },
        )
        bucket["rules"] += 1
        bucket["covered"] = True
        if rule.get("severity") == "error":
            bucket["blocking_rules"] += 1

    return summary
