from etl_copilot.spec_models import (
    DatasetSpec,
    Measure,
)


class SpecConverter:

    def from_dict(self, spec_dict):

        measures = []

        for measure in spec_dict.get("measures", []):

            measures.append(
                Measure(
                    name=measure.get(
                        "measure_name",
                        "measure",
                    ),
                    source_table=measure.get("source_table"),
                    column=measure.get("source_column"),
                    aggregation=measure.get("aggregation"),
                )
            )

        return DatasetSpec(
            entity_table=spec_dict.get("entity_table"),
            entity_key=spec_dict.get("entity_key"),
            attributes=spec_dict.get(
                "attributes",
                [],
            ),
            measures=measures,
            filters=[],
        )
