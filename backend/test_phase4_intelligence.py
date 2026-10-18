"""Phase 4 intelligence layer tests.

Covers the four new pure services and asserts that the three Phase 4 models
match migration 0005 exactly. No database, no network, no model calls - every
measurement is injected, so these run anywhere.
"""

import ast
import io
import unittest

from models.join_verification import (
    VERDICT_RISKY,
    VERDICT_SAFE,
    VERDICT_UNMEASURED,
    JoinVerification,
)
from models.monitoring_alert import MonitoringAlert
from models.pipeline_schedule import (
    CRON_FOR_FREQUENCY,
    FREQUENCIES,
    STATUS_ACTIVE,
    STATUS_PAUSED,
    PipelineSchedule,
)
from services.copilot_explainer import answer
from services.join_verification_service import (
    derive,
    fanout_risk,
    measure_relationship,
    orphan_rate,
    row_explosion_ratio,
)
from services.join_profiler import profile_from_row
from services.pipeline_diff import diff_pipeline_json
from services.quality_dimensions import (
    DIMENSIONS,
    dimension_for_rule,
    recommend_dimension_rules,
    score_dimensions,
)
from services.quality_rules import SUPPORTED_RULE_TYPES

MIGRATION = "migrations/versions/0005_pipeline_intelligence.py"


def profile_row(**overrides):
    """A measured profile row in the exact shape the profile query returns."""
    row = {
        "base_rows": 1000,
        "related_rows": 1000,
        "base_null_keys": 0,
        "related_null_keys": 0,
        "base_distinct_keys": 1000,
        "related_distinct_keys": 1000,
        "base_matched_rows": 1000,
        "related_matched_rows": 1000,
        "inner_join_rows": 1000,
        "max_rows_per_key": 1,
    }
    row.update(overrides)
    return row


class JoinVerificationServiceTests(unittest.TestCase):
    def test_one_to_one_join_is_measured_safe_with_no_fanout(self):
        result = measure_relationship(
            db_type="postgresql",
            schema="public",
            left_table="customer_master",
            left_column="customer_id",
            right_table="customer_kyc",
            right_column="customer_id",
            run_query=lambda sql: profile_row(),
            row_limit=50_000,
        )
        self.assertTrue(result["measured"])
        self.assertEqual(result["cardinality"], "1:1")
        self.assertEqual(result["fanout_risk"], "none")
        self.assertEqual(result["match_rate"], 100.0)
        self.assertEqual(result["orphan_rate"], 0.0)
        self.assertFalse(result["row_multiplication"])
        self.assertEqual(result["verdict"], VERDICT_SAFE)

    def test_fanout_is_measured_not_inferred_from_cardinality(self):
        """A 1:M relationship whose worst key has one partner cannot multiply."""
        profile = profile_from_row(
            profile_row(
                related_rows=1000,
                related_distinct_keys=1000,
                base_distinct_keys=500,  # base key not unique -> M:1 / 1:M family
                max_rows_per_key=1,
            )
        )
        self.assertIn(fanout_risk(profile), ("none", "low"))

    def test_high_fanout_is_escalated_by_measurement(self):
        profile = profile_from_row(
            profile_row(
                base_rows=121_500,
                related_rows=4_680_000,
                related_distinct_keys=121_000,
                base_matched_rows=121_000,
                related_matched_rows=4_680_000,
                inner_join_rows=4_680_000,
                max_rows_per_key=61,
            )
        )
        self.assertEqual(fanout_risk(profile), "high")
        self.assertGreater(row_explosion_ratio(profile), 38)
        self.assertAlmostEqual(orphan_rate(profile), 0.41, places=2)

    def test_orphan_rate_is_the_unmatched_share_of_base_rows(self):
        profile = profile_from_row(
            profile_row(base_rows=1000, base_matched_rows=880)
        )
        self.assertEqual(orphan_rate(profile), 12.0)
        self.assertEqual(round(profile.match_percentage, 1), 88.0)

    def test_row_explosion_ratio_of_a_grain_preserving_join_is_one(self):
        profile = profile_from_row(profile_row())
        self.assertEqual(row_explosion_ratio(profile), 1.0)

    def test_measurement_failure_never_reads_as_verified(self):
        def boom(sql):
            raise RuntimeError("connection refused")

        result = measure_relationship(
            db_type="postgresql",
            schema="public",
            left_table="a",
            left_column="x",
            right_table="b",
            right_column="y",
            run_query=boom,
            row_limit=50_000,
        )
        self.assertFalse(result["measured"])
        self.assertEqual(result["verdict"], VERDICT_UNMEASURED)
        self.assertIsNone(result["recommended_join"])
        self.assertIn("connection refused", result["error"])

    def test_empty_result_set_is_a_failure_not_a_pass(self):
        result = measure_relationship(
            db_type="postgresql",
            schema="public",
            left_table="a",
            left_column="x",
            right_table="b",
            right_column="y",
            run_query=lambda sql: [],
            row_limit=50_000,
        )
        self.assertFalse(result["measured"])
        self.assertEqual(result["verdict"], VERDICT_UNMEASURED)

    def test_self_join_is_rejected_before_any_query_runs(self):
        calls = []

        def spy(sql):
            calls.append(sql)
            return profile_row()

        result = measure_relationship(
            db_type="postgresql",
            schema="public",
            left_table="t",
            left_column="a",
            right_table="t",
            right_column="b",
            run_query=spy,
            row_limit=50_000,
        )
        self.assertFalse(result["measured"])
        self.assertEqual(calls, [], "no query should reach the database")

    def test_unsupported_dialect_is_rejected(self):
        result = measure_relationship(
            db_type="oracle",
            schema=None,
            left_table="a",
            left_column="x",
            right_table="b",
            right_column="y",
            run_query=lambda sql: profile_row(),
            row_limit=50_000,
        )
        self.assertFalse(result["measured"])

    def test_derived_payload_keys_match_the_model_columns(self):
        """derive() output must be directly assignable to JoinVerification."""
        payload = derive(
            profile_from_row(profile_row()),
            "left_t",
            "right_t",
            50_000,
        )
        columns = {column.name for column in JoinVerification.__table__.columns}
        self.assertTrue(
            set(payload).issubset(columns),
            f"unknown keys: {set(payload) - columns}",
        )

    def test_oversized_result_is_flagged_risky(self):
        result = measure_relationship(
            db_type="postgresql",
            schema="public",
            left_table="loan_master",
            left_column="loan_id",
            right_table="loan_repayment",
            right_column="loan_id",
            run_query=lambda sql: profile_row(
                base_rows=121_500,
                related_rows=4_680_000,
                related_distinct_keys=121_000,
                base_matched_rows=121_000,
                related_matched_rows=4_680_000,
                inner_join_rows=4_680_000,
                max_rows_per_key=61,
            ),
            row_limit=50_000,
        )
        self.assertEqual(result["verdict"], VERDICT_RISKY)
        self.assertTrue(result["reasons"])


class QualityDimensionTests(unittest.TestCase):
    COLUMNS = {
        "loan_master": [
            {
                "column_name": "loan_id",
                "data_type": "integer",
                "is_primary_key": True,
                "is_foreign_key": False,
                "is_nullable": False,
            },
            {
                "column_name": "sanctioned_amount",
                "data_type": "numeric(18,2)",
                "is_primary_key": False,
                "is_foreign_key": False,
                "is_nullable": True,
            },
            {
                "column_name": "disbursed_date",
                "data_type": "date",
                "is_primary_key": False,
                "is_foreign_key": False,
                "is_nullable": True,
            },
        ]
    }

    def test_all_six_dimensions_are_defined(self):
        self.assertEqual(
            set(DIMENSIONS),
            {
                "completeness",
                "uniqueness",
                "validity",
                "consistency",
                "accuracy",
                "freshness",
            },
        )

    def test_recommended_rules_only_use_the_existing_rule_engine(self):
        rules = recommend_dimension_rules(self.COLUMNS, ["loan_master"])
        self.assertTrue(rules)
        for rule in rules:
            self.assertIn(rule["rule_type"], SUPPORTED_RULE_TYPES)
            self.assertIn(rule["dimension"], DIMENSIONS)
            self.assertTrue(rule["reason"].strip())

    def test_key_column_gets_a_validity_rule(self):
        rules = recommend_dimension_rules(self.COLUMNS, ["loan_master"])
        validity = [r for r in rules if r["dimension"] == "validity"]
        self.assertTrue(validity)
        self.assertEqual(validity[0]["rule_config"]["column_name"], "loan_id")

    def test_money_column_gets_an_accuracy_range_rule(self):
        rules = recommend_dimension_rules(self.COLUMNS, ["loan_master"])
        accuracy = [r for r in rules if r["dimension"] == "accuracy"]
        self.assertTrue(accuracy)
        self.assertEqual(accuracy[0]["rule_type"], "numeric_range")

    def test_date_column_gets_a_freshness_rule(self):
        rules = recommend_dimension_rules(self.COLUMNS, ["loan_master"])
        freshness = [r for r in rules if r["dimension"] == "freshness"]
        self.assertTrue(freshness)

    def test_every_table_gets_a_blocking_completeness_rule(self):
        rules = recommend_dimension_rules(self.COLUMNS, ["loan_master"])
        completeness = [r for r in rules if r["dimension"] == "completeness"]
        self.assertTrue(completeness)
        self.assertEqual(completeness[0]["rule_type"], "row_count")
        self.assertEqual(completeness[0]["severity"], "error")

    def test_a_null_check_on_a_date_column_reports_as_freshness(self):
        self.assertEqual(
            dimension_for_rule("null_percentage", {"column_name": "load_date"}),
            "freshness",
        )
        self.assertEqual(
            dimension_for_rule("null_percentage", {"column_name": "customer_id"}),
            "completeness",
        )

    def test_uniqueness_rule_maps_to_the_uniqueness_dimension(self):
        self.assertEqual(dimension_for_rule("uniqueness", {}), "uniqueness")

    def test_score_dimensions_reports_coverage_and_blocking_counts(self):
        rules = recommend_dimension_rules(self.COLUMNS, ["loan_master"])
        summary = score_dimensions(rules)
        self.assertEqual(set(summary), set(DIMENSIONS))
        self.assertTrue(summary["completeness"]["covered"])
        self.assertGreaterEqual(summary["completeness"]["blocking_rules"], 1)
        # No rule here guards uniqueness, and the summary must say so rather
        # than quietly reporting full coverage.
        self.assertFalse(summary["uniqueness"]["covered"])


class PipelineDiffTests(unittest.TestCase):
    V1 = {
        "schema_version": 1,
        "user_request": "credit risk dataset",
        "tables": ["loan_master", "customer_master"],
        "joins": [
            {
                "left_table": "loan_master",
                "left_column": "customer_id",
                "right_table": "customer_master",
                "right_column": "customer_id",
                "join_type": "left",
            }
        ],
        "quality_rules": [
            {
                "rule_type": "uniqueness",
                "severity": "error",
                "rule_config": {"table_name": "loan_master", "column_name": "loan_id"},
            }
        ],
        "derived_columns": [{"name": "missed_emi_count"}],
        "cleaning_steps": [],
        "relationships": [],
    }

    def test_identical_versions_report_no_difference(self):
        diff = diff_pipeline_json(self.V1, dict(self.V1))
        self.assertEqual(diff["totals"], {"added": 0, "removed": 0, "changed": 0})
        self.assertIn("No structural difference", diff["summary"][0])

    def test_added_table_and_join_are_named(self):
        v2 = {
            **self.V1,
            "tables": [*self.V1["tables"], "loan_repayment"],
            "joins": [
                *self.V1["joins"],
                {
                    "left_table": "loan_master",
                    "left_column": "loan_id",
                    "right_table": "loan_repayment",
                    "right_column": "loan_id",
                    "join_type": "left",
                },
            ],
        }
        diff = diff_pipeline_json(self.V1, v2)
        self.assertIn("loan_repayment", diff["sections"]["tables"]["added"])
        self.assertEqual(len(diff["sections"]["joins"]["added"]), 1)
        self.assertIn(
            "loan_master.loan_id -> loan_repayment.loan_id [LEFT]",
            diff["sections"]["joins"]["added"],
        )

    def test_removed_quality_rule_is_named(self):
        v2 = {**self.V1, "quality_rules": []}
        diff = diff_pipeline_json(self.V1, v2)
        removed = diff["sections"]["quality_rules"]["removed"]
        self.assertEqual(len(removed), 1)
        self.assertIn("loan_master.loan_id", removed[0])

    def test_changed_join_type_is_reported_as_changed_not_add_remove(self):
        v2 = {
            **self.V1,
            "joins": [{**self.V1["joins"][0], "join_type": "inner"}],
        }
        diff = diff_pipeline_json(self.V1, v2)
        joins = diff["sections"]["joins"]
        self.assertEqual(joins["added"], [])
        self.assertEqual(joins["removed"], [])
        self.assertEqual(len(joins["changed"]), 1)
        self.assertEqual(joins["changed"][0]["after"]["join_type"], "inner")

    def test_requirement_change_is_reported_as_a_scalar_change(self):
        v2 = {**self.V1, "user_request": "churn dataset"}
        diff = diff_pipeline_json(self.V1, v2)
        self.assertIn("user_request", diff["scalar_changes"])

    def test_missing_sections_do_not_crash(self):
        diff = diff_pipeline_json({}, {})
        self.assertEqual(diff["totals"]["added"], 0)


class CopilotExplainerTests(unittest.TestCase):
    CONTEXT = {
        "plan": {
            "requirement_text": "credit risk dataset per loan",
            "status": "reviewed",
        },
        "recommendations": [
            {
                "kind": "join",
                "ref_key": "join:loan_master.customer_id->customer_master.customer_id",
                "title": "LEFT JOIN customer_master (M:1)",
                "payload": {
                    "left_table": "loan_master",
                    "left_column": "customer_id",
                    "right_table": "customer_master",
                    "join_type": "left",
                },
                "reason": (
                    "LEFT is recommended: the key is M:1, but "
                    "loan_master.customer_id is nullable, so an INNER join "
                    "would silently drop every row with no match."
                ),
                "alternative_reason": (
                    "INNER was rejected because it would change the row count "
                    "without saying so."
                ),
                "expected_impact": "Cardinality M:1, row explosion risk low.",
                "confidence": 0.99,
                "decision": "accepted",
                "origin": "declared",
            },
            {
                "kind": "derived_column",
                "ref_key": "dc:customer_segment",
                "title": "Derived column: customer_segment",
                "payload": {"name": "customer_segment"},
                "reason": (
                    "Segment groups customers by behaviour so the model can "
                    "learn per-cohort risk instead of one global rate."
                ),
                "alternative_reason": None,
                "expected_impact": None,
                "confidence": 0.6,
                "decision": "pending",
                "origin": "ai",
            },
        ],
        "verifications": [
            {
                "base_table": "loan_master",
                "base_column": "customer_id",
                "related_table": "customer_master",
                "related_column": "customer_id",
                "verified": True,
                "measured_at": "2026-10-08T12:00:00",
                "match_percentage": 88.0,
                "orphan_percentage": 12.0,
                "cardinality": "M:1",
                "fanout_risk": "low",
                "row_explosion_ratio": 0.88,
                "base_rows": 1000,
                "inner_result_rows": 880,
                "recommended_join": "left",
                "severity": "warning",
                "headline": "12% of loan_master rows have no customer",
            }
        ],
        "runs": [
            {
                "status": "completed",
                "started_at": "2026-10-08T11:00:00",
                "row_count": 121500,
                "execution_time": 42.5,
            },
            {"status": "failed", "started_at": "2026-10-07T11:00:00"},
        ],
    }

    def test_why_left_join_is_answered_from_the_stored_reason(self):
        result = answer(
            "Why did AI choose LEFT JOIN on customer_id?", self.CONTEXT
        )
        self.assertTrue(result["answered"])
        self.assertFalse(result["generated"])
        self.assertIn("nullable", result["answer"])
        self.assertIn("INNER was rejected", result["answer"])
        self.assertIn(
            "join:loan_master.customer_id->customer_master.customer_id",
            result["citations"],
        )

    def test_a_join_answer_attaches_the_measured_evidence(self):
        result = answer("Why LEFT JOIN customer_master?", self.CONTEXT)
        self.assertIn("88.0% of loan_master rows match", result["answer"])
        self.assertIn("12.0% are orphans", result["answer"])

    def test_derived_column_question_is_answered_with_its_business_reason(self):
        result = answer("Why was customer_segment created?", self.CONTEXT)
        self.assertTrue(result["answered"])
        self.assertIn("per-cohort risk", result["answer"])
        self.assertIn("dc:customer_segment", result["citations"])

    def test_a_pending_recommendation_says_it_is_not_active(self):
        result = answer("Why was customer_segment created?", self.CONTEXT)
        self.assertIn("pending your approval", result["answer"])

    def test_run_history_question_is_answered_from_the_runs(self):
        result = answer("Did the last run fail?", self.CONTEXT)
        self.assertEqual(result["source"], "run_history")
        self.assertIn("1 completed, 1 failed", result["answer"])

    def test_an_unmatched_question_admits_it_rather_than_inventing(self):
        result = answer("What is the weather in Nashik?", self.CONTEXT)
        self.assertFalse(result["answered"])
        self.assertIn("could not match your question", result["answer"])
        self.assertEqual(result["citations"], [])

    def test_an_unverified_relationship_is_reported_as_unverified(self):
        context = {
            **self.CONTEXT,
            "verifications": [
                {
                    "base_table": "loan_master",
                    "related_table": "branch_dim",
                    "verified": False,
                    "error": "connection refused",
                }
            ],
            "recommendations": [],
            "runs": [],
        }
        result = answer(
            "Is the branch_dim relationship verified?", context
        )
        self.assertIn("NOT verified", result["answer"])

    def test_the_copilot_never_claims_to_generate_an_answer(self):
        for question in (
            "why left join",
            "why customer_segment",
            "nonsense question",
        ):
            self.assertFalse(answer(question, self.CONTEXT)["generated"])


class ScheduleModelTests(unittest.TestCase):
    def test_pinned_version_column_is_not_nullable(self):
        """A schedule cannot exist without a saved version to execute."""
        column = PipelineSchedule.__table__.columns["pipeline_version_id"]
        self.assertFalse(column.nullable)

    def test_no_password_column_exists_on_a_schedule(self):
        names = {c.name for c in PipelineSchedule.__table__.columns}
        for forbidden in ("password", "secret", "token", "connection_string"):
            self.assertNotIn(forbidden, names)
        self.assertIn("credential_env", names)

    def test_only_an_active_schedule_fires(self):
        schedule = PipelineSchedule(status=STATUS_ACTIVE, is_deleted=False)
        self.assertTrue(schedule.is_active)
        self.assertFalse(
            PipelineSchedule(status=STATUS_PAUSED, is_deleted=False).is_active
        )
        self.assertFalse(
            PipelineSchedule(status=STATUS_ACTIVE, is_deleted=True).is_active
        )

    def test_every_named_frequency_except_once_and_cron_has_a_cron(self):
        for frequency in FREQUENCIES:
            if frequency in ("once", "cron"):
                continue
            self.assertIn(frequency, CRON_FOR_FREQUENCY)


class VerificationModelTests(unittest.TestCase):
    def test_unmeasured_verification_is_never_trustworthy(self):
        row = JoinVerification(measured=False, verdict=VERDICT_UNMEASURED)
        self.assertFalse(row.is_trustworthy)

    def test_a_risky_measured_verification_is_not_trustworthy_either(self):
        row = JoinVerification(measured=True, verdict=VERDICT_RISKY)
        self.assertFalse(row.is_trustworthy)

    def test_a_safe_measured_verification_is_trustworthy(self):
        row = JoinVerification(measured=True, verdict=VERDICT_SAFE)
        self.assertTrue(row.is_trustworthy)

    def test_row_explosion_ratio_is_none_without_a_measurement(self):
        self.assertIsNone(JoinVerification().row_explosion_ratio)
        self.assertEqual(
            JoinVerification(base_rows=1000, inner_rows=39000).row_explosion_ratio,
            39.0,
        )


class MigrationParityTests(unittest.TestCase):
    """The migration is the source of truth; the models must match it."""

    @staticmethod
    def _migration_tables():
        with io.open(MIGRATION, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        tables = {}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_table"
            ):
                name = node.args[0].value
                columns = set()
                for arg in node.args[1:]:
                    if not isinstance(arg, ast.Call):
                        continue
                    if (
                        isinstance(arg.func, ast.Attribute)
                        and arg.func.attr == "Column"
                    ) or (
                        isinstance(arg.func, ast.Name) and arg.func.id == "_uuid"
                    ):
                        columns.add(arg.args[0].value)
                tables[name] = columns
        return tables

    def test_models_match_migration_0005(self):
        tables = self._migration_tables()
        for table_name, model in (
            ("join_verifications", JoinVerification),
            ("pipeline_schedules", PipelineSchedule),
            ("monitoring_alerts", MonitoringAlert),
        ):
            with self.subTest(table=table_name):
                self.assertIn(table_name, tables)
                self.assertEqual(
                    {c.name for c in model.__table__.columns},
                    tables[table_name],
                )

    def test_migration_revision_chain_is_intact(self):
        with io.open(MIGRATION, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('revision = "0005_pipeline_intelligence"', source)
        self.assertIn('down_revision = "0004_pipeline_plan"', source)


if __name__ == "__main__":
    unittest.main()
