import unittest
from uuid import uuid4

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
from models.quality_rule import QualityRule
from models.source_profile_snapshot import SourceProfileSnapshot
from models.transformation_rule import TransformationRule
from routers.quality import get_db, router


class QualityApiTests(unittest.TestCase):
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
            QualityRule.__table__,
            SourceProfileSnapshot.__table__,
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
        self.project_id = uuid4()
        self.version_id = uuid4()
        self.snapshot_id = uuid4()
        with self.session_factory() as db:
            project = Project(
                id=self.project_id,
                project_name="Quality API test",
                file_id="test.csv",
                target_column="target",
            )
            definition = PipelineDefinition(
                project=project,
                pipeline_name="Quality pipeline",
                description="",
            )
            version = PipelineVersion(
                id=self.version_id,
                pipeline_definition=definition,
                version_number=1,
                pipeline_json={},
            )
            snapshot = SourceProfileSnapshot(
                id=self.snapshot_id,
                db_type="postgresql",
                schema_name="public",
                selected_tables=["customers"],
                profile_json={
                    "schema_name": "public",
                    "table_count": 1,
                    "tables": [
                        {
                            "table_name": "customers",
                            "row_count": 100,
                            "column_count": 2,
                            "columns": [
                                {
                                    "column_name": "age",
                                    "data_type": "INTEGER",
                                    "null_count": 12,
                                    "null_percentage": 12.0,
                                    "distinct_count": 50,
                                    "distinct_percentage": 56.8,
                                    "min": 18,
                                    "max": 70,
                                    "value_inventory": [],
                                    "inventory_complete": True,
                                },
                                {
                                    "column_name": "status",
                                    "data_type": "VARCHAR(30)",
                                    "null_count": 0,
                                    "null_percentage": 0.0,
                                    "distinct_count": 2,
                                    "distinct_percentage": 100.0,
                                    "min": "ACTIVE",
                                    "max": "INACTIVE",
                                    "value_inventory": [
                                        {"value": "ACTIVE", "count": 60, "percentage": 60},
                                        {"value": "INACTIVE", "count": 40, "percentage": 40},
                                    ],
                                    "inventory_complete": True,
                                },
                            ],
                        }
                    ],
                },
            )
            db.add_all([project, definition, version, snapshot])
            db.commit()

    def tearDown(self):
        self.client.close()
        Base.metadata.drop_all(bind=self.engine, tables=self.tables)
        self.engine.dispose()

    def _create_rule(self, **overrides):
        body = {
            "rule_name": "Age Null %",
            "rule_type": "null_percentage",
            "severity": "error",
            "enabled": True,
            "rule_config": {
                "table_name": "customers",
                "column_name": "age",
                "max_percentage": 5,
            },
        }
        body.update(overrides)
        return self.client.post(
            "/pipeline/version/%s/quality-rules" % self.version_id,
            json=body,
        )

    def test_create_list_and_get_rule(self):
        created = self._create_rule()
        self.assertEqual(created.status_code, 201, created.text)
        rule_id = created.json()["id"]

        listed = self.client.get(
            "/pipeline/version/%s/quality-rules" % self.version_id
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        self.assertEqual(listed.json()[0]["id"], rule_id)

        retrieved = self.client.get("/pipeline/quality-rules/%s" % rule_id)
        self.assertEqual(retrieved.status_code, 200)
        self.assertEqual(retrieved.json()["rule_name"], "Age Null %")

    def test_invalid_rule_input_and_missing_version(self):
        missing_version = self.client.post(
            "/pipeline/version/%s/quality-rules" % uuid4(),
            json={
                "rule_name": "Age Null",
                "rule_type": "null_percentage",
                "rule_config": {
                    "table_name": "customers",
                    "column_name": "age",
                    "max_percentage": 5,
                },
            },
        )
        self.assertEqual(missing_version.status_code, 404)

        invalid_rule = self._create_rule(rule_type="unknown")
        self.assertEqual(invalid_rule.status_code, 422)

    def test_evaluate_snapshot(self):
        self.assertEqual(self._create_rule().status_code, 201)
        response = self.client.post(
            "/quality/evaluate",
            json={
                "pipeline_version_id": str(self.version_id),
                "snapshot_id": str(self.snapshot_id),
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertEqual(report["overall_status"], "fail")
        self.assertEqual(report["summary"], {"pass": 0, "warning": 0, "fail": 1})
        self.assertEqual(report["results"][0]["actual_value"], 12.0)

    def test_missing_version_and_snapshot(self):
        missing_version = self.client.post(
            "/quality/evaluate",
            json={
                "pipeline_version_id": str(uuid4()),
                "snapshot_id": str(self.snapshot_id),
            },
        )
        self.assertEqual(missing_version.status_code, 404)

        missing_snapshot = self.client.post(
            "/quality/evaluate",
            json={
                "pipeline_version_id": str(self.version_id),
                "snapshot_id": str(uuid4()),
            },
        )
        self.assertEqual(missing_snapshot.status_code, 404)

    def test_disabled_rule_is_not_evaluated(self):
        self.assertEqual(self._create_rule(enabled=False).status_code, 201)
        response = self.client.post(
            "/quality/evaluate",
            json={
                "pipeline_version_id": str(self.version_id),
                "snapshot_id": str(self.snapshot_id),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["overall_status"], "warning")
        self.assertEqual(response.json()["results"], [])

    def test_missing_column_returns_not_found(self):
        response = self._create_rule(
            rule_type="uniqueness",
            rule_config={
                "table_name": "customers",
                "column_name": "missing",
            }
        )
        self.assertEqual(response.status_code, 201)
        evaluated = self.client.post(
            "/quality/evaluate",
            json={
                "pipeline_version_id": str(self.version_id),
                "snapshot_id": str(self.snapshot_id),
            },
        )
        self.assertEqual(evaluated.status_code, 404)

    def test_allowed_values_incomplete_inventory_warns(self):
        with self.session_factory() as db:
            snapshot = db.query(SourceProfileSnapshot).filter_by(id=self.snapshot_id).one()
            profile_json = dict(snapshot.profile_json)
            tables = [dict(table) for table in profile_json["tables"]]
            tables[0]["columns"] = [dict(column) for column in tables[0]["columns"]]
            tables[0]["columns"][1]["inventory_complete"] = False
            profile_json["tables"] = tables
            snapshot.profile_json = profile_json
            db.commit()
        created = self._create_rule(
            rule_name="Status Allowed Values",
            rule_type="allowed_values",
            rule_config={
                "table_name": "customers",
                "column_name": "status",
                "allowed_values": ["ACTIVE", "INACTIVE"],
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        response = self.client.post(
            "/quality/evaluate",
            json={
                "pipeline_version_id": str(self.version_id),
                "snapshot_id": str(self.snapshot_id),
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertEqual(report["overall_status"], "warning")
        self.assertEqual(report["results"][0]["status"], "warning")


if __name__ == "__main__":
    unittest.main()
