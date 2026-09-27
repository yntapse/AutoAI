from etl_copilot.openai_spec_generator import OpenAISpecGenerator

generator = OpenAISpecGenerator()

schema = {
    "tables": {
        "Customers": [
            "CustomerID",
            "CustomerName",
            "City",
        ],
        "Orders": [
            "OrderID",
            "CustomerID",
            "OrderAmount",
        ],
    }
}

invalid_spec = {
    "entity_table": "CustomerMaster",
    "entity_key": "CustomerID",
    "attributes": [
        "CustomerName",
    ],
    "measures": [
        {
            "source_table": "SalesOrders",
            "source_column": "Amount",
            "aggregation": "COUNT",
        }
    ],
    "filters": [],
}

errors = [
    "Table 'CustomerMaster' not found",
    "Table 'SalesOrders' not found",
]

result = generator.fix_spec(
    user_request="Show customer order count",
    schema=schema,
    invalid_spec=invalid_spec,
    validation_errors=errors,
)

print(result)
