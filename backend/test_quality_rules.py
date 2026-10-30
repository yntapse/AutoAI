import unittest
from types import SimpleNamespace
from uuid import uuid4

from services.quality_rules import (
    QualityRuleValidationError,
    evaluate_rules,
    validate_rule_definition,
)


def rule(rule_type, config, severity="error", enabled=True, name="Test rule"):
    return SimpleNamespace(
        id=uuid4(),
        rule_name=name,
        rule_type=rule_type,
        severity=severity,
        enabled=enabled,
        rule_config=config,
    )


def profile(column, row_count=100, table_name="customers"):
    return {
        "schema_name": "public",
        "table_count": 1,
        "tables": [
            {
                "table_name": table_name,
                "row_count": row_count,
                "column_count": 1,
                "columns": [{"column_name": "value", **column}],
            }
        ],
    }


class QualityRuleEvaluationTests(unittest.TestCase):
    def test_null_percentage(self):
        report = evaluate_rules(
            [rule("null_percentage", {
                "table_name": "customers",
                "column_name": "value",
                "max_percentage": 5,
            })],
            profile({"null_count": 12, "null_percentage": 12}),
        )
        self.assertEqual(report["results"][0]["status"], "fail")
        self.assertEqual(report["results"][0]["actual_value"], 12)
        self.assertEqual(report["results"][0]["expected_value"], "<= 5%")
        self.assertEqual(report["results"][0]["evidence"]["null_count"], 12)

    def test_uniqueness_excludes_nulls(self):
        passing = evaluate_rules(
            [rule("uniqueness", {"table_name": "customers", "column_name": "value"})],
            profile({"null_count": 2, "distinct_count": 8}, row_count=10),
        )
        failing = evaluate_rules(
            [rule("uniqueness", {"table_name": "customers", "column_name": "value"})],
            profile({"null_count": 2, "distinct_count": 7}, row_count=10),
        )
        self.assertEqual(passing["results"][0]["status"], "pass")
        self.assertEqual(failing["results"][0]["status"], "fail")

    def test_distinct_percentage(self):
        result = evaluate_rules(
            [rule("distinct_percentage", {
                "table_name": "customers",
                "column_name": "value",
                "min_percentage": 20,
            })],
            profile({
                "null_count": 10,
                "distinct_count": 18,
                "distinct_percentage": 20,
            }),
        )
        self.assertEqual(result["results"][0]["status"], "pass")

    def test_allowed_values_passes_and_fails_with_complete_inventory(self):
        configured = rule("allowed_values", {
            "table_name": "customers",
            "column_name": "value",
            "allowed_values": ["A", "B"],
        })
        passing = evaluate_rules(
            [configured],
            profile({
                "value_inventory": [
                    {"value": "A", "count": 3},
                    {"value": "B", "count": 2},
                ],
                "inventory_complete": True,
            }, row_count=5),
        )
        failing = evaluate_rules(
            [configured],
            profile({
                "value_inventory": [
                    {"value": "A", "count": 3},
                    {"value": "C", "count": 2},
                ],
                "inventory_complete": True,
            }, row_count=5),
        )
        self.assertEqual(passing["results"][0]["status"], "pass")
        self.assertEqual(failing["results"][0]["status"], "fail")
        self.assertEqual(failing["results"][0]["actual_value"], ["C"])

    def test_allowed_values_never_passes_for_incomplete_inventory(self):
        result = evaluate_rules(
            [rule("allowed_values", {
                "table_name": "customers",
                "column_name": "value",
                "allowed_values": ["A"],
            })],
            profile({
                "value_inventory": [{"value": "A", "count": 4}],
                "inventory_complete": False,
            }, row_count=4),
        )
        self.assertEqual(result["results"][0]["status"], "warning")

    def test_numeric_range(self):
        result = evaluate_rules(
            [rule("numeric_range", {
                "table_name": "customers",
                "column_name": "value",
                "minimum": 0,
                "maximum": 100,
            })],
            profile({"min": -1, "max": 50}),
        )
        self.assertEqual(result["results"][0]["status"], "fail")
        self.assertEqual(result["results"][0]["actual_value"], {"min": -1, "max": 50})

    def test_data_type_normalization(self):
        result = evaluate_rules(
            [rule("data_type", {
                "table_name": "customers",
                "column_name": "value",
                "expected_type": "date",
            })],
            profile({"data_type": "DATE"}),
        )
        self.assertEqual(result["results"][0]["status"], "pass")

    def test_row_count_uses_selected_table_total(self):
        tables = {
            "tables": [
                {"table_name": "customers", "row_count": 60, "columns": []},
                {"table_name": "orders", "row_count": 50, "columns": []},
            ]
        }
        result = evaluate_rules(
            [rule("row_count", {"minimum": 100})],
            tables,
        )
        self.assertEqual(result["results"][0]["status"], "pass")
        self.assertEqual(result["results"][0]["actual_value"], 110)

    def test_warning_severity_and_insufficient_evidence(self):
        failed_warning = rule(
            "null_percentage",
            {"table_name": "customers", "column_name": "value", "max_percentage": 5},
            severity="warning",
        )
        insufficient = rule(
            "numeric_range",
            {"table_name": "customers", "column_name": "value", "minimum": 0, "maximum": 10},
        )
        report = evaluate_rules(
            [failed_warning, insufficient],
            profile({"null_percentage": 12, "min": None, "max": None}),
        )
        self.assertEqual([item["status"] for item in report["results"]], ["warning", "warning"])
        self.assertEqual(report["overall_status"], "warning")

    def test_overall_status_precedence(self):
        rules = [
            rule("null_percentage", {
                "table_name": "customers",
                "column_name": "value",
                "max_percentage": 5,
            }),
            rule("data_type", {
                "table_name": "customers",
                "column_name": "value",
                "expected_type": "integer",
            }, severity="warning"),
        ]
        report = evaluate_rules(
            rules,
            profile({
                "null_percentage": 10,
                "data_type": "VARCHAR",
            }),
        )
        self.assertEqual(report["overall_status"], "fail")
        self.assertEqual(report["summary"], {"pass": 0, "warning": 1, "fail": 1})

    def test_no_enabled_rules_returns_warning(self):
        report = evaluate_rules(
            [rule("row_count", {"minimum": 1}, enabled=False)],
            {"tables": []},
        )
        self.assertEqual(report["overall_status"], "warning")
        self.assertEqual(report["summary"], {"pass": 0, "warning": 0, "fail": 0})

    def test_invalid_rule_definitions_are_rejected(self):
        invalid = [
            ("other", "error", {}),
            ("row_count", "critical", {"minimum": 1}),
            ("null_percentage", "error", {
                "table_name": "customers",
                "column_name": "value",
                "max_percentage": 101,
            }),
            ("numeric_range", "error", {
                "table_name": "customers",
                "column_name": "value",
                "minimum": 20,
                "maximum": 10,
            }),
            ("allowed_values", "error", {
                "table_name": "customers",
                "column_name": "value",
                "allowed_values": [],
            }),
            ("allowed_values", "error", {
                "table_name": "customers",
                "column_name": "value",
                "allowed_values": ["A", None],
            }),
            ("row_count", "error", {}),
        ]
        for rule_type, severity, config in invalid:
            with self.subTest(rule_type=rule_type, severity=severity, config=config):
                with self.assertRaises(QualityRuleValidationError):
                    validate_rule_definition(rule_type, severity, config)

    def test_missing_profile_column_is_reported(self):
        with self.assertRaisesRegex(LookupError, "Column 'missing'"):
            evaluate_rules(
                [rule("uniqueness", {"table_name": "customers", "column_name": "missing"})],
                profile({"null_count": 0, "distinct_count": 1}),
            )


if __name__ == "__main__":
    unittest.main()
