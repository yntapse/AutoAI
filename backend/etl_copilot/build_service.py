"""Execution, persistence and AutoML handoff.

The ONLY module in etl_copilot that holds a database connection.

Order of operations is fixed and not negotiable:

    compile -> execute -> RECONCILE -> persist -> hand off

An unreconciled plan is never persisted and never reaches AutoML.
"""

import os
from typing import Any, Dict, Optional

from services.db_connector import MAX_ROWS, DataSourceError, extract_to_dataframe

from etl_copilot.lineage import audit_payload, explain_dataset
from etl_copilot.reconciler import Reconciler, ReconciliationError
from etl_copilot.spec_models import CompiledPlan, DatasetSpec


class BuildError(Exception):
    pass


class BuildService:
    def __init__(self, output_dir: Optional[str] = None, reconciler=None):
        self.output_dir = output_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "generated_datasets",
        )
        self.reconciler = reconciler or Reconciler()

    def build(
        self,
        connection_string: str,
        spec: DatasetSpec,
        plan: CompiledPlan,
        dataset_name: str = "etl_copilot_dataset",
        persist: bool = True,
    ) -> Dict[str, Any]:
        # ---- 1. execute ----
        try:
            frame = extract_to_dataframe(
                connection_string, plan.sql, max_rows=MAX_ROWS
            )
        except DataSourceError as exc:
            raise BuildError("Dataset query failed: %s" % exc)

        # ---- 2. reconcile (HARD GATE) ----
        result = self.reconciler.reconcile(connection_string, spec, plan, frame)

        try:
            self.reconciler.gate(result)
        except ReconciliationError:
            # Re-raised deliberately: nothing is written when this fires.
            raise

        # ---- 3. persist ----
        file_path = None
        file_id = None

        if persist:
            if not os.path.isdir(self.output_dir):
                os.makedirs(self.output_dir)

            file_id = "%s_%d" % (dataset_name, abs(hash(plan.sql)) % (10 ** 10))
            file_path = os.path.join(self.output_dir, "%s.csv" % file_id)
            frame.to_csv(file_path, index=False)

        # ---- 4. lineage + handoff payload ----
        return {
            "file_id": file_id,
            "file_path": file_path,
            "row_count": len(frame),
            "columns": list(frame.columns),
            "reconciliation": {
                "passed": result.passed,
                "checks": result.checks,
            },
            "explanation": explain_dataset(spec, plan),
            "lineage": audit_payload(spec, plan),
            "automl_ready": bool(file_id) and result.passed,
            "target_column": spec.target_column,
            "target_type": spec.target_type,
        }
