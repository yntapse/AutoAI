import json
import os

from openai import OpenAI


def resolve_api_key():
    """Read and clean OPENAI_API_KEY.

    The raw value is stripped because a key set with Windows `set` or pasted
    into a .env file often carries trailing whitespace, which the API rejects
    as an incorrect key. main.py already strips it the same way (see
    main.py:188); this keeps the ETL Copilot consistent with that.
    """
    raw = os.getenv("OPENAI_API_KEY")

    if raw is None:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Set it in the shell or in backend/.env."
        )

    key = raw.strip()

    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is set but empty after stripping whitespace."
        )

    return key


def key_fingerprint(key):
    """Non-secret identity of a key, safe to log: prefix, length, head, tail."""
    return {
        "length": len(key),
        "head": key[:15],
        "tail": key[-15:],
        "prefix": key.split("-")[0] + "-" + (key.split("-")[1] if "-" in key[3:] else ""),
        "had_surrounding_whitespace": key != (os.getenv("OPENAI_API_KEY") or ""),
    }


# The system message carries the literal lowercase word "json", which OpenAI
# requires somewhere in the messages before it will accept
# response_format={"type": "json_object"}. The user prompts below say "JSON"
# in uppercase only, which does not satisfy that check.
SYSTEM_MESSAGE = (
    "You are an ETL Dataset Design Assistant. "
    "You work only from the catalog you are given: every table name and every "
    "column name you emit must be copied verbatim from that catalog. "
    "Reply with a single json object and nothing else."
)


def _allowed_tables_block(schema):
    """The catalog's real table names, listed on their own.

    The Format example below cannot be written without SOME names in it, and a
    model asked to fill a template reaches for the names it just read in that
    template. Listing the real names separately, immediately before the rules,
    gives it something closer and more authoritative to copy. The example's own
    names are placeholders precisely so that copying them is visibly wrong.
    """
    tables = schema.get("tables") or {}
    primary_keys = schema.get("primary_keys") or {}

    lines = []
    for name in tables:
        keys = primary_keys.get(name) or []
        if keys:
            lines.append("- %s   (primary key: %s)" % (name, ", ".join(keys)))
        else:
            lines.append("- %s" % name)

    return "\n".join(lines) if lines else "(the catalog is empty)"


NAME_RULES = """- entity_table and every source_table must be copied character for character
  from ALLOWED TABLE NAMES above. Do not shorten, expand, tidy, translate or
  invent a table name, and never use a name from the Format example.
- Every column name must be copied character for character from the column
  list of that same table in DATABASE SCHEMA.
- The user request describes what a column MEANS, not how it is spelled. Map
  "customer name" onto whichever column of the chosen table actually holds it.
- If the request asks for something the catalog does not contain, leave it out
  rather than inventing a table or a column for it."""

SHAPE_RULES = """- Use measure_name, source_table, source_column, aggregation
- Never use "type"
- Never use "source_key"
- aggregation must be one of: SUM, AVG, MIN, MAX, COUNT, COUNT_DISTINCT,
  STDDEV  (MEDIAN is not supported)
- attributes must be columns of entity_table only
- filters is a list of {"column", "operator", "value"}
- operator must be one of: eq, ne, gt, gte, lt, lte, is_null, is_not_null
- omit "value" only for is_null and is_not_null
- to rank, set "order_by" to an OUTPUT name (a measure_name, an attribute
  column, or entity_key) and "order_direction" to "asc" or "desc"
- there is no row limit: never emit "limit", "top" or "offset". Order the
  rows instead and the whole dataset comes back sorted.
- Return ONLY JSON"""

FORMAT_EXAMPLE = """{
    "entity_table": "<a name from ALLOWED TABLE NAMES>",
    "entity_key": "<the primary key column of that table>",
    "attributes": [
        "<a column of that table>",
        "<another column of that table>"
    ],
    "measures": [
        {
            "measure_name": "<a label you choose>",
            "source_table": "<a name from ALLOWED TABLE NAMES>",
            "source_column": "<a column of that table>",
            "aggregation": "COUNT"
        }
    ],
    "filters": []
}"""


class OpenAISpecGenerator:

    def __init__(self, api_key=None):
        self.api_key = api_key or resolve_api_key()
        self.client = OpenAI(api_key=self.api_key)

    def fingerprint(self):
        """What key this generator is actually using. For diagnostics."""
        return key_fingerprint(self.api_key)

    def _complete(self, prompt):
        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_MESSAGE},
                {"role": "user", "content": prompt},
            ],
        )

        return json.loads(response.choices[0].message.content)

    def generate_spec(
        self,
        user_request: str,
        schema: dict,
    ):

        prompt = """
You are an ETL Dataset Design Assistant.

USER REQUEST:
%s

DATABASE SCHEMA:
%s

ALLOWED TABLE NAMES (these are the only table names that exist):
%s

Return ONLY valid JSON.

Format - every value in angle brackets is a PLACEHOLDER to be replaced, never
copied:

%s

Rules:
%s
%s
""" % (
            user_request,
            json.dumps(schema, indent=2),
            _allowed_tables_block(schema),
            FORMAT_EXAMPLE,
            NAME_RULES,
            SHAPE_RULES,
        )

        return self._complete(prompt)

    def fix_spec(
        self,
        user_request: str,
        schema: dict,
        invalid_spec: dict,
        validation_errors: list,
    ):

        prompt = """
You produced an ETL spec that was rejected.

USER REQUEST:
%s

DATABASE SCHEMA:
%s

ALLOWED TABLE NAMES (these are the only table names that exist):
%s

THE SPEC THAT WAS REJECTED:
%s

WHY IT WAS REJECTED:
%s

Fix every one of those errors. An "unknown table" or "unknown column" error
means you used a name that is not in the catalog above - replace it with the
real name, do not rename it to something else you invented.

Return ONLY valid JSON.

Format - every value in angle brackets is a PLACEHOLDER to be replaced, never
copied:

%s

Rules:
%s
%s
""" % (
            user_request,
            json.dumps(schema, indent=2),
            _allowed_tables_block(schema),
            json.dumps(invalid_spec, indent=2, default=str),
            json.dumps(validation_errors, indent=2, default=str),
            FORMAT_EXAMPLE,
            NAME_RULES,
            SHAPE_RULES,
        )

        return self._complete(prompt)
