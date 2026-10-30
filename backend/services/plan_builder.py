"""Deterministic recommendation engine for the AI Pipeline Builder.

Takes a catalog snapshot (Sprint 1 metadata) plus a parsed requirement intent
and produces reviewable recommendations: which tables, which relationships,
which joins, what cardinality, what transformations, what quality rules.

PURE. No database handle, no network, no LLM. Everything here is reproducible
from its inputs, which is what makes the plan testable and the execution
deterministic. The LLM contributes only the `intent` argument - see
services/agents/planner_agent.py.
"""

import math
from typing import Any, Dict, List, Optional, Sequence

from services.data_cleaner import STEP_TYPES as CLEANING_STEP_TYPES
from services.quality_rules import SUPPORTED_RULE_TYPES

# Tables whose names say "this is plumbing, not business data".
_NOISE_TOKENS = (
    "audit", "log", "logs", "config", "configuration", "temp", "tmp", "bkp",
    "backup", "staging", "stg", "migration", "sysdiagrams", "test", "dummy",
    "archive", "history_bak",
)

_DATE_HINTS = ("date", "dt", "time", "timestamp", "created", "updated", "dob")
_AMOUNT_HINTS = ("amount", "amt", "balance", "value", "price", "salary", "income")

_JOIN_TYPES = ("inner", "left", "right", "full")

MAX_RECOMMENDED_TABLES = 8


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _tokens(text: str) -> List[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in str(text or ""))
    return [token for token in cleaned.split() if len(token) > 2]


SOURCE_SEPARATOR = "/"


def bare_table_name(table_name: str) -> str:
    """Strip a multi-source qualifier: 'crm_pg/customer' -> 'customer'.

    Single-source plans never carry a qualifier, so this is a no-op for them.
    Scoring always uses the bare name so a source name cannot dilute or
    inflate how well a table matches the requirement.
    """
    return str(table_name or "").rsplit(SOURCE_SEPARATOR, 1)[-1]


def _is_noise(table_name: str) -> bool:
    lowered = bare_table_name(table_name).lower()
    return any(token in lowered for token in _NOISE_TOKENS)


def _name_score(table_name: str, wanted: Sequence[str]) -> float:
    """How strongly a table name matches the requirement vocabulary.

    Measured as the share of the TABLE's own name tokens that the requirement
    vocabulary covers - not the share of the vocabulary the name covers. A long
    requirement must not dilute the score of a table that clearly belongs:
    'customer_master' is just as relevant to a ten-word requirement as to a
    three-word one.
    """
    name_tokens = set(_tokens(bare_table_name(table_name)))
    wanted_set = {str(token).lower() for token in wanted or []}
    if not name_tokens or not wanted_set:
        return 0.0

    matched = 0.0
    for name_token in name_tokens:
        if name_token in wanted_set:
            matched += 1.0
        elif any(
            name_token in token or token in name_token for token in wanted_set
        ):
            matched += 0.5
    return min(1.0, matched / len(name_tokens))


def _cardinality(left_unique: bool, right_unique: bool) -> str:
    if left_unique and right_unique:
        return "1:1"
    if right_unique:
        return "M:1"
    if left_unique:
        return "1:M"
    return "M:M"


def _explosion_risk(cardinality: str) -> str:
    return {"1:1": "none", "M:1": "low", "1:M": "medium", "M:M": "high"}[cardinality]


# --------------------------------------------------------------------------
# recommendation builders
# --------------------------------------------------------------------------


def recommend_tables(
    tables: Sequence[Dict[str, Any]],
    intent: Dict[str, Any],
    relationships: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Classify every catalog table as required, useful or unnecessary."""
    wanted = list(intent.get("keywords") or [])
    anchor_hint = str(intent.get("anchor_entity") or "").lower()

    degree: Dict[str, int] = {}
    for edge in relationships:
        degree[edge["left_table"]] = degree.get(edge["left_table"], 0) + 1
        degree[edge["right_table"]] = degree.get(edge["right_table"], 0) + 1

    max_degree = max(degree.values()) if degree else 0
    scored = []

    for table in tables:
        name = table["table_name"]
        noise = _is_noise(name)
        name_score = _name_score(name, wanted)
        connectivity = (degree.get(name, 0) / max_degree) if max_degree else 0.0
        has_pk = bool(table.get("has_primary_key"))

        score = (0.60 * name_score) + (0.25 * connectivity) + (0.15 if has_pk else 0.0)
        if noise:
            score = min(score, 0.15)
        if anchor_hint and anchor_hint in bare_table_name(name).lower():
            score = min(1.0, score + 0.25)

        scored.append(
            {
                "table_name": name,
                "score": round(score, 3),
                "name_score": round(name_score, 3),
                "connectivity": round(connectivity, 3),
                "has_primary_key": has_pk,
                "noise": noise,
                "row_count": table.get("row_count"),
                "column_count": table.get("column_count"),
            }
        )

    scored.sort(key=lambda row: row["score"], reverse=True)

    results: List[Dict[str, Any]] = []
    required_quota = min(MAX_RECOMMENDED_TABLES, max(1, len(scored)))

    for position, row in enumerate(scored):
        name = row["table_name"]
        if row["noise"]:
            classification = "unnecessary"
            reason = (
                f"'{name}' looks like operational plumbing rather than business data "
                f"(its name contains a system keyword). It carries no measure or "
                f"attribute the requirement asks for."
            )
            confidence = 0.86
        elif row["score"] >= 0.45 and position < required_quota:
            classification = "required"
            bits = []
            if row["name_score"] > 0:
                bits.append("its name matches the requirement vocabulary")
            if row["connectivity"] > 0.3:
                bits.append("it is well connected to the rest of the schema")
            if row["has_primary_key"]:
                bits.append("it has a primary key, so it can anchor a join safely")
            reason = f"'{name}' is recommended because " + ", and ".join(
                bits or ["it scores highest against the requirement"]
            ) + "."
            confidence = min(0.95, 0.55 + row["score"] * 0.4)
        elif row["score"] >= 0.20:
            classification = "useful"
            reason = (
                f"'{name}' is not needed to answer the requirement, but it joins to "
                f"the selected tables and would add context if you want it."
            )
            confidence = 0.6
        else:
            classification = "unnecessary"
            reason = (
                f"'{name}' has no name overlap with the requirement and no strong "
                f"link to the selected tables."
            )
            confidence = 0.7

        results.append(
            {
                "table_name": name,
                "classification": classification,
                "score": row["score"],
                "reason": reason,
                "confidence": round(confidence, 2),
                "row_count": row["row_count"],
                "column_count": row["column_count"],
            }
        )

    return results


def recommend_relationships(
    relationships: Sequence[Dict[str, Any]],
    columns_by_table: Dict[str, List[Dict[str, Any]]],
    selected_tables: Sequence[str],
) -> List[Dict[str, Any]]:
    """Declared foreign keys first, then name-matched candidates."""
    selected = set(selected_tables)
    seen = set()
    results: List[Dict[str, Any]] = []

    for edge in relationships:
        if edge["left_table"] not in selected or edge["right_table"] not in selected:
            continue
        key = (
            edge["left_table"], edge["left_column"],
            edge["right_table"], edge["right_column"],
        )
        seen.add(key)
        results.append(
            {
                **{k: edge[k] for k in
                   ("left_table", "left_column", "right_table", "right_column")},
                "origin": "declared",
                "confidence": 0.99,
                "reason": (
                    f"Declared foreign key "
                    f"{edge.get('constraint_name') or 'constraint'}: "
                    f"{edge['left_table']}.{edge['left_column']} references "
                    f"{edge['right_table']}.{edge['right_column']}. The database "
                    f"itself asserts this relationship."
                ),
            }
        )

    # Inferred: same normalized column name, one side a primary key.
    by_normalized: Dict[str, List[tuple]] = {}
    for table in selected_tables:
        for column in columns_by_table.get(table, []):
            normalized = column.get("normalized_name") or column["column_name"].lower()
            by_normalized.setdefault(normalized, []).append((table, column))

    for normalized, entries in by_normalized.items():
        if len(entries) < 2:
            continue
        pk_entries = [e for e in entries if e[1].get("is_primary_key")]
        if not pk_entries:
            continue
        right_table, right_column = pk_entries[0][0], pk_entries[0][1]["column_name"]
        for left_table, left_column in [
            (t, c["column_name"]) for t, c in entries if t != right_table
        ]:
            key = (left_table, left_column, right_table, right_column)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "left_table": left_table,
                    "left_column": left_column,
                    "right_table": right_table,
                    "right_column": right_column,
                    "origin": "inferred",
                    "confidence": 0.62,
                    "reason": (
                        f"No foreign key is declared, but "
                        f"{left_table}.{left_column} and {right_table}.{right_column} "
                        f"normalise to the same name ('{normalized}') and the right "
                        f"side is a primary key. This is a candidate only - it must "
                        f"be measured on real data before it is used."
                    ),
                }
            )

    return results


def recommend_joins(
    relationships: Sequence[Dict[str, Any]],
    columns_by_table: Dict[str, List[Dict[str, Any]]],
    anchor_table: Optional[str],
) -> List[Dict[str, Any]]:
    """A join type per relationship, with the reason and the row impact."""
    results: List[Dict[str, Any]] = []

    for edge in relationships:
        left, right = edge["left_table"], edge["right_table"]
        left_cols = {c["column_name"]: c for c in columns_by_table.get(left, [])}
        right_cols = {c["column_name"]: c for c in columns_by_table.get(right, [])}

        left_unique = bool(
            left_cols.get(edge["left_column"], {}).get("is_primary_key")
        )
        right_unique = bool(
            right_cols.get(edge["right_column"], {}).get("is_primary_key")
        )
        cardinality = _cardinality(left_unique, right_unique)
        risk = _explosion_risk(cardinality)
        nullable = bool(left_cols.get(edge["left_column"], {}).get("is_nullable", True))

        if cardinality in ("M:1", "1:1") and not nullable:
            join_type = "inner"
            reason = (
                f"INNER is safe here: the key is {cardinality} and "
                f"{left}.{edge['left_column']} is NOT NULL, so no row of {left} can "
                f"be dropped by the join."
            )
            alternative = (
                "LEFT would behave identically because every row matches, and it "
                "would hide a future referential break instead of surfacing it."
            )
        elif cardinality in ("M:1", "1:1") and nullable:
            join_type = "left"
            reason = (
                f"LEFT is recommended: the key is {cardinality}, but "
                f"{left}.{edge['left_column']} is nullable, so an INNER join would "
                f"silently drop every row with no match."
            )
            alternative = (
                "INNER was rejected because it would change the row count without "
                "saying so - the single most common cause of an understated total."
            )
        else:
            join_type = "left"
            reason = (
                f"LEFT with aggregation: the key is {cardinality}, so joining "
                f"{right} directly would multiply each {left} row. The compiler "
                f"aggregates {right} to the grain of {left} before joining."
            )
            alternative = (
                "A direct INNER or LEFT join without aggregation was rejected: it "
                "produces fan-out and inflates every SUM taken afterwards."
            )

        results.append(
            {
                "left_table": left,
                "left_column": edge["left_column"],
                "right_table": right,
                "right_column": edge["right_column"],
                "join_type": join_type,
                "cardinality": cardinality,
                "explosion_risk": risk,
                "requires_pre_aggregation": cardinality in ("1:M", "M:M"),
                "reason": reason,
                "alternative_reason": alternative,
                "confidence": edge.get("confidence", 0.8),
                "origin": edge.get("origin", "declared"),
                "is_anchor_join": anchor_table in (left, right) if anchor_table else False,
            }
        )

    return results


def recommend_transformations(
    columns_by_table: Dict[str, List[Dict[str, Any]]],
    selected_tables: Sequence[str],
    profiles: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Cleaning steps drawn only from data_cleaner.STEP_TYPES."""
    profiles = profiles or {}
    results: List[Dict[str, Any]] = []

    for table in selected_tables:
        table_profile = profiles.get(table, {})
        column_profiles = {
            c.get("column_name"): c for c in table_profile.get("columns", [])
        }

        for column in columns_by_table.get(table, []):
            name = column["column_name"]
            lowered = name.lower()
            data_type = str(column.get("data_type", "")).lower()
            profile = column_profiles.get(name, {})
            null_pct = profile.get("null_percentage")

            # 1. date stored as text
            if any(hint in lowered for hint in _DATE_HINTS) and (
                "char" in data_type or "text" in data_type or "varchar" in data_type
            ):
                results.append(
                    {
                        "step_type": "convert_data_type",
                        "table_name": table,
                        "configuration": {"column_name": name, "target_type": "date"},
                        "reason": (
                            f"{table}.{name} looks like a date but is stored as "
                            f"{column.get('data_type')}. Every date comparison "
                            f"against a text column silently matches nothing."
                        ),
                        "confidence": 0.9,
                        "expected_impact": "Date filters and late-payment logic start working.",
                        "risk_level": "medium",
                    }
                )

            # 2. high null rate on a measure
            if null_pct is not None and null_pct >= 5:
                numeric_like = any(hint in lowered for hint in _AMOUNT_HINTS)
                strategy = "mean" if numeric_like else "mode"
                results.append(
                    {
                        "step_type": "replace_null_values",
                        "table_name": table,
                        "configuration": {
                            "column_name": name,
                            "strategy": strategy,
                            "custom_value": None,
                        },
                        "reason": (
                            f"{table}.{name} is {null_pct:.1f}% null. Filling with the "
                            f"{strategy} keeps the row usable instead of dropping it."
                        ),
                        "confidence": 0.62,
                        "expected_impact": f"Recovers rows that a null-drop would remove.",
                        "risk_level": "medium",
                    }
                )

            # 3. nullable primary-key-ish column
            if column.get("is_primary_key") and column.get("is_nullable"):
                results.append(
                    {
                        "step_type": "remove_null_rows",
                        "table_name": table,
                        "configuration": {"scope": "column", "column_name": name},
                        "reason": (
                            f"{table}.{name} is a key column that allows nulls. A null "
                            f"key cannot join and cannot identify a row."
                        ),
                        "confidence": 0.88,
                        "expected_impact": "Removes rows that could never join correctly.",
                        "risk_level": "high",
                    }
                )

        # 4. duplicates on the table's key
        pk_columns = [
            c["column_name"] for c in columns_by_table.get(table, [])
            if c.get("is_primary_key")
        ]
        duplicate_pct = table_profile.get("duplicate_percentage")
        if pk_columns and (duplicate_pct is None or duplicate_pct > 0):
            results.append(
                {
                    "step_type": "remove_duplicates",
                    "table_name": table,
                    "configuration": {"scope": "columns", "columns": pk_columns},
                    "reason": (
                        f"Deduplicate {table} on its key ({', '.join(pk_columns)}) "
                        f"before joining. A duplicate key multiplies every row that "
                        f"joins to it."
                    ),
                    "confidence": 0.7,
                    "expected_impact": "Prevents silent row multiplication downstream.",
                    "risk_level": "medium",
                }
            )

    for item in results:
        assert item["step_type"] in CLEANING_STEP_TYPES, item["step_type"]
    return results


def recommend_quality_rules(
    columns_by_table: Dict[str, List[Dict[str, Any]]],
    selected_tables: Sequence[str],
) -> List[Dict[str, Any]]:
    """Quality rules drawn only from quality_rules.SUPPORTED_RULE_TYPES."""
    results: List[Dict[str, Any]] = []

    for table in selected_tables:
        columns = columns_by_table.get(table, [])
        pk_columns = [c for c in columns if c.get("is_primary_key")]

        for column in pk_columns:
            results.append(
                {
                    "rule_type": "null_percentage",
                    "severity": "error",
                    "rule_config": {
                        "table_name": table,
                        "column_name": column["column_name"],
                        "max_percentage": 0.0,
                    },
                    "reason": (
                        f"Reject the build if {table}.{column['column_name']} is ever "
                        f"null. It is the key - a null there cannot be joined or "
                        f"counted."
                    ),
                    "confidence": 0.95,
                    "risk_level": "high",
                }
            )
            results.append(
                {
                    "rule_type": "uniqueness",
                    "severity": "error",
                    "rule_config": {
                        "table_name": table,
                        "column_name": column["column_name"],
                    },
                    "reason": (
                        f"{table}.{column['column_name']} must stay unique. If it "
                        f"stops being unique, every downstream total inflates."
                    ),
                    "confidence": 0.92,
                    "risk_level": "high",
                }
            )

        for column in columns:
            if column.get("is_foreign_key"):
                results.append(
                    {
                        "rule_type": "null_percentage",
                        "severity": "warning",
                        "rule_config": {
                            "table_name": table,
                            "column_name": column["column_name"],
                            "max_percentage": 5.0,
                        },
                        "reason": (
                            f"Warn if more than 5% of {table}."
                            f"{column['column_name']} is null. A null foreign key "
                            f"means the row drops out of any inner join."
                        ),
                        "confidence": 0.8,
                        "risk_level": "medium",
                    }
                )

    for item in results:
        assert item["rule_type"] in SUPPORTED_RULE_TYPES, item["rule_type"]
    return results


def recommend_derived_columns(intent: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Derived columns the requirement implies. Suggestions only."""
    results: List[Dict[str, Any]] = []
    for item in intent.get("derived_columns") or []:
        results.append(
            {
                "name": item.get("name"),
                "expression": item.get("expression")
                or item.get("expression_hint"),
                "reason": item.get("reason")
                or "Implied by the requirement wording.",
                "confidence": float(item.get("confidence", 0.55)),
                "risk_level": "medium",
            }
        )
    return results


def recommend_filters(
    filters: Any,
    columns_by_table: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Keep only explicit, structured filters that target catalog columns."""
    operators = {"eq", "ne", "gt", "gte", "lt", "lte", "is_null", "is_not_null"}
    results: List[Dict[str, Any]] = []
    if not isinstance(filters, list):
        return results

    for item in filters:
        if not isinstance(item, dict):
            continue
        table_name = item.get("source_table")
        column_name = item.get("column")
        operator = str(item.get("operator") or "").strip().lower()
        if (
            not isinstance(table_name, str)
            or not isinstance(column_name, str)
            or operator not in operators
        ):
            continue

        table_columns = columns_by_table.get(table_name)
        if table_columns is None:
            continue
        canonical_column = next(
            (
                column.get("column_name")
                for column in table_columns
                if str(column.get("column_name", "")).casefold()
                == column_name.casefold()
            ),
            None,
        )
        if canonical_column is None:
            continue
        if operator not in {"is_null", "is_not_null"} and "value" not in item:
            continue
        if operator not in {"is_null", "is_not_null"}:
            value = item["value"]
            if value is None or not isinstance(value, (str, bool, int, float)):
                continue
            if isinstance(value, float) and not math.isfinite(value):
                continue

        result = {
            "source_table": table_name,
            "column": canonical_column,
            "operator": operator,
            "reason": str(item.get("reason") or "Explicitly stated in the requirement."),
            "confidence": float(item.get("confidence", 0.7)),
        }
        if operator not in {"is_null", "is_not_null"}:
            result["value"] = item["value"]
        results.append(result)
    return results


def recommend_aggregations(
    aggregations: Any,
    group_by: Any,
    columns_by_table: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Keep explicit, structured aggregates that resolve to catalog columns."""
    if not isinstance(aggregations, list) or not aggregations:
        return []
    if not isinstance(group_by, list):
        return []

    canonical_groups = []
    for group in group_by:
        if not isinstance(group, dict):
            return []
        table = group.get("source_table") or group.get("table_name")
        column = group.get("column") or group.get("column_name")
        available = columns_by_table.get(table, []) if isinstance(table, str) else []
        canonical = next(
            (
                item.get("column_name")
                for item in available
                if str(item.get("column_name", "")).casefold()
                == str(column or "").casefold()
            ),
            None,
        )
        if canonical is None:
            return []
        canonical_groups.append({"source_table": table, "column": canonical})

    results = []
    supported = {"SUM", "AVG", "MIN", "MAX", "COUNT", "COUNT_DISTINCT", "STDDEV"}
    for item in aggregations:
        if not isinstance(item, dict):
            continue
        table = item.get("source_table") or item.get("table_name")
        column = item.get("source_column") or item.get("column")
        function = str(item.get("function") or item.get("aggregation") or "").upper()
        name = item.get("name") or item.get("output_name")
        if function not in supported or not isinstance(name, str) or not name.strip():
            continue
        if column == "*" and function == "COUNT":
            canonical = "*"
        else:
            available = columns_by_table.get(table, []) if isinstance(table, str) else []
            canonical = next(
                (
                    spec.get("column_name")
                    for spec in available
                    if str(spec.get("column_name", "")).casefold()
                    == str(column or "").casefold()
                ),
                None,
            )
            if canonical is None:
                continue
        results.append(
            {
                "name": name.strip(),
                "function": function,
                "source_table": table,
                "source_column": canonical,
                "group_by": canonical_groups,
                "reason": str(item.get("reason") or "Explicitly requested aggregation."),
                "confidence": float(item.get("confidence", 0.7)),
                "risk_level": "medium",
            }
        )
    return results


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def build_plan(
    catalog: Dict[str, Any],
    intent: Dict[str, Any],
    profiles: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Assemble the full recommendation set for one requirement.

    catalog: {"tables": [...], "columns_by_table": {...}, "relationships": [...]}
    intent:  output of planner_agent.parse_requirement()
    """
    tables = list(catalog.get("tables") or [])
    columns_by_table = dict(catalog.get("columns_by_table") or {})
    all_relationships = list(catalog.get("relationships") or [])

    table_recs = recommend_tables(tables, intent, all_relationships)
    selected = [r["table_name"] for r in table_recs if r["classification"] == "required"]

    anchor = None
    for rec in table_recs:
        if rec["classification"] == "required":
            anchor = rec["table_name"]
            break

    relationship_recs = recommend_relationships(
        all_relationships, columns_by_table, selected
    )
    join_recs = recommend_joins(relationship_recs, columns_by_table, anchor)
    transformation_recs = recommend_transformations(
        columns_by_table, selected, profiles
    )
    quality_recs = recommend_quality_rules(columns_by_table, selected)
    derived_recs = recommend_derived_columns(intent)
    filter_recs = recommend_filters(intent.get("filters"), columns_by_table)
    aggregation_recs = recommend_aggregations(
        intent.get("aggregations"),
        intent.get("group_by"),
        columns_by_table,
    )

    return {
        "anchor_table": anchor,
        "selected_tables": selected,
        "tables": table_recs,
        "relationships": relationship_recs,
        "joins": join_recs,
        "transformations": transformation_recs,
        "quality_rules": quality_recs,
        "derived_columns": derived_recs,
        "filters": filter_recs,
        "aggregations": aggregation_recs,
    }
