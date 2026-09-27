from collections import defaultdict


class GrainCompiler:

    def compile(self, spec):

        entity_table = spec.entity_table
        entity_key = spec.entity_key

        select_parts = [f"t0.{entity_key}"]

        for attribute in spec.attributes:
            select_parts.append(f"t0.{attribute}")

        ctes = []
        join_parts = []

        measures_by_table = defaultdict(list)

        for measure in spec.measures:
            measures_by_table[measure.source_table].append(measure)

        alias_counter = 1

        for source_table, measures in measures_by_table.items():

            join_spec = None

            for j in spec.joins:
                if j.right_table == source_table:
                    join_spec = j
                    break

            if not join_spec:
                continue

            agg_selects = [f"{join_spec.right_column} AS entity_key"]

            for measure in measures:

                aggregation = measure.aggregation.lower()

                if aggregation == "sum":
                    agg_selects.append(f"SUM({measure.column}) AS {measure.name}")

                elif aggregation == "count":
                    agg_selects.append(f"COUNT({measure.column}) AS {measure.name}")

            cte_name = f"{source_table.lower()}_agg"

            ctes.append(f"""
{cte_name} AS (
    SELECT
        {', '.join(agg_selects)}
    FROM {source_table}
    GROUP BY {join_spec.right_column}
)
""".strip())

            table_alias = f"a{alias_counter}"

            join_parts.append(
                f"LEFT JOIN {cte_name} {table_alias} "
                f"ON t0.{join_spec.left_column} = {table_alias}.entity_key"
            )

            for measure in measures:
                select_parts.append(f"{table_alias}.{measure.name}")

            alias_counter += 1

        cte_sql = ",\n".join(ctes)

        sql = f"""
WITH
{cte_sql}

SELECT
    {', '.join(select_parts)}
FROM {entity_table} t0
{' '.join(join_parts)}
"""

        return sql.strip()
