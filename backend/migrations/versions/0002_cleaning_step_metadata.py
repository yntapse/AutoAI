"""Add ordered, configurable cleaning step metadata.

Revision ID: 0002_cleaning_step_metadata
Revises: 0001_pipeline_management_metrics
"""

from alembic import context, op
import sqlalchemy as sa

revision = "0002_cleaning_step_metadata"
down_revision = "0001_pipeline_management_metrics"
branch_labels = None
depends_on = None


def _columns():
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("cleaning_rules")
    }


def upgrade() -> None:
    if context.is_offline_mode():
        op.add_column(
            "cleaning_rules",
            sa.Column("step_name", sa.String(), nullable=False, server_default="Cleaning step"),
        )
        op.add_column(
            "cleaning_rules",
            sa.Column("step_order", sa.Integer(), nullable=False, server_default="0"),
        )
        op.add_column(
            "cleaning_rules",
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
        op.add_column(
            "cleaning_rules",
            sa.Column("created_by", sa.String(), nullable=True),
        )
        op.add_column(
            "cleaning_rules",
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.add_column(
            "cleaning_rules",
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        return

    columns = _columns()
    additions = (
        ("step_name", sa.String(), False, "Cleaning step"),
        ("step_order", sa.Integer(), False, "0"),
        ("enabled", sa.Boolean(), False, None),
        ("created_by", sa.String(), True, None),
        ("created_at", sa.DateTime(), False, None),
        ("updated_at", sa.DateTime(), False, None),
    )
    for name, column_type, nullable, default in additions:
        if name in columns:
            continue
        kwargs = {"nullable": nullable}
        if default is not None:
            kwargs["server_default"] = default
        elif name == "enabled":
            kwargs["server_default"] = sa.true()
        elif name in {"created_at", "updated_at"}:
            kwargs["server_default"] = sa.func.now()
        op.add_column(
            "cleaning_rules",
            sa.Column(name, column_type, **kwargs),
        )


def downgrade() -> None:
    if context.is_offline_mode():
        for name in ("updated_at", "created_at", "created_by", "enabled", "step_order", "step_name"):
            op.drop_column("cleaning_rules", name)
        return
    columns = _columns()
    for name in ("updated_at", "created_at", "created_by", "enabled", "step_order", "step_name"):
        if name in columns:
            op.drop_column("cleaning_rules", name)
