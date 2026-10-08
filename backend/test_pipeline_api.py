from datetime import datetime
import unittest
from uuid import UUID, uuid4
from unittest.mock import Mock, patch

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.agent_decision import AgentDecision
from models.cleaning_rule import CleaningRule
from models.pipeline_definition import PipelineDefinition
from models.pipeline_run import PipelineRun
from models.pipeline_version import PipelineVersion
from models.project import Project
from models.source_profile_snapshot import SourceProfileSnapshot
from models.transformation_rule import TransformationRule
from routers.pipelines import get_db, router
from routers.etl_copilot import (
    EtlCopilotBuildRequest,
    _build_and_register,
    _build_dataset_and_register,
)


class PipelineApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.tables = [
            Project.__table__,
            PipelineDefinition.__table__,
            PipelineVersion.__table__,
            PipelineRun.__table__,
            CleaningRule.__table__,
            TransformationRule.__table__,
            AgentDecision.__table__,
        ]
        Base.metadata.create_all(bind=self.engine, tables=self.tables)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
        )
        app = FastAPI()
        app.include_router(router)

        def override_get_db():
            db = self.session_factory()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        Base.metadata.drop_all(bind=self.engine, tables=self.tables)
        self.engine.dispose()

    def _create_project(self):
        project_id = uuid4()
        with self.session_factory() as db:
            db.add(
                Project(
                    id=project_id,
                    project_name="API test project",
                    file_id="test.csv",
                    target_column="target",
                )
            )
            db.commit()
        return project_id

    def test_save_list_versions_and_run_history(self):
        project_id = self._create_project()

        save_response = self.client.post(
            "/pipeline/save",
            json={
                "project_id": str(project_id),
                "pipeline_name": "  Customer cleanup  ",
                "description": "Initial pipeline",
                "pipeline_json": {"steps": [{"type": "drop_nulls"}]},
            },
        )

        self.assertEqual(save_response.status_code, 201)
        pipeline = save_response.json()
        self.assertEqual(pipeline["pipeline_name"], "Customer cleanup")
        self.assertEqual(len(pipeline["versions"]), 1)
        self.assertEqual(pipeline["versions"][0]["version_number"], 1)

        listed = self.client.get(f"/pipeline/{project_id}")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([item["id"] for item in listed.json()], [pipeline["id"]])

        versions = self.client.get(f"/pipeline/version/{pipeline['id']}")
        self.assertEqual(versions.status_code, 200)
        self.assertEqual([item["version_number"] for item in versions.json()], [1])

        with self.session_factory() as db:
            version = db.query(PipelineVersion).filter_by(
                pipeline_definition_id=UUID(pipeline["id"])
            ).one()
            db.add(
                PipelineRun(
                    pipeline_version_id=version.id,
                    project_id=project_id,
                    status="completed",
                    started_at=datetime.utcnow(),
                    execution_log="Pipeline completed",
                )
            )
            db.commit()

        history = self.client.get(f"/pipeline/run-history/{project_id}")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.json()), 1)
        self.assertEqual(history.json()[0]["status"], "completed")
        detail = self.client.get(f"/pipeline/detail/{pipeline['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["latest_run_status"], "completed")

    def test_save_rejects_unknown_project(self):
        response = self.client.post(
            "/pipeline/save",
            json={
                "project_id": str(uuid4()),
                "pipeline_name": "Missing project pipeline",
                "pipeline_json": {},
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Project not found.")

    def test_save_rejects_blank_pipeline_name(self):
        project_id = self._create_project()

        response = self.client.post(
            "/pipeline/save",
            json={
                "project_id": str(project_id),
                "pipeline_name": "   ",
                "pipeline_json": {},
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_save_links_initial_build_run(self):
        project_id = self._create_project()
        with self.session_factory() as db:
            run = PipelineRun(
                project_id=project_id,
                status="completed",
                dataset_file_id="dataset.csv",
                row_count=23,
                execution_time=1.25,
            )
            db.add(run)
            db.commit()
            run_id = run.id

        response = self.client.post(
            "/pipeline/save",
            json={
                "project_id": str(project_id),
                "pipeline_name": "Initial build",
                "pipeline_json": {"user_request": "select all"},
                "initial_run_id": str(run_id),
            },
        )

        self.assertEqual(response.status_code, 201)
        with self.session_factory() as db:
            saved_run = db.query(PipelineRun).filter_by(id=run_id).one()
            self.assertEqual(saved_run.pipeline_version_id, UUID(response.json()["versions"][0]["id"]))

    def test_version_update_compare_restore_clone_and_soft_delete(self):
        project_id = self._create_project()
        saved = self.client.post(
            "/pipeline/save",
            json={
                "project_id": str(project_id),
                "pipeline_name": "Versioned",
                "pipeline_json": {"user_request": "first", "tables": ["users"]},
            },
        ).json()
        pipeline_id = saved["id"]

        updated = self.client.put(
            f"/pipeline/{pipeline_id}",
            json={
                "pipeline_name": "Versioned revised",
                "pipeline_json": {"user_request": "second", "tables": ["users"]},
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(len(updated.json()["versions"]), 2)

        compared = self.client.get(f"/pipeline/version/{pipeline_id}/compare/1/2")
        self.assertEqual(compared.status_code, 200)
        self.assertEqual(compared.json()["changes"]["user_request"]["before"], "first")
        self.assertEqual(compared.json()["changes"]["user_request"]["after"], "second")

        restored = self.client.post(
            f"/pipeline/{pipeline_id}/restore/{saved['versions'][0]['id']}"
        )
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(
            [version["version_number"] for version in restored.json()["versions"]],
            [1, 2, 3],
        )
        self.assertEqual(restored.json()["versions"][2]["pipeline_json"]["user_request"], "first")

        cloned = self.client.post(f"/pipeline/{pipeline_id}/clone")
        self.assertEqual(cloned.status_code, 201)
        self.assertEqual(cloned.json()["versions"][0]["version_number"], 1)
        self.assertNotEqual(cloned.json()["id"], pipeline_id)

        deleted = self.client.delete(f"/pipeline/{pipeline_id}")
        self.assertEqual(deleted.status_code, 204)
        listing = self.client.get(f"/pipeline/{project_id}")
        self.assertEqual(
            [item["id"] for item in listing.json()],
            [cloned.json()["id"]],
        )

    def test_pipeline_configuration_rejects_credentials(self):
        project_id = self._create_project()
        response = self.client.post(
            "/pipeline/save",
            json={
                "project_id": str(project_id),
                "pipeline_name": "Unsafe",
                "pipeline_json": {
                    "user_request": "select all",
                    "connection": {"password": "never persist"},
                },
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("credentials", response.json()["detail"])

    def test_rerun_rejects_pipeline_without_saved_dataset_request(self):
        project_id = self._create_project()
        saved = self.client.post(
            "/pipeline/save",
            json={
                "project_id": str(project_id),
                "pipeline_name": "No request",
                "pipeline_json": {},
            },
        ).json()
        response = self.client.post(
            f"/pipeline/{saved['id']}/run",
            json={
                "db_type": "postgresql",
                "host": "localhost",
                "port": 5432,
                "database": "source",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("dataset request", response.json()["detail"])

    def test_rerun_queues_latest_saved_version(self):
        project_id = self._create_project()
        saved = self.client.post(
            "/pipeline/save",
            json={
                "project_id": str(project_id),
                "pipeline_name": "Runnable",
                "pipeline_json": {
                    "user_request": "select customer data",
                    "cleaning_steps": [
                        {
                            "step_name": "Remove empty",
                            "step_type": "remove_null_rows",
                            "configuration": {"scope": "all_columns"},
                            "enabled": True,
                        }
                    ],
                },
            },
        ).json()
        latest_version = saved["versions"][0]

        with patch("routers.etl_copilot._run_build_job") as run_job:
            response = self.client.post(
                f"/pipeline/{saved['id']}/run",
                json={
                    "db_type": "postgresql",
                    "host": "localhost",
                    "port": 5432,
                    "database": "source",
                    "username": "db-user",
                    "password": "request-only-secret",
                },
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["pipeline_version_id"], latest_version["id"])
        run_job.assert_called_once()
        build_request = run_job.call_args.args[1]
        self.assertEqual(build_request.password, "request-only-secret")
        self.assertEqual(build_request.cleaning_steps[0]["step_type"], "remove_null_rows")

    def test_cleaning_steps_are_saved_as_immutable_pipeline_versions(self):
        project_id = self._create_project()
        saved = self.client.post(
            "/pipeline/save",
            json={
                "project_id": str(project_id),
                "pipeline_name": "Cleaning versioning",
                "pipeline_json": {"user_request": "select users", "cleaning_steps": []},
            },
        ).json()
        pipeline_id = saved["id"]

        updated = self.client.put(
            f"/pipeline/{pipeline_id}/cleaning-steps",
            json={
                "steps": [
                    {
                        "step_name": "Remove empty rows",
                        "step_type": "remove_null_rows",
                        "configuration": {"scope": "all_columns"},
                        "enabled": True,
                    },
                    {
                        "step_name": "Normalize age",
                        "step_type": "convert_data_type",
                        "configuration": {"column_name": "age", "target_type": "integer"},
                        "enabled": False,
                    },
                ]
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(len(updated.json()["versions"]), 2)
        latest_version = updated.json()["versions"][-1]
        steps_response = self.client.get(
            f"/pipeline/version/{latest_version['id']}/cleaning-steps"
        )
        self.assertEqual(steps_response.status_code, 200)
        self.assertEqual([step["step_order"] for step in steps_response.json()], [1, 2])
        self.assertFalse(steps_response.json()[1]["enabled"])
        self.assertIsNotNone(steps_response.json()[0]["created_at"])

        restored = self.client.post(
            f"/pipeline/{pipeline_id}/restore/{saved['versions'][0]['id']}"
        )
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(len(restored.json()["versions"]), 3)
        newest_steps = self.client.get(
            f"/pipeline/version/{restored.json()['versions'][-1]['id']}/cleaning-steps"
        )
        self.assertEqual(newest_steps.json(), [])

    def test_cleaning_step_config_is_validated_before_persistence(self):
        project_id = self._create_project()
        saved = self.client.post(
            "/pipeline/save",
            json={
                "project_id": str(project_id),
                "pipeline_name": "Invalid cleaning",
                "pipeline_json": {"user_request": "select users"},
            },
        ).json()
        response = self.client.put(
            f"/pipeline/{saved['id']}/cleaning-steps",
            json={
                "steps": [
                    {
                        "step_name": "Bad replacement",
                        "step_type": "replace_null_values",
                        "configuration": {
                            "column_name": "age",
                            "strategy": "random",
                        },
                    }
                ]
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(len(self.client.get(f"/pipeline/version/{saved['id']}").json()), 1)

    def test_build_records_run_metrics_and_initial_build_history(self):
        project_id = self._create_project()
        request = EtlCopilotBuildRequest(
            db_type="postgresql",
            host="localhost",
            port=5432,
            database="source",
            user_request="select customer data",
            project_name="ETL test",
        )
        with patch(
            "routers.etl_copilot._build_dataset_and_register",
            return_value={
                "project_id": str(project_id),
                "file_id": "generated.csv",
                "rows": 41,
            },
        ):
            with self.session_factory() as db:
                payload = _build_and_register(db, request)

        self.assertEqual(payload["pipeline_version_id"], None)
        self.assertIsNotNone(payload["pipeline_run_id"])
        with self.session_factory() as db:
            run = db.query(PipelineRun).filter_by(id=UUID(payload["pipeline_run_id"])).one()
            self.assertEqual(run.status, "completed")
            self.assertIsNone(run.pipeline_version_id)
            self.assertEqual(run.dataset_file_id, "generated.csv")
            self.assertEqual(run.row_count, 41)
            self.assertGreaterEqual(run.execution_time, 0)

    def test_build_applies_cleaning_before_project_registration(self):
        source_frame = pd.DataFrame(
            {"age": [20.0, None, 100.0], "name": ["A", "B", "C"]}
        )
        request = EtlCopilotBuildRequest(
            db_type="postgresql",
            host="localhost",
            port=5432,
            database="source",
            username="test-user",
            user_request="select customer data",
            project_name="ETL test",
            target_column="age",
            cleaning_steps=[
                {
                    "step_name": "Fill missing age",
                    "step_type": "replace_null_values",
                    "configuration": {"column_name": "age", "strategy": "median"},
                    "enabled": True,
                },
                {
                    "step_name": "Remove duplicate names",
                    "step_type": "remove_duplicates",
                    "configuration": {
                        "scope": "selected_columns",
                        "columns": ["name"],
                    },
                    "enabled": True,
                },
            ],
        )
        copilot = Mock()
        copilot.create_dataset.return_value = {
            "frame": source_frame.copy(deep=True),
            "sql": "SELECT customer data",
            "columns": ["age", "name"],
        }
        registered = {}

        def capture_registration(**kwargs):
            registered["frame"] = kwargs["df"].copy(deep=True)
            return {
                "project_id": str(uuid4()),
                "project_name": kwargs["project_name"],
                "file_id": "cleaned.csv",
                "target_column": kwargs["target_column"],
                "rows": len(kwargs["df"]),
                "columns": len(kwargs["df"].columns),
            }

        with patch("routers.etl_copilot.get_copilot", return_value=copilot), patch(
            "routers.etl_copilot._create_project_from_frame",
            side_effect=capture_registration,
        ):
            with self.session_factory() as db:
                payload = _build_dataset_and_register(db, request)

        self.assertEqual(registered["frame"]["age"].tolist(), [20.0, 60.0, 100.0])
        self.assertEqual(payload["cleaning_summary"]["original_row_count"], 3)
        self.assertEqual(payload["cleaning_summary"]["new_row_count"], 3)
        self.assertEqual(payload["cleaning_summary"]["columns_changed"], ["age"])
        self.assertTrue(source_frame["age"].isna().iloc[1])

    def test_failed_rerun_is_persisted_against_its_saved_version(self):
        project_id = self._create_project()
        with self.session_factory() as db:
            pipeline = PipelineDefinition(
                project_id=project_id,
                pipeline_name="Failure test",
                versions=[
                    PipelineVersion(
                        version_number=1,
                        pipeline_json={"user_request": "select customer data"},
                    )
                ],
            )
            db.add(pipeline)
            db.commit()
            version_id = pipeline.versions[0].id

        request = EtlCopilotBuildRequest(
            db_type="postgresql",
            host="localhost",
            port=5432,
            database="source",
            user_request="select customer data",
            project_name="ETL test",
            pipeline_version_id=version_id,
        )
        with patch(
            "routers.etl_copilot._build_dataset_and_register",
            side_effect=RuntimeError("source unavailable"),
        ):
            with self.session_factory() as db:
                with self.assertRaises(RuntimeError):
                    _build_and_register(db, request)

        with self.session_factory() as db:
            run = db.query(PipelineRun).filter_by(
                pipeline_version_id=version_id
            ).one()
            self.assertEqual(run.project_id, project_id)
            self.assertEqual(run.status, "failed")
            self.assertIn("RuntimeError", run.execution_log)


if __name__ == "__main__":
    unittest.main()
