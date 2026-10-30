"""Execution, persistence and AutoML handoff.

The ONLY module in etl_copilot that holds a database connection.

Order of operations is fixed and not negotiable:

    compile -> execute -> RECONCILE -> persist -> hand off

An unreconciled plan is never persisted and never reaches AutoML.
"""

import hashlib
import os
from typing import Any, Dict, Optional

from services.db_connector import MAX_ROWS, DataSourceError, extract_to_dataframe

from etl_copilot.lineage import audit_payload, explain_dataset
from etl_copilot.reconciler import Reconciler, ReconciliationError
from etl_copilot.spec_models import CompiledPlan, DatasetSpec


class BuildError(Exception):
    pass


class BuildService:
    def __init__(
        self,
        output_dir: Optional[str] = None,
        reconciler=None,
        max_rows: Optional[int] = None,
    ):
        # Defaults to the platform cap in services.db_connector. It is
        # overridable because one row per entity means a wide entity table
        # can exceed 50,000 rows legitimately, and the cap then stops a
        # correct dataset. Nothing changes unless a caller passes a value.
        self.max_rows = max_rows or MAX_ROWS
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
            # plan.params carries the bound value for every :name placeholder
            # the compiler emitted. Dropping it would send SQL with unbound
            # placeholders the moment the spec carries a single filter.
            frame = extract_to_dataframe(
                connection_string,
                plan.sql,
                max_rows=self.max_rows,
                params=plan.params,
            )
        except DataSourceError as exc:
            # An empty result is a legitimate outcome of a narrow filter, not
            # a broken query. db_connector cannot tell the two apart, so the
            # message is made specific here rather than left generic.
            if "no rows" in str(exc).lower():
                raise BuildError(
                    "The dataset is empty - no rows matched. Check the "
                    "filters in the request, or the join between %s and its "
                    "source tables. (%s)" % (spec.entity_table, exc)
                )
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

            # hash() is salted per process (PYTHONHASHSEED), so the same SQL
            # produced a different id on every run - whatever the id was for
            # (dedup, cache, idempotence) it could not do. sha256 is stable.
            digest = hashlib.sha256(plan.sql.encode("utf-8")).hexdigest()[:12]
            file_id = "%s_%s" % (dataset_name, digest)
            file_path = os.path.join(self.output_dir, "%s.csv" % file_id)
            frame.to_csv(file_path, index=False)

        # ---- 4. lineage + handoff payload ----
        return {
            # The DataFrame itself, so a caller that registers the dataset
            # elsewhere (routers/etl_copilot.py hands it to AutoAI's
            # _create_project_from_frame) does not have to re-execute the
            # query or read the CSV back. It is the one non-JSON value in
            # this dict: a caller that serialises the result pops it first.
            "frame": frame,
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
