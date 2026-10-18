"""Phase 4: join verification history, pipeline schedules, monitoring alerts.

Revision ID: 0005_pipeline_intelligence
Revises: 0004_pipeline_plan
"""

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_pipeline_intelligence"
down_revision = "0004_pipeline_plan"
branch_labels = None
depends_on = None

_TABLES = (
    "monitoring_alerts",
    "pipeline_schedules",
    "join_verifications",
)


def _existing_tables():
    if context.is_offline_mode():
        return set()
    return set(sa.inspect(op.get_bind()).get_table_names())


def _uuid(name, nullable=True, primary_key=False):
    return sa.Column(
        name, postgresql.UUID(as_uuid=True), nullable=nullable, primary_key=primary_key
    )


def upgrade() -> None:
    existing = _existing_tables()

    if "join_verifications" not in existing:
        op.create_table(
            "join_verifications",
            _uuid("id", nullable=False, primary_key=True),
            _uuid("plan_id", nullable=False),
            _uuid("recommendation_id"),
            _uuid("left_source_id"),
            _uuid("right_source_id"),
            sa.Column("left_table", sa.String(), nullable=False),
            sa.Column("left_column", sa.String(), nullable=False),
            sa.Column("right_table", sa.String(), nullable=False),
            sa.Column("right_column", sa.String(), nullable=False),
            sa.Column("method", sa.String(), nullable=False, server_default="single_query"),
            sa.Column("measured", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("cardinality", sa.String(), nullable=True),
            sa.Column("match_rate", sa.Float(), nullable=True),
            sa.Column("orphan_rate", sa.Float(), nullable=True),
            sa.Column("base_rows", sa.BigInteger(), nullable=True),
            sa.Column("related_rows", sa.BigInteger(), nullable=True),
            sa.Column("base_null_keys", sa.BigInteger(), nullable=True),
            sa.Column("inner_rows", sa.BigInteger(), nullable=True),
            sa.Column("left_rows", sa.BigInteger(), nullable=True),
            sa.Column("max_rows_per_key", sa.BigInteger(), nullable=True),
            sa.Column("row_multiplication", sa.Boolean(), nullable=True),
            sa.Column("fanout_risk", sa.String(), nullable=True),
            sa.Column("recommended_join", sa.String(), nullable=True),
            sa.Column("verdict", sa.String(), nullable=False, server_default="unmeasured"),
            sa.Column("explanation", sa.Text(), nullable=True),
            sa.Column("reasons", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("verified_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["plan_id"], ["pipeline_plans.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["recommendation_id"], ["plan_recommendations.id"], ondelete="SET NULL"
            ),
        )
        op.create_index("ix_join_verifications_plan_id", "join_verifications", ["plan_id"])
        op.create_index(
            "ix_join_verifications_recommendation_id",
            "join_verifications",
            ["recommendation_id"],
        )

    if "pipeline_schedules" not in existing:
        op.create_table(
            "pipeline_schedules",
            _uuid("id", nullable=False, primary_key=True),
            _uuid("pipeline_definition_id", nullable=False),
            _uuid("pipeline_version_id", nullable=False),
            _uuid("data_source_id", nullable=False),
            sa.Column("frequency", sa.String(), nullable=False),
            sa.Column("cron_expression", sa.String(), nullable=True),
            sa.Column("run_at", sa.DateTime(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="active"),
            sa.Column("credential_env", sa.String(), nullable=True),
            sa.Column("target_column", sa.String(), nullable=True),
            sa.Column("freshness_sla_hours", sa.Integer(), nullable=True),
            sa.Column("next_run_at", sa.DateTime(), nullable=True),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
            sa.Column("last_run_status", sa.String(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.ForeignKeyConstraint(
                ["pipeline_definition_id"], ["pipeline_definitions.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["pipeline_version_id"], ["pipeline_versions.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"], ondelete="CASCADE"),
        )
        op.create_index(
            "ix_pipeline_schedules_pipeline_definition_id",
            "pipeline_schedules",
            ["pipeline_definition_id"],
        )
        op.create_index(
            "ix_pipeline_schedules_next_run_at", "pipeline_schedules", ["next_run_at"]
        )

    if "monitoring_alerts" not in existing:
        op.create_table(
            "monitoring_alerts",
            _uuid("id", nullable=False, primary_key=True),
            _uuid("pipeline_definition_id"),
            _uuid("data_source_id"),
            _uuid("pipeline_run_id"),
            sa.Column("alert_type", sa.String(), nullable=False),
            sa.Column("severity", sa.String(), nullable=False, server_default="warning"),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("detail", sa.JSON(), nullable=True),
            sa.Column("fingerprint", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="open"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["pipeline_definition_id"], ["pipeline_definitions.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"], ondelete="CASCADE"),
        )
        op.create_index(
            "ix_monitoring_alerts_pipeline_definition_id",
            "monitoring_alerts",
            ["pipeline_definition_id"],
        )
        op.create_index(
            "ix_monitoring_alerts_data_source_id", "monitoring_alerts", ["data_source_id"]
        )
        op.create_index("ix_monitoring_alerts_alert_type", "monitoring_alerts", ["alert_type"])
        op.create_index("ix_monitoring_alerts_fingerprint", "monitoring_alerts", ["fingerprint"])


def downgrade() -> None:
    existing = _existing_tables()
    for table in _TABLES:
        if context.is_offline_mode() or table in existing:
            op.drop_table(table)
