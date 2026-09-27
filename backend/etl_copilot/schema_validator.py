class SchemaValidator:

    def validate(self, spec, schema):

        errors = []

        tables = schema.get("tables", {})

        entity_table = spec.get("entity_table")

        if entity_table not in tables:
            errors.append(f"Table '{entity_table}' not found")

        for measure in spec.get("measures", []):

            source_table = measure.get("source_table")

            if source_table not in tables:
                errors.append(f"Table '{source_table}' not found")
                continue

            source_column = measure.get("column") or measure.get("source_column")

            available_columns = tables[source_table]

            if source_column not in available_columns:
                errors.append(f"Column '{source_column}' not found in '{source_table}'")

        return errors
