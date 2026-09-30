"""Lineage and explanation rendering.

The compiler emits MeasureLineage records; this module turns them into human
sentences and an audit payload. Requirements 7-9 of the architecture document
(explainable output, traceable measures, traceable joins) are satisfied here.
"""

from typing import Any, Dict, List

from etl_copilot.spec_models import CompiledPlan, DatasetSpec


def explain_dataset(spec: DatasetSpec, plan: CompiledPlan) -> List[str]:
    """Plain sentences a non-technical user can confirm or reject."""

    lines: List[str] = []

    lines.append(
        "One row per %s (%s)."
        % (spec.entity_table, ", ".join(spec.entity_key))
    )

    for attribute in spec.attributes:
        lines.append(
            "%s comes directly from %s.%s."
            % (
                attribute.name or attribute.source_column,
                attribute.source_table,
                attribute.source_column,
            )
        )

    for record in plan.lineage:
        lines.append(record.explanation)

        if record.nested_aggregation:
            lines.append(
                "  %s crosses %d tables, so it is aggregated in two steps to "
                "avoid double counting."
                % (record.measure_name, len(record.join_path) - 1)
            )

        if not record.path_verified:
            lines.append(
                "  WARNING: the join path for %s was not verified against the "
                "data." % record.measure_name
            )

    return lines


def audit_payload(
    spec: DatasetSpec,
    plan: CompiledPlan,
    include_sql: bool = True,
) -> Dict[str, Any]:
    """The structured record to persist alongside the dataset."""

    payload = {
        "spec_version": plan.spec_version,
        "entity_table": spec.entity_table,
        "entity_key": list(spec.entity_key),
        "declared_grain": list(plan.declared_grain),
        "tables_referenced": list(plan.tables_referenced),
        "output_columns": list(plan.output_columns),
        "measures": [
            {
                "name": record.measure_name,
                "source": record.source,
                "aggregation": record.aggregation,
                "join_path": record.join_path,
                "path_verified": record.path_verified,
                "cardinality_per_hop": record.cardinality_per_hop,
                "nested_aggregation": record.nested_aggregation,
                "zero_when_absent": record.zero_when_absent,
                "explanation": record.explanation,
            }
            for record in plan.lineage
        ],
        "join_paths": [
            {
                "source_table": path.source_table,
                "hops": [
                    "%s.%s -> %s.%s"
                    % (hop.left_table, hop.left_column, hop.right_table, hop.right_column)
                    for hop in path.hops
                ],
                "verified": path.verified,
                "discovered_via": path.discovered_via,
                "alternatives_considered": path.alternatives_considered,
            }
            for path in spec.join_paths
        ],
    }
    if include_sql:
        payload["sql"] = plan.sql
    return payload
