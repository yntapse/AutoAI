"""Pipeline operations API tests: scheduling, monitoring, run metrics.

SQLite in-memory, dependency override, no network. The AutoML readiness
endpoint is exercised only on its no-dataset path, which needs no file and no
import of main.py.
"""

import unittest
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base

# SQLAlchemy configures every registered mapper at once, so the full set must
# be imported or mapper configuration fails. Same reason as test_catalog_api.
from models.agent_decision import AgentDecision  # noqa: F401
from models.agent_run import AgentRun  # noqa: F401
from models.catalog_column import CatalogColumn  # noqa: F401
from models.catalog_relationship import CatalogRelationship  # noqa: F401
from models.catalog_scan import CatalogScan  # noqa: F401
from models.catalog_table import CatalogTable  # noqa: F401
from models.cleaning_rule import CleaningRule  # noqa: F401
from models.dataset_profile import DatasetProfile  # noqa: F401
from models.join_verification import JoinVerification  # noqa: F401
from models.pipeline_plan import PipelinePlan  # noqa: F401
from models.plan_recommendation import PlanRecommendation  # noqa: F401
from models.quality_rule import QualityRule  # noqa: F401
from models.source_profile_snapshot import SourceProfileSnapshot  # noqa: F401
from models.transformation_rule import TransformationRule  # noqa: F401

from models.data_source import DataSource
from models.monitoring_alert import MonitoringAlert
from models.pipeline_definition import PipelineDefinition
from models.pipeline_run import PipelineRun
from models.pipeline_schedule import (
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_PAUSED,
    PipelineSchedule,
)
from models.pipeline_version import PipelineVersion
from models.project import Project
from routers.orchestration import get_db, router


class OrchestrationApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.tables = [
            Project.__table__,
            DataSource.__table__,
            PipelineDefinition.__table__,
            PipelineVersion.__table__,
            PipelineRun.__table__,
            PipelineSchedule.__table__,
            MonitoringAlert.__table__,
        ]
        Base.metadata.create_all(bind=self.engine, tables=self.tables)
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, autocommit=False
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
        self.source_id = uuid4()
        self.pipeline_id = uuid4()
        self.version_id = uuid4()
        self._seed()

    def tearDown(self):
        Base.metadata.drop_all(bind=self.engine, tables=self.tables)
        self.engine.dispose()

    def _seed(self):
        db = self.session_factory()
        try:
            db.add(
                Project(
                    id=self.project_id,
                    project_name="Credit Risk",
                    file_id="seed.csv",
                    target_column="defaulted",
                )
            )
            db.add(
                DataSource(
                    id=self.source_id,
                    source_name="meridian_dw",
                    db_type="postgresql",
                    host="localhost",
                    port=5432,
                    database="meridian",
                    schema_name="public",
                    status="scanned",
                )
            )
            db.add(
                PipelineDefinition(
                    id=self.pipeline_id,
                    project_id=self.project_id,
                    pipeline_name="credit_risk_dataset",
                    status="active",
                )
            )
            db.add(
                PipelineVersion(
                    id=self.version_id,
                    pipeline_definition_id=self.pipeline_id,
                    version_number=1,
                    pipeline_json={"schema_version": 1, "tables": ["loan_master"]},
                )
            )
            db.commit()
        finally:
            db.close()

    def _add_runs(self):
        db = self.session_factory()
        try:
            base = datetime(2026, 10, 8, 9, 0, 0)
            for index, (status, rows, seconds) in enumerate(
                [
                    ("completed", 121_500, 40.0),
                    ("failed", None, 12.0),
                    ("completed", 120_900, 60.0),
                    ("running", None, None),
                ]
            ):
                db.add(
                    PipelineRun(
                        id=uuid4(),
                        pipeline_version_id=self.version_id,
                        project_id=self.project_id,
                        status=status,
                        started_at=base + timedelta(hours=index),
                        completed_at=(
                            base + timedelta(hours=index, minutes=2)
                            if seconds
                            else None
                        ),
                        row_count=rows,
                        execution_time=seconds,
                        dataset_file_id=f"dataset_{index}.csv" if rows else None,
                    )
                )
            db.commit()
        finally:
            db.close()

    def _create_schedule(self, **overrides):
        payload = {
            "pipeline_definition_id": str(self.pipeline_id),
            "data_source_id": str(self.source_id),
            "frequency": "daily",
        }
        payload.update(overrides)
        return self.client.post("/orchestration/schedules", json=payload)

    # ------------------------------------------------------------------
    # Run metrics
    # ------------------------------------------------------------------

    def test_pipeline_list_includes_metrics_and_version_count(self):
        self._add_runs()
        response = self.client.get("/orchestration/pipelines")
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["pipeline_name"], "credit_risk_dataset")
        self.assertEqual(row["version_count"], 1)
        self.assertEqual(row["latest_version"], 1)
        self.assertEqual(row["metrics"]["total_runs"], 4)
        self.assertEqual(row["metrics"]["succeeded"], 2)
        self.assertEqual(row["metrics"]["failed"], 1)
        self.assertEqual(row["metrics"]["running"], 1)

    def test_success_rate_ignores_runs_still_in_flight(self):
        self._add_runs()
        metrics = self.client.get("/orchestration/pipelines").json()[0]["metrics"]
        # 2 succeeded, 1 failed, 1 running -> 2/3, not 2/4
        self.assertAlmostEqual(metrics["success_rate"], 66.67, places=1)

    def test_pipeline_list_can_be_filtered_by_project(self):
        other = uuid4()
        response = self.client.get(
            f"/orchestration/pipelines?project_id={other}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_run_history_is_per_pipeline_and_newest_first(self):
        self._add_runs()
        response = self.client.get(
            f"/orchestration/pipelines/{self.pipeline_id}/runs"
        )
        self.assertEqual(response.status_code, 200, response.text)
        runs = response.json()
        self.assertEqual(len(runs), 4)
        timestamps = [run["started_at"] for run in runs]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_run_history_respects_the_limit(self):
        self._add_runs()
        runs = self.client.get(
            f"/orchestration/pipelines/{self.pipeline_id}/runs?limit=2"
        ).json()
        self.assertEqual(len(runs), 2)

    def test_metrics_endpoint_reports_durations_and_a_trend(self):
        self._add_runs()
        response = self.client.get(
            f"/orchestration/pipelines/{self.pipeline_id}/metrics"
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        metrics = body["metrics"]
        self.assertEqual(metrics["avg_duration_seconds"], 37.333)
        self.assertEqual(metrics["max_duration_seconds"], 60.0)
        self.assertEqual(metrics["total_rows_processed"], 242_400)
        # Trend is oldest-first so a sparkline reads left to right.
        trend = body["trend"]
        self.assertEqual(len(trend), 4)
        starts = [point["started_at"] for point in trend]
        self.assertEqual(starts, sorted(starts))

    def test_metrics_for_a_pipeline_that_never_ran_are_empty_not_an_error(self):
        body = self.client.get(
            f"/orchestration/pipelines/{self.pipeline_id}/metrics"
        ).json()
        self.assertEqual(body["metrics"]["total_runs"], 0)
        self.assertIsNone(body["metrics"]["success_rate"])
        self.assertIsNone(body["metrics"]["last_run"])

    def test_dashboard_rolls_up_portfolio_totals(self):
        self._add_runs()
        response = self.client.get("/orchestration/dashboard")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["totals"]["pipelines"], 1)
        self.assertEqual(body["totals"]["total_runs"], 4)
        self.assertEqual(body["totals"]["total_rows_processed"], 242_400)
        self.assertEqual(len(body["pipelines"]), 1)

    def test_dashboard_counts_a_pipeline_that_never_ran(self):
        body = self.client.get("/orchestration/dashboard").json()
        self.assertEqual(body["totals"]["pipelines_never_run"], 1)

    def test_unknown_pipeline_returns_404(self):
        self.assertEqual(
            self.client.get(f"/orchestration/pipelines/{uuid4()}/metrics").status_code,
            404,
        )

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def test_create_schedule_pins_the_latest_saved_version(self):
        response = self._create_schedule()
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["pipeline_version_id"], str(self.version_id))
        self.assertEqual(body["frequency"], "daily")
        self.assertEqual(body["cron_expression"], "0 2 * * *")
        self.assertEqual(body["status"], STATUS_ACTIVE)
        self.assertTrue(body["is_active"])
        self.assertIsNotNone(body["next_run_at"])
        self.assertEqual(body["description"], "Every day at 02:00")

    def test_named_frequencies_resolve_to_their_cron(self):
        for frequency, cron in (
            ("hourly", "0 * * * *"),
            ("daily", "0 2 * * *"),
            ("weekly", "0 2 * * 1"),
            ("monthly", "0 2 1 * *"),
        ):
            with self.subTest(frequency=frequency):
                body = self._create_schedule(frequency=frequency).json()
                self.assertEqual(body["cron_expression"], cron)

    def test_custom_cron_is_accepted_and_scheduled(self):
        body = self._create_schedule(
            frequency="cron", cron_expression="30 6 * * 1-5"
        ).json()
        self.assertEqual(body["cron_expression"], "30 6 * * 1-5")
        next_run = datetime.fromisoformat(body["next_run_at"])
        self.assertEqual((next_run.hour, next_run.minute), (6, 30))
        self.assertLess(next_run.weekday(), 5)

    def test_invalid_cron_is_rejected_with_a_readable_message(self):
        response = self._create_schedule(
            frequency="cron", cron_expression="99 * * * *"
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("minute", response.json()["detail"].lower())

    def test_cron_frequency_without_an_expression_is_rejected(self):
        response = self._create_schedule(frequency="cron")
        self.assertEqual(response.status_code, 422)

    def test_unknown_frequency_is_rejected(self):
        response = self._create_schedule(frequency="fortnightly")
        self.assertEqual(response.status_code, 422)

    def test_one_off_schedule_requires_a_future_time(self):
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        self.assertEqual(
            self._create_schedule(frequency="once", run_at=past).status_code, 422
        )
        future = (datetime.utcnow() + timedelta(hours=3)).isoformat()
        body = self._create_schedule(frequency="once", run_at=future).json()
        self.assertEqual(body["next_run_at"], body["run_at"])

    def test_a_password_in_credential_env_is_rejected(self):
        response = self._create_schedule(credential_env="hunter2!secret")
        self.assertEqual(response.status_code, 422)

    def test_credential_env_is_stored_as_an_uppercased_name(self):
        body = self._create_schedule(credential_env="meridian_pw").json()
        self.assertEqual(body["credential_env"], "MERIDIAN_PW")

    def test_schedule_never_stores_a_password_column(self):
        names = {c.name for c in PipelineSchedule.__table__.columns}
        for forbidden in ("password", "secret", "token", "connection_string"):
            self.assertNotIn(forbidden, names)

    def test_a_version_from_another_pipeline_is_rejected(self):
        response = self._create_schedule(pipeline_version_id=str(uuid4()))
        self.assertEqual(response.status_code, 404)

    def test_a_pipeline_with_no_saved_version_cannot_be_scheduled(self):
        empty_id = uuid4()
        db = self.session_factory()
        try:
            db.add(
                PipelineDefinition(
                    id=empty_id,
                    project_id=self.project_id,
                    pipeline_name="draft_only",
                    status="draft",
                )
            )
            db.commit()
        finally:
            db.close()
        response = self._create_schedule(
            pipeline_definition_id=str(empty_id)
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("no saved version", response.json()["detail"])

    def test_pause_clears_the_next_run_and_resume_recomputes_it(self):
        schedule_id = self._create_schedule().json()["id"]

        paused = self.client.post(
            f"/orchestration/schedules/{schedule_id}/pause"
        ).json()
        self.assertEqual(paused["status"], STATUS_PAUSED)
        self.assertIsNone(paused["next_run_at"])
        self.assertFalse(paused["is_active"])

        resumed = self.client.post(
            f"/orchestration/schedules/{schedule_id}/resume"
        ).json()
        self.assertEqual(resumed["status"], STATUS_ACTIVE)
        self.assertIsNotNone(resumed["next_run_at"])
        # Resuming must schedule forward, never fire for every missed minute.
        self.assertGreater(
            datetime.fromisoformat(resumed["next_run_at"]), datetime.utcnow()
        )

    def test_disable_deactivates_the_schedule(self):
        schedule_id = self._create_schedule().json()["id"]
        body = self.client.post(
            f"/orchestration/schedules/{schedule_id}/disable"
        ).json()
        self.assertEqual(body["status"], STATUS_DISABLED)
        self.assertFalse(body["is_active"])

    def test_update_changes_the_frequency_and_recomputes_the_next_run(self):
        schedule_id = self._create_schedule().json()["id"]
        response = self.client.put(
            f"/orchestration/schedules/{schedule_id}",
            json={"frequency": "hourly"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["frequency"], "hourly")
        self.assertEqual(body["cron_expression"], "0 * * * *")

    def test_delete_is_a_soft_delete_and_hides_the_schedule(self):
        schedule_id = self._create_schedule().json()["id"]
        self.assertEqual(
            self.client.delete(
                f"/orchestration/schedules/{schedule_id}"
            ).status_code,
            204,
        )
        self.assertEqual(
            self.client.get(
                f"/orchestration/schedules/{schedule_id}"
            ).status_code,
            404,
        )
        self.assertEqual(self.client.get("/orchestration/schedules").json(), [])

        db = self.session_factory()
        try:
            row = (
                db.query(PipelineSchedule)
                .filter(PipelineSchedule.id == UUID(schedule_id))
                .first()
            )
            self.assertIsNotNone(row, "row must survive for run history")
            self.assertTrue(row.is_deleted)
        finally:
            db.close()

    def test_due_endpoint_returns_only_active_schedules_that_are_due(self):
        due_id = self._create_schedule(frequency="hourly").json()["id"]
        paused_id = self._create_schedule(frequency="hourly").json()["id"]
        self.client.post(f"/orchestration/schedules/{paused_id}/pause")

        future = (datetime.utcnow() + timedelta(days=2)).isoformat()
        rows = self.client.get(
            f"/orchestration/schedules/due?as_of={future}"
        ).json()
        ids = {row["id"] for row in rows}
        self.assertIn(due_id, ids)
        self.assertNotIn(paused_id, ids)

    def test_nothing_is_due_before_the_next_run(self):
        self._create_schedule(frequency="hourly")
        rows = self.client.get(
            f"/orchestration/schedules/due?as_of={datetime.utcnow().isoformat()}"
        ).json()
        self.assertEqual(rows, [])

    def test_schedules_can_be_filtered_by_pipeline(self):
        self._create_schedule()
        rows = self.client.get(
            f"/orchestration/schedules?pipeline_id={self.pipeline_id}"
        ).json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            self.client.get(
                f"/orchestration/schedules?pipeline_id={uuid4()}"
            ).json(),
            [],
        )

    # ------------------------------------------------------------------
    # Monitoring
    # ------------------------------------------------------------------

    def _alert(self, **overrides):
        payload = {
            "pipeline_definition_id": str(self.pipeline_id),
            "alert_type": "run_failed",
            "severity": "critical",
            "title": "Nightly run failed",
            "message": "Connection to meridian_dw timed out.",
        }
        payload.update(overrides)
        return self.client.post("/orchestration/alerts", json=payload)

    def test_alert_is_recorded_and_opens(self):
        response = self._alert()
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["status"], "open")
        self.assertEqual(body["alert_type"], "run_failed")
        self.assertTrue(body["fingerprint"])

    def test_an_identical_open_alert_is_deduplicated(self):
        first = self._alert().json()
        second = self._alert().json()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.client.get("/orchestration/alerts").json()), 1)

    def test_a_different_alert_type_is_not_deduplicated(self):
        self._alert()
        self._alert(alert_type="schema_change", title="Column type changed")
        self.assertEqual(len(self.client.get("/orchestration/alerts").json()), 2)

    def test_unknown_alert_type_is_rejected(self):
        self.assertEqual(self._alert(alert_type="meteor_strike").status_code, 422)

    def test_unknown_severity_is_rejected(self):
        self.assertEqual(self._alert(severity="apocalyptic").status_code, 422)

    def test_resolving_an_alert_closes_it_and_frees_the_fingerprint(self):
        alert_id = self._alert().json()["id"]
        resolved = self.client.post(
            f"/orchestration/alerts/{alert_id}/resolve"
        ).json()
        self.assertEqual(resolved["status"], "resolved")
        self.assertIsNotNone(resolved["resolved_at"])
        # The same condition recurring after a fix is a new alert, not a
        # silent re-use of the closed one.
        self.assertNotEqual(self._alert().json()["id"], alert_id)

    def test_resolving_an_unknown_alert_returns_404(self):
        self.assertEqual(
            self.client.post(
                f"/orchestration/alerts/{uuid4()}/resolve"
            ).status_code,
            404,
        )

    def test_alerts_can_be_filtered_by_status(self):
        alert_id = self._alert().json()["id"]
        self.client.post(f"/orchestration/alerts/{alert_id}/resolve")
        self.assertEqual(
            len(self.client.get("/orchestration/alerts?status=open").json()), 0
        )
        self.assertEqual(
            len(self.client.get("/orchestration/alerts?status=resolved").json()),
            1,
        )

    def test_monitoring_overview_reports_health_and_open_alerts(self):
        self._add_runs()
        self._alert()
        response = self.client.get("/orchestration/monitoring/overview")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body["pipelines"]), 1)
        health = body["pipelines"][0]
        self.assertEqual(health["open_alerts"], 1)
        self.assertFalse(health["never_succeeded"])
        self.assertEqual(len(body["open_alerts"]), 1)
        self.assertIn("run_failed", body["alert_types"])

    def test_a_pipeline_that_never_succeeded_is_flagged(self):
        body = self.client.get("/orchestration/monitoring/overview").json()
        self.assertTrue(body["pipelines"][0]["never_succeeded"])
        self.assertIsNone(body["pipelines"][0]["hours_since_last_success"])

    def test_staleness_is_only_judged_against_a_declared_sla(self):
        self._add_runs()
        without_sla = self.client.get(
            f"/orchestration/monitoring/overview?pipeline_id={self.pipeline_id}"
        ).json()["pipelines"][0]
        self.assertIsNone(without_sla["freshness_sla_hours"])
        self.assertFalse(without_sla["stale"])

        self._create_schedule(freshness_sla_hours=1)
        with_sla = self.client.get(
            f"/orchestration/monitoring/overview?pipeline_id={self.pipeline_id}"
        ).json()["pipelines"][0]
        self.assertEqual(with_sla["freshness_sla_hours"], 1)
        self.assertTrue(with_sla["stale"], "last success is older than 1 hour")

    def test_monitoring_overview_can_be_scoped_to_one_pipeline(self):
        self.assertEqual(
            self.client.get(
                f"/orchestration/monitoring/overview?pipeline_id={uuid4()}"
            ).status_code,
            404,
        )

    # ------------------------------------------------------------------
    # AutoML handoff
    # ------------------------------------------------------------------

    def test_automl_readiness_explains_why_it_is_not_ready(self):
        response = self.client.get(
            f"/orchestration/pipelines/{self.pipeline_id}/automl-readiness"
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["dataset_ready"])
        self.assertIn("no successful run", body["reason"])
        self.assertEqual(body["target_candidates"], [])

    def test_automl_readiness_reports_a_missing_dataset_file(self):
        self._add_runs()
        body = self.client.get(
            f"/orchestration/pipelines/{self.pipeline_id}/automl-readiness"
        ).json()
        self.assertFalse(body["dataset_ready"])
        self.assertIsNotNone(body["dataset_file_id"])
        self.assertIn("no longer on disk", body["reason"])

    def test_automl_readiness_for_an_unknown_pipeline_is_404(self):
        self.assertEqual(
            self.client.get(
                f"/orchestration/pipelines/{uuid4()}/automl-readiness"
            ).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
