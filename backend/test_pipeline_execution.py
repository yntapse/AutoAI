import unittest

from services.pipeline_execution import (
    PipelineExecutionError,
    build_execution_graph,
    compile_saved_pipeline_sql,
)


class PipelineExecutionTests(unittest.TestCase):
    def setUp(self):
        self.base_sql = (
            'SELECT t0."loan_id" AS "loan_id", '
            't1."amount" AS "amount", t1."status" AS "status" '
            'FROM "public"."loans" AS t0 LEFT JOIN "public"."payments" AS t1 '
            'ON t0."loan_id" = t1."loan_id"'
        )
        self.alias_map = {
            "loan_id": "loans.loan_id",
            "amount": "payments.amount",
            "status": "payments.status",
        }

    def test_filters_and_grouped_aggregate_compile_with_bound_values(self):
        sql, params = compile_saved_pipeline_sql(
            "postgresql",
            self.base_sql,
            self.alias_map,
            {
                "filters": [
                    {
                        "source_table": "payments",
                        "column": "status",
                        "operator": "eq",
                        "value": "paid",
                    }
                ],
                "group_by": [{"source_table": "loans", "column": "loan_id"}],
                "aggregations": [
                    {
                        "name": "paid_total",
                        "function": "SUM",
                        "source_table": "payments",
                        "source_column": "amount",
                    }
                ],
            },
        )
        self.assertIn("WHERE src.\"status\" = :pipeline_filter_0", sql)
        self.assertIn('SUM(agg_src."amount") AS "paid_total"', sql)
        self.assertIn('GROUP BY agg_src."loan_id"', sql)
        self.assertEqual(params, {"pipeline_filter_0": "paid"})

    def test_safe_arithmetic_derived_column_compiles(self):
        sql, params = compile_saved_pipeline_sql(
            "mssql",
            self.base_sql,
            self.alias_map,
            {
                "derived_columns": [
                    {
                        "name": "amount_ratio",
                        "expression_hint": "payments.amount / NULLIF(payments.amount, 0)",
                    }
                ]
            },
        )
        self.assertIn(
            "derived_src.[amount] / NULLIF(derived_src.[amount], 0)",
            sql,
        )
        self.assertIn("AS [amount_ratio]", sql)
        self.assertEqual(params, {})

    def test_unsupported_pseudo_sql_fails_closed(self):
        with self.assertRaisesRegex(PipelineExecutionError, "invalid expression syntax"):
            compile_saved_pipeline_sql(
                "postgresql",
                self.base_sql,
                self.alias_map,
                {
                    "derived_columns": [
                        {
                            "name": "monthly_total",
                            "expression_hint": "SUM(amount) grouped by month",
                        }
                    ]
                },
            )

    def test_joined_child_aggregation_is_allowed_but_parent_measure_is_rejected(self):
        join = {
            "left_table": "payments",
            "left_column": "loan_id",
            "right_table": "loans",
            "right_column": "loan_id",
            "cardinality": "M:1",
        }
        child_aggregate = {
            "base_table": "loans",
            "tables": ["loans", "payments"],
            "joins": [join],
            "aggregations": [
                {
                    "name": "total_amount",
                    "function": "SUM",
                    "source_table": "payments",
                    "source_column": "amount",
                }
            ],
            "group_by": [{"source_table": "loans", "column": "loan_id"}],
        }
        sql, _ = compile_saved_pipeline_sql(
            "postgresql",
            self.base_sql,
            self.alias_map,
            child_aggregate,
        )
        self.assertIn("SUM(agg_src.", sql)

        unsafe = dict(child_aggregate)
        unsafe["aggregations"] = [
            {
                "name": "loan_amount",
                "function": "SUM",
                "source_table": "loans",
                "source_column": "amount",
            }
        ]
        with self.assertRaisesRegex(PipelineExecutionError, "one-side"):
            compile_saved_pipeline_sql(
                "postgresql",
                self.base_sql,
                self.alias_map,
                unsafe,
            )

    def test_execution_graph_has_stable_dependencies(self):
        graph = build_execution_graph(
            {
                "filters": [{}],
                "derived_columns": [{}],
                "aggregations": [{}],
                "cleaning_steps": [{}],
                "quality_rules": [{}],
            }
        )
        self.assertEqual(
            [item["stage"] for item in graph],
            [
                "extract_and_join",
                "filters",
                "derived_columns",
                "aggregations",
                "cleaning",
                "quality_validation",
                "register_dataset",
            ],
        )
        self.assertEqual(graph[-1]["depends_on"], ["quality_validation"])


if __name__ == "__main__":
    unittest.main()
