"""AI Pipeline Builder API tests.

SQLite in-memory, dependency override, no network and no real database.
Every plan is created with use_model=False so the heuristic planner runs and
no model is ever called - the whole flow is deterministic and offline.
"""

import json
import unittest
from uuid import UUID, uuid4
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base

# SQLAlchemy configures every registered mapper at once, and routers.plan pulls
# in the pipeline models whose relationships name AgentDecision and friends, so
# the full set must be imported or mapper configuration fails.
# test_catalog_api.py / test_quality_api.py do the same for the same reason.
from models.agent_decision import AgentDecision  # noqa: F401
from models.agent_run import AgentRun  # noqa: F401
from models.cleaning_rule import CleaningRule  # noqa: F401
from models.dataset_profile import DatasetProfile  # noqa: F401
from models.pipeline_run import PipelineRun  # noqa: F401
from models.quality_rule import QualityRule  # noqa: F401
from models.source_profile_snapshot import SourceProfileSnapshot  # noqa: F401
from models.transformation_rule import TransformationRule  # noqa: F401

from models.catalog_column import CatalogColumn
from models.catalog_relationship import CatalogRelationship
from models.catalog_scan import CatalogScan
from models.catalog_table import CatalogTable
from models.data_source import DataSource
from models.pipeline_definition import PipelineDefinition
from models.pipeline_plan import PipelinePlan
from models.pipeline_version import PipelineVersion
from models.plan_recommendation import PlanRecommendation
from models.project import Project
from routers.plan import get_db, router
from services.agents.planner_agent import parse_requirement
from services.plan_builder import build_plan

REQUIREMENT = (
    "Build a credit risk dataset for every loan with the customer and their "
    "repayment behaviour"
)


def _col(name, dtype, pos, pk=False, fk=False, nullable=True):
    return {
        "column_name": name,
        "data_type": dtype,
        "ordinal_position": pos,
        "is_primary_key": pk,
        "is_foreign_key": fk,
        "is_nullable": nullable,
        "normalized_name": name.replace("_", "").lower(),
    }


# A deliberately realistic BFSI schema:
#   customer_master  - dimension, PK customer_id
#   loan_master      - fact,      PK loan_id, FK customer_id (nullable), FK branch_id
#   loan_repayment   - child,     PK repayment_id, FK loan_id -> 1:M fan-out
#   branch_dim       - context only
#   sys_audit_log    - plumbing, must be classified unnecessary
SCHEMA = {
    "customer_master": {
        "row_count": 48_000,
        "has_primary_key": True,
        "columns": [
            _col("customer_id", "INTEGER", 1, pk=True, nullable=False),
            _col("full_name", "VARCHAR(200)", 2),
            _col("declared_income", "NUMERIC(18,2)", 3),
            _col("onboard_date", "VARCHAR(20)", 4),
        ],
    },
    "loan_master": {
        "row_count": 121_500,
        "has_primary_key": True,
        "columns": [
            _col("loan_id", "INTEGER", 1, pk=True, nullable=False),
            _col("customer_id", "INTEGER", 2, fk=True, nullable=True),
            _col("branch_id", "INTEGER", 3, fk=True, nullable=True),
            _col("sanctioned_amount", "NUMERIC(18,2)", 4),
        ],
    },
    "loan_repayment": {
        "row_count": 4_680_000,
        "has_primary_key": True,
        "columns": [
            _col("repayment_id", "BIGINT", 1, pk=True, nullable=False),
            _col("loan_id", "INTEGER", 2, fk=True, nullable=False),
            _col("paid_amount", "NUMERIC(18,2)", 3),
        ],
    },
    "branch_dim": {
        "row_count": 310,
        "has_primary_key": True,
        "columns": [
            _col("branch_id", "INTEGER", 1, pk=True, nullable=False),
            _col("branch_name", "VARCHAR(120)", 2),
        ],
    },
    "sys_audit_log": {
        "row_count": 9_900_000,
        "has_primary_key": True,
        "columns": [
            _col("audit_id", "BIGINT", 1, pk=True, nullable=False),
            _col("payload", "TEXT", 2),
        ],
    },
}

RELATIONSHIPS = [
    # M:1 - many loans to one customer, nullable key
    ("loan_master", "customer_id", "customer_master", "customer_id", "fk_loan_customer"),
    # M:1 - many repayments to one loan, NOT NULL key
    ("loan_repayment", "loan_id", "loan_master", "loan_id", "fk_repay_loan"),
    # 1:M - the fan-out direction, must be flagged
    ("loan_master", "loan_id", "loan_repayment", "loan_id", "fk_loan_repay"),
    # context edge
    ("loan_master", "branch_id", "branch_dim", "branch_id", "fk_loan_branch"),
]


class PlanApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.tables = [
            Project.__table__,
            DataSource.__table__,
            CatalogScan.__table__,
            CatalogTable.__table__,
            CatalogColumn.__table__,
            CatalogRelationship.__table__,
            PipelineDefinition.__table__,
            PipelineVersion.__table__,
            PipelinePlan.__table__,
            PlanRecommendation.__table__,
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

        self.source_id = uuid4()
        self.project_id = uuid4()
        self._seed()

    def tearDown(self):
        Base.metadata.drop_all(bind=self.engine, tables=self.tables)
        self.engine.dispose()

    # ------------------------------------------------------------------
    # fixtures
    # ------------------------------------------------------------------

    def _seed(self, scan_status="completed"):
        db = self.session_factory()
        try:
            db.add(
                Project(
                    id=self.project_id,
                    project_name="Credit Risk",
                    file_id="seed",
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
                    username="svc_ro",
                    schema_name="public",
                    status="scanned",
                )
            )
            scan = CatalogScan(
                id=uuid4(),
                data_source_id=self.source_id,
                schema_name="public",
                status=scan_status,
                table_count=len(SCHEMA),
            )
            db.add(scan)
            db.flush()
            self.scan_id = scan.id

            for table_name, spec in SCHEMA.items():
                table = CatalogTable(
                    id=uuid4(),
                    scan_id=scan.id,
                    data_source_id=self.source_id,
                    schema_name="public",
                    table_name=table_name,
                    column_count=len(spec["columns"]),
                    row_count=spec["row_count"],
                    has_primary_key=spec["has_primary_key"],
                )
                db.add(table)
                db.flush()
                for column in spec["columns"]:
                    db.add(
                        CatalogColumn(
                            id=uuid4(),
                            catalog_table_id=table.id,
                            **column,
                        )
                    )

            for left_t, left_c, right_t, right_c, name in RELATIONSHIPS:
                db.add(
                    CatalogRelationship(
                        id=uuid4(),
                        scan_id=scan.id,
                        data_source_id=self.source_id,
                        left_table=left_t,
                        left_column=left_c,
                        right_table=right_t,
                        right_column=right_c,
                        constraint_name=name,
                        origin="declared",
                        confidence=1.0,
                    )
                )
            db.commit()
        finally:
            db.close()

    def _create_plan(self, requirement=REQUIREMENT):
        return self.client.post(
            "/plan",
            json={
                "data_source_id": str(self.source_id),
                "requirement": requirement,
                "use_model": False,
            },
        )

    def _plan(self):
        response = self._create_plan()
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    @staticmethod
    def _by_ref(body, kind):
        return {rec["ref_key"]: rec for rec in body["recommendations"].get(kind, [])}

    # ------------------------------------------------------------------
    # step 2-3: requirement in, tables classified with a WHY
    # ------------------------------------------------------------------

    def test_create_plan_returns_201_with_recommendations_in_every_stage(self):
        body = self._plan()
        self.assertEqual(body["status"], "draft")
        self.assertEqual(body["intent_origin"], "heuristic")
        self.assertGreater(body["recommendation_count"], 0)
        for kind in ("table", "relationship", "join", "quality_rule"):
            self.assertIn(kind, body["recommendations"], f"missing {kind}")

    def test_every_recommendation_carries_a_reason_and_a_confidence(self):
        body = self._plan()
        for kind, recs in body["recommendations"].items():
            for rec in recs:
                self.assertTrue(rec["reason"].strip(), f"{kind}/{rec['ref_key']}")
                self.assertGreater(rec["confidence"], 0.0, rec["ref_key"])

    def test_tables_are_classified_required_useful_and_unnecessary(self):
        body = self._plan()
        tables = {
            rec["payload"]["table_name"]: rec["payload"]["classification"]
            for rec in body["recommendations"]["table"]
        }
        self.assertEqual(tables["loan_master"], "required")
        self.assertEqual(tables["customer_master"], "required")
        self.assertEqual(tables["loan_repayment"], "required")
        self.assertEqual(tables["sys_audit_log"], "unnecessary")
        self.assertEqual(
            set(tables.values()) & {"required", "unnecessary"},
            {"required", "unnecessary"},
        )

    def test_unnecessary_table_explains_why_it_was_excluded(self):
        body = self._plan()
        audit = self._by_ref(body, "table")["table:sys_audit_log"]
        self.assertIn("plumbing", audit["reason"])
        self.assertEqual(audit["decision"], "pending")

    def test_required_tables_are_preaccepted_and_others_are_not(self):
        body = self._plan()
        for rec in body["recommendations"]["table"]:
            expected = (
                "accepted"
                if rec["payload"]["classification"] == "required"
                else "pending"
            )
            self.assertEqual(rec["decision"], expected, rec["ref_key"])

    # ------------------------------------------------------------------
    # step 4-6: relationships, joins, cardinality, explosion risk
    # ------------------------------------------------------------------

    def test_declared_foreign_keys_become_relationship_recommendations(self):
        body = self._plan()
        rels = self._by_ref(body, "relationship")
        self.assertIn(
            "rel:loan_master.customer_id->customer_master.customer_id", rels
        )
        rel = rels["rel:loan_master.customer_id->customer_master.customer_id"]
        self.assertEqual(rel["origin"], "declared")
        self.assertGreaterEqual(rel["confidence"], 0.9)

    def test_many_to_one_nullable_key_recommends_left_join_with_alternative(self):
        body = self._plan()
        join = self._by_ref(body, "join")[
            "join:loan_master.customer_id->customer_master.customer_id"
        ]
        self.assertEqual(join["payload"]["cardinality"], "M:1")
        self.assertEqual(join["payload"]["join_type"], "left")
        self.assertEqual(join["payload"]["explosion_risk"], "low")
        self.assertFalse(join["payload"]["requires_pre_aggregation"])
        self.assertIn("INNER", join["alternative_reason"])

    def test_many_to_one_not_null_key_recommends_inner_join(self):
        body = self._plan()
        join = self._by_ref(body, "join")[
            "join:loan_repayment.loan_id->loan_master.loan_id"
        ]
        self.assertEqual(join["payload"]["cardinality"], "M:1")
        self.assertEqual(join["payload"]["join_type"], "inner")

    def test_one_to_many_join_is_flagged_as_fan_out_needing_pre_aggregation(self):
        body = self._plan()
        join = self._by_ref(body, "join")[
            "join:loan_master.loan_id->loan_repayment.loan_id"
        ]
        self.assertEqual(join["payload"]["cardinality"], "1:M")
        self.assertEqual(join["payload"]["explosion_risk"], "medium")
        self.assertTrue(join["payload"]["requires_pre_aggregation"])
        self.assertEqual(join["risk_level"], "medium")
        self.assertIn("multiply", join["reason"])
        self.assertIn("fan-out", join["alternative_reason"])

    # ------------------------------------------------------------------
    # step 7-8: transformations and quality rules stay inside the engines
    # ------------------------------------------------------------------

    def test_transformations_only_use_step_types_the_cleaner_supports(self):
        from services.data_cleaner import STEP_TYPES

        body = self._plan()
        recs = body["recommendations"].get("transformation", [])
        self.assertTrue(recs)
        for rec in recs:
            self.assertIn(rec["payload"]["step_type"], STEP_TYPES)

    def test_text_date_column_gets_a_type_conversion_recommendation(self):
        body = self._plan()
        refs = self._by_ref(body, "transformation")
        self.assertIn("tf:convert_data_type:customer_master:onboard_date", refs)

    def test_quality_rules_only_use_supported_rule_types(self):
        from services.quality_rules import SUPPORTED_RULE_TYPES

        body = self._plan()
        recs = body["recommendations"]["quality_rule"]
        self.assertTrue(recs)
        for rec in recs:
            self.assertIn(rec["payload"]["rule_type"], SUPPORTED_RULE_TYPES)
        self.assertIn(
            "qr:uniqueness:error:loan_master:loan_id",
            {rec["ref_key"] for rec in recs},
        )

    def test_explicit_aggregation_intent_becomes_a_catalog_validated_recommendation(self):
        client = Mock()
        client._complete.return_value = json.dumps(
            {
                "use_case": "loan_summary",
                "anchor_entity": "loan",
                "keywords": ["loan"],
                "filters": [],
                "group_by": [
                    {"source_table": "loan_master", "column": "loan_id"}
                ],
                "aggregations": [
                    {
                        "name": "total_sanctioned",
                        "function": "SUM",
                        "source_table": "loan_master",
                        "source_column": "sanctioned_amount",
                        "reason": "Sum the saved amount.",
                    },
                    {
                        "name": "unsafe",
                        "function": "DROP TABLE",
                        "source_table": "loan_master",
                        "source_column": "sanctioned_amount",
                    },
                ],
                "derived_columns": [],
            }
        )
        columns = {
            "loan_master": [
                {"column_name": "loan_id"},
                {"column_name": "sanctioned_amount"},
            ]
        }
        intent = parse_requirement(
            "Sum sanctioned amount by loan",
            ["loan_master"],
            client=client,
            columns_by_table=columns,
        )
        self.assertEqual(len(intent["aggregations"]), 1)
        self.assertEqual(intent["aggregations"][0]["function"], "SUM")

        plan = build_plan(
            {
                "tables": [
                    {
                        "table_name": "loan_master",
                        "row_count": 1,
                        "column_count": 2,
                        "has_primary_key": True,
                    }
                ],
                "columns_by_table": columns,
                "relationships": [],
            },
            intent,
        )
        self.assertEqual(len(plan["aggregations"]), 1)
        self.assertEqual(
            plan["aggregations"][0]["group_by"],
            [{"source_table": "loan_master", "column": "loan_id"}],
        )

    # ------------------------------------------------------------------
    # step 9: human approval - nothing executes on its own
    # ------------------------------------------------------------------

    def test_accept_and_reject_update_the_decision_and_the_counts(self):
        body = self._plan()
        pending = [
            rec
            for recs in body["recommendations"].values()
            for rec in recs
            if rec["decision"] == "pending"
        ]
        self.assertTrue(pending)
        target = pending[0]

        accept = self.client.post(
            f"/plan/{body['id']}/recommendations/{target['id']}/decide",
            json={"decision": "accepted", "decided_by": "piyush"},
        )
        self.assertEqual(accept.status_code, 200, accept.text)
        self.assertEqual(accept.json()["decision"], "accepted")

        reject = self.client.post(
            f"/plan/{body['id']}/recommendations/{target['id']}/decide",
            json={"decision": "rejected"},
        )
        self.assertEqual(reject.status_code, 200)
        self.assertEqual(reject.json()["decision"], "rejected")

        after = self.client.get(f"/plan/{body['id']}").json()
        self.assertEqual(after["status"], "reviewed")
        self.assertEqual(
            after["accepted_count"],
            sum(
                1
                for recs in after["recommendations"].values()
                for rec in recs
                if rec["decision"] in ("accepted", "edited")
            ),
        )

    def test_edit_requires_a_payload_and_the_edit_is_what_compilation_uses(self):
        body = self._plan()
        join = self._by_ref(body, "join")[
            "join:loan_master.customer_id->customer_master.customer_id"
        ]

        missing = self.client.post(
            f"/plan/{body['id']}/recommendations/{join['id']}/decide",
            json={"decision": "edited"},
        )
        self.assertEqual(missing.status_code, 422)

        edited_payload = dict(join["payload"], join_type="inner")
        response = self.client.post(
            f"/plan/{body['id']}/recommendations/{join['id']}/decide",
            json={"decision": "edited", "user_payload": edited_payload},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["user_payload"]["join_type"], "inner")

        db = self.session_factory()
        try:
            rec = (
                db.query(PlanRecommendation)
                .filter(PlanRecommendation.id == UUID(join["id"]))
                .first()
            )
            self.assertEqual(rec.effective_payload["join_type"], "inner")
        finally:
            db.close()

    def test_unknown_decision_is_rejected(self):
        body = self._plan()
        rec = body["recommendations"]["table"][0]
        response = self.client.post(
            f"/plan/{body['id']}/recommendations/{rec['id']}/decide",
            json={"decision": "maybe"},
        )
        self.assertEqual(response.status_code, 422)

    # ------------------------------------------------------------------
    # step 10: visual canvas
    # ------------------------------------------------------------------

    def test_canvas_returns_nodes_for_kept_tables_and_edges_for_joins(self):
        body = self._plan()
        canvas = self.client.get(f"/plan/{body['id']}/canvas")
        self.assertEqual(canvas.status_code, 200, canvas.text)
        payload = canvas.json()

        labels = {node["label"] for node in payload["nodes"]}
        self.assertIn("loan_master", labels)
        self.assertNotIn("sys_audit_log", labels)
        self.assertTrue(payload["edges"])
        for edge in payload["edges"]:
            self.assertIn("cardinality", edge)
            self.assertIn("explosion_risk", edge)
        self.assertIn("stages", payload)

    def test_rejected_table_disappears_from_the_canvas(self):
        body = self._plan()
        rec = self._by_ref(body, "table")["table:loan_repayment"]
        self.client.post(
            f"/plan/{body['id']}/recommendations/{rec['id']}/decide",
            json={"decision": "rejected"},
        )
        canvas = self.client.get(f"/plan/{body['id']}/canvas").json()
        self.assertNotIn(
            "loan_repayment", {node["label"] for node in canvas["nodes"]}
        )

    # ------------------------------------------------------------------
    # step 12: preview before anything runs
    # ------------------------------------------------------------------

    def test_preview_estimates_impact_without_running_sql(self):
        body = self._plan()
        preview = self.client.get(f"/plan/{body['id']}/preview")
        self.assertEqual(preview.status_code, 200, preview.text)
        payload = preview.json()
        self.assertGreater(payload["tables_selected"], 0)
        self.assertIsNotNone(payload["estimated_rows"])
        self.assertIn("row_estimate_basis", payload)
        self.assertIn("duplicate_impact", payload)
        self.assertIn("null_impact", payload)
        self.assertIn("quality_impact", payload)

    def test_preview_warns_when_a_fan_out_join_is_accepted(self):
        body = self._plan()
        join = self._by_ref(body, "join")[
            "join:loan_master.loan_id->loan_repayment.loan_id"
        ]
        self.client.post(
            f"/plan/{body['id']}/recommendations/{join['id']}/decide",
            json={"decision": "accepted"},
        )
        payload = self.client.get(f"/plan/{body['id']}/preview").json()
        self.assertTrue(payload["warnings"])
        self.assertIn("pre-aggregation", payload["warnings"][0])

    # ------------------------------------------------------------------
    # step 13-14: compile freezes the plan; execution never re-plans
    # ------------------------------------------------------------------

    def test_compile_writes_a_pipeline_version_and_freezes_the_decisions(self):
        body = self._plan()
        response = self.client.post(
            f"/plan/{body['id']}/compile",
            json={
                "pipeline_name": "credit_risk_dataset",
                "project_id": str(self.project_id),
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertEqual(result["version_number"], 1)
        self.assertIn("loan_master", result["tables"])
        self.assertIn("never regenerated", result["note"])

        db = self.session_factory()
        try:
            version = (
                db.query(PipelineVersion)
                .filter(PipelineVersion.id == UUID(result["pipeline_version_id"]))
                .first()
            )
            self.assertIsNotNone(version)
            saved = version.pipeline_json
            self.assertEqual(saved["schema_version"], 1)
            self.assertEqual(saved["plan_id"], body["id"])
            self.assertEqual(saved["user_request"], REQUIREMENT)
            self.assertIn("loan_master", saved["tables"])
            self.assertEqual(saved["base_table"], body["intent"]["base_table"])
            self.assertEqual(saved["tables"][0], body["intent"]["base_table"])
            self.assertEqual(
                saved["selected_columns"]["loan_master"],
                ["loan_id", "customer_id", "branch_id", "sanctioned_amount"],
            )
            self.assertEqual(saved["target_column"], "defaulted")
            self.assertIn("transformations", saved)
            self.assertIn("filters", saved)
            self.assertTrue(saved["decisions"])
            # the audit trail covers every recommendation, not only the accepted
            self.assertEqual(
                len(saved["decisions"]), body["recommendation_count"]
            )

            plan = (
                db.query(PipelinePlan)
                .filter(PipelinePlan.id == UUID(body["id"]))
                .first()
            )
            self.assertEqual(plan.status, "compiled")
            self.assertIsNotNone(plan.pipeline_definition_id)
        finally:
            db.close()

    def test_compile_persists_only_approved_structured_filters(self):
        body = self._plan()
        filter_payload = {
            "source_table": "loan_master",
            "column": "sanctioned_amount",
            "operator": "gte",
            "value": 1000,
            "reason": "The requirement specifies a minimum loan amount.",
        }
        db = self.session_factory()
        try:
            db.add(
                PlanRecommendation(
                    plan_id=UUID(body["id"]),
                    kind="filter",
                    ref_key="filter:loan_master.sanctioned_amount",
                    title="Minimum loan amount",
                    payload=filter_payload,
                    reason=filter_payload["reason"],
                    confidence=1.0,
                    risk_level="low",
                    origin="deterministic",
                    decision="accepted",
                    sort_order=999,
                )
            )
            db.commit()
        finally:
            db.close()

        result = self.client.post(
            f"/plan/{body['id']}/compile",
            json={
                "pipeline_name": "Filtered definition",
                "project_id": str(self.project_id),
            },
        )
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(result.json()["filters"], 1)

        db = self.session_factory()
        try:
            version = db.query(PipelineVersion).filter(
                PipelineVersion.id == UUID(result.json()["pipeline_version_id"])
            ).one()
            self.assertEqual(version.pipeline_json["filters"], [filter_payload])
        finally:
            db.close()

    def test_compile_persists_only_approved_aggregations_and_execution_graph(self):
        body = self._plan()
        aggregation = {
            "name": "total_sanctioned",
            "function": "SUM",
            "source_table": "loan_master",
            "source_column": "sanctioned_amount",
            "group_by": [
                {"source_table": "loan_master", "column": "loan_id"}
            ],
            "reason": "Aggregate sanctioned value for every loan.",
        }
        db = self.session_factory()
        try:
            db.add(
                PlanRecommendation(
                    plan_id=UUID(body["id"]),
                    kind="aggregation",
                    ref_key="agg:SUM:loan_master.sanctioned_amount:total_sanctioned",
                    title="Sum sanctioned amount",
                    payload=aggregation,
                    reason=aggregation["reason"],
                    confidence=1.0,
                    risk_level="medium",
                    origin="deterministic",
                    decision="accepted",
                    sort_order=999,
                )
            )
            db.commit()
        finally:
            db.close()

        response = self.client.post(
            f"/plan/{body['id']}/compile",
            json={
                "pipeline_name": "Aggregated definition",
                "project_id": str(self.project_id),
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["aggregations"], 1)

        db = self.session_factory()
        try:
            version = db.query(PipelineVersion).filter(
                PipelineVersion.id == UUID(response.json()["pipeline_version_id"])
            ).one()
            self.assertEqual(version.pipeline_json["aggregations"], [aggregation])
            self.assertEqual(
                version.pipeline_json["group_by"],
                aggregation["group_by"],
            )
            stages = [
                stage["stage"]
                for stage in version.pipeline_json["execution_graph"]
            ]
            self.assertIn("aggregations", stages)
            self.assertEqual(stages[-1], "register_dataset")
        finally:
            db.close()

    def test_compile_rejects_non_executable_approved_derived_expression(self):
        body = self._plan()
        payload = {
            "name": "tenure_days",
            "expression_hint": "DATEDIFF(DAY, customer_master.onboard_date, CURRENT_DATE)",
            "reason": "Candidate feature.",
        }
        with self.session_factory() as db:
            db.add(
                PlanRecommendation(
                    plan_id=UUID(body["id"]),
                    kind="derived_column",
                    ref_key="dc:tenure_days",
                    title="Derived column: tenure_days",
                    payload=payload,
                    reason=payload["reason"],
                    confidence=0.8,
                    risk_level="medium",
                    origin="ai",
                    decision="accepted",
                    sort_order=999,
                )
            )
            db.commit()

        response = self.client.post(
            f"/plan/{body['id']}/compile",
            json={
                "pipeline_name": "Unsupported derived column",
                "project_id": str(self.project_id),
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("expression", response.json()["detail"].lower())

    def test_compiled_definition_is_byte_identical_on_a_second_compile(self):
        """Determinism: the same plan compiles to the same JSON every time."""
        import json

        body = self._plan()
        payloads = []
        for name in ("run_a", "run_b"):
            result = self.client.post(
                f"/plan/{body['id']}/compile",
                json={"pipeline_name": name, "project_id": str(self.project_id)},
            )
            self.assertEqual(result.status_code, 201, result.text)
            db = self.session_factory()
            try:
                version = (
                    db.query(PipelineVersion)
                    .filter(PipelineVersion.id == UUID(result.json()["pipeline_version_id"]))
                    .first()
                )
                payloads.append(json.dumps(version.pipeline_json, sort_keys=True))
            finally:
                db.close()
        self.assertEqual(payloads[0], payloads[1])

    def test_edited_recommendation_is_what_lands_in_the_compiled_definition(self):
        body = self._plan()
        join = self._by_ref(body, "join")[
            "join:loan_master.customer_id->customer_master.customer_id"
        ]
        self.client.post(
            f"/plan/{body['id']}/recommendations/{join['id']}/decide",
            json={
                "decision": "edited",
                "user_payload": dict(join["payload"], join_type="inner"),
            },
        )
        result = self.client.post(
            f"/plan/{body['id']}/compile",
            json={"pipeline_name": "edited", "project_id": str(self.project_id)},
        )
        self.assertEqual(result.status_code, 201, result.text)

        db = self.session_factory()
        try:
            version = (
                db.query(PipelineVersion)
                .filter(PipelineVersion.id == UUID(result.json()["pipeline_version_id"]))
                .first()
            )
            joins = version.pipeline_json["joins"]
            self.assertTrue(joins)
            self.assertEqual(joins[0]["join_type"], "inner")
        finally:
            db.close()

    def test_compile_refuses_when_no_table_is_accepted(self):
        body = self._plan()
        for rec in body["recommendations"]["table"]:
            self.client.post(
                f"/plan/{body['id']}/recommendations/{rec['id']}/decide",
                json={"decision": "rejected"},
            )
        response = self.client.post(
            f"/plan/{body['id']}/compile",
            json={"pipeline_name": "empty", "project_id": str(self.project_id)},
        )
        self.assertEqual(response.status_code, 409)

    def test_compile_rejects_an_unknown_project(self):
        body = self._plan()
        response = self.client.post(
            f"/plan/{body['id']}/compile",
            json={"pipeline_name": "orphan", "project_id": str(uuid4())},
        )
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # guard rails
    # ------------------------------------------------------------------

    def test_plan_creation_requires_a_completed_scan(self):
        db = self.session_factory()
        try:
            scan = db.query(CatalogScan).filter(CatalogScan.id == self.scan_id).first()
            scan.status = "running"
            db.commit()
        finally:
            db.close()

        response = self._create_plan()
        self.assertEqual(response.status_code, 409)
        self.assertIn("scan", response.json()["detail"].lower())

    def test_unknown_source_returns_404(self):
        response = self.client.post(
            "/plan",
            json={
                "data_source_id": str(uuid4()),
                "requirement": REQUIREMENT,
                "use_model": False,
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_blank_requirement_is_rejected(self):
        response = self.client.post(
            "/plan",
            json={
                "data_source_id": str(self.source_id),
                "requirement": "   ",
                "use_model": False,
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_explicit_filter_intent_becomes_an_approvable_recommendation(self):
        from services.agents.planner_agent import parse_requirement
        from services.plan_builder import recommend_filters

        client = Mock()
        client._complete.return_value = json.dumps(
            {
                "use_case": "loan_amount",
                "anchor_entity": "loan",
                "keywords": ["loan"],
                "filters": [
                    {
                        "source_table": "loan_master",
                        "column": "sanctioned_amount",
                        "operator": "gte",
                        "value": 1000,
                        "reason": "The requirement states a minimum amount.",
                    }
                ],
                "derived_columns": [],
            }
        )
        intent = parse_requirement(
            "Build a dataset for loans above 1000",
            ["loan_master"],
            client=client,
            columns_by_table={
                "loan_master": [{"column_name": "sanctioned_amount"}]
            },
        )

        recommendations = recommend_filters(
            intent["filters"],
            {
                "loan_master": [
                    {"column_name": "sanctioned_amount"},
                ]
            },
        )
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["operator"], "gte")
        self.assertEqual(recommendations[0]["value"], 1000)

    def test_list_plans_returns_the_created_plan(self):
        body = self._plan()
        listing = self.client.get("/plan")
        self.assertEqual(listing.status_code, 200)
        self.assertIn(body["id"], {row["id"] for row in listing.json()})

    def test_unknown_plan_returns_404(self):
        self.assertEqual(self.client.get(f"/plan/{uuid4()}").status_code, 404)

    def test_the_planner_receives_schema_names_but_not_row_metadata(self):
        """The planner gets catalog names, but no counts or profile values."""
        from services.agents.planner_agent import _PROMPT

        rendered = _PROMPT.format(
            requirement=REQUIREMENT,
            columns=json.dumps(
                {
                    table: [column["column_name"] for column in spec["columns"]]
                    for table, spec in SCHEMA.items()
                },
                sort_keys=True,
            ),
        )
        self.assertIn("sanctioned_amount", rendered)
        self.assertNotIn("121500", rendered)
        self.assertNotIn("4680000", rendered)
        self.assertIn("no row data", rendered.lower())


if __name__ == "__main__":
    unittest.main()
