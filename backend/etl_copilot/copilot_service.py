from etl_copilot.openai_spec_generator import OpenAISpecGenerator
from etl_copilot.schema_validator import SchemaValidator
from etl_copilot.spec_converter import SpecConverter
from etl_copilot.grain_compiler import GrainCompiler
from etl_copilot.join_discovery import JoinDiscovery
from etl_copilot.join_path_resolver import JoinPathResolver


class ETLCopilot:

    def __init__(self):

        self.generator = OpenAISpecGenerator()
        self.validator = SchemaValidator()
        self.converter = SpecConverter()
        self.compiler = GrainCompiler()

        self.join_discovery = JoinDiscovery()
        self.path_resolver = JoinPathResolver()

    def create_dataset(
        self,
        user_request: str,
        schema: dict,
    ):

        spec_dict = self.generator.generate_spec(
            user_request,
            schema,
        )

        print("\nOPENAI SPEC:")
        print(spec_dict)
        print()

        errors = self.validator.validate(
            spec_dict,
            schema,
        )

        if errors:

            spec_dict = self.generator.fix_spec(
                user_request=user_request,
                schema=schema,
                invalid_spec=spec_dict,
                validation_errors=errors,
            )

            errors = self.validator.validate(
                spec_dict,
                schema,
            )

            if errors:
                raise Exception(f"Spec validation failed: {errors}")

        spec = self.converter.from_dict(spec_dict)

        spec.joins = self.join_discovery.discover(schema)

        print("\nDISCOVERED JOINS:")
        print(spec.joins)
        print()

        spec.join_paths = []

        for measure in spec.measures:

            path = self.path_resolver.resolve(
                spec.joins,
                spec.entity_table,
                measure.source_table,
            )

            spec.join_paths.append(path)

        print("\nJOIN PATHS:")
        print(spec.join_paths)
        print()

        sql = self.compiler.compile(spec)

        return {
            "spec": spec_dict,
            "sql": sql,
        }
