from etl_copilot.schema_validator import SchemaValidator

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

spec = {
    "entity_table": "Customers",
    "measures": [
        {
            "source_table": "Orders",
            "source_column": "OrderAmount",
        }
    ],
}

validator = SchemaValidator()

errors = validator.validate(
    spec,
    schema,
)

print(errors)

