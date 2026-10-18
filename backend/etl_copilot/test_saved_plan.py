import unittest
from unittest.mock import Mock

from etl_copilot.copilot_service import ETLCopilot
from etl_copilot.spec_models import Catalog, ColumnInfo, JoinHop, TableInfo


class SavedPlanExecutionTests(unittest.TestCase):
    def test_saved_spec_is_verified_compiled_and_built_without_llm(self):
        catalog = Catalog(
            db_type="postgresql",
            schema_name="public",
            tables=[
                TableInfo(
                    name="customers",
                    columns=[
                        ColumnInfo(name="id", type="INTEGER", primary_key=True),
                        ColumnInfo(name="name", type="VARCHAR(100)"),
                        ColumnInfo(name="city", type="VARCHAR(50)"),
                    ],
                ),
                TableInfo(
                    name="orders",
                    columns=[
                        ColumnInfo(name="order_id", type="INTEGER", primary_key=True),
                        ColumnInfo(name="customer_id", type="INTEGER"),
                        ColumnInfo(name="amount", type="NUMERIC"),
                    ],
                ),
            ],
            relationships=[
                JoinHop(
                    left_table="customers",
                    left_column="id",
                    right_table="orders",
                    right_column="customer_id",
                )
            ],
        )
        saved_spec = {
            "entity_table": "customers",
            "entity_key": ["id"],
            "attributes": [
                {
                    "source_table": "customers",
                    "source_column": "name",
                }
            ],
            "measures": [
                {
                    "name": "total_amount",
                    "source_table": "orders",
                    "source_column": "amount",
                    "aggregation": "SUM",
                    "distinct_key": ["order_id"],
                }
            ],
            "filters": [
                {
                    "source_table": "customers",
                    "column": "city",
                    "operator": "eq",
                    "value": "Oslo",
                }
            ],
            "join_paths": [
                {
                    "entity_table": "customers",
                    "source_table": "orders",
                    "hops": [
                        {
                            "left_table": "customers",
                            "left_column": "id",
                            "right_table": "orders",
                            "right_column": "customer_id",
                            "join_type": "left",
                            "cardinality": "one_to_many",
                            "match_rate": 100.0,
                            "row_multiplication": True,
                        }
                    ],
                    "verified": True,
                    "discovered_via": "foreign_key",
                    "alternatives_considered": 1,
                }
            ],
        }
        build = Mock(return_value={"frame": "built-frame"})
        generator = Mock()
        verifier = Mock()
        verifier.verify_all.side_effect = lambda _connection, _db_type, _schema, paths: (
            paths,
            [],
        )
        copilot = ETLCopilot(
            generator=generator,
            build_service=Mock(build=build),
        )
        copilot.schema_reader.get_schema = Mock(return_value=catalog)
        copilot.verifier = verifier

        result = copilot.create_dataset_from_spec(
            saved_spec=saved_spec,
            connection_string="postgresql://test",
            schema="public",
            dataset_name="saved-pipeline",
            persist=False,
        )

        generator.generate_spec.assert_not_called()
        generator.fix_spec.assert_not_called()
        verifier.verify_all.assert_called_once()
        build.assert_called_once()
        compiled_plan = build.call_args.kwargs["plan"]
        self.assertIn("customers", compiled_plan.sql)
        self.assertIn("orders", compiled_plan.sql)
        self.assertIn("city", compiled_plan.sql)
        self.assertIn("Oslo", compiled_plan.params.values())
        self.assertEqual(result["frame"], "built-frame")


if __name__ == "__main__":
    unittest.main()
