import unittest
from unittest.mock import Mock, patch

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.cleaning import router


class CleaningApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.frame = pd.DataFrame(
            {"age": [10.0, None, 100.0], "name": ["A", "B", "C"]}
        )
        copilot = Mock()
        copilot.create_dataset.return_value = {"frame": self.frame.copy(deep=True)}
        self.copilot = copilot
        self.patch = patch("routers.cleaning.get_copilot", return_value=copilot)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.client.close()

    def _request(self, steps):
        return self.client.post(
            "/cleaning/preview",
            json={
                "db_type": "postgresql",
                "host": "localhost",
                "port": 5432,
                "database": "source",
                "username": "test-user",
                "user_request": "select records",
                "cleaning_steps": steps,
            },
        )

    def test_preview_returns_counts_columns_steps_and_sample_without_persisting(self):
        response = self._request(
            [
                {
                    "step_name": "Drop missing ages",
                    "step_type": "remove_null_rows",
                    "configuration": {
                        "scope": "column",
                        "column_name": "age",
                    },
                    "enabled": True,
                }
            ]
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["original_row_count"], 3)
        self.assertEqual(result["new_row_count"], 2)
        self.assertEqual(result["rows_removed"], 1)
        self.assertEqual(result["columns_changed"], [])
        self.assertEqual(result["applied_steps"][0]["status"], "applied")
        self.assertEqual(len(result["sample_rows"]), 2)
        self.copilot.create_dataset.assert_called_once()
        self.assertTrue(self.frame["age"].isna().iloc[1])

    def test_invalid_step_is_rejected_before_dataset_query(self):
        response = self._request(
            [
                {
                    "step_type": "not_supported",
                    "configuration": {},
                }
            ]
        )
        self.assertEqual(response.status_code, 422)
        self.copilot.create_dataset.assert_not_called()

    def test_preview_reports_non_row_column_changes(self):
        response = self._request(
            [
                {
                    "step_name": "Fill missing",
                    "step_type": "replace_null_values",
                    "configuration": {
                        "column_name": "age",
                        "strategy": "median",
                    },
                }
            ]
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["columns_changed"], ["age"])
        self.assertEqual(response.json()["rows_removed"], 0)


if __name__ == "__main__":
    unittest.main()
