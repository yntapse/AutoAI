from etl_copilot.spec_models import DatasetSpec, Measure
from etl_copilot.spec_validator import validate_spec

spec = DatasetSpec(
    entity_table="Customers",
    entity_key="CustomerID",
    attributes=["CustomerName"],
    measures=[
        Measure(
            name="total_sales",
            source_table="Orders",
            source_column="OrderAmount",
            aggregation="SUM",
        )
    ],
)

validate_spec(spec)

print("Spec validation successful")
