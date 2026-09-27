from typing import Optional

from services.db_connector import (
    DataSourceError,
    default_schema,
    list_columns,
    list_foreign_keys,
    list_tables,
)

from etl_copilot.spec_models import (
    Catalog,
    ColumnInfo,
    JoinSpec,
    TableInfo,
)


class SchemaReader:

    def get_schema(
        self,
        connection_string: str,
        schema: Optional[str] = None,
    ) -> Catalog:

        connection_lower = connection_string.lower()

        if connection_lower.startswith("mssql"):
            db_type = "mssql"
        elif connection_lower.startswith("postgresql"):
            db_type = "postgresql"
        else:
            raise DataSourceError(
                "Unsupported database connection type. "
                "Expected PostgreSQL or SQL Server."
            )

        resolved_schema = schema or default_schema(db_type)

        table_names = list_tables(
            connection_string,
            schema=resolved_schema,
        )

        columns_by_table = list_columns(
            connection_string,
            table_names,
            schema=resolved_schema,
        )

        foreign_keys = list_foreign_keys(
            connection_string,
            table_names,
            schema=resolved_schema,
        )

        tables = []

        for table_name in table_names:
            column_models = [
                ColumnInfo(
                    name=column["name"],
                    type=column["type"],
                    nullable=column["nullable"],
                    primary_key=column["primary_key"],
                )
                for column in columns_by_table.get(table_name, [])
            ]

            tables.append(
                TableInfo(
                    name=table_name,
                    columns=column_models,
                )
            )

        relationships = [
            JoinSpec(
                left_table=relationship["left_table"],
                left_column=relationship["left_column"],
                right_table=relationship["right_table"],
                right_column=relationship["right_column"],
            )
            for relationship in foreign_keys
        ]

        return Catalog(
            db_type=db_type,
            schema_name=resolved_schema,
            tables=tables,
            relationships=relationships,
        )
