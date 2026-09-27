from etl_copilot.spec_models import DatasetSpec

def validate_spec(spec: DatasetSpec) -> None:
    """
    Basic validation for Dataset Spec.
    Raises ValueError if invalid.
    """

    if not spec.entity_table:
        raise ValueError("Entity table is required.")

    if not spec.entity_key:
        raise ValueError("Entity key is required.")

    if len(spec.measures) == 0 and len(spec.attributes) == 0:
        raise ValueError("Dataset must contain at least one attribute or measure.")

    measure_names = set()

    for measure in spec.measures:
        if measure.name in measure_names:
            raise ValueError(f"Duplicate measure name: {measure.name}")

        if not measure.source_table:
            raise ValueError(
                f"Measure '{measure.name}' is missing source_table."
)

        measure_names.add(measure.name)

        allowed = ["sum", "count", "avg", "min", "max", "nunique", "median", "std"]

        if measure.aggregation.lower() not in allowed:
            raise ValueError(f"Unsupported aggregation: {measure.aggregation}")
