"""AI Pipeline Builder: plans and their reviewable recommendations.

Revision ID: 0004_pipeline_plan
Revises: 0003_metadata_catalog
"""

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_pipeline_plan"
down_revision = "0003_metadata_catalog"
branch_labels = None
depends_on = None

_TABLES = (
    "plan_recommendations",
    "pipeline_plans",
)


def _existing_tables():
    if context.is_offline_mode():
        return set()
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _existing_tables()

    if "pipeline_plans" not in existing:
        op.create_table(
            "pipeline_plans",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("data_source_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("catalog_scan_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("pipeline_definition_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("requirement_text", sa.Text(), nullable=False),
            sa.Column("schema_name", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="draft"),
            sa.Column("intent", sa.JSON(), nullable=False),
            sa.Column("intent_origin", sa.String(), nullable=False, server_default="heuristic"),
            sa.Column("recommendation_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("accepted_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["catalog_scan_id"], ["catalog_scans.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(
                ["pipeline_definition_id"], ["pipeline_definitions.id"], ondelete="SET NULL"
            ),
        )
        op.create_index("ix_pipeline_plans_data_source_id", "pipeline_plans", ["data_source_id"])
        op.create_index("ix_pipeline_plans_catalog_scan_id", "pipeline_plans", ["catalog_scan_id"])
        op.create_index(
            "ix_pipeline_plans_pipeline_definition_id",
            "pipeline_plans",
            ["pipeline_definition_id"],
        )

    if "plan_recommendations" not in existing:
        op.create_table(
            "plan_recommendations",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("ref_key", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("alternative_reason", sa.Text(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("expected_impact", sa.Text(), nullable=True),
            sa.Column("risk_level", sa.String(), nullable=False, server_default="low"),
            sa.Column("origin", sa.String(), nullable=False, server_default="deterministic"),
            sa.Column("decision", sa.String(), nullable=False, server_default="pending"),
            sa.Column("user_payload", sa.JSON(), nullable=True),
            sa.Column("decided_by", sa.String(), nullable=True),
            sa.Column("decided_at", sa.DateTime(), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["plan_id"], ["pipeline_plans.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("plan_id", "ref_key", name="uq_plan_recommendations_ref"),
        )
        op.create_index("ix_plan_recommendations_plan_id", "plan_recommendations", ["plan_id"])
        op.create_index("ix_plan_recommendations_kind", "plan_recommendations", ["kind"])


def downgrade() -> None:
    existing = _existing_tables()
    for table in _TABLES:
        if context.is_offline_mode() or table in existing:
            op.drop_table(table)
