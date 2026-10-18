"""Semantic diff between two saved pipeline versions. Feature 8 of Phase 4.

routers/pipelines.py already has a compare endpoint, but it reports which
top-level JSON keys differ - which tells a reviewer that "joins changed"
without saying which join. This module produces the diff a reviewer can act
on: which joins, rules, columns and tables were added or removed.

PURE. No database, no network, no LLM.
"""

from typing import Any, Dict, List, Sequence

# Each section: the pipeline_json key, and how to build a stable identity for
# one item so the same join compares equal across versions.
_SECTIONS = {
    "tables": None,  # plain list of names
    "joins": ("left_table", "left_column", "right_table", "right_column"),
    "relationships": ("left_table", "left_column", "right_table", "right_column"),
    "cleaning_steps": ("rule_type", "step_order"),
    "quality_rules": ("rule_type", "severity"),
    "derived_columns": ("name",),
}


def _identity(item: Any, keys: Sequence[str]) -> str:
    if not isinstance(item, dict):
        return str(item)
    parts = [str(item.get(key, "")) for key in keys]
    # rule_config holds the table/column a rule applies to; include it so two
    # rules of the same type on different columns are not treated as one.
    config = item.get("rule_config")
    if isinstance(config, dict):
        parts.append(str(config.get("table_name", "")))
        parts.append(str(config.get("column_name", "")))
    table = item.get("table_name")
    if table and "table_name" not in keys:
        parts.append(str(table))
    return "|".join(parts)


def _index(items: Any, keys: Sequence[str]) -> Dict[str, Any]:
    if not isinstance(items, list):
        return {}
    indexed: Dict[str, Any] = {}
    for item in items:
        key = _identity(item, keys or ())
        # A genuine duplicate gets a suffix so neither copy is lost.
        if key in indexed:
            suffix = 2
            while f"{key}#{suffix}" in indexed:
                suffix += 1
            key = f"{key}#{suffix}"
        indexed[key] = item
    return indexed


def _label(section: str, item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)
    if section in ("joins", "relationships"):
        join_type = item.get("join_type")
        suffix = f" [{join_type.upper()}]" if join_type else ""
        return (
            f"{item.get('left_table')}.{item.get('left_column')} -> "
            f"{item.get('right_table')}.{item.get('right_column')}{suffix}"
        )
    if section == "cleaning_steps":
        config = item.get("rule_config") or {}
        return (
            f"{item.get('rule_type')} on "
            f"{config.get('column_name') or item.get('table_name') or 'table'}"
        )
    if section == "quality_rules":
        config = item.get("rule_config") or {}
        return (
            f"{item.get('rule_type')} ({item.get('severity')}) on "
            f"{config.get('table_name')}.{config.get('column_name') or '*'}"
        )
    if section == "derived_columns":
        return str(item.get("name"))
    return str(item)


def diff_pipeline_json(
    before: Dict[str, Any], after: Dict[str, Any]
) -> Dict[str, Any]:
    """Added / removed / changed, per section, with readable labels."""
    before = before or {}
    after = after or {}

    sections: Dict[str, Any] = {}
    totals = {"added": 0, "removed": 0, "changed": 0}

    for section, keys in _SECTIONS.items():
        old = _index(before.get(section), keys)
        new = _index(after.get(section), keys)

        added_keys = [key for key in new if key not in old]
        removed_keys = [key for key in old if key not in new]
        changed_keys = [
            key for key in new if key in old and new[key] != old[key]
        ]

        sections[section] = {
            "added": [_label(section, new[key]) for key in added_keys],
            "removed": [_label(section, old[key]) for key in removed_keys],
            "changed": [
                {
                    "item": _label(section, new[key]),
                    "before": old[key],
                    "after": new[key],
                }
                for key in changed_keys
            ],
            "unchanged": len(
                [key for key in new if key in old and new[key] == old[key]]
            ),
        }
        totals["added"] += len(added_keys)
        totals["removed"] += len(removed_keys)
        totals["changed"] += len(changed_keys)

    scalar_changes = {}
    for key in ("user_request", "intent", "source", "schema_version"):
        if before.get(key) != after.get(key):
            scalar_changes[key] = {
                "before": before.get(key),
                "after": after.get(key),
            }

    return {
        "sections": sections,
        "scalar_changes": scalar_changes,
        "totals": totals,
        "summary": _summary(sections, totals),
    }


def _summary(sections: Dict[str, Any], totals: Dict[str, int]) -> List[str]:
    lines: List[str] = []
    for section, payload in sections.items():
        readable = section.replace("_", " ")
        if payload["added"]:
            lines.append(f"{len(payload['added'])} {readable} added")
        if payload["removed"]:
            lines.append(f"{len(payload['removed'])} {readable} removed")
        if payload["changed"]:
            lines.append(f"{len(payload['changed'])} {readable} changed")
    if not lines:
        lines.append("No structural difference between these two versions.")
    return lines
