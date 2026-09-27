"""Join verification. The ONLY module allowed to set verified=True.

Each hop of a JoinPath is measured independently against the real data using
the existing services.join_profiler, which the platform already ships and
which this module does not reimplement.

A verified hop carries cardinality, match_rate and row_multiplication.
row_multiplication is what tells the compiler whether a measure sourced past
that hop needs nested aggregation or a distinct key.

Profiles are cached per (connection, hop) because verification is a property
of the data, not of the request - so it is paid once per source, not once per
dataset.
"""

from typing import Any, Dict, List, Optional, Tuple

from services.db_connector import DataSourceError, extract_to_dataframe
from services.join_profiler import (
    JoinProfileError,
    build_profile_query,
    profile_from_row,
    recommend,
)

from etl_copilot.spec_models import JoinHop, JoinPath, ValidationIssue


def _hop_key(hop: JoinHop) -> Tuple[str, str, str, str]:
    return (hop.left_table, hop.left_column, hop.right_table, hop.right_column)


class JoinVerifier:
    """Measures each hop. Nothing else may mark a path verified."""

    def __init__(self, max_rows: Optional[int] = None):
        self._cache: Dict[Any, Any] = {}
        self.max_rows = max_rows

    def verify_hop(
        self,
        connection_string: str,
        db_type: str,
        schema: Optional[str],
        hop: JoinHop,
    ) -> Tuple[JoinHop, List[ValidationIssue]]:
        """Measure one hop and return it with its statistics filled in."""

        issues: List[ValidationIssue] = []
        cache_key = (connection_string, schema, _hop_key(hop))

        if cache_key in self._cache:
            return self._cache[cache_key], issues

        try:
            sql = build_profile_query(
                db_type=db_type,
                schema=schema,
                base_table=hop.left_table,
                base_column=hop.left_column,
                related_table=hop.right_table,
                related_column=hop.right_column,
            )
        except JoinProfileError as exc:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="join_profile_failed",
                    message="Could not build a profile query for %s.%s -> %s.%s: %s"
                    % (
                        hop.left_table,
                        hop.left_column,
                        hop.right_table,
                        hop.right_column,
                        exc,
                    ),
                    field="join_paths",
                )
            )
            return hop, issues

        try:
            frame = extract_to_dataframe(connection_string, sql)
        except DataSourceError as exc:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="join_profile_failed",
                    message="While profiling %s -> %s: %s"
                    % (hop.left_table, hop.right_table, exc),
                    field="join_paths",
                )
            )
            return hop, issues

        profile = profile_from_row(frame.iloc[0].to_dict())

        measured = hop.copy(
            update={
                "cardinality": profile.cardinality,
                "match_rate": profile.match_percentage,
                "row_multiplication": profile.row_multiplication,
            }
        )

        advice = recommend(
            profile,
            hop.left_table,
            hop.right_table,
            self.max_rows or profile.left_result_rows or 0,
        )

        if advice.severity == "danger":
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="join_risk",
                    message="%s -> %s: %s"
                    % (hop.left_table, hop.right_table, advice.headline),
                    field="join_paths",
                    suggestion="; ".join(advice.reasons) if advice.reasons else None,
                )
            )

        self._cache[cache_key] = measured
        return measured, issues

    def verify_path(
        self,
        connection_string: str,
        db_type: str,
        schema: Optional[str],
        path: JoinPath,
    ) -> Tuple[JoinPath, List[ValidationIssue]]:
        """Verify every hop. verified=True only if all hops measured cleanly."""

        if not path.hops:
            # Entity-table source: nothing to join, nothing to measure.
            return path.copy(update={"verified": True}), []

        issues: List[ValidationIssue] = []
        measured_hops: List[JoinHop] = []
        all_measured = True

        for hop in path.hops:
            measured, hop_issues = self.verify_hop(
                connection_string, db_type, schema, hop
            )
            issues.extend(hop_issues)
            measured_hops.append(measured)

            if measured.cardinality is None:
                all_measured = False

        return (
            path.copy(
                update={
                    "hops": measured_hops,
                    "verified": all_measured,
                }
            ),
            issues,
        )

    def verify_all(
        self,
        connection_string: str,
        db_type: str,
        schema: Optional[str],
        paths: List[JoinPath],
    ) -> Tuple[List[JoinPath], List[ValidationIssue]]:
        verified: List[JoinPath] = []
        issues: List[ValidationIssue] = []

        for path in paths:
            path_verified, path_issues = self.verify_path(
                connection_string, db_type, schema, path
            )
            verified.append(path_verified)
            issues.extend(path_issues)

        return verified, issues
