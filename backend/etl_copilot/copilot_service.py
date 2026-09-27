"""ETL Copilot orchestration.

Pipeline (the architecture document's target flow):

    user request
      -> OpenAI spec generation        (intent only, never SQL)
      -> Pydantic DatasetSpec          (extra="forbid")
      -> Catalog                        (real introspection)
      -> JoinGraph                      (deterministic FK paths)
      -> JoinVerifier                   (the only setter of verified=True)
      -> SpecValidator                  (ValidationIssue list)
      -> DatasetCompiler                (CTE per grain, nested multi-hop)
      -> Reconciler                     (HARD GATE)
      -> BuildService                   (execute, persist)
      -> Lineage                        (explain, trace)

The LLM is called at most twice: once to draft, once to repair a spec that
failed validation. It never sees a data row and never produces SQL.
"""

from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from etl_copilot.build_service import BuildService
from etl_copilot.dataset_compiler import DatasetCompiler, SpecCompileError
from etl_copilot.join_graph import resolve_paths_for_spec
from etl_copilot.join_verifier import JoinVerifier
from etl_copilot.lineage import explain_dataset
from etl_copilot.openai_spec_generator import OpenAISpecGenerator
from etl_copilot.schema_reader import SchemaReader
from etl_copilot.spec_models import (
    Catalog,
    DatasetSpec,
    ValidationIssue,
)
from etl_copilot.spec_validator import errors_only, validate_spec


class CopilotError(Exception):
    """Raised with the validation issues that could not be resolved."""

    def __init__(self, message: str, issues: Optional[List[ValidationIssue]] = None):
        super(CopilotError, self).__init__(message)
        self.issues = issues or []


def catalog_for_llm(catalog: Catalog) -> Dict[str, Any]:
    """Sanitised, metadata-only payload. No data values ever leave here."""

    return {
        "tables": {
            table.name: [column.name for column in table.columns]
            for table in catalog.tables
        },
        "primary_keys": {
            table.name: [
                column.name for column in table.columns if column.primary_key
            ]
            for table in catalog.tables
        },
        "relationships": [
            {
                "left_table": rel.left_table,
                "left_column": rel.left_column,
                "right_table": rel.right_table,
                "right_column": rel.right_column,
            }
            for rel in catalog.relationships
        ],
    }


class ETLCopilot:
    def __init__(self, generator=None, build_service=None):
        self.generator = generator or OpenAISpecGenerator()
        self.schema_reader = SchemaReader()
        self.verifier = JoinVerifier()
        self.build_service = build_service or BuildService()

    # -- stages ----------------------------------------------------------

    def discover_schema(self, connection_string: str, schema: Optional[str] = None) -> Catalog:
        return self.schema_reader.get_schema(connection_string, schema=schema)

    def generate_spec(self, user_request: str, catalog: Catalog) -> DatasetSpec:
        """One structured call, then at most one repair attempt."""

        payload = catalog_for_llm(catalog)
        raw = self.generator.generate_spec(user_request, payload)

        try:
            return self._parse(raw, catalog)
        except ValidationError as exc:
            repaired = self.generator.fix_spec(
                user_request=user_request,
                schema=payload,
                invalid_spec=raw,
                validation_errors=exc.errors(),
            )
            return self._parse(repaired, catalog)

    def _parse(self, raw: Dict[str, Any], catalog: Catalog) -> DatasetSpec:
        spec = DatasetSpec(**raw)
        spec.db_type = spec.db_type or catalog.db_type
        spec.schema_name = spec.schema_name or catalog.schema_name
        return spec

    def resolve_and_verify(
        self,
        connection_string: str,
        catalog: Catalog,
        spec: DatasetSpec,
    ) -> List[ValidationIssue]:
        """Deterministic paths, then measured verification."""

        paths, issues = resolve_paths_for_spec(catalog, spec)

        if errors_only(issues):
            spec.join_paths = paths
            return issues

        verified, verify_issues = self.verifier.verify_all(
            connection_string,
            catalog.db_type,
            catalog.schema_name,
            paths,
        )

        spec.join_paths = verified
        return issues + verify_issues

    def compile_spec(self, spec: DatasetSpec):
        compiler = DatasetCompiler(
            db_type=spec.db_type or "postgresql",
            schema=spec.schema_name,
        )
        return compiler.compile(spec)

    # -- the whole pipeline ---------------------------------------------

    def create_dataset(
        self,
        user_request: str,
        connection_string: str,
        schema: Optional[str] = None,
        dataset_name: str = "etl_copilot_dataset",
        persist: bool = True,
    ) -> Dict[str, Any]:

        catalog = self.discover_schema(connection_string, schema=schema)
        spec = self.generate_spec(user_request, catalog)

        issues = self.resolve_and_verify(connection_string, catalog, spec)
        issues += validate_spec(spec, catalog, spec.join_paths)

        blocking = errors_only(issues)
        if blocking:
            raise CopilotError(
                "Specification could not be validated:\n"
                + "\n".join("  - [%s] %s" % (i.code, i.message) for i in blocking),
                issues,
            )

        try:
            plan = self.compile_spec(spec)
        except SpecCompileError as exc:
            raise CopilotError(str(exc), issues)

        result = self.build_service.build(
            connection_string=connection_string,
            spec=spec,
            plan=plan,
            dataset_name=dataset_name,
            persist=persist,
        )

        result["spec"] = spec.dict()
        result["sql"] = plan.sql
        result["warnings"] = [i.dict() for i in issues if not i.is_error]
        return result

    # -- dry run (no execution) -----------------------------------------

    def preview_plan(
        self,
        user_request: str,
        connection_string: str,
        schema: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Everything except execution, for review before Build."""

        catalog = self.discover_schema(connection_string, schema=schema)
        spec = self.generate_spec(user_request, catalog)

        issues = self.resolve_and_verify(connection_string, catalog, spec)
        issues += validate_spec(spec, catalog, spec.join_paths)

        blocking = errors_only(issues)
        plan = None
        explanation: List[str] = []

        if not blocking:
            plan = self.compile_spec(spec)
            explanation = explain_dataset(spec, plan)

        return {
            "spec": spec.dict(),
            "sql": plan.sql if plan else None,
            "issues": [i.dict() for i in issues],
            "blocking": bool(blocking),
            "explanation": explanation,
        }
