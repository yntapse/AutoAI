import json
import os

from openai import OpenAI


class OpenAISpecGenerator:

    def __init__(self):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def generate_spec(
        self,
        user_request: str,
        schema: dict,
    ):

        prompt = f"""
You are an ETL Dataset Design Assistant.

USER REQUEST:
{user_request}

DATABASE SCHEMA:
{json.dumps(schema, indent=2)}

Return ONLY valid JSON.

Format:

{{
    "entity_table": "Customers",
    "entity_key": "CustomerID",
    "attributes": [
        "CustomerName",
        "City"
    ],
    "measures": [
        {{
            "measure_name": "OrderCount",
            "source_table": "Orders",
            "source_column": "OrderID",
            "aggregation": "COUNT"
        }}
    ],
    "filters": []
}}

Rules:
- Use measure_name
- Use source_table
- Use source_column
- Use aggregation
- Never use "type"
- Never use "source_key"
- source_column must be a real column from the schema
- Return ONLY JSON
"""

        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        )

        return json.loads(response.choices[0].message.content)

    def fix_spec(
        self,
        user_request: str,
        schema: dict,
        invalid_spec: dict,
        validation_errors: list,
    ):

        prompt = f"""
You generated an invalid ETL spec.

USER REQUEST:
{user_request}

DATABASE SCHEMA:
{json.dumps(schema, indent=2)}

INVALID SPEC:
{json.dumps(invalid_spec, indent=2)}

VALIDATION ERRORS:
{json.dumps(validation_errors, indent=2)}

Fix the spec.

Return ONLY valid JSON.

Format:

{{
    "entity_table": "Customers",
    "entity_key": "CustomerID",
    "attributes": [],
    "measures": [
        {{
            "measure_name": "OrderCount",
            "source_table": "Orders",
            "source_column": "OrderID",
            "aggregation": "COUNT"
        }}
    ],
    "filters": []
}}

Rules:
- Use measure_name
- Use source_table
- Use source_column
- Use aggregation
- Never use "type"
- Never use "source_key"
- Fix all validation errors
- Return ONLY JSON
"""

        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        )

        return json.loads(response.choices[0].message.content)
