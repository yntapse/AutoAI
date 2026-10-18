"""Planner agent: turns a business requirement into a structured intent.

This is the ONLY place a model sees the requirement. It receives the sentence
plus table and column NAMES - never a row value. It returns an intent object
that services/plan_builder.py consumes deterministically.

If no API key is configured, or the model fails or returns malformed JSON, the
heuristic parser runs instead. The builder therefore always has an intent and
the whole pipeline-builder flow works offline - which is also what makes it
testable without a network.
"""

import json
import logging
import math
import re
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "build", "create", "prepare", "make", "generate", "dataset", "data", "the",
    "and", "for", "with", "from", "into", "that", "this", "please", "need",
    "want", "would", "like", "should", "about", "using", "all", "our", "set",
}

# requirement phrase -> (anchor entity, extra keywords, derived column hints)
_PATTERNS = (
    (
        ("churn", "attrition", "retention"),
        "customer",
        ["customer", "transaction", "complaint", "account", "activity"],
        [
            {"name": "days_since_last_transaction",
             "expression_hint": "DATEDIFF(DAY, MAX(transaction_date), CURRENT_DATE)",
             "reason": "Churn is defined by inactivity, so recency is the primary signal."},
            {"name": "transaction_count_90d",
             "expression_hint": "COUNT(transaction_id) WHERE last 90 days",
             "reason": "Activity volume separates a dormant customer from a quiet one."},
        ],
    ),
    (
        ("fraud", "aml", "suspicious"),
        "transaction",
        ["transaction", "account", "customer", "channel", "merchant"],
        [
            {"name": "amount_vs_customer_mean",
             "expression_hint": "txn_amount / NULLIF(AVG(txn_amount) OVER (PARTITION BY customer_id), 0)",
             "reason": "Fraud shows as a deviation from the customer's own baseline, not an absolute amount."},
            {"name": "txn_velocity_1h",
             "expression_hint": "COUNT(transaction_id) OVER (PARTITION BY account_id, 1 hour)",
             "reason": "Rapid sequences are a stronger signal than any single transaction."},
        ],
    ),
    (
        ("360", "single view", "customer view", "master"),
        "customer",
        ["customer", "account", "loan", "transaction", "address", "contact"],
        [
            {"name": "relationship_tenure_months",
             "expression_hint": "DATEDIFF(MONTH, onboard_date, CURRENT_DATE)",
             "reason": "Tenure distinguishes a genuinely new customer from a reactivated one."},
        ],
    ),
    (
        ("credit", "risk", "default", "delinquen", "npa"),
        "loan",
        ["loan", "repayment", "customer", "collateral", "emi"],
        [
            {"name": "missed_emi_count",
             "expression_hint": "COUNT(*) WHERE paid_date > due_date + 30",
             "reason": "Direct behavioural signal of distress."},
            {"name": "loan_to_income_ratio",
             "expression_hint": "sanctioned_amount / NULLIF(declared_income, 0)",
             "reason": "Strongest single predictor of delinquency in retail portfolios."},
        ],
    ),
    (
        ("sales", "revenue", "forecast", "demand"),
        "order",
        ["order", "sale", "product", "customer", "invoice", "region"],
        [
            {"name": "monthly_revenue",
             "expression_hint": "SUM(amount) grouped by month",
             "reason": "Forecasting needs a regular time grain."},
        ],
    ),
)


def _keywords(requirement: str) -> List[str]:
    cleaned = "".join(
        ch.lower() if ch.isalnum() else " " for ch in str(requirement or "")
    )
    words = [w for w in cleaned.split() if len(w) > 2 and w not in _STOPWORDS]
    singular = []
    for word in words:
        if word.endswith("ies") and len(word) > 4:
            singular.append(word[:-3] + "y")
        elif word.endswith("s") and not word.endswith("ss"):
            singular.append(word[:-1])
        else:
            singular.append(word)
    return sorted(set(words) | set(singular))


def parse_requirement_heuristic(
    requirement: str,
    table_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Deterministic requirement parser. Always available, never fails."""
    lowered = str(requirement or "").lower()
    keywords = _keywords(requirement)
    anchor = None
    derived: List[Dict[str, Any]] = []
    use_case = "general"

    for triggers, anchor_entity, extra, derived_hints in _PATTERNS:
        if any(trigger in lowered for trigger in triggers):
            anchor = anchor_entity
            use_case = triggers[0]
            keywords = sorted(set(keywords) | set(extra))
            derived = [dict(item, confidence=0.6) for item in derived_hints]
            break

    if anchor is None and table_names:
        for name in table_names:
            if any(token in name.lower() for token in keywords):
                anchor = name.lower()
                break

    return {
        "requirement": requirement,
        "use_case": use_case,
        "anchor_entity": anchor,
        "keywords": keywords,
        "derived_columns": derived,
        "filters": [],
        "group_by": [],
        "aggregations": [],
        "origin": "heuristic",
    }


_PROMPT = """You are the planning step of a data engineering platform.

Available tables and columns (names only):
{columns}

A user wrote this requirement:
{requirement}

Return STRICT JSON, no prose, with exactly these keys:
{{
  "use_case": "<short slug>",
  "anchor_entity": "<the one entity the dataset has one row per, lowercase>",
  "keywords": ["<words that should match table names>"],
  "filters": [
     {{"source_table": "<table>", "column": "<column>", "operator": "eq|ne|gt|gte|lt|lte|is_null|is_not_null", "value": <JSON scalar>, "reason": "<why>"}}
  ],
  "group_by": [
     {{"source_table": "<table>", "column": "<column>"}}
  ],
  "aggregations": [
     {{"name": "<output_name>", "function": "SUM|AVG|MIN|MAX|COUNT|COUNT_DISTINCT|STDDEV", "source_table": "<table>", "source_column": "<column or * for COUNT>", "reason": "<why>"}}
  ],
  "derived_columns": [
     {{"name": "<snake_case>", "expression": "<arithmetic over listed columns; NULLIF is allowed>", "reason": "<why>"}}
  ]
}}

Rules:
- Use only the table and column names given. Invent nothing.
- Include filters only when the requirement explicitly states a condition. Never
  invent thresholds or infer a filter from domain conventions. Use only the
  listed operators; is_null and is_not_null do not need a value.
- keywords should be single lowercase words.
- At most 5 derived_columns.
- Include aggregations and group_by only when explicitly requested. Every
  aggregation must use a listed source column; COUNT may use "*" when the
  requirement explicitly asks to count rows. Never invent a group-by grain.
- Include group_by entries only for explicit grouping dimensions.
- Use only the listed aggregation functions. Do not propose date bucketing,
  window functions, or other expressions as aggregations.
- Derived expressions may use only column references, numeric constants,
  parentheses, +, -, *, /, %, unary signs, and NULLIF(value, value). Use
  table.column references when column names are ambiguous. Do not use SQL
  statements, dates, aggregate functions, or window functions here.
- You are given schema names only. You have no row data and must not ask for any."""


def parse_requirement(
    requirement: str,
    table_names: Optional[Sequence[str]] = None,
    client: Any = None,
    columns_by_table: Optional[Dict[str, Sequence[Any]]] = None,
) -> Dict[str, Any]:
    """Intent for a requirement. Uses the model when available, else heuristics.

    `client` is injectable so tests never touch the network.
    """
    fallback = parse_requirement_heuristic(requirement, table_names)

    generator = client
    if generator is None:
        try:
            from etl_copilot.openai_spec_generator import OpenAISpecGenerator

            generator = OpenAISpecGenerator()
        except Exception as exc:  # no key, no package, bad config
            logger.info("Planner agent falling back to heuristics: %s", exc)
            return fallback

    prompt_columns = columns_by_table or {
        table: [] for table in (table_names or [])
    }
    prompt = _PROMPT.format(
        requirement=str(requirement or "").strip(),
        columns=json.dumps(
            {
                table: [
                    str(column.get("column_name"))
                    if isinstance(column, dict)
                    else str(column)
                    for column in columns
                ]
                for table, columns in prompt_columns.items()
            },
            sort_keys=True,
        )
        or "(none)",
    )

    try:
        raw = generator._complete(prompt)  # noqa: SLF001 - the generator's own entry point
    except Exception as exc:
        logger.warning("Planner agent model call failed: %s", exc)
        return fallback

    parsed = _extract_json(raw)
    if not parsed:
        logger.warning("Planner agent returned unparseable JSON; using heuristics.")
        return fallback

    allowed = {str(name).lower() for name in (table_names or [])}
    keywords = [
        str(word).lower()
        for word in (parsed.get("keywords") or [])
        if isinstance(word, (str, int))
    ]
    derived = []
    for item in (parsed.get("derived_columns") or [])[:5]:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        derived.append(
            {
                "name": str(item["name"]),
                "expression": str(
                    item.get("expression") or item.get("expression_hint") or ""
                ),
                "reason": str(item.get("reason") or "Proposed by the planner."),
                "confidence": 0.7,
            }
        )

    allowed_by_lower = {str(name).lower(): str(name) for name in (table_names or [])}
    allowed_operators = {
        "eq", "ne", "gt", "gte", "lt", "lte", "is_null", "is_not_null"
    }
    filters = []
    for item in (parsed.get("filters") or [])[:20]:
        if not isinstance(item, dict):
            continue
        source_table = item.get("source_table")
        column = item.get("column")
        operator = str(item.get("operator") or "").strip().lower()
        if (
            not isinstance(source_table, str)
            or source_table.lower() not in allowed_by_lower
            or not isinstance(column, str)
            or not column.strip()
            or operator not in allowed_operators
        ):
            continue
        canonical_table = allowed_by_lower[source_table.lower()]
        table_columns = (columns_by_table or {}).get(canonical_table)
        if columns_by_table is not None and table_columns is not None:
            available_columns = {
                (
                    str(column.get("column_name"))
                    if isinstance(column, dict)
                    else str(column)
                ).casefold()
                for column in table_columns
            }
            if column.casefold() not in available_columns:
                continue
        normalized = {
            "source_table": canonical_table,
            "column": column.strip(),
            "operator": operator,
            "reason": str(item.get("reason") or "Explicitly stated in the requirement."),
            "confidence": 0.7,
        }
        if operator not in {"is_null", "is_not_null"}:
            if "value" not in item:
                continue
            value = item["value"]
            if value is None or not isinstance(value, (str, bool, int, float)):
                continue
            if isinstance(value, float) and not math.isfinite(value):
                continue
            normalized["value"] = value
        filters.append(normalized)

    group_by = []
    for item in (parsed.get("group_by") or [])[:20]:
        if not isinstance(item, dict):
            continue
        source_table = item.get("source_table")
        column = item.get("column")
        if (
            not isinstance(source_table, str)
            or source_table.lower() not in allowed_by_lower
            or not isinstance(column, str)
            or not column.strip()
        ):
            continue
        canonical_table = allowed_by_lower[source_table.lower()]
        table_columns = (columns_by_table or {}).get(canonical_table)
        if columns_by_table is not None and table_columns is not None:
            allowed_columns = {
                (
                    str(spec.get("column_name"))
                    if isinstance(spec, dict)
                    else str(spec)
                ).casefold()
                for spec in table_columns
            }
            if column.casefold() not in allowed_columns:
                continue
        group_by.append({"source_table": canonical_table, "column": column.strip()})

    aggregations = []
    allowed_aggregations = {
        "SUM", "AVG", "MIN", "MAX", "COUNT", "COUNT_DISTINCT", "STDDEV"
    }
    for item in (parsed.get("aggregations") or [])[:20]:
        if not isinstance(item, dict):
            continue
        source_table = item.get("source_table")
        source_column = item.get("source_column") or item.get("column")
        name = item.get("name") or item.get("output_name")
        function = str(item.get("function") or item.get("aggregation") or "").upper()
        if (
            not isinstance(source_table, str)
            or source_table.lower() not in allowed_by_lower
            or not isinstance(name, str)
            or not name.strip()
            or function not in allowed_aggregations
            or not isinstance(source_column, str)
        ):
            continue
        canonical_table = allowed_by_lower[source_table.lower()]
        if source_column == "*" and function == "COUNT":
            canonical_column = "*"
        else:
            table_columns = (columns_by_table or {}).get(canonical_table)
            allowed_columns = {
                (
                    str(spec.get("column_name"))
                    if isinstance(spec, dict)
                    else str(spec)
                ).casefold()
                for spec in table_columns or []
            }
            if columns_by_table is not None and source_column.casefold() not in allowed_columns:
                continue
            canonical_column = source_column
        aggregations.append(
            {
                "name": name.strip(),
                "function": function,
                "source_table": canonical_table,
                "source_column": canonical_column,
                "reason": str(item.get("reason") or "Explicitly requested aggregation."),
            }
        )

    anchor = str(parsed.get("anchor_entity") or "").lower() or fallback["anchor_entity"]
    if allowed and anchor and not any(anchor in name for name in allowed):
        # The model named something that is not in the schema. Do not trust it.
        anchor = fallback["anchor_entity"]

    return {
        "requirement": requirement,
        "use_case": str(parsed.get("use_case") or fallback["use_case"]),
        "anchor_entity": anchor,
        "keywords": sorted(set(keywords) | set(fallback["keywords"])),
        "derived_columns": derived or fallback["derived_columns"],
        "filters": filters,
        "group_by": group_by,
        "aggregations": aggregations,
        "origin": "model",
    }


def _extract_json(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except Exception:
        return None
    return value if isinstance(value, dict) else None
