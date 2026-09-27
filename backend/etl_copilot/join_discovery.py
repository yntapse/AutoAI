from etl_copilot.spec_models import JoinSpec


class JoinDiscovery:

    def discover(
        self,
        schema: dict,
    ):

        joins = []

        tables = schema.get(
            "tables",
            {},
        )

        table_names = list(tables.keys())

        for left_table in table_names:

            left_columns = tables.get(
                left_table,
                [],
            )

            for right_table in table_names:

                if left_table == right_table:
                    continue

                right_columns = tables.get(
                    right_table,
                    [],
                )

                for column in left_columns:

                    if column in right_columns:

                        joins.append(
                            JoinSpec(
                                left_table=left_table,
                                left_column=column,
                                right_table=right_table,
                                right_column=column,
                                verified=False,
                            )
                        )

        return joins
