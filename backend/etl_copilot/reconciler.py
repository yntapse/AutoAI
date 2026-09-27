"""Reconciliation. A HARD GATE, not a report.

Compares the compiled dataset against control totals measured at the SOURCE
grain. An unreconciled plan must never be persisted or handed to AutoML.

What this proves: a SUM was aggregated exactly once, and the output has one
row per distinct entity key. What it does NOT prove: that the join type,
filter, measure or time window was the one the user meant. It is a necessary
floor, not a correctness guarantee.
"""

from typing import Any, Dict, List, Optional

from services.db_connector import DataSourceError, extract_to_dataframe

from etl_copilot.spec_models import AggFunction, CompiledPlan, DatasetSpec


class ReconciliationError(Exception):
    """Raised when output totals do not match the source grain."""


class ReconciliationResult(object):
    def __init__(self):
        self.checks: List[Dict[str, Any]] = []
        self.passed: bool = True

    def record(self, name, expected, actual, tolerance=0.0):
        ok = False
        if expected is None and actual is None:
            ok = True
        elif expected is None or actual is None:
            ok = False
        else:
            try:
                ok = abs(float(expected) - float(actual)) <= tolerance
            except (TypeError, ValueError):
                ok = expected == actual

        self.checks.append(
            {
                "check": name,
                "expected": expected,
                "actual": actual,
                "passed": ok,
            }
        )
        if not ok:
            self.passed = False
        return ok

    def failures(self):
        return [check for check in self.checks if not check["passed"]]

    def summary(self):
        lines = []
        for check in self.checks:
            lines.append(
                "  [%s] %s: expected %s, got %s"
                % (
                    "PASS" if check["passed"] else "FAIL",
                    check["check"],
                    check["expected"],
                    check["actual"],
                )
            )
        return "\n".join(lines)


class Reconciler:
    """Runs the compiler's control queries and compares them to the output."""

    def __init__(self, tolerance: float = 0.01):
        self.tolerance = tolerance

    def reconcile(
        self,
        connection_string: str,
        spec: DatasetSpec,
        plan: CompiledPlan,
        frame,
    ) -> ReconciliationResult:
        result = ReconciliationResult()

        # ---- entity row count ----
        entity_count_queries = [
            query for query in plan.reconciliation if query.kind == "entity_count"
        ]

        for query in entity_count_queries:
            expected = self._scalar(connection_string, query.sql)
            result.record("entity_row_count", expected, len(frame))

        # ---- distinct entity keys in the output ----
        entity_key = spec.entity_key[0]
        if entity_key in frame.columns:
            distinct_keys = int(frame[entity_key].nunique(dropna=False))
            result.record("output_rows_equal_distinct_keys", distinct_keys, len(frame))

        # ---- per-measure source-grain totals ----
        measures_by_name = {measure.name: measure for measure in spec.measures}

        for query in plan.reconciliation:
            if query.kind != "measure_total":
                continue

            measure = measures_by_name.get(query.measure_name)
            if measure is None:
                continue
            if measure.aggregation is not AggFunction.SUM:
                continue
            if query.measure_name not in frame.columns:
                result.record(
                    "measure_present:%s" % query.measure_name, True, False
                )
                continue

            expected = self._scalar(connection_string, query.sql)
            actual = frame[query.measure_name].fillna(0).sum()

            result.record(
                "source_grain_total:%s" % query.measure_name,
                expected,
                actual,
                tolerance=self.tolerance,
            )

        return result

    def gate(self, result: ReconciliationResult) -> None:
        """Raise unless every check passed. This is the hard gate."""
        if result.passed:
            return

        raise ReconciliationError(
            "Reconciliation failed - the dataset was NOT persisted.\n%s"
            % result.summary()
        )

    def _scalar(self, connection_string: str, sql: str) -> Optional[Any]:
        try:
            frame = extract_to_dataframe(connection_string, sql)
        except DataSourceError as exc:
            raise ReconciliationError(
                "Could not run a reconciliation query: %s" % exc
            )

        if frame.empty:
            return None
        return frame.iloc[0, 0]
