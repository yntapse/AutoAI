import unittest

import pandas as pd

from services.data_cleaner import (
    CleaningValidationError,
    apply_cleaning_steps,
    validate_cleaning_steps,
)


class DataCleaningTests(unittest.TestCase):
    def test_remove_null_rows_by_column_and_all_columns(self):
        frame = pd.DataFrame(
            {"age": [20, None, 40], "city": ["A", "B", None]}
        )
        by_column, summary = apply_cleaning_steps(
            frame,
            [
                {
                    "step_type": "remove_null_rows",
                    "configuration": {"scope": "column", "column_name": "age"},
                }
            ],
        )
        self.assertEqual(len(by_column), 2)
        self.assertEqual(summary["rows_removed"], 1)
        all_columns, _ = apply_cleaning_steps(
            frame,
            [
                {
                    "step_type": "remove_null_rows",
                    "configuration": {"scope": "all_columns"},
                }
            ],
        )
        self.assertEqual(len(all_columns), 1)
        self.assertEqual(summary["columns_changed"], [])

    def test_replace_null_strategies(self):
        frame = pd.DataFrame(
            {
                "zero": [1.0, None, 3.0],
                "mean": [1.0, None, 5.0],
                "median": [1.0, None, 5.0],
                "mode": ["x", None, "x"],
                "custom": ["a", None, "b"],
            }
        )
        steps = [
            ("zero", "zero"),
            ("mean", "mean"),
            ("median", "median"),
            ("mode", "mode"),
            ("custom", "custom"),
        ]
        configurations = [
            {
                "step_type": "replace_null_values",
                "configuration": {
                    "column_name": column,
                    "strategy": strategy,
                    **({"custom_value": "manual"} if strategy == "custom" else {}),
                },
            }
            for column, strategy in steps
        ]
        cleaned, _ = apply_cleaning_steps(frame, configurations)
        self.assertEqual(cleaned.loc[1].to_dict(), {
            "zero": 0.0,
            "mean": 3.0,
            "median": 3.0,
            "mode": "x",
            "custom": "manual",
        })
        self.assertTrue(pd.isna(frame.loc[1, "custom"]))

    def test_remove_duplicates_entire_row_and_selected_columns(self):
        frame = pd.DataFrame(
            {
                "id": [1, 1, 2, 2],
                "value": ["first", "second", "third", "third"],
            }
        )
        all_row, _ = apply_cleaning_steps(
            frame,
            [
                {
                    "step_type": "remove_duplicates",
                    "configuration": {"scope": "entire_row"},
                }
            ],
        )
        self.assertEqual(len(all_row), 3)
        selected, summary = apply_cleaning_steps(
            frame,
            [
                {
                    "step_type": "remove_duplicates",
                    "configuration": {
                        "scope": "selected_columns",
                        "columns": ["id"],
                    },
                }
            ],
        )
        self.assertEqual(len(selected), 2)
        self.assertEqual(summary["rows_removed"], 2)
        self.assertEqual(summary["columns_changed"], [])

    def test_convert_data_types(self):
        frame = pd.DataFrame(
            {
                "string": [1, 2],
                "integer": ["1", "2"],
                "float": ["1.5", "2.5"],
                "boolean": ["yes", "no"],
                "date": ["2024-01-01", "2024-01-02"],
            }
        )
        steps = [
            {
                "step_type": "convert_data_type",
                "configuration": {"column_name": column, "target_type": target},
            }
            for column, target in [
                ("string", "string"),
                ("integer", "integer"),
                ("float", "float"),
                ("boolean", "boolean"),
                ("date", "date"),
            ]
        ]
        cleaned, summary = apply_cleaning_steps(frame, steps)
        self.assertEqual(str(cleaned["integer"].dtype), "Int64")
        self.assertEqual(cleaned["boolean"].tolist(), [True, False])
        self.assertEqual(summary["columns_changed"], ["string", "integer", "float", "boolean", "date"])

    def test_convert_type_rejects_invalid_data(self):
        with self.assertRaisesRegex(CleaningValidationError, "could not convert"):
            apply_cleaning_steps(
                pd.DataFrame({"age": ["10", "unknown"]}),
                [
                    {
                        "step_type": "convert_data_type",
                        "configuration": {
                            "column_name": "age",
                            "target_type": "integer",
                        },
                    }
                ],
            )

    def test_outlier_remove_cap_and_median_use_iqr(self):
        frame = pd.DataFrame({"value": [1, 2, 3, 4, 100]})
        config = {"column_name": "value", "strategy": "remove"}
        removed, summary = apply_cleaning_steps(
            frame, [{"step_type": "handle_outliers", "configuration": config}]
        )
        self.assertEqual(removed["value"].tolist(), [1, 2, 3, 4])
        self.assertEqual(summary["rows_removed"], 1)

        capped, _ = apply_cleaning_steps(
            frame,
            [
                {
                    "step_type": "handle_outliers",
                    "configuration": {**config, "strategy": "cap"},
                }
            ],
        )
        self.assertEqual(capped["value"].iloc[-1], 7.0)
        replaced, _ = apply_cleaning_steps(
            frame,
            [
                {
                    "step_type": "handle_outliers",
                    "configuration": {**config, "strategy": "median"},
                }
            ],
        )
        self.assertEqual(replaced["value"].iloc[-1], 3)

    def test_disabled_steps_and_step_order(self):
        frame = pd.DataFrame({"id": [1, 1], "value": [None, 2]})
        result, summary = apply_cleaning_steps(
            frame,
            [
                {
                    "step_name": "Deduplicate",
                    "step_type": "remove_duplicates",
                    "configuration": {"scope": "entire_row"},
                    "enabled": True,
                },
                {
                    "step_name": "Disabled null removal",
                    "step_type": "remove_null_rows",
                    "configuration": {"scope": "all_columns"},
                    "enabled": False,
                },
            ],
        )
        self.assertEqual(len(result), 2)
        self.assertEqual(summary["applied_steps"][1]["status"], "disabled")

    def test_invalid_configs_and_missing_columns_are_rejected(self):
        with self.assertRaisesRegex(CleaningValidationError, "unsupported step_type"):
            validate_cleaning_steps([{"step_type": "unknown", "configuration": {}}])
        with self.assertRaisesRegex(CleaningValidationError, "unsupported step_type"):
            validate_cleaning_steps([{"step_type": [], "configuration": {}}])
        with self.assertRaisesRegex(CleaningValidationError, "unsupported field"):
            validate_cleaning_steps([{"step_type": "remove_duplicates", "typo": True}])
        with self.assertRaisesRegex(CleaningValidationError, "step_name must not be blank"):
            validate_cleaning_steps(
                [{"step_type": "remove_duplicates", "step_name": " ", "configuration": {}}]
            )
        with self.assertRaisesRegex(CleaningValidationError, "strategy"):
            validate_cleaning_steps(
                [
                    {
                        "step_type": "replace_null_values",
                        "configuration": {"column_name": "x", "strategy": "random"},
                    }
                ]
            )
        with self.assertRaisesRegex(CleaningValidationError, "does not exist"):
            apply_cleaning_steps(
                pd.DataFrame({"age": [1]}),
                [
                    {
                        "step_type": "remove_null_rows",
                        "configuration": {
                            "scope": "column",
                            "column_name": "missing",
                        },
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
