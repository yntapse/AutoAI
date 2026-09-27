import os
import sys

if not os.getenv("OPENAI_API_KEY"):
    print("SKIP: OPENAI_API_KEY is not set (live LLM test).")
    sys.exit(0)

from etl_copilot.openai_spec_generator import OpenAISpecGenerator

schema = {
    "tables": {
        "Customers": ["CustomerID", "CustomerName", "City"],
        "Orders": ["OrderID", "CustomerID", "OrderAmount"],
    },
    "relationships": [
        {
            "left_table": "Customers",
            "left_column": "CustomerID",
            "right_table": "Orders",
            "right_column": "CustomerID",
        }
    ],
}

generator = OpenAISpecGenerator()

result = generator.generate_spec(
    user_request="Create customer sales dataset with order count",
    schema=schema,
)

print(result)
