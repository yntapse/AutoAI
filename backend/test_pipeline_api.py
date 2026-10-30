import json
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.agent_decision import AgentDecision
from models.cleaning_rule import CleaningRule
from models.catalog_column import CatalogColumn
from models.catalog_relationship import CatalogRelationship
from models.catalog_scan import CatalogScan
from models.catalog_table import CatalogTable
from models.data_source import DataSource
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
    _build_compiled_pipeline_frame,
    _evaluate_saved_quality_rules,
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
            DataSource.__table__,
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
        self.assertIn("executable definition", response.json()["detail"])

    def test_rerun_queues_compiled_saved_definition(self):
        project_id = self._create_project()
        saved = self.client.post(
            "/pipeline/save",
            json={
                "project_id": str(project_id),
                "pipeline_name": "Compiled definition",
                "pipeline_json": {
                    "schema_version": 1,
                    "base_table": "customers",
                    "tables": ["customers"],
                    "joins": [],
                    "target_column": "target",
                },
            },
        ).json()

        with patch("routers.etl_copilot._run_build_job") as run_job:
            response = self.client.post(
                f"/pipeline/{saved['id']}/run",
                json={
                    "db_type": "postgresql",
                    "host": "localhost",
                    "port": 5432,
                    "database": "source",
                },
            )

        self.assertEqual(response.status_code, 202)
        run_job.assert_called_once()
        build_request = run_job.call_args.args[1]
        self.assertEqual(build_request.pipeline_version_id, UUID(saved["versions"][0]["id"]))
        self.assertEqual(build_request.target_column, "target")

    def test_compiled_pipeline_run_must_use_its_saved_data_source(self):
        project_id = self._create_project()
        source_id = uuid4()
        with self.session_factory() as db:
            db.add(
                DataSource(
                    id=source_id,
                    source_name="source-one",
                    db_type="postgresql",
                    host="db.internal",
                    port=5432,
                    database="source",
                    schema_name="public",
                )
            )
            db.commit()

        saved = self.client.post(
            "/pipeline/save",
            json={
                "project_id": str(project_id),
                "pipeline_name": "Source-bound definition",
                "pipeline_json": {
                    "schema_version": 1,
                    "source": {"data_source_id": str(source_id)},
                    "base_table": "customers",
                    "tables": ["customers"],
                    "joins": [],
                },
            },
        ).json()
        response = self.client.post(
            f"/pipeline/{saved['id']}/run",
            json={
                "db_type": "postgresql",
                "host": "other-db.internal",
                "port": 5432,
                "database": "source",
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("saved data source", response.json()["detail"])

    def test_rerun_queues_latest_saved_version(self):
        project_id = self._create_project()
        saved = self.client.post(
            "/pipeline/save",
            json={
                "project_id": str(project_id),
                "pipeline_name": "Runnable",
                "pipeline_json": {
                    "user_request": "select customer data",
                    "generated_plan": {
                        "entity_table": "customers",
                        "entity_key": ["id"],
                    },
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

    def test_pipeline_run_passes_saved_json_to_build(self):
        project_id = self._create_project()
        saved_json = {
            "user_request": "select customer data",
            "generated_plan": {
                "entity_table": "customers",
                "entity_key": ["id"],
            },
            "selected_tables": ["customers"],
        }
        with self.session_factory() as db:
            pipeline = PipelineDefinition(
                project_id=project_id,
                pipeline_name="Saved plan",
            )
            pipeline.versions.append(
                PipelineVersion(version_number=1, pipeline_json=saved_json)
            )
            db.add(pipeline)
            db.commit()
            version_id = pipeline.versions[0].id
            pipeline_id = pipeline.id

        request = EtlCopilotBuildRequest(
            db_type="postgresql",
            host="localhost",
            port=5432,
            database="source",
            user_request="select customer data",
            project_name="Saved plan rerun",
            pipeline_version_id=version_id,
        )
        with patch(
            "routers.etl_copilot._build_dataset_and_register",
            return_value={
                "project_id": str(project_id),
                "file_id": "rerun.csv",
                "rows": 12,
                "lineage": {"tables": ["customers"]},
                "quality_report": {"overall_status": "pass"},
            },
        ) as build_dataset:
            with self.session_factory() as db:
                payload = _build_and_register(db, request)

        self.assertEqual(
            build_dataset.call_args.kwargs["pipeline_json"],
            saved_json,
        )
        self.assertEqual(payload["file_id"], "rerun.csv")
        with self.session_factory() as db:
            run = db.query(PipelineRun).filter_by(
                pipeline_version_id=version_id
            ).one()
            execution_log = json.loads(run.execution_log)
        self.assertEqual(execution_log["lineage"], {"tables": ["customers"]})
        self.assertEqual(
            execution_log["quality_report"]["overall_status"],
            "pass",
        )

    def test_saved_pipeline_build_uses_generated_plan_without_replanning(self):
        saved_plan = {
            "entity_table": "customers",
            "entity_key": ["id"],
            "filters": [
                {"column": "active", "operator": "eq", "value": True}
            ],
        }
        copilot = Mock()
        copilot.create_dataset_from_spec.return_value = {
            "frame": pd.DataFrame({"target": [1, 0]}),
            "columns": ["target"],
            "target_column": "target",
            "sql": "SELECT target FROM customers",
        }
        request = EtlCopilotBuildRequest(
            db_type="postgresql",
            host="localhost",
            port=5432,
            database="source",
            user_request="select customer data",
            project_name="Saved plan rerun",
            target_column="target",
        )
        with (
            patch("routers.etl_copilot.get_copilot", return_value=copilot),
            patch("routers.etl_copilot._connection_string", return_value="db"),
            patch("routers.etl_copilot._resolved_schema", return_value="public"),
            patch(
                "routers.datamodel._create_project_from_frame",
                return_value={
                    "project_id": "project-id",
                    "file_id": "dataset.csv",
                    "rows": 2,
                    "columns": 1,
                },
            ),
        ):
            _build_dataset_and_register(
                Mock(),
                request,
                pipeline_json={"generated_plan": saved_plan},
            )

        copilot.create_dataset.assert_not_called()
        copilot.create_dataset_from_spec.assert_called_once()
        self.assertEqual(
            copilot.create_dataset_from_spec.call_args.kwargs["saved_spec"],
            saved_plan,
        )

    def test_compiled_pipeline_executes_saved_joins_filters_and_transformations(self):
        request = EtlCopilotBuildRequest(
            db_type="postgresql",
            host="localhost",
            port=5432,
            database="source",
            user_request="must not be interpreted",
            project_name="Compiled execution",
            target_column="target",
        )
        source_frame = pd.DataFrame(
            {
                "active": [True, True],
                "target": [1.0, None],
            }
        )
        saved = {
            "schema_version": 1,
            "base_table": "customers",
            "tables": ["customers"],
            "joins": [],
            "selected_columns": {"customers": ["active", "target"]},
            "filters": [
                {
                    "source_table": "customers",
                    "column": "active",
                    "operator": "eq",
                    "value": True,
                }
            ],
            "transformations": [
                {
                    "step_type": "replace_null_values",
                    "table_name": "customers",
                    "configuration": {
                        "column_name": "target",
                        "strategy": "custom",
                        "custom_value": 0,
                    },
                }
            ],
            "quality_rules": [],
        }
        with (
            patch(
                "routers.etl_copilot._connection_string",
                return_value="postgresql://source",
            ),
            patch("routers.etl_copilot._resolved_schema", return_value="public"),
            patch(
                "routers.etl_copilot._columns_for_build",
                return_value={"customers": ["active", "target"]},
            ),
            patch(
                "routers.etl_copilot.extract_to_dataframe",
                return_value=source_frame,
            ) as extract,
            patch("routers.etl_copilot.get_copilot") as get_copilot,
            patch(
                "routers.etl_copilot._create_project_from_frame",
                return_value={
                    "project_id": "project-id",
                    "file_id": "dataset.csv",
                    "rows": 2,
                    "columns": 2,
                },
            ) as register_dataset,
        ):
            payload = _build_dataset_and_register(
                Mock(),
                request,
                pipeline_json=saved,
            )

        get_copilot.assert_not_called()
        result_frame = register_dataset.call_args.kwargs["df"]
        self.assertEqual(result_frame["target"].tolist(), [1.0, 0.0])
        self.assertEqual(payload["file_id"], "dataset.csv")
        self.assertEqual(payload["lineage"]["tables"], ["customers"])
        self.assertEqual(payload["lineage"]["filters"], saved["filters"])
        self.assertIn("WHERE", extract.call_args.args[1])
        self.assertEqual(extract.call_args.kwargs["params"], {"pipeline_filter_0": True})
        self.assertEqual(
            payload["execution_metrics"]["row_counts"]["registered"],
            2,
        )
        self.assertEqual(
            payload["execution_metrics"]["execution_graph"][0]["stage"],
            "extract_and_join",
        )

    def test_compiled_pipeline_executes_saved_aggregations_and_derived_columns(self):
        request = EtlCopilotBuildRequest(
            db_type="postgresql",
            host="localhost",
            port=5432,
            database="source",
            user_request="must not be interpreted",
            project_name="Aggregated execution",
        )
        saved = {
            "base_table": "loans",
            "tables": ["loans"],
            "joins": [],
            "selected_columns": {"loans": ["loan_id", "amount"]},
            "derived_columns": [
                {
                    "name": "amount_ratio",
                    "expression": "loans.amount / NULLIF(loans.amount, 0)",
                }
            ],
            "group_by": [{"source_table": "loans", "column": "loan_id"}],
            "aggregations": [
                {
                    "name": "total_amount",
                    "function": "SUM",
                    "source_table": "loans",
                    "source_column": "amount",
                }
            ],
        }
        with (
            patch(
                "routers.etl_copilot._connection_string",
                return_value="postgresql://source",
            ),
            patch(
                "routers.etl_copilot._columns_for_build",
                return_value={"loans": ["loan_id", "amount"]},
            ),
            patch(
                "routers.etl_copilot.extract_to_dataframe",
                return_value=pd.DataFrame({"loan_id": [1], "total_amount": [5]}),
            ) as extract,
        ):
            result = _build_compiled_pipeline_frame(request, saved)

        sql = extract.call_args.args[1]
        self.assertIn("NULLIF", sql)
        self.assertIn('SUM(agg_src."amount") AS "total_amount"', sql)
        self.assertEqual(result["frame"]["total_amount"].tolist(), [5])
        self.assertEqual(result["execution_graph"][0]["stage"], "extract_and_join")

    def test_compiled_pipeline_executes_the_saved_join_edges(self):
        request = EtlCopilotBuildRequest(
            db_type="postgresql",
            host="localhost",
            port=5432,
            database="source",
            user_request="ignored",
            project_name="Compiled join",
        )
        saved = {
            "base_table": "orders",
            "tables": ["orders", "customers"],
            "joins": [
                {
                    "left_table": "orders",
                    "left_column": "customer_id",
                    "right_table": "customers",
                    "right_column": "id",
                    "join_type": "left",
                }
            ],
        }
        with (
            patch(
                "routers.etl_copilot._connection_string",
                return_value="postgresql://source",
            ),
            patch(
                "routers.etl_copilot._columns_for_build",
                return_value={
                    "orders": ["customer_id", "amount"],
                    "customers": ["id", "name"],
                },
            ),
            patch(
                "routers.etl_copilot.extract_to_dataframe",
                return_value=pd.DataFrame(),
            ),
        ):
            built = _build_compiled_pipeline_frame(request, saved)

        self.assertIn("LEFT JOIN", built["sql"])
        self.assertIn("FROM \"public\".\"orders\" AS t0", built["sql"])
        self.assertIn("ON t0.\"customer_id\" = t1.\"id\"", built["sql"])
        self.assertEqual(
            built["alias_map"],
            {
                "customer_id": "orders.customer_id",
                "amount": "orders.amount",
                "id": "customers.id",
                "name": "customers.name",
            },
        )

    def test_saved_quality_rules_gate_execution(self):
        request = EtlCopilotBuildRequest(
            db_type="postgresql",
            host="localhost",
            port=5432,
            database="source",
            user_request="ignored",
            project_name="Quality check",
        )
        pipeline_json = {
            "quality_rules": [
                {
                    "rule_name": "Unique customer key",
                    "rule_type": "uniqueness",
                    "severity": "error",
                    "rule_config": {
                        "table_name": "customers",
                        "column_name": "id",
                    },
                }
            ]
        }
        with (
            patch(
                "routers.etl_copilot._columns_for_build",
                return_value={"customers": ["id"]},
            ),
            patch(
                "routers.etl_copilot.list_columns",
                return_value={
                    "customers": [
                        {
                            "name": "id",
                            "type": "INTEGER",
                            "nullable": False,
                            "primary_key": True,
                        }
                    ]
                },
            ),
            patch(
                "routers.etl_copilot.extract_to_dataframe",
                return_value=pd.DataFrame({"id": [1, 1]}),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                _evaluate_saved_quality_rules(
                    request,
                    pipeline_json,
                    ["customers"],
                    "public",
                    "postgresql://source",
                )

        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(
            raised.exception.detail["report"]["overall_status"],
            "fail",
        )

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
                "execution_metrics": {
                    "stage_durations_seconds": {"extract_and_join": 0.2},
                    "row_counts": {"query_output": 41, "registered": 41},
                    "quality_metrics": {"quality_score": 95},
                },
                "quality_report": {"overall_status": "pass"},
                "lineage": {"tables": ["customers"]},
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

    def test_pipeline_run_history_persists_execution_and_quality_metrics(self):
        project_id = self._create_project()
        with self.session_factory() as db:
            pipeline = PipelineDefinition(
                project_id=project_id,
                pipeline_name="Metrics history",
                versions=[
                    PipelineVersion(
                        version_number=1,
                        pipeline_json={"generated_plan": {}},
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
            user_request="ignored",
            project_name="Metrics history",
            pipeline_version_id=version_id,
        )
        metrics = {
            "stage_durations_seconds": {"extract_and_join": 0.2},
            "row_counts": {"query_output": 41, "registered": 41},
            "quality_metrics": {"quality_score": 95},
        }
        with patch(
            "routers.etl_copilot._build_dataset_and_register",
            return_value={
                "project_id": str(project_id),
                "file_id": "generated.csv",
                "rows": 41,
                "execution_metrics": metrics,
                "quality_report": {"overall_status": "pass"},
                "lineage": {"tables": ["customers"]},
            },
        ):
            with self.session_factory() as db:
                payload = _build_and_register(db, request)

        with self.session_factory() as db:
            run = db.query(PipelineRun).filter_by(
                id=UUID(payload["pipeline_run_id"])
            ).one()
            log = json.loads(run.execution_log)
            self.assertEqual(log["execution_metrics"], metrics)
            self.assertEqual(log["quality_metrics"]["overall_status"], "pass")
            self.assertEqual(log["row_counts"]["registered"], 41)

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
